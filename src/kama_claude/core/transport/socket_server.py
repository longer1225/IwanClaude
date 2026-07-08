# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入 asyncio 库，用于异步 I/O 操作
import asyncio
# 导入 json 库，用于 JSON 序列化和反序列化
import json
# 导入 logging 库，用于日志记录
import logging
# 导入类型提示模块：Awaitable 表示可等待对象，Callable 表示可调用对象
from collections.abc import Awaitable, Callable
# 导入 Any 类型，表示任意类型
from typing import Any

# 导入 Pydantic 库：BaseModel 用于定义数据模型，ValidationError 用于验证异常
from pydantic import BaseModel, ValidationError

# 从 envelope 模块导入 JSON-RPC 协议相关的模型和常量
from kama_claude.core.bus.envelope import (
    INTERNAL_ERROR,           # 服务器内部错误码
    INVALID_REQUEST,          # 请求格式错误码
    METHOD_NOT_FOUND,         # 方法不存在错误码
    PARSE_ERROR,              # JSON 解析错误码
    JsonRpcRequest,           # JSON-RPC 请求模型
    JsonRpcSuccess,           # JSON-RPC 成功响应模型
    make_error,               # 错误响应构造函数
)

# 创建日志记录器，使用当前模块名作为 logger 名称
logger = logging.getLogger(__name__)

# 定义命令处理器类型别名：接收参数字典，返回可等待的任意结果
type CommandHandler = Callable[[dict[str, Any]], Awaitable[Any]]

# 定义单帧最大字节数：1 MB，防止超大请求导致内存问题
_MAX_LINE_BYTES = 1 * 1024 * 1024  # 1 MB per frame


# SocketServer 类：基于 asyncio 的 TCP 服务器，实现 JSON-RPC over NDJSON 协议
class SocketServer:
    # 初始化方法：创建服务器实例
    # 传参：
    #   host - 监听的主机地址（如 "127.0.0.1"）
    #   port - 监听的端口号（如 7437）
    def __init__(self, host: str, port: int) -> None:
        # 保存监听地址
        self._host = host
        # 保存监听端口
        self._port = port
        # 初始化处理器字典：key 是方法名（如 "core.ping"），value 是对应的处理函数
        self._handlers: dict[str, CommandHandler] = {}
        # 初始化服务器对象：None 表示服务器尚未启动
        self._server: asyncio.AbstractServer | None = None

    # 注册一个方法名对应的命令处理函数
    # 传参：
    #   method - 方法名称（如 "core.ping"）
    #   handler - 处理函数，接收参数字典，返回可等待的结果
    def register(self, method: str, handler: CommandHandler) -> None:
        # 将方法名和处理函数绑定到字典中
        self._handlers[method] = handler

    # 启动 TCP 服务器；若端口已被占用则退出进程
    # 返回值：服务器监听的地址字符串（如 "127.0.0.1:7437"）
    async def start(self) -> str:
        try:
            # 尝试连接到自身地址，检测端口是否已被占用
            _r, w = await asyncio.open_connection(self._host, self._port)
            # 连接成功说明端口已被占用，关闭连接
            w.close()
            await w.wait_closed()
            # 抛出 SystemExit 异常，终止进程并提示错误信息
            raise SystemExit(f"core already running at {self._host}:{self._port}")
        # 连接被拒绝或发生其他错误，说明端口未被占用，继续启动
        except (ConnectionRefusedError, OSError):
            pass

        # 创建并启动 TCP 服务器
        # _handle_connection 是每个新连接的处理函数
        # limit 参数设置单次读取的最大字节数
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
            limit=_MAX_LINE_BYTES,
        )
        # 返回服务器监听地址
        return f"{self._host}:{self._port}"

    # 关闭服务器，最多等待 2 秒
    async def stop(self) -> None:
        # 如果服务器尚未启动，直接返回
        if self._server is None:
            return
        # 关闭服务器，停止接受新连接
        self._server.close()
        # 等待服务器完全关闭，设置 2 秒超时
        await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)

    # 处理单个客户端连接，完成后关闭写流
    # 传参：
    #   reader - 异步读取器，用于从客户端读取数据
    #   writer - 异步写入器，用于向客户端发送数据
    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # 获取客户端的远程地址（IP 和端口），默认值为 "<unknown>"
        peer = writer.get_extra_info("peername", "<unknown>")
        # 记录客户端连接日志（debug 级别）
        logger.debug("client connected: %s", peer)
        try:
            # 进入读取循环，持续处理客户端发来的请求
            await self._read_loop(reader, writer)
        finally:
            # 无论发生什么，都关闭写入器
            writer.close()
            try:
                # 等待写入器完全关闭，设置 1 秒超时
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except TimeoutError:
                # 超时则忽略，继续执行
                pass
            # 记录客户端断开连接日志（debug 级别）
            logger.debug("client disconnected: %s", peer)

    # 持续读取换行分隔的 JSON 行并逐行分发处理
    # 传参：
    #   reader - 异步读取器
    #   writer - 异步写入器
    async def _read_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # 无限循环，持续读取客户端数据
        while True:
            try:
                # 读取一行数据（以换行符 \n 为分隔）
                line = await reader.readline()
            except asyncio.LimitOverrunError:
                # 数据超过最大限制，发送错误响应并退出循环
                await self._send(writer, make_error(None, INVALID_REQUEST, "Request too large"))
                return

            # 如果读取到空字节，说明客户端断开连接
            if not line:
                return

            # 处理单行 JSON 请求
            await self._handle_line(line, writer)

    # 解析单行 JSON-RPC 请求并调用对应 handler，将结果或错误写回客户端
    # 传参：
    #   line - 接收到的一行字节数据
    #   writer - 异步写入器，用于发送响应
    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        # 第一步：解析 JSON
        try:
            # 将字节数据解码并解析为 Python 对象
            raw: Any = json.loads(line)
        except json.JSONDecodeError as e:
            # JSON 解析失败，发送 PARSE_ERROR 错误响应
            await self._send(writer, make_error(None, PARSE_ERROR, f"Parse error: {e}"))
            return

        # 第二步：验证 JSON-RPC 请求格式
        try:
            # 使用 Pydantic 模型验证请求格式
            req = JsonRpcRequest.model_validate(raw)
        except ValidationError as e:
            # 请求格式不符合 JSON-RPC 规范，发送 INVALID_REQUEST 错误响应
            await self._send(writer, make_error(None, INVALID_REQUEST, "Invalid Request", str(e)))
            return

        # 第三步：查找对应的处理函数
        handler = self._handlers.get(req.method)
        if handler is None:
            # 方法不存在，发送 METHOD_NOT_FOUND 错误响应
            await self._send(
                writer,
                make_error(req.id, METHOD_NOT_FOUND, f"Method not found: {req.method}"),
            )
            return

        # 第四步：执行处理函数
        try:
            # 调用处理函数，传入请求参数
            result = await handler(req.params)
        except ValidationError as e:
            # 参数验证失败，发送 INVALID_REQUEST 错误响应
            await self._send(
                writer,
                make_error(req.id, INVALID_REQUEST, "Invalid params", str(e)),
            )
            return
        except Exception as e:
            # 处理函数执行过程中发生未知异常
            # 记录异常日志（exception 级别，会打印完整堆栈）
            logger.exception("handler %s raised: %s", req.method, e)
            # 发送 INTERNAL_ERROR 错误响应（不暴露具体错误信息给客户端）
            await self._send(writer, make_error(req.id, INTERNAL_ERROR, "Internal error"))
            return

        # 第五步：构造成功响应
        # 如果返回结果是 Pydantic 模型，转换为字典；否则直接使用
        result_data: Any = result.model_dump() if isinstance(result, BaseModel) else result
        # 封装为 JSON-RPC 成功响应并发送
        await self._send(writer, JsonRpcSuccess(id=req.id, result=result_data))

    # 将 pydantic 消息序列化为 JSON 行并写入流，随后刷新缓冲区
    # 传参：
    #   writer - 异步写入器
    #   msg - Pydantic 模型对象（如 JsonRpcSuccess 或 JsonRpcError）
    async def _send(self, writer: asyncio.StreamWriter, msg: BaseModel) -> None:
        # 将 Pydantic 模型序列化为 JSON 字符串，编码为字节，添加换行符
        writer.write(msg.model_dump_json().encode() + b"\n")
        # 等待数据完全发送到 socket 缓冲区
        await writer.drain()
