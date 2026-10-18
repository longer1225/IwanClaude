"""
信任闸门（Layer 0 · B 档）的 RPC 级集成测试

用真实 daemon 子进程覆盖 `session.send_message → TrustGate` 这条链路：
未答复时挂起且不落盘、答复 allow 后开跑、deny 立刻拦住、超时 fail-closed。
LLM 指向死端口 + 假 key：provider 能构造（不校验有效性），run 立刻 llm_error，
因此既不需要真 token，也不会因为等待网络而拖慢用例。
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.transport.socket_client import SocketClient


# 功能：把测试 daemon 的信任文件/超时与 LLM 端点都改道，避免碰用户信任档与真实 API
# 设计：信任超时压到 2s（用例内可验证超时分支）；LLM 指向 127.0.0.1:9 死端口，
#       使"放行后开跑"不依赖真实网络，run 以 llm_error 快速收口但仍返回 run_id
@pytest.fixture
def daemon_env(tmp_path: Path) -> dict[str, str]:
    return {
        "DEEPSEEK_API_KEY": "test-key-not-validated",
        "IWAN_LLM_BASE_URL": "http://127.0.0.1:9",
        "IWAN_TRUST_TIMEOUT_S": "2",
    }


# 功能：建一个连上 daemon 并已订阅事件的客户端（返回客户端、事件表与事件循环任务）
# 设计：信任用例既需要 RPC 也需要观察 trust.requested/trust.changed，统一在这里起
#       run_event_loop；调用方负责 close 与取消任务（见各用例 finally）
async def _connect(free_port: int) -> tuple[SocketClient, list[dict[str, Any]], asyncio.Task[None]]:
    client = SocketClient("127.0.0.1", free_port)
    await client.connect()
    events: list[dict[str, Any]] = []

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)

    client.on_event(on_event)
    loop_task = asyncio.create_task(client.run_event_loop())
    await client.send_command("event.subscribe", {"topics": ["trust.*", "run.*"], "scope": "global"})
    return client, events, loop_task


# 功能：未答复信任时发送被挂起、消息不落盘；答复 allow 后 run 启动并返回 run_id
# 设计：用未完成的 send_command 任务 + 稳定 sleep 断言"挂起"（不是超时失败），
#       再以 get_history 为空证明闸门发生在落盘之前——这是 B 档与旧"只通知"的核心差别
async def test_send_held_until_trust_allowed(
    running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path: Path
) -> None:
    proj = tmp_path / "proj_allow"
    proj.mkdir()
    client, events, loop_task = await _connect(free_port)
    try:
        created = await client.send_command(
            "session.create", {"mode": "chat", "cwd": str(proj)}
        )
        assert created.get("trust") == "ask"
        sid = created["session_id"]

        send_task: asyncio.Task[dict[str, Any]] = asyncio.create_task(
            client.send_command("session.send_message", {"session_id": sid, "content": "hi"})
        )
        await asyncio.sleep(0.8)
        assert not send_task.done(), "未答复信任时不应返回"

        history = await client.send_command("session.get_history", {"session_id": sid})
        assert history.get("messages") == [], "闸门拦下的发送不能先落盘用户消息"

        answered = await client.send_command(
            "trust.respond",
            {"session_id": sid, "cwd": str(proj), "decision": "allow", "persistent": False},
        )
        assert answered.get("ok") is True

        result = await asyncio.wait_for(send_task, timeout=30.0)
        assert result.get("run_id"), f"放行后必须启动 run，实际回包：{result}"
        assert result.get("trust_blocked") in (False, None)

        history2 = await client.send_command("session.get_history", {"session_id": sid})
        assert history2.get("messages"), "run 结束后用户消息应已落盘"
    finally:
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)
        await client.close()


# 功能：答复拒绝后发送立即被拦，回包带 trust_blocked 与可读原因，且零落盘
# 设计：deny 分支不能走超时（否则要等满 2s 才知道结果）；用 0.5s 内返回 + 历史为空
#       双重断言，确保"拒绝=不执行"是硬语义而不是文案
async def test_send_blocked_by_deny(
    running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path: Path
) -> None:
    proj = tmp_path / "proj_deny"
    proj.mkdir()
    client, _events, loop_task = await _connect(free_port)
    try:
        created = await client.send_command("session.create", {"mode": "chat", "cwd": str(proj)})
        sid = created["session_id"]
        send_task: asyncio.Task[dict[str, Any]] = asyncio.create_task(
            client.send_command("session.send_message", {"session_id": sid, "content": "hi"})
        )
        await asyncio.sleep(0.6)
        await client.send_command(
            "trust.respond",
            {"session_id": sid, "cwd": str(proj), "decision": "deny", "persistent": False},
        )
        result = await asyncio.wait_for(send_task, timeout=5.0)
        assert result.get("trust_blocked") is True
        assert "拒绝" in str(result.get("trust_reason", ""))
        assert not result.get("run_id")
        history = await client.send_command("session.get_history", {"session_id": sid})
        assert history.get("messages") == []
    finally:
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)
        await client.close()


# 功能：无人答复时按超时 fail-closed；且该会话随即被钉成 deny，第二次发送立即被拦
# 设计：fixture 已把超时压到 2s，等 4s 足够覆盖超时；第二次发送用 1s 内返回断言
#       "转 deny 后不再等待"，避免超时后反复挂满 2s 的体验退化
async def test_send_times_out_and_pins_deny(
    running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path: Path
) -> None:
    proj = tmp_path / "proj_timeout"
    proj.mkdir()
    client, events, loop_task = await _connect(free_port)
    try:
        created = await client.send_command("session.create", {"mode": "chat", "cwd": str(proj)})
        sid = created["session_id"]
        # 事件是异步推送的：给广播留一点到达时间再断言，避免偶发空表
        for _ in range(20):
            if any(e.get("type") == "trust.requested" for e in events):
                break
            await asyncio.sleep(0.1)
        asked = [e for e in events if e.get("type") == "trust.requested"]
        assert asked, "create 应广播 trust.requested 让客户端弹卡"

        result = await client.send_command(
            "session.send_message", {"session_id": sid, "content": "no answer"}
        )
        assert result.get("trust_blocked") is True
        assert "超时" in str(result.get("trust_reason", ""))
        history = await client.send_command("session.get_history", {"session_id": sid})
        assert history.get("messages") == []

        again = await asyncio.wait_for(
            client.send_command("session.send_message", {"session_id": sid, "content": "again"}),
            timeout=1.5,
        )
        assert again.get("trust_blocked") is True, "超时后应按 deny 立即拦下，而不是再等一轮"
    finally:
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)
        await client.close()
