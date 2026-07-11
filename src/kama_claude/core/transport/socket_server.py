# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许类型注解中使用前向引用
from __future__ import annotations

# 导入 asyncio：Python 的异步 I/O 库，用于网络通信
import asyncio
# 导入 json：用于序列化/反序列化网络传输的数据
import json
# 导入 logging：用于记录日志
import logging
# 导入 Awaitable 和 Callable：类型注解，用于定义异步回调函数类型
from collections.abc import Awaitable, Callable
# 导入 ContextVar：上下文变量，用于在异步任务中传递数据（类似线程本地变量）
from contextvars import ContextVar
# 导入 datetime：用于生成时间戳
from datetime import UTC, datetime
# 导入 Any：类型注解，表示任意类型
from typing import Any

# 导入 Pydantic：用于数据验证和模型定义
from pydantic import BaseModel, ValidationError

# 导入 JSON-RPC 相关的模型和错误码
from kama_claude.core.bus.envelope import (
    INTERNAL_ERROR,      # 内部错误码 (-32603)
    INVALID_REQUEST,     # 无效请求错误码 (-32600)
    METHOD_NOT_FOUND,    # 方法未找到错误码 (-32601)
    PARSE_ERROR,         # 解析错误码 (-32700)
    HandlerError,        # 自定义 handler 错误类型
    JsonRpcError,        # JSON-RPC 错误响应模型
    JsonRpcRequest,      # JSON-RPC 请求模型
    JsonRpcSuccess,      # JSON-RPC 成功响应模型
    make_error,          # 创建错误响应的工厂函数
)
# 导入 TraceRecord：系统级追踪记录模型
from kama_claude.core.trace.record import TraceRecord
# 导入 TraceWriter：非阻塞的追踪记录写入器
from kama_claude.core.trace.writer import TraceWriter
# 导入 IpcEventBroadcaster：IPC 事件广播器
from kama_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster

# 创建模块级别的日志记录器，名称为当前模块名
logger = logging.getLogger(__name__)

# 定义命令处理器类型：接收一个字典参数，返回一个可 await 的对象
# Callable[[dict[str, Any]], Awaitable[Any]]：这是一个类型别名
# - Callable：表示可调用的函数或对象
# - [dict[str, Any]]：参数是一个字典
# - Awaitable[Any]：返回值是可以用 await 等待的对象（如 async def 函数）
type CommandHandler = Callable[[dict[str, Any]], Awaitable[Any]]

# 定义一个上下文变量，用于存储当前连接的 StreamWriter
# ContextVar 是"任务本地"的变量，每个异步任务有自己独立的值
# 为什么需要它？因为 handler 需要向客户端发送数据时，需要知道是哪个客户端
_writer_var: ContextVar[asyncio.StreamWriter] = ContextVar("_writer_var")


# 返回当前时间的 ISO 格式字符串（UTC 时区）
# 用于生成追踪记录的时间戳
def _now() -> str:
    return datetime.now(UTC).isoformat()


# 返回当前 handler 调用所属连接的 StreamWriter
# 通过上下文变量获取，这样 handler 可以向正确的客户端发送数据
def get_connection_writer() -> asyncio.StreamWriter:
    return _writer_var.get()

# 单帧最大字节数：1 MB（防止收到过大的消息导致内存溢出）
# 什么是帧？这里指一次网络传输的单个消息（一行 JSON）
_MAX_LINE_BYTES = 1 * 1024 * 1024  # 1 MB per frame


# SocketServer 类：TCP 服务器，用于处理客户端连接和 JSON-RPC 请求
class SocketServer:
    # 初始化方法：保存连接地址、端口，以及依赖的广播器和追踪器
    def __init__(
        self,
        host: str,                              # 监听的主机地址（通常是 127.0.0.1）
        port: int,                              # 监听的端口
        broadcaster: IpcEventBroadcaster | None = None,  # 事件广播器（可选）
        trace: TraceWriter | None = None,       # 追踪记录写入器（可选）
    ) -> None:
        # 保存主机地址
        self._host = host
        # 保存端口
        self._port = port
        # 命令处理器字典：key 是方法名，value 是处理函数
        self._handlers: dict[str, CommandHandler] = {}
        # TCP 服务器实例（start() 后才创建）
        self._server: asyncio.AbstractServer | None = None
        # 事件广播器，用于向客户端推送事件
        self._broadcaster = broadcaster
        # 追踪记录写入器，用于记录系统级追踪信息
        self._trace = trace

    # 注册一个方法名对应的命令处理函数
    # 什么是注册？就是把方法名和处理函数绑定起来
    # 以后收到这个方法的请求，就调用对应的处理函数
    def register(self, method: str, handler: CommandHandler) -> None:
        self._handlers[method] = handler

    # 启动 TCP 服务器；若端口已被占用则退出进程
    async def start(self) -> str:
        # 先检查端口是否已被占用：尝试连接自己的端口
        try:
            # 如果连接成功，说明端口已被占用（可能已有另一个 Core daemon 在运行）
            _r, w = await asyncio.open_connection(self._host, self._port)
            w.close()
            await w.wait_closed()
            # 退出进程，并提示用户
            raise SystemExit(f"core already running at {self._host}:{self._port}")
        except (ConnectionRefusedError, OSError):
            # 连接被拒绝，说明端口未被占用，可以继续启动
            pass

        # 创建并启动 TCP 服务器
        # asyncio.start_server()：创建一个 TCP 服务器
        # 参数：
        #   self._handle_connection：处理每个新连接的回调函数
        #   host/port：监听的地址和端口
        #   limit：单个消息的最大字节数，防止内存溢出
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
            limit=_MAX_LINE_BYTES,
        )
        # 返回监听地址，用于日志输出
        return f"{self._host}:{self._port}"

    # 关闭服务器，最多等待 2 秒
    async def stop(self) -> None:
        # 如果服务器还没启动，直接返回
        if self._server is None:
            return
        # 关闭服务器（停止接受新连接）
        self._server.close()
        # 等待服务器真正关闭，超时 2 秒
        await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)

    # 处理单个客户端连接，完成后关闭写流
    # 当有新客户端连接时，asyncio.start_server 会自动调用这个方法
    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,   # 用于从客户端读取数据
        writer: asyncio.StreamWriter,   # 用于向客户端写入数据
    ) -> None:
        # 获取客户端地址（IP:端口），用于日志
        peer = writer.get_extra_info("peername", "<unknown>")
        logger.debug("client connected: %s", peer)
        
        try:
            # 进入读取循环，持续读取客户端消息
            await self._read_loop(reader, writer)
        finally:
            # 无论如何（正常退出或异常），都要清理资源
            # 如果有广播器，取消该客户端的订阅
            if self._broadcaster is not None:
                self._broadcaster.unsubscribe(writer)
            # 关闭写入端（发送 TCP FIN 包）
            writer.close()
            try:
                # 等待连接真正关闭，超时 1 秒
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except TimeoutError:
                # 如果超时，忽略错误，强制关闭
                pass
            # 记录客户端断开连接的日志
            logger.debug("client disconnected: %s", peer)

    # 持续读取换行分隔的 JSON 行并逐行分发处理
    # 使用 NDJSON（Newline-Delimited JSON）格式，每行一个 JSON 对象
    async def _read_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # 无限循环：持续读取消息
        while True:
            try:
                # 读取一行数据（直到遇到换行符）
                # 这是一个异步阻塞操作：如果没有数据，会暂停并让出 CPU
                line = await reader.readline()
            except asyncio.LimitOverrunError:
                # 消息超过了最大长度限制
                await self._send(writer, make_error(None, INVALID_REQUEST, "Request too large"))
                return

            # 如果读到空字节，说明连接已经关闭（TCP FIN 包）
            if not line:
                return

            # 处理这一行消息
            await self._handle_line(line, writer)

    # 解析单行 JSON-RPC 请求并调用对应 handler，将结果或错误写回客户端
    # 这是整个服务器的核心处理逻辑
    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        # 第一步：解析 JSON
        try:
            # 将字节数据解析为 Python 字典
            raw: Any = json.loads(line)
        except json.JSONDecodeError as e:
            # JSON 格式错误，返回 PARSE_ERROR
            await self._send(writer, make_error(None, PARSE_ERROR, f"Parse error: {e}"))
            return

        # 第二步：验证请求格式
        try:
            # 使用 Pydantic 验证请求是否符合 JSON-RPC 规范
            req = JsonRpcRequest.model_validate(raw)
        except ValidationError as e:
            # 请求格式不符合规范，返回 INVALID_REQUEST
            await self._send(writer, make_error(None, INVALID_REQUEST, "Invalid Request", str(e)))
            return

        # ========== 追踪埋点 1：记录客户端发来的命令 ==========
        # 如果配置了追踪器，记录这次请求
        if self._trace is not None:
            # 获取客户端 ID（IP:端口）
            client_id = str(writer.get_extra_info("peername", "<unknown>"))
            # 发送追踪记录
            self._trace.emit(
                TraceRecord(
                    ts=_now(),                           # 时间戳
                    direction="CLIENT→CORE",              # 数据流向：客户端→核心
                    layer="ipc",                          # 所在层：IPC 层
                    kind="command",                       # 记录类型：命令
                    client_id=client_id,                  # 客户端 ID
                    data={"method": req.method,           # 原始数据：方法名
                          "id": req.id,                  # 请求 ID
                          "params": req.params},          # 参数
                )
            )

        # 第三步：查找处理器
        handler = self._handlers.get(req.method)
        if handler is None:
            # 方法未找到，返回 METHOD_NOT_FOUND
            await self._send(
                writer,
                make_error(req.id, METHOD_NOT_FOUND, f"Method not found: {req.method}"),
            )
            return

        # 第四步：设置上下文变量（当前连接的 writer）
        # 这样 handler 内部可以通过 get_connection_writer() 获取当前客户端的 writer
        _writer_var.set(writer)
        
        # 第五步：调用处理器
        try:
            # await handler(req.params)：调用处理函数，等待结果
            # 这是异步调用，不会阻塞事件循环
            result = await handler(req.params)
        except HandlerError as e:
            # handler 主动抛出的错误（业务逻辑错误）
            await self._send(writer, make_error(req.id, e.code, str(e), e.data))
            return
        except ValidationError as e:
            # 参数验证错误
            await self._send(
                writer,
                make_error(req.id, INVALID_REQUEST, "Invalid params", str(e)),
            )
            return
        except Exception as e:
            # 其他未知错误（内部错误）
            logger.exception("handler %s raised: %s", req.method, e)
            await self._send(writer, make_error(req.id, INTERNAL_ERROR, "Internal error"))
            return

        # 第六步：处理结果
        # 如果结果是 Pydantic 模型，转换为字典
        result_data: Any = result.model_dump() if isinstance(result, BaseModel) else result
        # 发送成功响应
        await self._send(writer, JsonRpcSuccess(id=req.id, result=result_data))

    # 将 pydantic 消息序列化为 JSON 行并写入流，随后刷新缓冲区
    # 这是向客户端发送数据的统一方法
    async def _send(self, writer: asyncio.StreamWriter, msg: BaseModel) -> None:
        # 将 Pydantic 模型转换为 JSON 字符串，编码为字节，加上换行符
        # 为什么加换行符？因为使用的是 NDJSON（Newline-Delimited JSON）格式
        # 每个消息占一行，客户端通过换行符来分割消息
        writer.write(msg.model_dump_json().encode() + b"\n")
        # 刷新缓冲区，确保数据真正发送出去
        # 如果不调用 drain()，数据可能还在内存缓冲区中，没有发送
        await writer.drain()
        
        # ========== 追踪埋点 2：记录核心发回的响应 ==========
        # 如果配置了追踪器，记录这次响应
        if self._trace is not None:
            # 判断是错误响应还是成功响应
            kind = "error" if isinstance(msg, JsonRpcError) else "response"
            # 获取客户端 ID
            client_id = str(writer.get_extra_info("peername", "<unknown>"))
            # 发送追踪记录
            self._trace.emit(
                TraceRecord(
                    ts=_now(),                           # 时间戳
                    direction="CORE→CLIENT",              # 数据流向：核心→客户端
                    layer="ipc",                          # 所在层：IPC 层
                    kind=kind,                            # 记录类型：响应或错误
                    client_id=client_id,                  # 客户端 ID
                    data=msg.model_dump(),                # 原始数据：完整的响应消息
                )
            )
