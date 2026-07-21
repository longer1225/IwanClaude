"""
IPC 客户端

该模块实现了 TCP 客户端，用于连接到核心服务并进行 JSON-RPC 通信。

核心功能：
- 建立和管理 TCP 连接
- 发送 JSON-RPC 命令并等待响应
- 接收和处理服务器推送的事件
- 管理待处理的 RPC 请求（pending futures）

设计要点：
- 使用 asyncio StreamReader/StreamWriter 实现异步 TCP 通信
- 使用 Future 实现 RPC 请求的异步等待机制
- 支持命令响应模式和事件推送模式
- 使用 JSON Lines 格式进行消息传输
- 支持最大 64MB 的单行消息，兼容 MCP 大文件工具结果
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from iwan_claude.core.bus.envelope import JsonRpcRequest

# 事件处理器类型定义
# 接收事件数据字典，返回异步任务
type EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

# 单行消息的最大字节数，64MB
# 这个值需要足够大以兼容 MCP 工具可能返回的大文件结果
_MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MB per frame


class IpcError(RuntimeError):
    """
    IPC 错误类

    用于封装 JSON-RPC 响应中的错误信息。

    属性：
        code: 错误码，符合 JSON-RPC 2.0 规范
        message: 错误消息

    使用示例：
        >>> raise IpcError(-32601, "Method not found")
    """

    def __init__(self, code: int, message: str) -> None:
        """
        初始化 IPC 错误

        参数：
            code: 错误码，如 -32600（无效请求）、-32601（方法未找到）、-32602（无效参数）
            message: 错误描述信息
        """
        super().__init__(f"[{code}] {message}")
        self.code = code


class SocketClient:
    """
    TCP 客户端类

    实现与核心服务的异步 TCP 通信，支持 JSON-RPC 请求响应和事件推送。

    工作原理：
    1. 通过 connect() 建立 TCP 连接
    2. 通过 send_command() 发送 JSON-RPC 命令，返回 Future 等待响应
    3. 通过 run_event_loop() 持续读取服务器消息
    4. 通过 _dispatch() 路由消息到对应的 Future（RPC 响应）或事件处理器
    5. 通过 close() 关闭连接并清理资源

    特点：
    - 支持异步命令发送和响应等待
    - 支持事件推送订阅
    - 自动管理 pending futures
    - 连接关闭时自动取消所有待处理请求
    """

    def __init__(self, host: str, port: int) -> None:
        """
        初始化 TCP 客户端

        参数：
            host: 核心服务的主机地址
            port: 核心服务的端口号

        属性：
            _host: 主机地址
            _port: 端口号
            _reader: 异步流读取器，用于读取服务器消息
            _writer: 异步流写入器，用于发送消息到服务器
            _pending: 待处理的 RPC 请求字典，key 为请求 ID，value 为 Future
            _event_handlers: 事件处理器列表，处理服务器推送的事件
        """
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._event_handlers: list[EventHandler] = []

    async def connect(self) -> None:
        """
        建立到核心服务的 TCP 连接

        使用 asyncio.open_connection() 创建异步 TCP 连接，设置最大行限制为 64MB。

        实现细节：
        - limit 参数设置为 _MAX_LINE_BYTES，确保能接收大消息
        - 返回的 reader 和 writer 分别赋值给 _reader 和 _writer 属性

        异常：
            ConnectionRefusedError: 核心服务未启动或端口被占用
            OSError: 网络连接失败

        使用示例：
            >>> client = SocketClient("127.0.0.1", 7437)
            >>> await client.connect()
        """
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port, limit=_MAX_LINE_BYTES
        )

    async def close(self) -> None:
        """
        关闭 TCP 连接并等待底层 socket 释放

        实现步骤：
        1. 检查 writer 是否存在
        2. 调用 writer.close() 关闭连接
        3. 等待最多 1 秒让连接完全关闭
        4. 超时则放弃等待

        使用示例：
            >>> await client.close()
        """
        if self._writer is not None:
            self._writer.close()
            try:
                await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
            except TimeoutError:
                pass

    def on_event(self, handler: EventHandler) -> None:
        """
        注册服务器推送事件的回调

        可以多次调用以添加多个事件处理器，所有处理器都会在事件发生时被调用。

        参数：
            handler: 事件处理器函数，接收事件数据字典并返回异步任务

        使用示例：
            >>> async def handle_event(data):
            ...     print(f"收到事件: {data}")
            ... 
            >>> client.on_event(handle_event)
        """
        self._event_handlers.append(handler)

    async def send_command(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        发送 JSON-RPC 命令并等待响应

        创建一个唯一的请求 ID，将命令封装为 JsonRpcRequest，发送到服务器，
        并返回一个 Future 等待响应。

        参数：
            method: JSON-RPC 方法名，如 "session.create"、"run.start"
            params: 方法参数字典

        返回：
            dict[str, Any]: RPC 响应的 result 字段

        异常：
            RuntimeError: 连接未建立
            IpcError: RPC 调用失败，包含错误码和错误消息

        实现原理：
        1. 生成唯一的 request ID（UUID）
        2. 创建 JsonRpcRequest 对象
        3. 创建 Future 并存储到 _pending 字典
        4. 将请求序列化为 JSON 并发送
        5. 等待 Future 完成并返回结果

        使用示例：
            >>> result = await client.send_command("session.create", {"name": "test"})
            >>> print(result)
            {"session_id": "abc123"}
        """
        if self._writer is None:
            raise RuntimeError("not connected — call connect() first")
        req_id = str(uuid.uuid4())
        request = JsonRpcRequest(id=req_id, method=method, params=params)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        self._writer.write(request.model_dump_json().encode() + b"\n")
        await self._writer.drain()
        return await fut

    async def run_event_loop(self) -> None:
        """
        持续读取服务器消息并分发

        这是客户端的主事件循环，负责：
        1. 持续读取服务器发送的消息
        2. 将消息路由到对应的处理器

        异常处理：
        - ConnectionResetError/OSError: 连接断开，退出循环
        - ValueError/LimitOverrunError: 单行消息过大，丢弃并继续
        - 空行: 连接关闭，退出循环

        清理逻辑（finally）：
        - 取消所有待处理的 Future
        - 清空 pending 字典

        使用示例：
            >>> async def main():
            ...     client = SocketClient("127.0.0.1", 7437)
            ...     await client.connect()
            ...     await client.run_event_loop()  # 阻塞直到连接关闭
        """
        if self._reader is None:
            raise RuntimeError("not connected — call connect() first")
        try:
            while True:
                try:
                    line = await self._reader.readline()
                except (ConnectionResetError, OSError):
                    break
                except (ValueError, asyncio.LimitOverrunError):
                    # 单行超出 limit；丢弃本行，继续读取后续消息
                    continue
                if not line:
                    break
                await self._dispatch(line)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            self._pending.clear()

    async def _dispatch(self, line: bytes) -> None:
        """
        解析单行消息并路由到对应处理器

        根据消息类型，将消息路由到：
        1. JSON-RPC 响应 → 对应的 pending Future
        2. 事件推送 → 所有注册的事件处理器

        参数：
            line: 服务器发送的单行消息（字节序列）

        实现原理：
        1. 将字节序列解码为 JSON 字典
        2. 检查是否包含 "jsonrpc" 字段（RPC 响应）
        3. 如果是 RPC 响应，查找对应的 Future 并设置结果或异常
        4. 如果是事件（kind == "event"），调用所有事件处理器

        容错设计：
        - JSON 解析失败时直接返回，不抛出异常
        - Future 已完成时不重复设置结果
        """
        try:
            msg: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            return

        if "jsonrpc" in msg:
            req_id: str | None = msg.get("id")
            if req_id and req_id in self._pending:
                fut = self._pending.pop(req_id)
                if not fut.done():
                    if "error" in msg:
                        err = msg["error"]
                        fut.set_exception(
                            IpcError(err.get("code", -1), err.get("message", "unknown"))
                        )
                    else:
                        fut.set_result(msg.get("result") or {})
        elif msg.get("kind") == "event":
            event_data: dict[str, Any] = msg.get("event", {})
            for handler in self._event_handlers:
                await handler(event_data)
