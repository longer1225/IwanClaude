# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
# 什么是延迟注解？就是让类型注解在运行时才求值，避免循环导入问题
from __future__ import annotations

# 导入 asyncio：Python 的异步 I/O 框架，用于编写并发代码
import asyncio
# 导入 datetime：用于生成时间戳
import datetime
# 导入 fnmatch：用于文件名通配符匹配（如 "session.*" 匹配所有 session 事件）
import fnmatch
# 导入 json：用于序列化/反序列化数据
import json
# 导入 logging：用于日志记录
import logging
# 导入 signal：用于处理系统信号（如 Ctrl+C 中断）
import signal
# 导入 time：用于计算运行时长
import time
# 导入 UTC：标准时间时区
from datetime import UTC
# 导入 Path：面向对象的文件路径操作
from pathlib import Path
# 导入 Any：类型提示，表示任意类型
from typing import Any

# 导入 BaseModel：Pydantic 的基类，用于数据验证和序列化
from pydantic import BaseModel

# 导入 kama_claude：获取版本号
import kama_claude
# 导入所有命令模型：定义 CLI 和 Core 之间的通信协议
# AgentRunCommand：agent.run 命令的请求参数
# AgentRunResult：agent.run 命令的返回结果
# EventSubscribeCommand：event.subscribe 命令的请求参数
# EventSubscribeResult：event.subscribe 命令的返回结果
# PongResult：core.ping 命令的返回结果
# SessionCloseCommand：session.close 命令的请求参数
# SessionCloseResult：session.close 命令的返回结果
# SessionCreateCommand：session.create 命令的请求参数
# SessionCreateResult：session.create 命令的返回结果
# SessionGetHistoryCommand：session.get_history 命令的请求参数
# SessionGetHistoryResult：session.get_history 命令的返回结果
# SessionSendMessageCommand：session.send_message 命令的请求参数
# SessionSendMessageResult：session.send_message 命令的返回结果
from kama_claude.core.bus.commands import (
    AgentRunCommand,
    AgentRunResult,
    EventSubscribeCommand,
    EventSubscribeResult,
    PongResult,
    SessionCloseCommand,
    SessionCloseResult,
    SessionCreateCommand,
    SessionCreateResult,
    SessionGetHistoryCommand,
    SessionGetHistoryResult,
    SessionSendMessageCommand,
    SessionSendMessageResult,
)
# 导入 EventPushEnvelope：包装事件数据，用于通过 IPC 推送给客户端
from kama_claude.core.bus.envelope import EventPushEnvelope
# 导入 KamaConfig 和 get_config：配置类和获取配置的函数
from kama_claude.core.config import KamaConfig, get_config
# 导入 EventBus：事件总线，用于系统内部发布/订阅事件
from kama_claude.core.events.bus import EventBus
# 导入 setup_logging：初始化日志系统
from kama_claude.core.logging_setup import setup_logging
# 导入 AgentRunner：执行 agent 循环的核心类
from kama_claude.core.runner import AgentRunner
# 导入 events_file 和 new_run_id：获取事件文件路径和生成新的 run ID
from kama_claude.core.runs import events_file, new_run_id
# 导入 SessionManager 和 SessionStore：会话管理和持久化存储
from kama_claude.core.session import SessionManager, SessionStore
# 导入 TraceRecord：系统级追踪记录的数据模型
from kama_claude.core.trace.record import TraceRecord
# 导入 TraceWriter：非阻塞的追踪日志写入器
from kama_claude.core.trace.writer import TraceWriter
# 导入 IpcEventBroadcaster：将 EventBus 事件广播到 IPC 客户端
from kama_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster
# 导入 SocketServer 和 get_connection_writer：TCP socket 服务器和获取连接写入器
from kama_claude.core.transport.socket_server import SocketServer, get_connection_writer

# 创建日志记录器（名称为当前模块名）
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.datetime.now(UTC).isoformat()


# CoreApp 类：KamaClaude 的守护进程核心应用
# 什么是守护进程？就是在后台持续运行的进程，负责接收和处理 CLI/TUI 的请求
# 为什么需要守护进程？因为 agent 执行可能需要很长时间，CLI 不能一直等待
# 设计思路：采用客户端-服务器模式，Core 作为唯一的 agent 执行体，CLI/TUI 通过 IPC 通信
class CoreApp:
    # 初始化：创建所有核心组件的实例
    def __init__(self) -> None:
        # 记录启动时间（用于计算运行时长）
        self._start_time = time.monotonic()
        # 创建 EventBus：系统内部的事件总线，用于发布/订阅事件
        self._bus = EventBus()
        # IpcEventBroadcaster：将 EventBus 事件广播到 IPC 客户端（初始为 None，run 时初始化）
        self._broadcaster: IpcEventBroadcaster | None = None
        # TraceWriter：非阻塞的追踪日志写入器（初始为 None，run 时根据配置初始化）
        self._trace: TraceWriter | None = None
        # KamaConfig：配置对象（初始为 None，run 时加载）
        self._config: KamaConfig | None = None
        # 正在运行的 run 任务集合（用于优雅关闭时取消所有任务）
        self._running_runs: set[asyncio.Task[Any]] = set()
        # SessionManager：会话管理器（初始为 None，run 时初始化）
        self._sessions: SessionManager | None = None

    # 处理 core.ping 请求：返回服务版本、运行时长和接收时间
    # 作用：让客户端检查 daemon 是否正在运行
    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        # 获取客户端标识（可选）
        client = params.get("client", "unknown")
        # 记录调试日志
        logger.debug("ping from %s", client)
        # 返回 PongResult：包含版本号、运行时长（毫秒）和接收时间
        return PongResult(
            server_version=kama_claude.__version__,
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    # 将 EventBus 事件写入 trace（作为 EventBus 订阅者）
    # 作用：将系统内部事件记录到全局追踪文件，便于调试
    async def _trace_event_handler(self, event: BaseModel) -> None:
        # 断言 trace 已初始化（应为 True）
        assert self._trace is not None
        # 将 Pydantic 模型转为字典
        event_dict = event.model_dump()
        # 发射追踪记录：方向 CORE（内部事件），层 event，类型 event
        self._trace.emit(
            TraceRecord(
                ts=_now(),           # 时间戳
                direction="CORE",    # 数据流方向（内部事件）
                layer="event",       # 所在层（事件层）
                kind="event",        # 记录类型（事件）
                run_id=event_dict.get("run_id"),  # 关联的 run ID（可选）
                data=event_dict,     # 事件数据
            )
        )

    # 处理 agent.run 请求：异步创建 AgentRunner 并立即返回 run_id
    # 作用：支持一次性任务（one_shot 模式），与 chat 模式的区别是任务完成后自动关闭 session
    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        # 断言 session 管理器已初始化
        assert self._sessions is not None
        # 使用 Pydantic 验证请求参数
        cmd = AgentRunCommand.model_validate(params)
        # 创建 one_shot 模式的 session（任务完成后自动关闭）
        session = await self._sessions.create(mode="one_shot", title=cmd.goal[:40])
        # 生成新的 run ID
        run_id = new_run_id()
        # 创建异步任务执行 agent run（不阻塞当前协程）
        run_task = asyncio.create_task(
            self._sessions.send_message(session.id, cmd.goal, run_id=run_id)
        )
        # 将任务添加到运行中的任务集合（用于优雅关闭）
        self._running_runs.add(run_task)
        # 任务完成后自动从集合中移除
        run_task.add_done_callback(self._running_runs.discard)
        # 立即返回 run_id（不等待任务完成）
        return AgentRunResult(run_id=run_id)

    # 处理 session.create 请求：创建 chat 或 one_shot session，并返回 session_id
    # 作用：为 chat 模式创建持久会话，支持多轮对话
    async def _session_create_handler(self, params: dict[str, Any]) -> SessionCreateResult:
        # 断言 session 管理器已初始化
        assert self._sessions is not None
        # 使用 Pydantic 验证请求参数
        cmd = SessionCreateCommand.model_validate(params)
        # 创建 session（模式由客户端指定：chat 或 one_shot）
        session = await self._sessions.create(mode=cmd.mode, title=cmd.title)
        # 返回 session_id 和当前状态
        return SessionCreateResult(session_id=session.id, status=session.status)

    # 处理 session.send_message 请求：向 session 发送一条用户消息并同步等待对应 run 完成
    # 作用：在已有的 session 中发送消息，共享对话历史
    async def _session_send_handler(self, params: dict[str, Any]) -> SessionSendMessageResult:
        # 断言 session 管理器已初始化
        assert self._sessions is not None
        # 使用 Pydantic 验证请求参数
        cmd = SessionSendMessageCommand.model_validate(params)
        # 发送消息并等待 run 完成（同步等待）
        run_id = await self._sessions.send_message(cmd.session_id, cmd.content)
        # 返回本次 run 的 ID
        return SessionSendMessageResult(run_id=run_id)

    # 处理 session.get_history 请求：返回 session 的完整 Anthropic messages 历史
    # 作用：让客户端获取会话的完整对话记录
    async def _session_history_handler(self, params: dict[str, Any]) -> SessionGetHistoryResult:
        # 断言 session 管理器已初始化
        assert self._sessions is not None
        # 使用 Pydantic 验证请求参数
        cmd = SessionGetHistoryCommand.model_validate(params)
        # 从 SessionStore 读取对话历史
        messages = await self._sessions.get_history(cmd.session_id)
        # 返回消息列表（可直接传给 Anthropic API）
        return SessionGetHistoryResult(messages=messages)

    # 处理 session.close 请求：关闭 session 并返回 closed 状态
    # 作用：释放会话资源，标记会话为已关闭
    async def _session_close_handler(self, params: dict[str, Any]) -> SessionCloseResult:
        # 断言 session 管理器已初始化
        assert self._sessions is not None
        # 使用 Pydantic 验证请求参数
        cmd = SessionCloseCommand.model_validate(params)
        # 关闭 session
        await self._sessions.close(cmd.session_id)
        # 返回关闭状态
        return SessionCloseResult(status="closed")

    # 处理 event.subscribe 请求：注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流
    # 作用：让客户端订阅感兴趣的事件，实现实时更新
    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        # 使用 Pydantic 验证请求参数
        cmd = EventSubscribeCommand.model_validate(params)
        # 获取当前连接的写入器（用于推送事件）
        writer = get_connection_writer()

        # 记录回放的事件数量（默认为 0）
        replayed_count = 0
        # 如果指定了回放 run ID，先回放历史事件
        if cmd.replay_from_run is not None:
            replayed_count = await self._replay_events(
                cmd.replay_from_run, writer, cmd.topics
            )

        # 断言广播器已初始化
        assert self._broadcaster is not None
        # 注册订阅：将客户端写入器添加到广播器的订阅列表
        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        # 返回订阅 ID 和已回放的事件数量
        return EventSubscribeResult(subscription_id=sub_id, replayed_count=replayed_count)

    # 从 events.jsonl 向 writer 回放匹配 topic 的历史事件，返回已回放条数
    # 作用：让新连接的客户端可以看到之前的事件历史
    async def _replay_events(
        self,
        run_id: str,           # 要回放的 run ID
        writer: asyncio.StreamWriter,  # 客户端连接的写入器
        topics: list[str],     # 订阅的事件主题列表
    ) -> int:
        # 获取标准 run 路径下的 events.jsonl 文件
        path = events_file(run_id)
        # 如果标准路径不存在，在 session 目录下查找
        if not path.exists():
            for candidate in Path("~/.kama/sessions").expanduser().glob(
                f"*/runs/{run_id}/events.jsonl"
            ):
                path = candidate
                break
        # 如果文件仍然不存在，返回 0
        if not path.exists():
            return 0

        # 记录已回放的事件数量
        count = 0
        # 逐行读取 events.jsonl
        for line in path.read_text().splitlines():
            # 跳过空行
            if not line:
                continue
            try:
                # 解析 JSON 行
                event = json.loads(line)
            except json.JSONDecodeError:
                # JSON 解析失败，跳过
                continue
            # 获取事件类型
            event_type: str = event.get("type", "")
            # 检查事件类型是否匹配订阅的主题（支持通配符）
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue
            # 包装事件为 Envelope 格式
            envelope = EventPushEnvelope(event=event)
            # 写入客户端连接
            writer.write(envelope.model_dump_json().encode() + b"\n")
            # 增加计数
            count += 1

        # 如果有回放的事件，刷新写入缓冲区
        if count:
            await writer.drain()
        # 返回已回放的事件数量
        return count

    # 启动守护进程：加载配置、初始化日志、启动 trace、启动 TCP 服务器，并等待退出信号
    # 这是 Core daemon 的主入口方法
    async def run(self) -> None:
        # 重置启动时间（确保准确）
        self._start_time = time.monotonic()
        # 加载配置（从环境变量、配置文件等）
        self._config = get_config()
        # 初始化日志系统（根据配置设置日志级别和格式）
        setup_logging(self._config)

        # 如果启用了追踪，初始化 TraceWriter
        if self._config.trace.enabled:
            # 获取追踪文件路径（展开用户目录）
            trace_path = Path(self._config.trace.file).expanduser()
            # 创建 TraceWriter（非阻塞写入）
            self._trace = TraceWriter(trace_path)
            # 启动 TraceWriter（启动 drain task）
            await self._trace.start()
            # 订阅 EventBus：所有事件都记录到 trace
            self._bus.subscribe(self._trace_event_handler)

        # 创建 IpcEventBroadcaster：将 EventBus 事件广播到 IPC 客户端
        self._broadcaster = IpcEventBroadcaster(trace=self._trace)
        # 订阅 EventBus：所有事件都通过广播器推送给订阅的客户端
        self._bus.subscribe(self._broadcaster.handle)

        # 创建会话存储根目录（~/.kama/sessions）
        sessions_root = Path("~/.kama/sessions").expanduser()
        # 创建 SessionStore：负责会话数据的持久化
        store = SessionStore(sessions_root)
        # 创建 SessionManager：管理所有会话的生命周期
        self._sessions = SessionManager(
            store,
            # runner_factory：工厂函数，每次调用创建新的 AgentRunner
            # 为什么用工厂？因为每次 run 需要独立的执行环境
            runner_factory=lambda: AgentRunner(self._config, bus=self._bus, trace=self._trace),  # type: ignore[arg-type]
            bus=self._bus,
        )

        # 创建 SocketServer：TCP socket 服务器，监听客户端连接
        server = SocketServer(
            self._config.host,        # 监听地址（默认 localhost）
            self._config.port,        # 监听端口（默认 10389）
            self._broadcaster,        # 事件广播器（用于推送事件）
            trace=self._trace,        # 追踪写入器（用于记录 IPC 层日志）
        )

        # 注册所有 JSON-RPC 命令处理器
        # core.ping：检查 daemon 是否运行
        server.register("core.ping", self._ping_handler)
        # agent.run：启动一次性任务
        server.register("agent.run", self._agent_run_handler)
        # event.subscribe：订阅事件
        server.register("event.subscribe", self._subscribe_handler)
        # session.create：创建会话
        server.register("session.create", self._session_create_handler)
        # session.send_message：向会话发送消息
        server.register("session.send_message", self._session_send_handler)
        # session.get_history：获取会话历史
        server.register("session.get_history", self._session_history_handler)
        # session.close：关闭会话
        server.register("session.close", self._session_close_handler)

        # 启动 TCP 服务器，返回监听地址
        addr = await server.start()
        # 记录启动日志
        logger.info("kama-core %s listening addr=%s", kama_claude.__version__, addr)
        logger.info("config: %s", self._config)

        # 获取当前 asyncio 事件循环
        loop = asyncio.get_running_loop()
        # 创建 shutdown 事件（用于等待退出信号）
        shutdown = asyncio.Event()
        # 添加 SIGINT 信号处理器（Ctrl+C）
        loop.add_signal_handler(signal.SIGINT, shutdown.set)
        # 添加 SIGTERM 信号处理器（kill 命令）
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)

        # 等待 shutdown 事件（阻塞直到收到退出信号）
        await shutdown.wait()

        # 收到退出信号，开始优雅关闭
        logger.info("shutting down")
        # 取消所有正在运行的 run 任务
        for run_task in list(self._running_runs):
            run_task.cancel()
        # 等待所有任务完成（忽略异常）
        if self._running_runs:
            await asyncio.gather(*self._running_runs, return_exceptions=True)
        # 停止 TCP 服务器
        await server.stop()
        # 停止 trace（等待队列清空）
        if self._trace is not None:
            await self._trace.stop()


# 同步入口函数：启动 CoreApp 事件循环
# 作用：将异步的 CoreApp.run() 包装为同步调用，便于命令行启动
def run() -> None:
    asyncio.run(CoreApp().run())
