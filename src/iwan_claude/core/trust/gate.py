"""
项目信任闸门（Layer 0 · B 档：未答复不启动 run）

【背景】
早期实现是"只通知不等待"：会话创建时广播 trust.requested 弹卡片，但 run 照常启动，
trust=ask 只在工具调用时逐次审批。用户看到"我还没选信任，它就回答了"，观感与
"信任门"的语义不符。本模块把信任做成**真闸门**：

- trust == "allow"：直接放行（不弹卡）；
- trust == "deny" ：直接拒绝，不启动 run（fail-closed）；
- trust == "ask"  ：广播信任卡，**挂起本次发送**，直到用户答复或超时；
  超时按拒绝处理（同样不启动 run）。

【为什么闸门在 send 而不在 create】
create 是"开一个会话看看"的动作（TUI 会批量建会话做浏览），挂起它会拖死界面；
而"要动这个目录"这件事发生在发消息时。所以卡片仍在 create 时先弹（体验好），
但真正的阻塞点是 send_message——由 CoreApp._session_send_handler 调用本闸门。

【豁免】
闸门只挂在 RPC handler 上，因此内部调用（定时任务的 SessionManager.send_message、
工作流节点派生子 Agent）天然绕过——无人值守路径不会被信任卡挂死；这些路径的
工具级审批仍走原有的 fail-closed 超时。

【并发】
一个会话同时只允许一条 send（SessionManager 的 per-session 锁保证），所以
`_waiters` 每个 session 至多一个 Future；多个会话等同一个 cwd 时，一次
trust.respond(cwd) 会唤醒全部（`resolve` 返回值供调用方同步内存信任档）。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from iwan_claude.core.bus.events import TrustRequestedEvent
from iwan_claude.core.trust.store import normalize_dir_key

logger = logging.getLogger(__name__)

# 事件发布函数（CoreApp 注入 EventBus.publish）
PublishFn = Callable[[Any], Awaitable[None]]
# 目录内是否有指令文件（AGENTS.md/CLAUDE.md）——信任卡提示用，注入以便单测
InstructionProbe = Callable[[str], bool]


@dataclass
class TrustGateResult:
    """闸门判定结果：allowed=False 时调用方必须不启动 run"""
    allowed: bool
    reason: str = ""
    timed_out: bool = False


class TrustGate:
    """会话级信任闸门：ask 时挂起发送，直到答复/超时"""

    # 注入事件发布、超时秒数、指令文件探测
    def __init__(
        self,
        publish: PublishFn,
        timeout_s: float = 120.0,
        has_instruction_files: InstructionProbe | None = None,
    ) -> None:
        self._publish = publish
        # <=0 表示不超时（与 permission.timeout_s 的 0 语义一致）
        self._timeout_s = float(timeout_s)
        self._probe = has_instruction_files or (lambda _cwd: False)
        # session_id → 等待中的 Future
        self._waiters: dict[str, asyncio.Future[str]] = {}
        # 规范化 cwd → 等待该目录的 session 集合
        self._by_cwd: dict[str, set[str]] = {}
        # 已广播过卡片的 "session|cwd"（防同一会话重复弹卡）
        self._asked: set[str] = set()

    # 组卡片去重键：会话 + 规范化目录（同一会话换目录要重新问）
    @staticmethod
    def _key(session_id: str, cwd: str) -> str:
        return f"{session_id}|{normalize_dir_key(cwd)}"

    # 广播一次信任卡（已发过则跳过）
    async def note(self, session_id: str, cwd: str) -> None:
        key = self._key(session_id, cwd)
        if key in self._asked:
            return
        self._asked.add(key)
        await self._publish(
            TrustRequestedEvent(
                session_id=session_id,
                cwd=cwd,
                has_instruction_files=self._probe(cwd),
                ts=_now(),
            )
        )
        logger.info("trust gate: asked session=%s cwd=%s", session_id, cwd)

    # 判定并（ask 时）等待：allowed=False 时调用方不得启动 run
    async def check(self, session_id: str, cwd: str, trust: str) -> TrustGateResult:
        if trust == "allow":
            return TrustGateResult(allowed=True)
        if trust == "deny":
            logger.info("trust gate: deny session=%s cwd=%s", session_id, cwd)
            return TrustGateResult(allowed=False, reason="该目录已被标记为不可信，任务未启动")

        # ask：先保证卡片已发，再挂起等待答复
        await self.note(session_id, cwd)
        loop = asyncio.get_running_loop()
        fut = self._waiters.get(session_id)
        if fut is None or fut.done():
            fut = loop.create_future()
            self._waiters[session_id] = fut
            self._by_cwd.setdefault(normalize_dir_key(cwd), set()).add(session_id)
        timeout = self._timeout_s if self._timeout_s > 0 else None
        try:
            decision = await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            logger.info(
                "trust gate: timeout session=%s cwd=%s after %.0fs (fail-closed)",
                session_id, cwd, self._timeout_s,
            )
            return TrustGateResult(
                allowed=False,
                reason=f"等待信任答复超时（{self._timeout_s:.0f}s），已按拒绝处理",
                timed_out=True,
            )
        finally:
            self._drop(session_id, cwd, fut)
        if decision == "allow":
            logger.info("trust gate: allow session=%s cwd=%s", session_id, cwd)
            return TrustGateResult(allowed=True)
        logger.info("trust gate: denied by user session=%s cwd=%s", session_id, cwd)
        return TrustGateResult(allowed=False, reason="用户拒绝信任该目录，任务未启动")

    # 答复到达：唤醒指定会话与同目录的所有等待者，返回被唤醒的 session_id
    def resolve(self, session_id: str, cwd: str, decision: str) -> list[str]:
        targets: set[str] = set()
        if session_id and session_id in self._waiters:
            targets.add(session_id)
        if cwd:
            key = normalize_dir_key(cwd)
            targets |= self._by_cwd.get(key, set())
        resolved: list[str] = []
        for sid in sorted(targets):
            fut = self._waiters.get(sid)
            if fut is None or fut.done():
                continue
            fut.set_result(decision)
            resolved.append(sid)
        return resolved

    # 会话结束：作废其待答复闸门（按拒绝收口，避免发送协程永久挂起）
    def forget(self, session_id: str) -> None:
        fut = self._waiters.pop(session_id, None)
        for key, ids in list(self._by_cwd.items()):
            ids.discard(session_id)
            if not ids:
                self._by_cwd.pop(key, None)
        for key in [k for k in self._asked if k.startswith(f"{session_id}|")]:
            self._asked.discard(key)
        if fut is not None and not fut.done():
            fut.set_result("deny")

    # 从等待表里摘掉一个会话（正常收尾路径）
    def _drop(self, session_id: str, cwd: str, fut: asyncio.Future[str]) -> None:
        if self._waiters.get(session_id) is fut:
            self._waiters.pop(session_id, None)
        key = normalize_dir_key(cwd)
        ids = self._by_cwd.get(key)
        if ids is not None:
            ids.discard(session_id)
            if not ids:
                self._by_cwd.pop(key, None)

    # 观测用：当前等待答复的会话数
    def pending_count(self) -> int:
        return len(self._waiters)


# 本地时间戳（与事件模型一致：ISO 8601 UTC）
def _now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()
