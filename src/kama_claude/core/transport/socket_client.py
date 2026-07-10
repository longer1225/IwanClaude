# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：Python 的异步 I/O 库，用于网络通信
import asyncio
# 导入 json：用于序列化/反序列化网络传输的数据
import json
# 导入 uuid：用于生成唯一的请求 ID
import uuid
# 导入 Awaitable 和 Callable：类型注解，用于定义异步回调函数类型
from collections.abc import Awaitable, Callable
# 导入 Any：类型注解，表示任意类型
from typing import Any

# 导入 JsonRpcRequest：JSON-RPC 请求的 Pydantic 模型
from kama_claude.core.bus.envelope import JsonRpcRequest

# 定义事件处理器类型：接收一个字典事件，返回一个异步任务（Awaitable[None]）
# 什么是 Callable？就是一个可调用的函数或对象
# 什么是 Awaitable？就是可以用 await 等待的对象（比如 async def 函数）
type EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

# 单帧最大字节数：1 MB（防止收到过大的消息导致内存溢出）
# 什么是帧？这里指一次网络传输的单个消息（一行 JSON）
_MAX_LINE_BYTES = 1 * 1024 * 1024  # 1 MB per frame


# IpcError 类：IPC 通信过程中的错误类型
# 继承自 RuntimeError，增加了 code 字段用于错误分类
class IpcError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        # 调用父类构造函数，格式化错误消息
        super().__init__(f"[{code}] {message}")
        # 保存错误码，方便上层判断错误类型
        self.code = code


# SocketClient 类：TCP 客户端，用于与 Core daemon 通信
# 它是一个通用的网络客户端，可以被任何进程使用（CLI、TUI、甚至其他服务）
class SocketClient:
    # 初始化方法：保存连接地址和端口
    def __init__(self, host: str, port: int) -> None:
        # 目标主机地址（通常是 127.0.0.1，即本地回环地址）
        self._host = host
        # 目标端口（Core daemon 监听的端口）
        self._port = port
        # 异步流读取器：用于从网络连接读取数据
        # None 表示尚未连接
        self._reader: asyncio.StreamReader | None = None
        # 异步流写入器：用于向网络连接写入数据
        # None 表示尚未连接
        self._writer: asyncio.StreamWriter | None = None
        # 待处理的请求字典：key 是请求 ID，value 是 Future 对象
        # 什么是 Future？就是一个"未来结果"的占位符，请求发出去后，
        # 对应的 Future 会等待响应回来，然后设置结果
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # 事件处理器列表：当收到服务器推送的事件时，会调用这些处理器
        # 支持注册多个处理器（比如同时打印到终端和写入日志）
        self._event_handlers: list[EventHandler] = []

    # 建立到 Core daemon 的 TCP 连接
    # async def：这是一个异步函数，可以使用 await
    async def connect(self) -> None:
        # asyncio.open_connection()：创建一个 TCP 连接
        # 返回两个对象：StreamReader（读）和 StreamWriter（写）
        # limit：单个消息的最大字节数，防止内存溢出
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port, limit=_MAX_LINE_BYTES
        )

    # 关闭 TCP 连接并等待底层 socket 释放
    async def close(self) -> None:
        if self._writer is not None:
            # 关闭写入端（发送 TCP FIN 包）
            self._writer.close()
            try:
                # 等待连接真正关闭，超时 1 秒
                await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
            except TimeoutError:
                # 如果超时，忽略错误，强制关闭
                pass

    # 注册服务器推送事件的回调，可多次调用以添加多个 handler
    # 这是一个同步方法（没有 async），因为只是添加到列表，不需要等待
    def on_event(self, handler: EventHandler) -> None:
        self._event_handlers.append(handler)

    # 发送 JSON-RPC 命令并等待响应，成功返回 result dict，失败抛出 IpcError
    # 这是一个"请求-响应"模式：发送命令，等待回复
    async def send_command(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        # 检查是否已连接
        if self._writer is None:
            raise RuntimeError("not connected — call connect() first")
        
        # 生成唯一的请求 ID（UUID），用于匹配响应
        # 为什么需要 ID？因为网络是异步的，可能同时发送多个请求，
        # 需要通过 ID 来区分哪个响应对应哪个请求
        req_id = str(uuid.uuid4())
        
        # 创建 JSON-RPC 请求对象
        request = JsonRpcRequest(id=req_id, method=method, params=params)
        
        # 创建一个 Future 对象，用于等待响应
        # asyncio.get_running_loop()：获取当前运行的事件循环
        # create_future()：创建一个新的 Future
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        
        # 将 Future 存入待处理字典，用请求 ID 作为 key
        self._pending[req_id] = fut
        
        # 将请求序列化为 JSON 字符串，编码为字节，加上换行符
        # 为什么加换行符？因为使用的是 NDJSON（Newline-Delimited JSON）格式，
        # 每个消息占一行，服务器通过换行符来分割消息
        self._writer.write(request.model_dump_json().encode() + b"\n")
        
        # 刷新缓冲区，确保数据真正发送出去
        # 如果不调用 drain()，数据可能还在内存缓冲区中，没有发送
        await self._writer.drain()
        
        # 等待 Future 完成（即等待服务器响应）
        # 响应回来后，_dispatch 方法会设置 Future 的结果
        return await fut

    # 持续读取服务器消息，分发 RPC 响应到 pending future 或事件到 event handler
    # 这是一个"事件循环"，需要在后台持续运行
    async def run_event_loop(self) -> None:
        # 检查是否已连接
        if self._reader is None:
            raise RuntimeError("not connected — call connect() first")
        
        try:
            # 无限循环：持续读取消息
            while True:
                try:
                    # 读取一行数据（直到遇到换行符）
                    # 这是一个阻塞操作，但因为是 async，不会阻塞事件循环
                    line = await self._reader.readline()
                except (ConnectionResetError, OSError):
                    # 连接被重置（服务器断开），退出循环
                    break
                
                # 如果读取到空字节，说明连接已经关闭
                if not line:
                    break
                
                # 解析并分发消息
                await self._dispatch(line)
        
        finally:
            # 清理：取消所有待处理的请求
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            # 清空待处理字典
            self._pending.clear()

    # 解析单行消息并路由到 pending future（RPC 响应）或 event handler（服务器推送）
    async def _dispatch(self, line: bytes) -> None:
        try:
            # 将字节数据解析为 JSON 字典
            msg: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            # 如果不是合法的 JSON，忽略这条消息
            return

        # ====================== 判断消息类型 ======================
        # 如果消息包含 "jsonrpc" 字段，说明是 JSON-RPC 响应
        if "jsonrpc" in msg:
            # 获取请求 ID
            req_id: str | None = msg.get("id")
            # 如果 ID 存在且在待处理字典中
            if req_id and req_id in self._pending:
                # 从待处理字典中取出对应的 Future
                fut = self._pending.pop(req_id)
                # 如果 Future 还没有完成
                if not fut.done():
                    # 如果消息包含 error 字段，说明请求失败
                    if "error" in msg:
                        err = msg["error"]
                        # 设置 Future 为异常状态（抛出 IpcError）
                        fut.set_exception(
                            IpcError(err.get("code", -1), err.get("message", "unknown"))
                        )
                    else:
                        # 请求成功，设置 Future 的结果为 result 字段
                        fut.set_result(msg.get("result") or {})
        
        # 如果消息包含 "kind": "event"，说明是服务器推送的事件
        elif msg.get("kind") == "event":
            # 获取事件数据
            event_data: dict[str, Any] = msg.get("event", {})
            # 遍历所有注册的事件处理器，调用它们处理事件
            for handler in self._event_handlers:
                await handler(event_data)
