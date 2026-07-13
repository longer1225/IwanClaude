# 导入 Python 3.7+ 的类型注解特性
from __future__ import annotations

# 导入 asyncio（异步 I/O 框架）
import asyncio

# 导入 datetime（用于生成时间戳）
import datetime

# 导入 fnmatch（文件名通配符匹配，用于事件 topic 过滤）
import fnmatch

# 导入 json（用于解析 events.jsonl 文件）
import json

# 导入 logging（用于记录日志）
import logging

# 导入 signal（用于处理系统信号，如 SIGINT、SIGTERM）
import signal

# 导入 time（用于计算运行时长）
import time

# 导入 UTC 时区
from datetime import UTC

# 导入 Path（用于处理文件路径）
from pathlib import Path

# 导入 Any（表示任意类型）
from typing import Any

# 导入 BaseModel（Pydantic 基类，用于数据验证）
from pydantic import BaseModel

# 导入 kama_claude 包（获取版本号）
import kama_claude

# 导入 RPC 命令和响应的数据模型
from kama_claude.core.bus.commands import (
    AgentRunCommand,           # agent.run 命令的请求模型
    AgentRunResult,           # agent.run 命令的响应模型
    EventSubscribeCommand,    # event.subscribe 命令的请求模型
    EventSubscribeResult,     # event.subscribe 命令的响应模型
    PermissionRespondCommand, # permission.respond 命令的请求模型
    PermissionRespondResult,  # permission.respond 命令的响应模型
    PongResult,               # core.ping 命令的响应模型
    SessionCloseCommand,      # session.close 命令的请求模型
    SessionCloseResult,       # session.close 命令的响应模型
    SessionCreateCommand,     # session.create 命令的请求模型
    SessionCreateResult,      # session.create 命令的响应模型
    SessionGetHistoryCommand, # session.get_history 命令的请求模型
    SessionGetHistoryResult,  # session.get_history 命令的响应模型
    SessionSendMessageCommand,# session.send_message 命令的请求模型
    SessionSendMessageResult, # session.send_message 命令的响应模型
)

# 导入事件推送的封装格式
from kama_claude.core.bus.envelope import EventPushEnvelope

# 导入配置相关
from kama_claude.core.config import KamaConfig, get_config

# 导入事件总线
from kama_claude.core.events.bus import EventBus

# 导入日志设置
from kama_claude.core.logging_setup import setup_logging

# 导入权限管理器
from kama_claude.core.permissions.manager import PermissionManager

# 导入策略文件加载函数
from kama_claude.core.permissions.storage import load_policy_file

# 导入 AgentRunner（agent 执行器）
from kama_claude.core.runner import AgentRunner

# 导入 run 相关的辅助函数
from kama_claude.core.runs import events_file, new_run_id

# 导入会话管理相关
from kama_claude.core.session import SessionManager, SessionStore

# 导入追踪记录模型
from kama_claude.core.trace.record import TraceRecord

# 导入追踪日志写入器
from kama_claude.core.trace.writer import TraceWriter

# 导入 IPC 事件广播器
from kama_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster

# 导入 Socket 服务器和连接 writer 获取函数
from kama_claude.core.transport.socket_server import SocketServer, get_connection_writer

# 创建日志记录器（属于当前模块）
logger = logging.getLogger(__name__)


# 返回当前 UTC 时间的 ISO 8601 格式字符串（用于事件时间戳）
# ISO 8601 格式：2026-07-12T10:30:00.123456Z
def _now() -> str:
    return datetime.datetime.now(UTC).isoformat()


# CoreApp 是 KamaClaude 守护进程（daemon）的核心类
# 职责：
# 1. 初始化所有核心组件（EventBus、TraceWriter、SessionManager、PermissionManager）
# 2. 注册 RPC 命令处理器（响应客户端的 JSON-RPC 请求）
# 3. 启动 TCP Socket 服务器，监听客户端连接
# 4. 处理系统信号，实现优雅关闭
class CoreApp:
    def __init__(self) -> None:
        # 守护进程启动时间（用于计算运行时长）
        self._start_time = time.monotonic()
        
        # EventBus：事件总线，用于内部组件间通信
        self._bus = EventBus()
        
        # IpcEventBroadcaster：IPC 事件广播器，用于向客户端推送事件
        # 初始为 None，在 run() 方法中初始化
        self._broadcaster: IpcEventBroadcaster | None = None
        
        # TraceWriter：系统级追踪日志写入器
        # 初始为 None，在 run() 方法中根据配置初始化
        self._trace: TraceWriter | None = None
        
        # KamaConfig：配置对象，包含 host、port、trace、permission 等配置
        # 初始为 None，在 run() 方法中加载
        self._config: KamaConfig | None = None
        
        # 正在运行的 run 任务集合（用于优雅关闭时取消所有任务）
        # 每个元素是 asyncio.Task 对象
        self._running_runs: set[asyncio.Task[Any]] = set()
        
        # SessionManager：会话管理器（管理所有 session 的生命周期）
        # 初始为 None，在 run() 方法中初始化
        self._sessions: SessionManager | None = None
        
        # PermissionManager：权限管理器（处理工具调用的权限审批）
        # 初始为 None，在 run() 方法中初始化
        self._permission_manager: PermissionManager | None = None

    # 处理 core.ping 请求（客户端心跳检测）
    # 参数 params: JSON-RPC 请求参数
    # 返回值: PongResult（包含版本号、运行时长、接收时间）
    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        # 获取客户端标识（可选，用于日志）
        client = params.get("client", "unknown")
        logger.debug("ping from %s", client)
        
        # 返回 PongResult：
        # - server_version: kama_claude 包版本号
        # - uptime_ms: 守护进程运行时长（毫秒）
        # - received_at: 请求接收时间（ISO 8601 格式）
        return PongResult(
            server_version=kama_claude.__version__,
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    # 将 EventBus 事件写入系统级追踪日志（作为 EventBus 的订阅者）
    # 参数 event: EventBus 发布的事件（Pydantic BaseModel）
    # 返回值: 无
    async def _trace_event_handler(self, event: BaseModel) -> None:
        # 断言 _trace 已初始化（应为 True，因为 run() 中已初始化）
        assert self._trace is not None
        
        # 将 Pydantic 模型转换为字典
        event_dict = event.model_dump()
        
        # 创建 TraceRecord 并写入 trace 文件
        # direction="CORE"：表示这是 Core 内部事件
        # layer="event"：表示这是事件层的事件
        # kind="event"：表示这是 EventBus 发布的事件
        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="CORE",
                layer="event",
                kind="event",
                run_id=event_dict.get("run_id"),
                data=event_dict,
            )
        )

    # 处理 agent.run 请求（一次性任务，无 session）
    # 参数 params: JSON-RPC 请求参数
    # 返回值: AgentRunResult（包含 run_id）
    # 流程：创建 one_shot session → 创建 run → 异步执行 → 立即返回 run_id
    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        # 断言 _sessions 已初始化
        assert self._sessions is not None
        
        # 使用 Pydantic 验证请求参数
        cmd = AgentRunCommand.model_validate(params)
        
        # 创建 one_shot 模式的 session（一次性任务，完成后自动关闭）
        # title 取 goal 的前 40 个字符
        session = await self._sessions.create(mode="one_shot", title=cmd.goal[:40])
        
        # 生成新的 run ID
        run_id = new_run_id()
        
        # 创建异步任务执行 run（不等待完成，立即返回）
        run_task = asyncio.create_task(
            self._sessions.send_message(session.id, cmd.goal, run_id=run_id)
        )
        
        # 将任务加入运行集合（用于优雅关闭时取消）
        self._running_runs.add(run_task)
        
        # 添加回调：任务完成后从集合中移除
        run_task.add_done_callback(self._running_runs.discard)
        
        # 立即返回 run_id（客户端通过订阅事件获取实时进度）
        return AgentRunResult(run_id=run_id)

    # 处理 session.create 请求（创建聊天或一次性会话）
    # 参数 params: JSON-RPC 请求参数
    # 返回值: SessionCreateResult（包含 session_id 和 status）
    async def _session_create_handler(self, params: dict[str, Any]) -> SessionCreateResult:
        # 断言 _sessions 已初始化
        assert self._sessions is not None
        
        # 使用 Pydantic 验证请求参数
        cmd = SessionCreateCommand.model_validate(params)
        
        # 创建 session（mode 为 "chat" 或 "one_shot"）
        session = await self._sessions.create(mode=cmd.mode, title=cmd.title)
        
        # 返回 session_id 和当前状态
        return SessionCreateResult(session_id=session.id, status=session.status)

    # 处理 session.send_message 请求（向会话发送用户消息）
    # 参数 params: JSON-RPC 请求参数
    # 返回值: SessionSendMessageResult（包含 run_id）
    # 流程：追加用户消息 → 创建 run → 同步等待完成 → 返回 run_id
    async def _session_send_handler(self, params: dict[str, Any]) -> SessionSendMessageResult:
        # 断言 _sessions 已初始化
        assert self._sessions is not None
        
        # 使用 Pydantic 验证请求参数
        cmd = SessionSendMessageCommand.model_validate(params)
        
        # 调用 SessionManager.send_message()，同步等待 run 完成
        run_id = await self._sessions.send_message(cmd.session_id, cmd.content)
        
        # 返回 run_id（客户端通过订阅事件获取实时进度）
        return SessionSendMessageResult(run_id=run_id)

    # 处理 session.get_history 请求（获取会话的完整消息历史）
    # 参数 params: JSON-RPC 请求参数
    # 返回值: SessionGetHistoryResult（包含 messages 列表）
    async def _session_history_handler(self, params: dict[str, Any]) -> SessionGetHistoryResult:
        # 断言 _sessions 已初始化
        assert self._sessions is not None
        
        # 使用 Pydantic 验证请求参数
        cmd = SessionGetHistoryCommand.model_validate(params)
        
        # 获取会话的完整消息历史
        messages = await self._sessions.get_history(cmd.session_id)
        
        # 返回消息历史
        return SessionGetHistoryResult(messages=messages)

    # 处理 permission.respond 请求（接收客户端的权限审批响应）
    # 参数 params: JSON-RPC 请求参数
    # 返回值: PermissionRespondResult（空响应）
    # 流程：解析 tool_use_id 和 decision → 调用 PermissionManager.respond() → resolve Future
    async def _permission_respond_handler(self, params: dict[str, Any]) -> PermissionRespondResult:
        # 使用 Pydantic 验证请求参数
        cmd = PermissionRespondCommand.model_validate(params)
        
        # 记录日志
        logger.info("permission.respond received tool_use_id=%s decision=%s", cmd.tool_use_id, cmd.decision)
        
        # 检查 PermissionManager 是否已初始化
        if self._permission_manager is None:
            logger.error("permission.respond: PermissionManager not initialized")
            return PermissionRespondResult()
        
        # 调用 PermissionManager.respond()，resolve 对应的 Future
        # 这会唤醒正在等待权限审批的 AgentLoop 协程
        self._permission_manager.respond(cmd.tool_use_id, cmd.decision)
        
        # 返回空响应（客户端不需要额外信息）
        return PermissionRespondResult()

    # 处理 session.close 请求（关闭会话）
    # 参数 params: JSON-RPC 请求参数
    # 返回值: SessionCloseResult（包含 closed 状态）
    async def _session_close_handler(self, params: dict[str, Any]) -> SessionCloseResult:
        # 断言 _sessions 已初始化
        assert self._sessions is not None
        
        # 使用 Pydantic 验证请求参数
        cmd = SessionCloseCommand.model_validate(params)
        
        # 关闭会话
        await self._sessions.close(cmd.session_id)
        
        # 返回 closed 状态
        return SessionCloseResult(status="closed")

    # 处理 event.subscribe 请求（注册客户端事件订阅）
    # 参数 params: JSON-RPC 请求参数
    # 返回值: EventSubscribeResult（包含 subscription_id 和 replayed_count）
    # 流程：可选回放历史事件 → 注册到 IpcEventBroadcaster → 返回订阅 ID
    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        # 使用 Pydantic 验证请求参数
        cmd = EventSubscribeCommand.model_validate(params)
        
        # 获取当前连接的 StreamWriter（用于推送事件）
        # get_connection_writer() 通过 ContextVar 获取当前连接的 writer
        writer = get_connection_writer()

        # 如果请求中指定了 replay_from_run，回放该 run 的历史事件
        replayed_count = 0
        if cmd.replay_from_run is not None:
            replayed_count = await self._replay_events(
                cmd.replay_from_run, writer, cmd.topics
            )

        # 断言 _broadcaster 已初始化
        assert self._broadcaster is not None
        
        # 将客户端的 writer 注册到 IpcEventBroadcaster
        # topics: 客户端感兴趣的事件类型列表（如 "run.*", "tool.*"）
        # scope: 订阅范围（"global" 或特定 session_id）
        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        
        # 返回订阅 ID 和已回放的事件数量
        return EventSubscribeResult(subscription_id=sub_id, replayed_count=replayed_count)

    # 从 events.jsonl 文件向客户端回放匹配 topic 的历史事件
    # 参数 run_id: 要回放的 run ID
    # 参数 writer: 客户端连接的 StreamWriter
    # 参数 topics: 客户端订阅的事件类型列表（支持通配符，如 "run.*"）
    # 返回值: 已回放的事件数量
    async def _replay_events(
        self,
        run_id: str,
        writer: asyncio.StreamWriter,
        topics: list[str],
    ) -> int:
        # 构建 events.jsonl 文件路径
        path = events_file(run_id)
        
        # 如果路径不存在，搜索 sessions 目录下的 events.jsonl
        if not path.exists():
            for candidate in Path("~/.kama/sessions").expanduser().glob(
                f"*/runs/{run_id}/events.jsonl"
            ):
                path = candidate
                break
        
        # 如果还是找不到文件，返回 0
        if not path.exists():
            return 0

        # 计数器：已回放的事件数量
        count = 0
        
        # 逐行读取 events.jsonl 文件
        for line in path.read_text().splitlines():
            # 跳过空行
            if not line:
                continue
            
            # 解析 JSON 行
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # 解析失败，跳过该行
                continue
            
            # 获取事件类型
            event_type: str = event.get("type", "")
            
            # 检查事件类型是否匹配客户端订阅的任何 topic
            # fnmatch.fnmatch() 支持通配符匹配（如 "run.*" 匹配 "run.started"）
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue
            
            # 将事件封装为 EventPushEnvelope 格式
            envelope = EventPushEnvelope(event=event)
            
            # 写入到客户端连接（JSON 字符串 + 换行符）
            writer.write(envelope.model_dump_json().encode() + b"\n")
            
            # 计数 +1
            count += 1

        # 如果有回放事件，刷新写入缓冲区
        if count:
            await writer.drain()
        
        # 返回已回放的事件数量
        return count

    # 启动守护进程的主方法（核心流程）
    # 流程：
    # 1. 加载配置
    # 2. 初始化日志
    # 3. 启动 TraceWriter（系统级追踪）
    # 4. 初始化 PermissionManager（权限管理）
    # 5. 初始化 IpcEventBroadcaster（事件广播）
    # 6. 初始化 SessionManager（会话管理）
    # 7. 创建并启动 SocketServer（TCP 服务器）
    # 8. 注册所有 RPC 命令处理器
    # 9. 等待退出信号（SIGINT/SIGTERM）
    # 10. 优雅关闭（取消运行中的任务、停止服务器、停止 trace）
    async def run(self) -> None:
        # 重置启动时间（用于计算运行时长）
        self._start_time = time.monotonic()
        
        # 加载配置（从环境变量和配置文件）
        self._config = get_config()
        
        # 初始化日志系统
        setup_logging(self._config)

        # 如果配置启用了 trace，初始化 TraceWriter
        if self._config.trace.enabled:
            # 构建 trace 文件路径（展开 ~）
            trace_path = Path(self._config.trace.file).expanduser()
            
            # 创建 TraceWriter 实例
            self._trace = TraceWriter(trace_path)
            
            # 启动 trace（创建后台 drain task）
            await self._trace.start()
            
            # 订阅 EventBus 事件，将所有事件写入 trace
            self._bus.subscribe(self._trace_event_handler)

        # 构建策略文件路径（~/.kama/policy.toml）
        policy_file = Path("~/.kama/policy.toml").expanduser()
        
        # 初始化 PermissionManager（权限管理器）
        # - policy_file: 持久化策略文件路径
        # - timeout_s: 权限审批超时时间（秒）
        self._permission_manager = PermissionManager(
            policy_file=policy_file,
            timeout_s=self._config.permission.timeout_s,
        )
        
        # 记录权限管理器初始化日志
        logger.info(
            "permission manager: timeout_s=%.1f  persistent=%d entries",
            self._config.permission.timeout_s,
            len(load_policy_file(policy_file)),
        )

        # 初始化 IpcEventBroadcaster（IPC 事件广播器）
        # 用于将 EventBus 事件推送给所有订阅的客户端
        self._broadcaster = IpcEventBroadcaster(trace=self._trace)
        
        # 订阅 EventBus 事件，通过 IpcEventBroadcaster 广播给客户端
        self._bus.subscribe(self._broadcaster.handle)
        
        # 构建 sessions 存储目录路径（~/.kama/sessions）
        sessions_root = Path("~/.kama/sessions").expanduser()
        
        # 创建 SessionStore（文件存储层）
        store = SessionStore(sessions_root)
        
        # 初始化 SessionManager（会话管理器）
        # 参数：
        # - store: SessionStore 实例（文件存储）
        # - runner_factory: AgentRunner 工厂函数（每次调用创建新的 runner）
        # - bus: EventBus 实例（用于发布会话相关事件）
        self._sessions = SessionManager(
            store,
            # runner_factory：工厂函数，每次调用创建新的 AgentRunner
            # 为什么用工厂？因为每次 run 需要独立的执行环境（ExecutionContext、TaskManager 等）
            runner_factory=lambda: AgentRunner(  # type: ignore[arg-type]
                self._config,              # 配置对象
                bus=self._bus,             # 事件总线
                trace=self._trace,         # 追踪写入器
                permission_manager=self._permission_manager,  # 权限管理器
            ),
            bus=self._bus,
        )

        # 创建 SocketServer（TCP Socket 服务器）
        # 参数：
        # - host: 监听地址
        # - port: 监听端口
        # - broadcaster: IpcEventBroadcaster（用于推送事件）
        # - trace: TraceWriter（用于记录 IPC 层日志）
        server = SocketServer(
            self._config.host,
            self._config.port,
            self._broadcaster,
            trace=self._trace,
        )
        
        # 注册所有 RPC 命令处理器
        # 每个命令对应一个 handler 方法
        server.register("core.ping", self._ping_handler)
        server.register("agent.run", self._agent_run_handler)
        server.register("event.subscribe", self._subscribe_handler)
        server.register("session.create", self._session_create_handler)
        server.register("session.send_message", self._session_send_handler)
        server.register("session.get_history", self._session_history_handler)
        server.register("session.close", self._session_close_handler)
        server.register("permission.respond", self._permission_respond_handler)

        # 启动 TCP 服务器，返回监听地址
        addr = await server.start()
        
        # 记录启动日志
        logger.info("kama-core %s listening addr=%s", kama_claude.__version__, addr)
        logger.info("config: %s", self._config)

        # 获取当前 asyncio 事件循环
        loop = asyncio.get_running_loop()
        
        # 创建 shutdown 事件（用于等待退出信号）
        shutdown = asyncio.Event()
        
        # 注册 SIGINT 信号处理器（Ctrl+C）
        loop.add_signal_handler(signal.SIGINT, shutdown.set)
        
        # 注册 SIGTERM 信号处理器（kill 命令）
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)

        # 等待 shutdown 事件（阻塞直到收到退出信号）
        await shutdown.wait()

        # === 优雅关闭 ===
        logger.info("shutting down")
        
        # 取消所有正在运行的 run 任务
        for run_task in list(self._running_runs):
            run_task.cancel()
        
        # 等待所有任务完成（返回异常但不抛出）
        if self._running_runs:
            await asyncio.gather(*self._running_runs, return_exceptions=True)
        
        # 停止 SocketServer（关闭所有连接）
        await server.stop()
        
        # 停止 TraceWriter（等待队列清空）
        if self._trace is not None:
            await self._trace.stop()


# 守护进程的同步入口函数
# 调用 asyncio.run() 启动 CoreApp.run() 的事件循环
def run() -> None:
    asyncio.run(CoreApp().run())
