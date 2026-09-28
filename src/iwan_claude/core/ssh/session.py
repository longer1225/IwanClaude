"""
交互式终端会话（M4c）— ssh -tt 子进程池：合帧、水位、击杀

【学习要点】
1. 无 node-pty 的远程 pty 方案：spawn `ssh.exe -tt user@host`，远端 pty 由
   sshd 分配；初始尺寸用 `stty rows/cols; exec $SHELL -l` 包装设定。动态
   resize v1 不传播（协议字段已留）——Windows 下无控制通道可注入 SIGWINCH，
   这是架构限制而非偷懒，写进协议注释防止误读成"没做完"。
2. 合帧参数 30ms/16KB 是"人眼延迟"与"NDJSON 帧率"的折中：终端输出天然是
   小碎块流（逐字符回显、ANSI 序列），每块发一帧会把 bus 和 GUI 都打成筛子；
   攒 30ms 人眼无感，16KB 硬顶保证大刷屏（cat 大文件）不等定时器。
3. 在途水位 1MB：已交给事件总线但尚未送达订阅者的字节计入在途；超限即
   【关会话】而不是丢数据——"终端断线"是可恢复的用户事故，"静默吃屏"是
   不可恢复的信任事故，方向必须选前者（fail-closed 的观感版）。
4. 读循环与发射解耦：reader 只做 read→pending（永不 await 网络），独立
   flusher 把 pending 交给 emit 并背背压。emit 卡住（慢客户端）时 pending
   上涨、水位生效——读循环不被卡死，进程击杀路径永远可达。
5. Job Object 挂 ssh.exe 进程树：daemon 被强杀时句柄回收连坐击杀，
   不留拿着半条 shell 会话的孤儿 ssh。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from iwan_claude.core.ssh.argv import build_ssh_argv
from iwan_claude.core.ssh.keys import key_paths
from iwan_claude.core.tools.job_object import assign_process_to_job, close_job, create_process_job

log = logging.getLogger(__name__)

# 合帧参数：攒 30ms 或攒满 16KB 即发（先到为准）
_COALESCE_S = 0.03
_COALESCE_BYTES = 16 * 1024
# 在途水位：pending + 已发射未完成的总量超过即熔断关会话
_INFLIGHT_LIMIT = 1024 * 1024
# 单次 stdout read 的块大小（4KB 与终端写粒度同量级）
_READ_CHUNK = 4096

# 输出回调：(session_id, base64 数据) → 交给 bus 发射
EmitOutput = Callable[[str, str], Awaitable[None]]
# 关闭回调：(session_id, exit_code, reason) → 发 ssh.closed
EmitClosed = Callable[[str, int, str], Awaitable[None]]


class SshTermSession:
    """
    单条 ssh -tt 交互会话的生命周期

    【学习要点】close() 幂等：用户点关页签、远端 exit、水位熔断三条路都会
    触发关闭，谁先到谁定性 reason，后来的不再覆盖——"closed (user)" 与
    "closed (overflow)" 的区别是排障时唯一的事实来源。
    """

    def __init__(
        self,
        session_id: str,
        conn: dict[str, Any],
        rows: int,
        cols: int,
        emit_output: EmitOutput,
        emit_closed: EmitClosed,
    ) -> None:
        self.session_id = session_id
        self._conn = conn
        self._rows = max(1, int(rows))
        self._cols = max(1, int(cols))
        self._emit_output = emit_output
        self._emit_closed = emit_closed
        self._proc: asyncio.subprocess.Process | None = None
        self._job: int | None = None
        self._pending: list[bytes] = []
        self._pending_bytes = 0
        self._inflight = 0
        self._closed = False
        self._reason = ""
        self._reader: asyncio.Task[None] | None = None
        self._flusher: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    # 启动 ssh 子进程与读/发两个后台任务；失败返回错误文案（None=成功）
    async def start(self) -> str | None:
        priv, _, kh = key_paths()
        identity: str | None = None
        raw_key = str(self._conn.get("key_file") or "").strip()
        if raw_key:
            identity = raw_key
        elif priv.exists():
            identity = str(priv)
        # 初始尺寸 + 换登录 shell 的远端引导命令（-tt 下 stdin 即终端输入通道）
        boot = (
            f"stty rows {self._rows} cols {self._cols} 2>/dev/null; "
            "exec ${SHELL:-/bin/bash} -l"
        )
        argv = build_ssh_argv(
            self._conn, boot, known_hosts=str(kh), identity=identity, allocate_tty=True,
        )
        self._job = create_process_job()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assign_process_to_job(self._job, self._proc.pid)
        except FileNotFoundError:
            close_job(self._job)
            return "ssh 命令不存在——请安装 OpenSSH 客户端"
        except Exception as e:
            close_job(self._job)
            return f"启动 ssh 失败：{type(e).__name__}: {e}"
        self._reader = asyncio.create_task(self._read_loop())
        self._flusher = asyncio.create_task(self._flush_loop())
        return None

    # 读循环：read→pending，永不 await 发射（解耦背压，见模块注释 4）
    async def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        while True:
            try:
                chunk = await proc.stdout.read(_READ_CHUNK)
            except (asyncio.LimitOverrunError, ValueError):
                chunk = b""  # 理论上 -tt 行很长，read(n) 不分行——防御性收口
            if not chunk:
                break
            self._pending.append(chunk)
            self._pending_bytes += len(chunk)
            # 只有攒满 16KB 才提前唤醒发射器；细水长流的输出交给 30ms 节拍
            if self._pending_bytes >= _COALESCE_BYTES:
                self._wake.set()
            if self._pending_bytes + self._inflight > _INFLIGHT_LIMIT:
                await self.close("overflow：输出积压超过 1MB（客户端消费过慢）")
                return
        # EOF 只说明管道关了，进程退出码要等子进程被 reap 后才回填——
        # 不等的话这里读到的 returncode 恒是 None，"exit -1" 会骗过排障的人
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except TimeoutError:
            pass
        rc = proc.returncode if proc.returncode is not None else -1
        if not self._closed:
            await self.close(f"远端会话结束（exit {rc}）")

    # 发射循环：每轮把已攒的字节全部成批发出——提前成批靠 16KB 唤醒，
    # 兜底成批靠 30ms 节拍，两个触发条件共用这同一个取批动作
    async def _flush_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=_COALESCE_S)
            except TimeoutError:
                pass
            self._wake.clear()
            if self._closed:
                break
            batch = self._take_batch()
            if batch is None:
                continue
            self._inflight += len(batch)
            try:
                await self._emit_output(self.session_id, base64.b64encode(batch).decode("ascii"))
            except Exception:
                log.exception("ssh term: 发射输出失败 sid=%s", self.session_id)
                await self.close("事件总线发射失败")
                return
            finally:
                self._inflight -= len(batch)
        # 关闭前清尾：最后一屏输出不该因为熔断而丢
        tail = self._take_batch()
        if tail:
            try:
                await self._emit_output(self.session_id, base64.b64encode(tail).decode("ascii"))
            except Exception:
                pass

    # 取空待发缓冲并拼接；无数据返回 None
    def _take_batch(self) -> bytes | None:
        if not self._pending:
            return None
        batch = b"".join(self._pending)
        self._pending.clear()
        self._pending_bytes = 0
        return batch

    # 键盘输入：写 stdin（空写 no-op；broken pipe 视同远端已死）
    async def write(self, data: bytes) -> str | None:
        if self._closed or self._proc is None or self._proc.stdin is None:
            return "会话已关闭"
        try:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError, ValueError) as e:
            await self.close(f"输入通道断开：{e}")
            return "会话已断开"
        return None

    # 关闭会话（幂等）：定性 reason → 杀进程树 → 停任务 → 发 ssh.closed
    async def close(self, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        self._reason = reason
        proc = self._proc
        rc = -1
        if proc is not None and proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                pass
        if proc is not None:
            rc = proc.returncode if proc.returncode is not None else -1
        close_job(self._job)
        self._job = None
        # 发射器先"温柔退场"：置 _closed 后它下一拍会 break 出环并清完尾批。
        # ssh 被拒即死的场景里，拒绝原因这行诊断恰好全在尾批——硬 cancel
        # 会把最后一段输出生吞成"静默吃屏"，正是水位注释里发誓要避免的方向。
        self._wake.set()
        if self._flusher is not None and self._flusher is not asyncio.current_task():
            try:
                # wait_for 超时自带 cancel：2s 还发不完尾批才认栽强杀
                await asyncio.wait_for(self._flusher, timeout=2.0)
            except Exception as e:
                # 超时=慢客户端吞尾批、异常=发射器 bug——都只降级为"少一屏"，
                # 但必须留痕，否则静默吞屏又回到排障黑箱
                log.warning("ssh term: 等待发射器收尾异常 sid=%s err=%r", self.session_id, e)
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()
        try:
            await self._emit_closed(self.session_id, rc, reason)
        except Exception:
            log.exception("ssh term: 发送关闭事件失败 sid=%s", self.session_id)
        log.info("ssh term: 会话 %s 关闭（%s）", self.session_id, reason)

    @property
    def closed(self) -> bool:
        return self._closed


class SshSessionManager:
    """
    会话池：id → SshTermSession，daemon 退出时 kill_all

    【学习要点】close 时从字典摘除包在 manager 注入的 closed 回调里：
    会话自己只负责"死了要喊"，池子负责"喊完收尸"——所有权单向，
    session 不持有 manager 引用，无析构环。发射器（output/closed 两路）
    都在构造期由 app 注入：manager 与 bus 的关系像 handler 与 server，
    不存在"构造后才接上"的半初始化窗口。
    """

    def __init__(self, emit_output: EmitOutput, emit_closed: EmitClosed) -> None:
        self._sessions: dict[str, SshTermSession] = {}
        self._emit_output = emit_output
        self._emit_closed = emit_closed

    # 开一条会话：建 id、启动 ssh、入池；失败返回 ("", 错误文案)
    async def open(self, conn: dict[str, Any], rows: int, cols: int) -> tuple[str, str]:
        sid = uuid.uuid4().hex[:12]

        async def _closed(session_id: str, code: int, reason: str) -> None:
            self._sessions.pop(session_id, None)
            await self._emit_closed(session_id, code, reason)

        session = SshTermSession(sid, conn, rows, cols, self._emit_output, _closed)
        err = await session.start()
        if err is not None:
            return "", err
        self._sessions[sid] = session
        return sid, ""

    # 按 id 取会话；不存在返回 None
    def get(self, session_id: str) -> SshTermSession | None:
        return self._sessions.get(session_id)

    # 主动关闭（用户点页签 ×）；未知 id 返回 False
    async def close(self, session_id: str, reason: str) -> bool:
        s = self._sessions.get(session_id)
        if s is None:
            return False
        await s.close(reason)
        return True

    # daemon 停机兜底：全数击杀（GUI 侧靠 TCP 断开自行标「已断开」）
    async def kill_all(self) -> None:
        for s in list(self._sessions.values()):
            await s.close("daemon 停机")
