"""
IPC 服务器

该模块实现了 TCP 服务器，用于监听客户端连接并处理 JSON-RPC 命令。

核心功能：
- 监听指定端口，接受客户端连接
- 解析 JSON-RPC 请求并调用对应的处理函数
- 将处理结果或错误写回客户端
- 支持事件广播器集成，实现事件推送
- 支持请求追踪和日志记录

设计要点：
- 使用 asyncio.start_server() 实现异步 TCP 服务器
- 使用 ContextVar 在处理函数中传递连接上下文
- 每条命令独立作为 task 执行，避免阻塞读循环
- 支持最大 64MB 的单行消息，兼容 MCP 大文件工具结果
- 完整的错误处理机制，符合 JSON-RPC 2.0 规范
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from iwan_claude.core.bus.envelope import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    HandlerError,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcSuccess,
    make_error,
)
from iwan_claude.core.trace.record import TraceRecord
from iwan_claude.core.trace.writer import TraceWriter
from iwan_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster

logger = logging.getLogger(__name__)

# 命令处理器类型定义
# 接收命令参数字典，返回异步任务，结果可以是任意类型或 BaseModel
type CommandHandler = Callable[[dict[str, Any]], Awaitable[Any]]

# 上下文变量，存储当前处理协程中的 StreamWriter
# 用于在 handler 中获取连接上下文，实现事件推送等功能
_writer_var: ContextVar[asyncio.StreamWriter] = ContextVar("_writer_var")


def _now() -> str:
    """
    获取当前时间的 ISO 格式字符串

    返回：
        str: 当前 UTC 时间的 ISO 8601 格式字符串
    """
    return datetime.now(UTC).isoformat()


def get_connection_writer() -> asyncio.StreamWriter:
    """
    返回当前 handler 调用所属连接的 StreamWriter

    通过 ContextVar 获取当前处理协程的 StreamWriter，
    用于在 handler 中获取连接上下文，实现事件订阅等功能。

    返回：
        asyncio.StreamWriter: 当前连接的写入器

    使用示例：
        >>> async def my_handler(params):
        ...     writer = get_connection_writer()
        ...     # 使用 writer 进行事件订阅
        ...     broadcaster.subscribe(writer, ["run.*"])
    """
    return _writer_var.get()

# 单行消息的最大字节数，64MB
# 兼容 MCP 工具可能返回的大文件结果
_MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MB per frame


class SocketServer:
    """
    TCP 服务器类

    实现异步 TCP 服务器，处理客户端连接和 JSON-RPC 命令。

    工作原理：
    1. 通过 start() 启动服务器，监听指定端口
    2. 客户端连接时，_handle_connection() 被调用
    3. _read_loop() 持续读取客户端消息
    4. 每条消息独立作为 task 在 _handle_line() 中处理
    5. _handle_line() 解析 JSON-RPC 请求，调用对应 handler
    6. _send() 将结果或错误写回客户端
    7. 通过 stop() 关闭服务器

    特点：
    - 支持并发连接，每条命令独立执行
    - 使用 ContextVar 传递连接上下文
    - 集成事件广播器，支持事件推送
    - 支持请求追踪，记录命令和响应详情
    - 完整的错误处理，符合 JSON-RPC 2.0 规范
    """

    def __init__(
        self,
        host: str,
        port: int,
        broadcaster: IpcEventBroadcaster | None = None,
        trace: TraceWriter | None = None,
    ) -> None:
        """
        初始化 TCP 服务器

        参数：
            host: 监听的主机地址
            port: 监听的端口号
            broadcaster: 事件广播器，用于推送事件到客户端，默认为 None
            trace: 追踪写入器，用于记录请求和响应详情，默认为 None

        属性：
            _host: 主机地址
            _port: 端口号
            _handlers: 命令处理器字典，key 为方法名，value 为处理函数
            _server: asyncio AbstractServer 实例，None 表示未启动
            _broadcaster: 事件广播器实例
            _trace: 追踪写入器实例
            _active_writers: 活跃连接的 StreamWriter 集合
        """
        self._host = host
        self._port = port
        self._handlers: dict[str, CommandHandler] = {}
        self._server: asyncio.AbstractServer | None = None
        self._broadcaster = broadcaster
        self._trace = trace
        self._active_writers: set[asyncio.StreamWriter] = set()

    def register(self, method: str, handler: CommandHandler) -> None:
        """
        注册命令处理函数

        将方法名与处理函数关联，当收到对应方法的 JSON-RPC 请求时调用。

        参数：
            method: JSON-RPC 方法名，如 "session.create"、"run.start"
            handler: 处理函数，接收参数字典并返回异步任务

        使用示例：
            >>> async def handle_session_create(params):
            ...     return {"session_id": "abc123"}
            ... 
            >>> server = SocketServer("127.0.0.1", 7437)
            >>> server.register("session.create", handle_session_create)
        """
        self._handlers[method] = handler

    async def start(self) -> str:
        """
        启动 TCP 服务器

        先检查端口是否已被占用，如果已占用则退出进程；
        否则启动服务器并返回监听地址。

        返回：
            str: 服务器监听地址，格式为 "host:port"

        实现原理：
        1. 尝试连接到指定端口，如果成功说明端口已被占用
        2. 抛出 SystemExit 异常，退出进程
        3. 如果连接被拒绝（ConnectionRefusedError），说明端口可用
        4. 使用 asyncio.start_server() 启动服务器
        5. 设置最大行限制为 64MB

        使用示例：
            >>> addr = await server.start()
            >>> print(f"Server running at {addr}")
        """
        try:
            _r, w = await asyncio.open_connection(self._host, self._port)
            w.close()
            await w.wait_closed()
            raise SystemExit(f"core already running at {self._host}:{self._port}")
        except (ConnectionRefusedError, OSError):
            pass

        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
            limit=_MAX_LINE_BYTES,
        )
        return f"{self._host}:{self._port}"

    async def stop(self) -> None:
        """
        关闭服务器

        关闭所有活跃连接，然后关闭服务器。

        实现步骤：
        1. 检查服务器是否已启动
        2. 遍历所有活跃连接，调用 close() 关闭
        3. 调用 server.close() 关闭服务器
        4. 等待最多 2 秒让服务器完全关闭
        5. 超时则放弃等待

        使用示例：
            >>> await server.stop()
        """
        if self._server is None:
            return
        for writer in list(self._active_writers):
            try:
                writer.close()
            except Exception:
                pass
        self._server.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
        except (TimeoutError, asyncio.CancelledError):
            pass

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        处理单个客户端连接

        当客户端连接时被调用，管理连接的生命周期。

        参数：
            reader: 客户端的 StreamReader，用于读取消息
            writer: 客户端的 StreamWriter，用于发送消息

        实现步骤：
        1. 获取客户端地址并记录日志
        2. 将 writer 添加到活跃连接集合
        3. 调用 _read_loop() 处理消息
        4. 在 finally 块中清理资源：
           - 从活跃连接集合中移除
           - 从广播器中取消订阅
           - 关闭连接

        使用示例：
            由 asyncio.start_server() 自动调用，无需手动调用
        """
        peer = writer.get_extra_info("peername", "<unknown>")
        logger.debug("client connected: %s", peer)
        self._active_writers.add(writer)
        try:
            await self._read_loop(reader, writer)
        finally:
            self._active_writers.discard(writer)
            if self._broadcaster is not None:
                self._broadcaster.unsubscribe(writer)
            try:
                writer.close()
            except Exception:
                pass
            logger.debug("client disconnected: %s", peer)

    async def _read_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        持续读取客户端消息

        循环读取客户端发送的消息，每条消息独立作为 task 处理。

        参数：
            reader: 客户端的 StreamReader
            writer: 客户端的 StreamWriter

        实现原理：
        1. 循环读取单行消息（JSON Lines 格式）
        2. 如果消息过大（LimitOverrunError），发送错误并返回
        3. 如果读取到空行，说明连接已关闭，返回
        4. 将每条消息作为独立 task 执行，避免阻塞读循环

        关键设计：
        - 使用 asyncio.create_task() 异步执行每条命令
        - 这样即使某个 handler 耗时很长（如 session.send_message），
          其他命令（如 permission.respond）也能被及时处理

        使用示例：
            由 _handle_connection() 调用，无需手动调用
        """
        while True:
            try:
                line = await reader.readline()
            except asyncio.LimitOverrunError:
                await self._send(writer, make_error(None, INVALID_REQUEST, "Request too large"))
                return

            if not line:
                return

            # 每条命令独立作为 task 执行，避免长时间运行的 handler（如 session.send_message）
            # 阻塞读循环，使 permission.respond 等并发命令能被及时处理
            asyncio.create_task(self._handle_line(line, writer))

    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        """
        解析并处理单行 JSON-RPC 请求

        将单行消息解析为 JSON-RPC 请求，调用对应 handler，
        将结果或错误写回客户端。

        参数：
            line: 客户端发送的单行消息（字节序列）
            writer: 客户端的 StreamWriter

        实现步骤：
        1. 解码字节序列为 JSON 字典
        2. 验证 JSON-RPC 请求格式
        3. 如果启用了追踪，记录请求详情
        4. 查找对应的 handler
        5. 设置 ContextVar，传递连接上下文
        6. 调用 handler 并获取结果
        7. 处理各种异常并返回错误
        8. 将结果序列化并发送给客户端

        错误处理：
        - JSONDecodeError: 返回 PARSE_ERROR (-32700)
        - ValidationError: 返回 INVALID_REQUEST (-32600)
        - HandlerError: 返回自定义错误码
        - 其他 Exception: 返回 INTERNAL_ERROR (-32603)

        使用示例：
            由 _read_loop() 作为 task 调用，无需手动调用
        """
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError as e:
            await self._send(writer, make_error(None, PARSE_ERROR, f"Parse error: {e}"))
            return

        try:
            req = JsonRpcRequest.model_validate(raw)
        except ValidationError as e:
            await self._send(writer, make_error(None, INVALID_REQUEST, "Invalid Request", str(e)))
            return

        if self._trace is not None:
            client_id = str(writer.get_extra_info("peername", "<unknown>"))
            self._trace.emit(
                TraceRecord(
                    ts=_now(),
                    direction="CLIENT→CORE",
                    layer="ipc",
                    kind="command",
                    client_id=client_id,
                    data={"method": req.method, "id": req.id, "params": req.params},
                )
            )

        handler = self._handlers.get(req.method)
        if handler is None:
            await self._send(
                writer,
                make_error(req.id, METHOD_NOT_FOUND, f"Method not found: {req.method}"),
            )
            return

        _writer_var.set(writer)
        try:
            result = await handler(req.params)
        except HandlerError as e:
            await self._send(writer, make_error(req.id, e.code, str(e), e.data))
            return
        except ValidationError as e:
            await self._send(
                writer,
                make_error(req.id, INVALID_REQUEST, "Invalid params", str(e)),
            )
            return
        except Exception as e:
            logger.exception("handler %s raised: %s", req.method, e)
            await self._send(writer, make_error(req.id, INTERNAL_ERROR, "Internal error"))
            return

        result_data: Any = result.model_dump() if isinstance(result, BaseModel) else result
        try:
            await self._send(writer, JsonRpcSuccess(id=req.id, result=result_data))
        except (ConnectionResetError, BrokenPipeError, OSError):
            logger.debug("client disconnected before response for %s", req.method)

    async def _send(self, writer: asyncio.StreamWriter, msg: BaseModel) -> None:
        """
        将消息发送给客户端

        将 pydantic 模型序列化为 JSON 行格式并写入流，随后刷新缓冲区。

        参数：
            writer: 客户端的 StreamWriter
            msg: 要发送的消息，必须是 BaseModel 的子类

        实现步骤：
        1. 将消息序列化为 JSON 字符串
        2. 编码为字节序列并添加换行符
        3. 写入流并刷新缓冲区
        4. 如果启用了追踪，记录发送详情

        使用示例：
            >>> await self._send(writer, JsonRpcSuccess(id="abc", result={}))
        """
        writer.write(msg.model_dump_json().encode() + b"\n")
        await writer.drain()
        if self._trace is not None:
            kind = "error" if isinstance(msg, JsonRpcError) else "response"
            client_id = str(writer.get_extra_info("peername", "<unknown>"))
            self._trace.emit(
                TraceRecord(
                    ts=_now(),
                    direction="CORE→CLIENT",
                    layer="ipc",
                    kind=kind,
                    client_id=client_id,
                    data=msg.model_dump(),
                )
            )
