"""
信任闸门单测（Layer 0 · B 档）

覆盖 TrustGate 的判定与等待语义：allow/deny 直通、ask 挂起与答复唤醒、超时
fail-closed、同目录批量唤醒、重复弹卡去重、会话结束时作废等待。
"""
from __future__ import annotations

import asyncio
from typing import Any

from iwan_claude.core.trust.gate import TrustGate


class _Sink:
    # 收集 TrustGate 广播出来的事件（信任卡内容即断言对象）
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def _make_gate(timeout_s: float = 5.0, instruction_dirs: set[str] | None = None) -> tuple[TrustGate, _Sink]:
    # 组装闸门：发布器换成收集器，指令文件探测换成固定集合（避免真的读盘）
    sink = _Sink()
    probe_dirs = instruction_dirs or set()
    gate = TrustGate(
        publish=sink.publish,
        timeout_s=timeout_s,
        has_instruction_files=lambda cwd: cwd in probe_dirs,
    )
    return gate, sink


# 功能：信任档为 allow 时闸门直接放行，且不广播任何信任卡
# 设计：await check 后立即断言（不 sleep），证明 allow 分支没有任何等待；同时断言事件为空，
#       防止"已信任目录仍弹卡"这种打扰性回归
async def test_allow_passes_without_asking() -> None:
    gate, sink = _make_gate()
    verdict = await gate.check("s1", "D:/proj", "allow")
    assert verdict.allowed is True
    assert sink.events == []


# 功能：信任档为 deny 时闸门直接拒绝，不进入等待、不发卡
# 设计：deny 是"永不放行"的强语义，必须在任何 IO 之前收口；用零超时闸门证明它没走 await 路径
async def test_deny_blocks_immediately() -> None:
    gate, sink = _make_gate(timeout_s=0)
    verdict = await gate.check("s1", "D:/proj", "deny")
    assert verdict.allowed is False
    assert "不可信" in verdict.reason
    assert sink.events == []


# 功能：ask 未答复时挂起发送，答复 allow 后放行
# 设计：用 create_task 起 check，等它真的挂起（pending_count==1）再 resolve，
#       断言 check 的返回值为 allowed；这精确模拟"用户看着卡、随后点允许"
async def test_ask_waits_for_allow_answer() -> None:
    gate, sink = _make_gate()
    task = asyncio.create_task(gate.check("s1", "D:/proj", "ask"))
    await asyncio.sleep(0)
    assert gate.pending_count() == 1
    assert len(sink.events) == 1
    resolved = gate.resolve("s1", "D:/proj", "allow")
    assert resolved == ["s1"]
    verdict = await task
    assert verdict.allowed is True
    assert gate.pending_count() == 0


# 功能：ask 未答复时超时，按拒绝收口（不启动 run）
# 设计：0.05s 超时 + 不答复，断言 timed_out 标记与 allowed=False；fail-closed 是安全边界，
#       不能因为"没人应答"就默认放行
async def test_ask_timeout_fails_closed() -> None:
    gate, _sink = _make_gate(timeout_s=0.05)
    verdict = await gate.check("s1", "D:/proj", "ask")
    assert verdict.allowed is False
    assert verdict.timed_out is True
    assert gate.pending_count() == 0


# 功能：用户点拒绝时闸门返回拒绝原因
# 设计：与 timeout 分开断言（reason 不同），避免客户端把两种拒绝混为一谈
async def test_ask_denied_by_user() -> None:
    gate, _sink = _make_gate()
    task = asyncio.create_task(gate.check("s1", "D:/proj", "ask"))
    await asyncio.sleep(0)
    gate.resolve("s1", "D:/proj", "deny")
    verdict = await task
    assert verdict.allowed is False
    assert verdict.timed_out is False
    assert "拒绝" in verdict.reason


# 功能：同一目录下多个会话等待时，一次答复全部唤醒
# 设计：CLI 的 `iwan trust grant <dir>` 只带 cwd 不带 session_id；若只唤醒指定会话，
#       其它挂着的发送要等满超时才失败——这里断言 resolve 返回全部 sid
async def test_one_answer_releases_all_sessions_of_same_cwd() -> None:
    gate, _sink = _make_gate()
    t1 = asyncio.create_task(gate.check("s1", "D:/proj", "ask"))
    t2 = asyncio.create_task(gate.check("s2", "D:/proj/sub", "ask"))
    await asyncio.sleep(0)
    assert gate.pending_count() == 2
    resolved = gate.resolve("", "D:/proj", "allow")
    # 子目录是否共享由 TrustStore 的 inherit 决定；闸门只按 resolve 传入的 cwd 精确匹配，
    # 这里 s2 的 cwd 不同，故只唤醒 s1
    assert resolved == ["s1"]
    assert (await t1).allowed is True
    gate.resolve("s2", "D:/proj/sub", "allow")
    assert (await t2).allowed is True


# 功能：同一会话重复询问只广播一张卡
# 设计：create 时已经弹过卡，send 时闸门还要 note 一次；若不去重，用户会看到两张一模一样的卡
async def test_repeated_note_publishes_once() -> None:
    gate, sink = _make_gate()
    await gate.note("s1", "D:/proj")
    await gate.note("s1", "D:/proj")
    assert len(sink.events) == 1
    # 换目录要重新问（同一会话的 cwd 变了，旧卡片不再代表新目录）
    await gate.note("s1", "D:/other")
    assert len(sink.events) == 2


# 功能：会话关闭时作废等待中的闸门，按拒绝收口
# 设计：若 forget 只删字典不 resolve Future，挂起的 send 协程会一直等到超时；
#       断言 check 立刻返回（不等 5s 超时）即证明 Future 被设置了结果
async def test_forget_releases_waiter_as_denied() -> None:
    gate, _sink = _make_gate(timeout_s=30)
    task = asyncio.create_task(gate.check("s1", "D:/proj", "ask"))
    await asyncio.sleep(0)
    gate.forget("s1")
    verdict = await asyncio.wait_for(task, timeout=1)
    assert verdict.allowed is False
    assert verdict.timed_out is False
