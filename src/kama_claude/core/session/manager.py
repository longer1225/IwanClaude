# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：用于异步编程和锁
import asyncio
# 导入 uuid：用于生成唯一的 session ID
import uuid
# 导入 Callable：类型提示，表示可调用对象
from collections.abc import Callable
# 导入 datetime：用于生成时间戳
from datetime import UTC, datetime
# 导入 TYPE_CHECKING 和 Any：类型提示
from typing import TYPE_CHECKING, Any

# 导入 HandlerError：JSON-RPC 错误类型
from kama_claude.core.bus.envelope import HandlerError
# 导入会话相关事件
from kama_claude.core.bus.events import (
    SessionClosedEvent,
    SessionCreatedEvent,
    SessionMessageReceivedEvent,
    SessionResumedEvent,
    SessionWaitingForInputEvent,
)
# 导入 EventBus：事件总线
from kama_claude.core.events.bus import EventBus
# 导入 new_run_id：生成新的 run ID
from kama_claude.core.runs import new_run_id
# 导入 Session 和 SessionMode：会话模型和模式
from kama_claude.core.session.model import Session, SessionMode
# 导入 SessionStore：会话存储
from kama_claude.core.session.store import SessionStore

# TYPE_CHECKING：仅在类型检查时导入（避免循环导入）
if TYPE_CHECKING:
    from kama_claude.core.runner import AgentRunner

# JSON-RPC 错误码：
# -32010：session 不存在
SESSION_NOT_FOUND = -32010
# -32011：session 已关闭
SESSION_CLOSED = -32011
# -32012：session 正忙（正在处理消息）
SESSION_BUSY = -32012


# 返回当前 UTC 时间的 ISO 8601 字符串
def _now() -> str:
    return datetime.now(UTC).isoformat()


# SessionManager 类：管理所有会话的生命周期
# 什么是会话管理？就是负责创建、维护、关闭会话，确保会话状态正确
class SessionManager:
    # 初始化：注入文件存储、runner 工厂和事件总线
    def __init__(
        self,
        store: SessionStore,           # 文件存储（负责读写文件）
        runner_factory: Callable[[], AgentRunner],  # runner 工厂（创建新的 AgentRunner）
        bus: EventBus,                 # 事件总线（发布会话事件）
    ) -> None:
        self._store = store                  # 文件存储
        self._runner_factory = runner_factory  # runner 工厂
        self._bus = bus                      # 事件总线
        self._sessions: dict[str, Session] = {}  # 内存中的会话缓存（sid → Session）
        self._locks: dict[str, asyncio.Lock] = {}  # 会话级别的锁（防止并发访问）

    # 创建新会话
    async def create(self, mode: SessionMode, title: str = "") -> Session:
        # 生成唯一的 session ID（格式：sess-xxxxxxxxxxxx）
        sid = f"sess-{uuid.uuid4().hex[:12]}"
        # 获取当前时间
        ts = _now()
        # 创建 Session 对象
        session = Session(
            id=sid,           # 会话 ID
            mode=mode,        # 会话模式（one_shot 或 chat）
            status="active",  # 初始状态为 active
            title=title,      # 会话标题
            created_at=ts,    # 创建时间
            updated_at=ts,    # 更新时间
            run_ids=[],       # 关联的 run ID 列表（初始为空）
        )
        # 将 session 添加到内存缓存
        self._sessions[sid] = session
        # 为 session 创建锁（防止并发操作）
        self._locks[sid] = asyncio.Lock()
        # 将 session 元数据写入文件
        self._store.write_meta(session)
        # 发布 SessionCreatedEvent 事件（通知其他组件）
        await self._bus.publish(SessionCreatedEvent(session_id=sid, mode=mode, ts=ts))
        # 返回创建的 session
        return session

    # 处理用户消息：追加到对话历史并启动一次 agent run
    async def send_message(self, sid: str, content: str, *, run_id: str | None = None) -> str:
        # 获取 session（不存在则报错）
        session = self._get_session(sid)
        # 获取 session 的锁
        lock = self._locks[sid]
        # 如果锁已被持有，说明 session 正在处理其他消息
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")

        # 获取锁（异步上下文管理器）
        async with lock:
            # 如果 session 已关闭，报错
            if session.status == "closed":
                raise HandlerError(SESSION_CLOSED, "session already closed")

            # 如果 session 正在等待输入，发布恢复事件
            if session.status == "waiting_for_input":
                await self._bus.publish(SessionResumedEvent(session_id=sid, ts=_now()))

            # 将用户消息追加到 thread.jsonl（对话历史）
            self._store.append_message(sid, "user", content)
            # 发布消息接收事件
            await self._bus.publish(
                SessionMessageReceivedEvent(session_id=sid, content=content, ts=_now())
            )

            # 如果会话没有标题，用第一条消息的前 40 个字符作为标题
            if not session.title:
                session.title = content[:40]

            # 生成 run ID（如果没有指定）
            run_id = run_id or new_run_id()
            # 将 run ID 添加到 session 的 run_ids 列表
            session.run_ids.append(run_id)
            # 更新会话的更新时间
            session.updated_at = _now()
            # 将更新后的 session 元数据写入文件
            self._store.write_meta(session)

            # 创建 AgentRunner（通过工厂方法）
            runner = self._runner_factory()
            # 执行 agent run（等待完成）
            await runner.run_and_capture(
                content,          # 用户消息内容（作为本次 run 的 goal）
                run_id=run_id,    # run ID
                session=session,  # 当前 session（用于获取对话历史）
                store=self._store,  # 会话存储（用于写入笔记）
            )

            # 更新会话的更新时间
            session.updated_at = _now()
            # 根据会话模式设置状态
            if session.mode == "one_shot":
                # one_shot 模式：任务完成后自动关闭
                session.status = "closed"
                await self._bus.publish(SessionClosedEvent(session_id=sid, ts=session.updated_at))
            else:
                # chat 模式：等待用户输入下一条消息
                session.status = "waiting_for_input"
                await self._bus.publish(
                    SessionWaitingForInputEvent(
                        session_id=sid,
                        last_run_id=run_id,
                        ts=session.updated_at,
                    )
                )
            # 将更新后的 session 元数据写入文件
            self._store.write_meta(session)
            # 返回 run ID
            return run_id

    # 关闭指定 session
    async def close(self, sid: str) -> None:
        # 获取 session（不存在则报错）
        session = self._get_session(sid)
        # 获取 session 的锁
        lock = self._locks[sid]
        # 如果锁已被持有，说明 session 正在处理其他消息
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")
        # 获取锁（异步上下文管理器）
        async with lock:
            # 设置会话状态为 closed
            session.status = "closed"
            # 更新会话的更新时间
            session.updated_at = _now()
            # 将更新后的 session 元数据写入文件
            self._store.write_meta(session)
            # 发布 SessionClosedEvent 事件
            await self._bus.publish(SessionClosedEvent(session_id=sid, ts=session.updated_at))

    # 读取指定 session 的完整对话历史
    async def get_history(self, sid: str) -> list[dict[str, Any]]:
        # 验证 session 存在
        self._get_session(sid)
        # 从文件存储读取对话历史
        return self._store.read_messages(sid)

    # 从内存缓存获取 session（内部方法）
    # 不存在时抛出 JSON-RPC 结构化错误
    def _get_session(self, sid: str) -> Session:
        session = self._sessions.get(sid)
        if session is None:
            raise HandlerError(SESSION_NOT_FOUND, "session not found")
        return session
