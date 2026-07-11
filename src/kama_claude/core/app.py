# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：用于异步编程
import asyncio
# 导入 datetime：用于生成时间戳
import datetime
# 导入 fnmatch：用于文件名匹配
import fnmatch
# 导入 json：用于 JSON 序列化/反序列化
import json
# 导入 logging：用于日志记录
import logging
# 导入 signal：用于处理系统信号（如 Ctrl+C）
import signal
# 导入 time：用于计时
import time
# 导入 datetime：用于生成时间戳
from datetime import UTC
# 导入 Path：用于文件路径操作
from pathlib import Path
# 导入 Any：类型注解
from typing import Any

# 导入 pydantic：用于数据验证和序列化
from pydantic import BaseModel

# 导入包版本信息
import kama_claude
# 导入命令模型：定义 JSON-RPC 请求和响应的结构
from kama_claude.core.bus.commands import (
    AgentRunCommand,
    AgentRunResult,
    EventSubscribeCommand,
    EventSubscribeResult,
    PongResult,
)
# 导入事件推送信封：用于封装事件推送消息
from kama_claude.core.bus.envelope import EventPushEnvelope
# 导入配置类：用于加载配置
from kama_claude.core.config import KamaConfig, get_config
# 导入事件总线：用于发布/订阅事件
from kama_claude.core.events.bus import EventBus
# 导入日志设置：用于配置日志
from kama_claude.core.logging_setup import setup_logging
# 导入 AgentRunner：用于执行 agent run
from kama_claude.core.runner import AgentRunner
# 导入运行相关的函数：用于生成 run_id 和获取事件文件路径
from kama_claude.core.runs import events_file, new_run_id
# 导入追踪记录：用于创建追踪记录
from kama_claude.core.trace.record import TraceRecord
# 导入追踪写入器：用于写入追踪记录
from kama_claude.core.trace.writer import TraceWriter
# 导入 IPC 广播器：用于向订阅的客户端广播事件
from kama_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster
# 导入 Socket 服务器：用于监听 TCP 连接
from kama_claude.core.transport.socket_server import SocketServer, get_connection_writer

# 创建日志记录器
logger = logging.getLogger(__name__)


# 返回当前时间的 ISO 格式字符串（UTC 时区）
def _now() -> str:
    return datetime.datetime.now(UTC).isoformat()


# CoreApp 类：守护进程的主入口
# 什么是守护进程？就是在后台持续运行的进程，等待客户端连接并处理请求
class CoreApp:
    def __init__(self) -> None:
        # 启动时间（用于计算运行时长）
        self._start_time = time.monotonic()
        # 事件总线：用于内部事件通信
        self._bus = EventBus()
        # IPC 广播器：用于向客户端广播事件（初始为 None，run 时初始化）
        self._broadcaster: IpcEventBroadcaster | None = None
        # 追踪写入器：用于写入追踪记录（初始为 None，run 时根据配置初始化）
        self._trace: TraceWriter | None = None
        # 配置对象：用于存储配置（初始为 None，run 时加载）
        self._config: KamaConfig | None = None
        # 正在运行的任务集合：用于管理和取消运行中的任务
        self._running_runs: set[asyncio.Task[None]] = set()

    # 处理 core.ping 请求，返回服务版本、运行时长和接收时间
    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        # 获取客户端标识（默认为 unknown）
        client = params.get("client", "unknown")
        # 记录日志
        logger.debug("ping from %s", client)
        # 返回 PongResult
        return PongResult(
            server_version=kama_claude.__version__,
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    # 将 EventBus 事件写入 trace（作为 EventBus 订阅者）
    async def _trace_event_handler(self, event: BaseModel) -> None:
        # 确保追踪写入器已经初始化
        assert self._trace is not None
        # 将事件转换为字典
        event_dict = event.model_dump()
        # 写入追踪记录：方向 CORE，层 event
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

    # 启动一次 agent run：异步创建 AgentRunner 并立即返回 run_id
    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        # 确保配置已经加载
        assert self._config is not None
        # 验证参数并转换为 AgentRunCommand 对象
        cmd = AgentRunCommand.model_validate(params)
        # 生成新的 run_id
        run_id = new_run_id()
        # 创建 AgentRunner（传入配置、事件总线和追踪写入器）
        runner = AgentRunner(self._config, bus=self._bus, trace=self._trace)
        # 异步创建任务（不等待完成）
        run_task = asyncio.create_task(runner.run(cmd.goal, run_id=run_id))
        # 将任务添加到运行任务集合
        self._running_runs.add(run_task)
        # 任务完成后自动从集合中移除
        run_task.add_done_callback(self._running_runs.discard)
        # 立即返回 run_id（不等待任务完成）
        return AgentRunResult(run_id=run_id)

    # 注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流
    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        # 验证参数并转换为 EventSubscribeCommand 对象
        cmd = EventSubscribeCommand.model_validate(params)
        # 获取当前连接的 writer（用于发送事件）
        writer = get_connection_writer()

        # 回放的事件数量（默认为 0）
        replayed_count = 0
        # 如果指定了要回放的 run_id
        if cmd.replay_from_run is not None:
            # 回放历史事件
            replayed_count = await self._replay_events(
                cmd.replay_from_run, writer, cmd.topics
            )

        # 确保广播器已经初始化
        assert self._broadcaster is not None
        # 注册订阅：返回订阅 ID
        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        # 返回订阅结果
        return EventSubscribeResult(subscription_id=sub_id, replayed_count=replayed_count)

    # 从 events.jsonl 向 writer 回放匹配 topic 的历史事件，返回已回放条数
    async def _replay_events(
        self,
        run_id: str,
        writer: asyncio.StreamWriter,
        topics: list[str],
    ) -> int:
        # 获取事件文件路径
        path = events_file(run_id)
        # 如果文件不存在，返回 0
        if not path.exists():
            return 0

        # 记录回放的事件数量
        count = 0
        # 读取文件内容，按行分割
        for line in path.read_text().splitlines():
            # 跳过空行
            if not line:
                continue
            try:
                # 解析 JSON
                event = json.loads(line)
            except json.JSONDecodeError:
                # 如果 JSON 格式错误，跳过
                continue
            # 获取事件类型
            event_type: str = event.get("type", "")
            # 检查事件类型是否匹配订阅的 topic
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue
            # 创建事件推送信封
            envelope = EventPushEnvelope(event=event)
            # 写入 writer（发送给客户端）
            writer.write(envelope.model_dump_json().encode() + b"\n")
            # 计数加 1
            count += 1

        # 如果有回放的事件，刷新缓冲区
        if count:
            await writer.drain()
        # 返回回放的事件数量
        return count

    # 启动守护进程：加载配置、初始化日志、启动 trace、启动 TCP 服务器，并等待退出信号
    async def run(self) -> None:
        # 记录启动时间
        self._start_time = time.monotonic()
        # 加载配置
        self._config = get_config()
        # 设置日志
        setup_logging(self._config)

        # 如果启用了追踪
        if self._config.trace.enabled:
            # 解析追踪文件路径（支持 ~ 表示用户目录）
            trace_path = Path(self._config.trace.file).expanduser()
            # 创建追踪写入器
            self._trace = TraceWriter(trace_path)
            # 启动追踪写入器（启动后台 drain task）
            await self._trace.start()
            # 订阅事件总线：所有事件都会被写入追踪文件
            self._bus.subscribe(self._trace_event_handler)

        # 创建 IPC 广播器（用于向客户端广播事件）
        self._broadcaster = IpcEventBroadcaster(trace=self._trace)
        # 订阅事件总线：所有事件都会被广播到客户端
        self._bus.subscribe(self._broadcaster.handle)

        # 创建 Socket 服务器（监听指定的 host 和 port）
        server = SocketServer(
            self._config.host,
            self._config.port,
            self._broadcaster,
            trace=self._trace,
        )
        # 注册命令处理器
        server.register("core.ping", self._ping_handler)       # 处理 ping 请求
        server.register("agent.run", self._agent_run_handler)   # 处理 agent.run 请求
        server.register("event.subscribe", self._subscribe_handler)  # 处理事件订阅

        # 启动服务器，返回监听地址
        addr = await server.start()
        # 记录日志
        logger.info("kama-core %s listening addr=%s", kama_claude.__version__, addr)
        logger.info("config: %s", self._config)

        # 获取当前事件循环
        loop = asyncio.get_running_loop()
        # 创建关闭事件（用于等待退出信号）
        shutdown = asyncio.Event()
        # 添加 SIGINT 信号处理器（Ctrl+C）
        loop.add_signal_handler(signal.SIGINT, shutdown.set)
        # 添加 SIGTERM 信号处理器（kill 命令）
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)

        # 等待关闭事件（阻塞直到收到退出信号）
        await shutdown.wait()

        # 记录关闭日志
        logger.info("shutting down")
        # 取消所有正在运行的任务
        for run_task in list(self._running_runs):
            run_task.cancel()
        # 等待所有任务完成（或取消）
        if self._running_runs:
            await asyncio.gather(*self._running_runs, return_exceptions=True)
        # 停止服务器
        await server.stop()
        # 如果启用了追踪，停止追踪写入器（等待队列清空）
        if self._trace is not None:
            await self._trace.stop()


# 同步入口：启动 CoreApp 事件循环
def run() -> None:
    asyncio.run(CoreApp().run())
