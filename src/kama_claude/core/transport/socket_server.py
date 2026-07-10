# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：Python 的异步 I/O 库，用于网络通信
import asyncio
# 导入 json：用于序列化/反序列化网络数据
import json
# 导入 logging：用于日志记录
import logging
# 导入 Awaitable 和 Callable：类型注解，定义异步回调函数类型
from collections.abc import Awaitable, Callable
# 导入 ContextVar：上下文变量，用于在异步任务中传递数据
# 什么是 ContextVar？就是一个"线程/任务本地"的变量，每个异步任务有自己的值
from contextvars import ContextVar
# 导入 Any：类型注解，表示任意类型
from typing import Any

# 导入 pydantic：数据验证和序列化库
# BaseModel：所有 pydantic 模型的基类
# ValidationError：验证失败时抛出的异常
from pydantic import BaseModel, ValidationError

# 导入 JSON-RPC 相关的模型和错误码
from kama_claude.core.bus.envelope import (
    INTERNAL_ERROR,     # 内部错误码（-32603）
    INVALID_REQUEST,    # 无效请求错误码（-32600）
    METHOD_NOT_FOUND,   # 方法未找到错误码（-32601）
    PARSE_ERROR,        # 解析错误码（-32700）
    JsonRpcRequest,     # JSON-RPC 请求模型
    JsonRpcSuccess,     # JSON-RPC 成功响应模型
    make_error,         # 创建错误响应的辅助函数
)
# 导入 IpcEventBroadcaster：事件广播器，用于向客户端推送事件
from kama_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster

# 创建日志记录器
logger = logging.getLogger(__name__)

# 定义命令处理器类型：接收一个字典参数，返回一个异步任务
# Callable[[dict[str, Any]], Awaitable[Any]] 表示：
#   - 接收一个 dict[str, Any] 类型的参数
#   - 返回一个可以 await 的对象（异步函数）
type CommandHandler = Callable[[dict[str, Any]], Awaitable[Any]]

# 上下文变量：存储当前连接的 StreamWriter
# 什么是 ContextVar？
#   想象一下，服务器同时处理多个客户端连接，每个连接有自己的 writer
#   当处理某个连接的请求时，我们需要知道是哪个连接的请求
#   ContextVar 就是用来在"当前处理流程"中传递这个信息的
#   就像一个线程本地变量，但适用于异步任务
_writer_var: ContextVar[asyncio.StreamWriter] = ContextVar("_writer_var")


# 返回当前 handler 调用所属连接的 StreamWriter
# 当 RPC handler（如 _subscribe_handler）需要向客户端发送数据时，
# 通过这个函数获取当前连接的 writer
def get_connection_writer() -> asyncio.StreamWriter:
    return _writer_var.get()

# 单帧最大字节数：1 MB（防止收到过大的消息导致内存溢出）
_MAX_LINE_BYTES = 1 * 1024 * 1024  # 1 MB per frame


# SocketServer 类：TCP 服务器，处理客户端连接和 JSON-RPC 命令
# 什么是 TCP 服务器？就是监听一个端口，等待客户端连接，处理客户端请求的程序
class SocketServer:
    # 初始化方法：创建服务器实例
    # 传参：
    #   host - 监听地址（如 "127.0.0.1"）
    #   port - 监听端口（如 12345）
    #   broadcaster - 事件广播器（可选，用于向客户端推送事件）
    def __init__(
        self, host: str, port: int, broadcaster: IpcEventBroadcaster | None = None
    ) -> None:
        # 保存监听地址
        self._host = host
        # 保存监听端口
        self._port = port
        # 命令处理器字典：key 是方法名（如 "agent.run"），value 是处理函数
        # 当客户端发送某个方法的请求时，通过这个字典找到对应的处理器
        self._handlers: dict[str, CommandHandler] = {}
        # asyncio.AbstractServer：asyncio 提供的服务器抽象类
        # None 表示服务器尚未启动
        self._server: asyncio.AbstractServer | None = None
        # 事件广播器：用于向所有订阅的客户端推送事件
        self._broadcaster = broadcaster

    # 注册一个方法名对应的命令处理函数
    # 什么是注册？就是把方法名和处理函数绑定起来，以后收到这个方法的请求就调用这个函数
    def register(self, method: str, handler: 
        
        ommandHandler) -> None:
        self._handlers[method] = handler

    # 启动 TCP 服务器；若端口已被占用则退出进程
    async def start(self) -> str:
        # ====================== 检查端口是否被占用 ======================
        # 为什么要检查？因为如果端口已经被占用，启动会失败
        # 我们主动尝试连接自己的端口，如果能连上，说明已经有一个 core 在运行了
        try:
            # 尝试连接到自己的地址和端口
            _r, w = await asyncio.open_connection(self._host, self._port)
            # 如果连接成功，说明端口已被占用，关闭连接并退出
            w.close()
            await w.wait_closed()
            raise SystemExit(f"core already running at {self._host}:{self._port}")
        except (ConnectionRefusedError, OSError):
            # 连接被拒绝，说明端口没有被占用，可以启动服务器
            pass

        # ====================== 启动 TCP 服务器 ======================
        # asyncio.start_server()：创建一个 TCP 服务器
        # 参数：
        #   self._handle_connection - 处理每个新连接的回调函数
        #   host - 监听地址
        #   port - 监听端口
        #   limit - 单个消息的最大字节数
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
            limit=_MAX_LINE_BYTES,
        )
        # 返回监听地址（用于日志）
        return f"{self._host}:{self._port}"

    # 关闭服务器，最多等待 2 秒
    async def stop(self) -> None:
        if self._server is None:
            return
        # 关闭服务器（停止接受新连接）
        self._server.close()
        # 等待服务器完全关闭，超时 2 秒
        await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)

    # 处理单个客户端连接，完成后关闭写流
    # 当有新客户端连接时，asyncio.start_server 会调用这个函数
    # 传参：
    #   reader - 用于从客户端读取数据
    #   writer - 用于向客户端写入数据
    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # 获取客户端地址（IP 和端口），用于日志
        peer = writer.get_extra_info("peername", "<unknown>")
        logger.debug("client connected: %s", peer)
        
        try:
            # 进入读取循环，持续处理客户端请求
            await self._read_loop(reader, writer)
        finally:
            # 无论如何（正常结束或异常），都要清理资源
            # 如果有广播器，取消该客户端的订阅
            if self._broadcaster is not None:
                self._broadcaster.unsubscribe(writer)
            # 关闭写入端
            writer.close()
            try:
                # 等待连接真正关闭，超时 1 秒
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except TimeoutError:
                pass
            logger.debug("client disconnected: %s", peer)

    # 持续读取换行分隔的 JSON 行并逐行分发处理
    # 什么是 NDJSON？就是每行一个 JSON 对象，用换行符分隔
    # 为什么用 NDJSON？因为网络传输是流式的，需要一种简单的方式来分割消息
    async def _read_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        while True:
            try:
                # 读取一行数据（直到遇到换行符）
                line = await reader.readline()
            except asyncio.LimitOverrunError:
                # 如果消息超过了最大长度，返回错误并关闭连接
                await self._send(writer, make_error(None, INVALID_REQUEST, "Request too large"))
                return

            # 如果读到空字节，说明客户端关闭了连接
            if not line:
                return

            # 处理这一行数据
            await self._handle_line(line, writer)

    # 解析单行 JSON-RPC 请求并调用对应 handler，将结果或错误写回客户端
    # 这是 JSON-RPC 协议处理的核心方法
    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        # ====================== 第一步：解析 JSON ======================
        try:
            # 将字节数据解析为 Python 对象（字典或列表）
            raw: Any = json.loads(line)
        except json.JSONDecodeError as e:
            # 如果 JSON 格式错误，返回 PARSE_ERROR
            await self._send(writer, make_error(None, PARSE_ERROR, f"Parse error: {e}"))
            return

        # ====================== 第二步：验证请求格式 ======================
        try:
            # 将原始数据验证为 JsonRpcRequest 模型
            # 什么是模型验证？就是检查数据是否符合预期的结构
            # 比如必须包含 "jsonrpc"、"method" 字段等
            req = JsonRpcRequest.model_validate(raw)
        except ValidationError as e:
            # 如果验证失败，返回 INVALID_REQUEST
            await self._send(writer, make_error(None, INVALID_REQUEST, "Invalid Request", str(e)))
            return

        # ====================== 第三步：查找处理器 ======================
        # 根据方法名查找对应的处理函数
        handler = self._handlers.get(req.method)
        if handler is None:
            # 如果方法名不存在，返回 METHOD_NOT_FOUND
            await self._send(
                writer,
                make_error(req.id, METHOD_NOT_FOUND, f"Method not found: {req.method}"),
            )
            return

        # ====================== 第四步：设置上下文并执行处理器 ======================
        # 设置当前连接的 writer 到上下文变量
        # 这样 handler 内部可以通过 get_connection_writer() 获取
        _writer_var.set(writer)
        
        try:
            # 调用处理器，传入参数
            # handler 是一个异步函数，所以需要 await
            result = await handler(req.params)
        except ValidationError as e:
            # 参数验证失败（handler 内部的验证）
            await self._send(
                writer,
                make_error(req.id, INVALID_REQUEST, "Invalid params", str(e)),
            )
            return
        except Exception as e:
            # 其他所有异常（运行时错误）
            logger.exception("handler %s raised: %s", req.method, e)
            await self._send(writer, make_error(req.id, INTERNAL_ERROR, "Internal error"))
            return

        # ====================== 第五步：返回成功响应 ======================
        # 如果结果是 pydantic 模型，转为字典；否则直接使用
        result_data: Any = result.model_dump() if isinstance(result, BaseModel) else result
        # 创建成功响应并发送
        await self._send(writer, JsonRpcSuccess(id=req.id, result=result_data))

    # 将 pydantic 消息序列化为 JSON 行并写入流，随后刷新缓冲区
    async def _send(self, writer: asyncio.StreamWriter, msg: BaseModel) -> None:
        # 将消息序列化为 JSON 字符串，编码为字节，加上换行符
        writer.write(msg.model_dump_json().encode() + b"\n")
        # 刷新缓冲区，确保数据真正发送出去
        await writer.drain()
