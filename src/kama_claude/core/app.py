# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：Python 的异步 I/O 库，用于网络通信和并发编程
import asyncio
# 导入 datetime：用于获取当前时间
import datetime
# 导入 fnmatch：用于文件名/字符串的通配符匹配（如 "run.*" 匹配 "run.started"）
import fnmatch
# 导入 json：用于序列化/反序列化数据
import json
# 导入 logging：用于日志记录
import logging
# 导入 signal：用于处理系统信号（如 Ctrl+C）
import signal
# 导入 time：用于计算运行时长
import time
# 导入 Any：类型注解，表示任意类型
from typing import Any

# 导入 kama_claude 包本身，用于获取版本号
import kama_claude
# 导入命令模型：定义了各种 RPC 命令的结构
from kama_claude.core.bus.commands import (
    AgentRunCommand,    # agent.run 命令的参数结构
    AgentRunResult,     # agent.run 命令的返回结果结构
    EventSubscribeCommand,  # event.subscribe 命令的参数结构
    EventSubscribeResult,   # event.subscribe 命令的返回结果结构
    PongResult,         # core.ping 命令的返回结果结构
)
# 导入 EventPushEnvelope：事件推送的封装格式（发给客户端的事件包）
from kama_claude.core.bus.envelope import EventPushEnvelope
# 导入配置相关：KamaConfig 配置类，get_config 获取配置的函数
from kama_claude.core.config import KamaConfig, get_config
# 导入 EventBus：事件总线，用于发布-订阅模式
from kama_claude.core.events.bus import EventBus
# 导入日志设置函数：配置 logging 模块
from kama_claude.core.logging_setup import setup_logging
# 导入 AgentRunner：执行 Agent 的核心组件
from kama_claude.core.runner import AgentRunner
# 导入运行记录相关：events_file 获取事件文件路径，new_run_id 生成新的运行 ID
from kama_claude.core.runs import events_file, new_run_id
# 导入 IpcEventBroadcaster：IPC 事件广播器，负责将事件推送给所有订阅的客户端
from kama_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster
# 导入 SocketServer：TCP 服务器，处理客户端连接和 RPC 命令
# get_connection_writer：获取当前连接的写入器（在 RPC 处理中使用）
from kama_claude.core.transport.socket_server import SocketServer, get_connection_writer

# 创建日志记录器，用于记录 Core daemon 的运行日志
logger = logging.getLogger(__name__)


# CoreApp 类：Core daemon 的主应用程序
# 什么是 daemon？就是在后台长期运行的服务进程，比如操作系统的后台服务
class CoreApp:
    # 初始化方法：创建核心组件
    def __init__(self) -> None:
        # 记录启动时间，用于计算运行时长
        self._start_time = time.monotonic()
        
        # 创建事件总线：所有事件在这里发布和订阅
        # 什么是事件总线？就是一个消息中转站，发布者把事件发到总线上，
        # 订阅者从总线上接收事件，实现解耦
        self._bus = EventBus()
        
        # 创建 IPC 事件广播器：负责将事件推送给所有连接的客户端
        # 为什么需要广播器？因为可能有多个客户端（CLI、TUI）连接，
        # 广播器统一管理所有客户端的订阅，把事件推送给订阅了该事件类型的客户端
        self._broadcaster = IpcEventBroadcaster()
        
        # 将广播器订阅到事件总线：事件总线上的任何事件都会被广播器处理
        # 这样，当 Agent 发布事件时，广播器会自动把事件推送给客户端
        self._bus.subscribe(self._broadcaster.handle)
        
        # 当前运行的 Agent 任务：None 表示没有运行，否则是一个 asyncio.Task
        # 什么是 asyncio.Task？就是一个后台运行的异步任务，可以并发执行
        self._current_run_task: asyncio.Task[None] | None = None
        
        # 配置对象：在 run() 方法中加载，所以初始为 None
        self._config: KamaConfig | None = None

    # 处理 core.ping 请求，返回服务版本、运行时长和接收时间
    # 这是一个 RPC handler（处理器），当客户端发送 core.ping 命令时被调用
    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        # 从参数中获取客户端标识（可选，默认为 "unknown"）
        client = params.get("client", "unknown")
        # 记录调试日志
        logger.debug("ping from %s", client)
        # 返回 PongResult 对象，包含：
        #   server_version: 服务版本号
        #   uptime_ms: 运行时长（毫秒）
        #   received_at: 接收时间（ISO 格式）
        return PongResult(
            server_version=kama_claude.__version__,
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    # 启动一次 agent run：立即返回 run_id，后台 task 执行 runner.run()
    # 这是 agent.run 命令的处理器
    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        # 断言配置已加载（如果为 None，会抛出 AssertionError）
        assert self._config is not None
        # 将参数验证为 AgentRunCommand 模型（确保参数格式正确）
        cmd = AgentRunCommand.model_validate(params)

        # 检查是否有正在运行的任务
        # 如果当前任务存在且未完成，说明已经有一个 run 在执行
        if self._current_run_task is not None and not self._current_run_task.done():
            raise RuntimeError("a run is already in progress")

        # 生成新的运行 ID（唯一标识这次运行）
        run_id = new_run_id()
        # 创建 AgentRunner 实例，传入配置和事件总线
        runner = AgentRunner(self._config, bus=self._bus)
        # 创建后台任务，执行 runner.run()
        # 为什么用 create_task？因为我们不想等待 run 完成，
        # 而是立即返回 run_id 给客户端，让客户端通过事件流获取进度
        self._current_run_task = asyncio.create_task(
            runner.run(cmd.goal, run_id=run_id)
        )
        # 立即返回结果，包含 run_id
        return AgentRunResult(run_id=run_id)

    # 注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流
    # 这是 event.subscribe 命令的处理器
    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        # 将参数验证为 EventSubscribeCommand 模型
        cmd = EventSubscribeCommand.model_validate(params)
        # 获取当前连接的写入器（StreamWriter）
        # 什么是 get_connection_writer？它是一个上下文变量，
        # 在处理 RPC 请求时，SocketServer 会设置当前连接的 writer，
        # 这里通过它获取，用于向客户端推送事件
        writer = get_connection_writer()

        # 回放的事件数量，初始为 0
        replayed_count = 0
        # 如果请求中指定了 replay_from_run（要回放的 run ID）
        if cmd.replay_from_run is not None:
            # 调用 _replay_events 回放历史事件
            # 参数：run_id、writer（发送给哪个客户端）、topics（订阅的事件模式）
            replayed_count = await self._replay_events(
                cmd.replay_from_run, writer, cmd.topics
            )

        # 将客户端注册到广播器，订阅指定的 topics 和 scope
        # 返回订阅 ID，用于后续取消订阅
        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        # 返回订阅结果，包含订阅 ID 和回放的事件数量
        return EventSubscribeResult(subscription_id=sub_id, replayed_count=replayed_count)

    # 从 events.jsonl 向 writer 回放匹配 topic 的历史事件，返回已回放条数
    # 什么是回放？就是把之前某个 run 的事件重新发送给新连接的客户端
    # 比如 TUI 启动时，可以回放之前的事件，让界面显示完整的运行历史
    async def _replay_events(
        self,
        run_id: str,          # 要回放的 run ID
        writer: asyncio.StreamWriter,  # 发送给哪个客户端
        topics: list[str],    # 订阅的事件模式（如 ["run.*", "step.*"]）
    ) -> int:
        # 获取该 run 的事件文件路径
        path = events_file(run_id)
        # 如果文件不存在，返回 0
        if not path.exists():
            return 0

        # 已回放的事件数量
        count = 0
        # 读取事件文件的所有行
        for line in path.read_text().splitlines():
            # 跳过空行
            if not line:
                continue
            try:
                # 解析 JSON 事件
                event = json.loads(line)
            except json.JSONDecodeError:
                # 如果解析失败，跳过该行
                continue
            # 获取事件类型
            event_type: str = event.get("type", "")
            # 检查事件类型是否匹配订阅的 topics（支持通配符）
            # 什么是 fnmatch？比如 "run.*" 可以匹配 "run.started"、"run.finished"
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue
            # 创建事件推送包
            envelope = EventPushEnvelope(event=event)
            # 写入到客户端连接
            writer.write(envelope.model_dump_json().encode() + b"\n")
            # 计数加 1
            count += 1

        # 如果有回放的事件，刷新缓冲区确保发送
        if count:
            await writer.drain()
        # 返回回放的事件数量
        return count

    # 启动守护进程：加载配置、初始化日志、启动 TCP 服务器，并等待退出信号
    async def run(self) -> None:
        # 重新记录启动时间（因为 __init__ 可能在很久前调用）
        self._start_time = time.monotonic()
        # 获取配置（从文件、环境变量等）
        self._config = get_config()
        # 初始化日志系统
        setup_logging(self._config)

        # 创建 TCP 服务器，监听指定的 host 和 port
        # 传入广播器，服务器需要它来推送事件
        server = SocketServer(self._config.host, self._config.port, self._broadcaster)
        
        # 注册 RPC 命令处理器
        # 当客户端发送 "core.ping" 命令时，调用 _ping_handler
        server.register("core.ping", self._ping_handler)
        # 当客户端发送 "agent.run" 命令时，调用 _agent_run_handler
        server.register("agent.run", self._agent_run_handler)
        # 当客户端发送 "event.subscribe" 命令时，调用 _subscribe_handler
        server.register("event.subscribe", self._subscribe_handler)

        # 启动服务器，获取监听地址
        addr = await server.start()
        # 记录启动日志
        logger.info("kama-core %s listening addr=%s", kama_claude.__version__, addr)
        logger.info("config: %s", self._config)

        # 获取当前运行的事件循环
        loop = asyncio.get_running_loop()
        # 创建一个 shutdown 事件，用于等待退出信号
        shutdown = asyncio.Event()
        
        # 添加信号处理器：当收到 SIGINT（Ctrl+C）时，点亮 shutdown 事件
        loop.add_signal_handler(signal.SIGINT, shutdown.set)
        # 添加信号处理器：当收到 SIGTERM（系统终止信号）时，点亮 shutdown 事件
        # 什么是 SIGTERM？就是系统发送给进程的终止信号，比如 `kill` 命令
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)

        # 等待 shutdown 事件被点亮（即等待退出信号）
        # 这是一个阻塞点，直到收到退出信号才会继续执行
        await shutdown.wait()

        # 收到退出信号，记录日志
        logger.info("shutting down")
        # 停止服务器，关闭所有连接
        await server.stop()


# 同步入口：启动 CoreApp 事件循环
# 这是 Core daemon 的启动入口，通过命令 `kama daemon start` 调用
def run() -> None:
    # asyncio.run()：创建一个新的事件循环，运行 CoreApp().run()
    # 什么是事件循环？就是异步编程的调度器，负责管理所有异步任务的执行
    asyncio.run(CoreApp().run())
