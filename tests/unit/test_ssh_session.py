# 功能：SshTermSession 关闭路径的字节不变式——尾批输出必在 ssh.closed 之前发完，EOF 后退出码等 reap 才定性
# 设计：绕开 start()（不 spawn 真 ssh）：直接摆内部状态 + 假 emit 收集器，
# 把"发射顺序"和"退出码回填"这两个只有异步竞态才会暴露的行为钉成确定性
# 单测。假 proc 的 returncode 走 property：wait() 前恒 None、wait() 后变 255，
# 专门模拟"EOF 早于 reap 到达"的真实时序（rc_test 实测过：不 wait 必读到
# None）——集成层的轮询测不出这两毫秒级窗口。
from __future__ import annotations

import asyncio
import base64
from typing import Any

from iwan_claude.core.ssh.session import SshTermSession


class _FakeStdout:
    # 功能：模拟远端先吐一段输出再 EOF 的 stdout
    # 设计：read() 第一次给数据、之后恒空——读循环靠空 bytes 判 EOF，与真管道语义一致
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, _n: int) -> bytes:
        if self._data:
            out, self._data = self._data, b""
            return out
        return b""


class _FakeProc:
    # 功能：模拟"EOF 已到但 reap 未完"的子进程：returncode 只在 wait() 后回填
    # 设计：wait() 睡一小拍再定 255——若读循环不等 wait，close 拿到的就是 None→-1，
    # 断言 exit 255 即可捕获这次修的竞态；kill 为 no-op 防误杀分支炸测试
    def __init__(self, data: bytes) -> None:
        self.stdout = _FakeStdout(data)
        self._rc: int | None = None

    @property
    def returncode(self) -> int | None:
        return self._rc

    async def wait(self) -> int:
        await asyncio.sleep(0.05)
        if self._rc is None:
            self._rc = 255
        return self._rc

    def kill(self) -> None:
        return None


# 功能：远端 EOF 后，close 定性原因里带真实退出码 255（而非兜底的 -1）
# 设计：只跑 _read_loop（reader 内部自己调 close），假 proc 的 wait 慢半拍——
# 复现"管道先关、进程后收尸"的时序；若回归成"EOF 直接读 returncode"，
# 文案会退化成 exit -1，断言当场失败
async def test_read_loop_waits_reap_for_real_exit_code() -> None:
    closed: list[tuple[int, str]] = []

    async def emit_output(_sid: str, _b64: str) -> None:
        return None

    async def emit_closed(_sid: str, code: int, reason: str) -> None:
        closed.append((code, reason))

    s = SshTermSession("t1", {"host": "h", "user": "u", "port": 22}, 24, 80, emit_output, emit_closed)
    s._proc = _FakeProc(b"refused\r\n")  # type: ignore[assignment]
    await s._read_loop()
    assert closed, "EOF 后必须发 closed"
    code, reason = closed[0]
    assert code == 255 and "exit 255" in reason


# 功能：用户主动 close 时，待发尾批先经 ssh.output 发出，ssh.closed 随后到
# 设计：不起 reader、手动挂 flusher，把 pending 里塞一个"拒绝原因"字节块；
# close 的 2s 温柔等待窗口内 flusher 应自然 break 并清尾——断言用列表
# 顺序（outputs 非空且 closed 最后一条是它），钉死"输出早于关闭事件"这条
# 保证 GUI 先见字后见横幅的顺序契约
async def test_close_flushes_tail_before_closed_event() -> None:
    outputs: list[bytes] = []
    closed: list[tuple[int, str]] = []

    async def emit_output(_sid: str, b64: str) -> None:
        outputs.append(base64.b64decode(b64))

    async def emit_closed(_sid: str, code: int, reason: str) -> None:
        closed.append((code, reason))

    s = SshTermSession("t2", {"host": "h", "user": "u", "port": 22}, 24, 80, emit_output, emit_closed)
    s._pending.append(b"Connection refused")
    s._pending_bytes = len(b"Connection refused")
    s._flusher = asyncio.create_task(s._flush_loop())
    await asyncio.sleep(0)  # 让 flusher 进入 wait_for，确保 close 叫醒的是睡着的它
    await s.close("用户关闭")

    assert b"".join(outputs) == b"Connection refused"
    assert closed == [(-1, "用户关闭")]


# 功能：水位熔断关闭路径仍保持"尾批先于 closed"的同一契约
# 设计：用大量 pending 字节（超 1MB 水位）让 reader 在 EOF 前触发 overflow
# close——区别于上两条的"温和退场"，这条验证熔断场景下 flusher 的收尾
# 等待没有把关闭卡成死锁（close 从 reader 任务发起、_reader 是当前任务
# 的 self-cancel 防护也要走到）
async def test_overflow_close_still_emits_then_closes() -> None:
    outputs: list[bytes] = []
    reasons: list[str] = []

    async def emit_output(_sid: str, b64: str) -> None:
        outputs.append(base64.b64decode(b64))

    async def emit_closed(_sid: str, code: int, reason: str) -> None:
        reasons.append(reason)

    s = SshTermSession("t3", {"host": "h", "user": "u", "port": 22}, 24, 80, emit_output, emit_closed)
    proc: Any = _FakeProc(b"x" * (1024 * 1024 + 8))
    s._proc = proc
    s._flusher = asyncio.create_task(s._flush_loop())
    await asyncio.wait_for(s._read_loop(), timeout=5.0)
    assert reasons and "overflow" in reasons[0]
    assert b"".join(outputs), "熔断也必须把待发尾批发完，不许静默吃屏"
