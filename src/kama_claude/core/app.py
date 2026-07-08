# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入 asyncio 库，用于异步 I/O 操作
import asyncio
# 导入 datetime 模块，用于处理日期和时间
import datetime
# 导入 logging 库，用于日志记录
import logging
# 导入 signal 模块，用于处理系统信号（如 Ctrl+C）
import signal
# 导入 time 模块，用于获取时间戳
import time
# 导入 Any 类型，表示任意类型
from typing import Any

# 导入 kama_claude 包本身，用于获取版本号
import kama_claude
# 从 commands 模块导入 PongResult 模型，用于构造 ping 命令的响应
from kama_claude.core.bus.commands import PongResult
# 从 config 模块导入 get_config 函数，用于加载配置
from kama_claude.core.config import get_config
# 从 logging_setup 模块导入 setup_logging 函数，用于初始化日志系统
from kama_claude.core.logging_setup import setup_logging
# 从 socket_server 模块导入 SocketServer 类，用于创建 TCP 服务器
from kama_claude.core.transport.socket_server import SocketServer

# 创建日志记录器，使用当前模块名作为 logger 名称
logger = logging.getLogger(__name__)


# CoreApp 类：KamaClaude 守护进程的主应用类
class CoreApp:
    # 初始化方法：创建应用实例
    def __init__(self) -> None:
        # 记录应用启动时间（使用 monotonic 时间，不受系统时间调整影响）
        self._start_time = time.monotonic()

    # 处理 core.ping 请求，返回服务版本、运行时长和接收时间
    # 函数作用：作为 core.ping 方法的处理函数，计算并返回服务器状态信息
    # 传参：params - 请求参数字典，包含 client 字段
    # 返回值：PongResult 对象，包含 server_version、uptime_ms、received_at
    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        # 从参数中获取客户端标识，默认值为 "unknown"
        client = params.get("client", "unknown")
        # 记录 debug 级别日志，显示收到 ping 请求的客户端
        logger.debug("ping from %s", client)
        # 返回 PongResult 对象
        return PongResult(
            # 服务器版本号，取自 kama_claude.__version__
            server_version=kama_claude.__version__,
            # 服务器运行时长（毫秒）：当前时间减去启动时间，乘以 1000 后取整
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            # 请求接收时间：当前 UTC 时间，ISO 8601 格式
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    # 启动守护进程：加载配置、初始化日志、启动 TCP 服务器，并等待退出信号
    # 函数作用：应用的主入口方法，负责启动整个守护进程
    # 返回值：None
    async def run(self) -> None:
        # 重新记录启动时间（确保准确）
        self._start_time = time.monotonic()
        # 加载运行时配置（默认值 → TOML → .env → 环境变量）
        config = get_config()
        # 根据配置初始化日志系统
        setup_logging(config)

        # 创建 SocketServer 实例，传入配置中的 host 和 port
        server = SocketServer(config.host, config.port)
        # 注册 core.ping 方法的处理函数
        server.register("core.ping", self._ping_handler)

        # 启动 TCP 服务器，获取监听地址
        addr = await server.start()
        # 记录 info 级别日志，显示服务器版本和监听地址
        logger.info("kama-core %s listening addr=%s", kama_claude.__version__, addr)
        # 记录 info 级别日志，显示当前配置
        logger.info("config: %s", config)

        # 获取当前运行的事件循环
        loop = asyncio.get_running_loop()
        # 创建一个事件对象，用于等待退出信号
        shutdown = asyncio.Event()
        # 注册 SIGINT 信号处理器（Ctrl+C），触发时设置 shutdown 事件
        loop.add_signal_handler(signal.SIGINT, shutdown.set)
        # 注册 SIGTERM 信号处理器（kill 命令），触发时设置 shutdown 事件
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)

        # 等待 shutdown 事件被设置（阻塞直到收到退出信号）
        await shutdown.wait()

        # 收到退出信号，记录 info 级别日志
        logger.info("shutting down")
        # 关闭 TCP 服务器
        await server.stop()


# 同步入口：启动 CoreApp 事件循环
# 函数作用：作为守护进程的同步入口点，启动异步事件循环
# 返回值：None
def run() -> None:
    # 创建 CoreApp 实例并运行其 run 方法
    asyncio.run(CoreApp().run())
