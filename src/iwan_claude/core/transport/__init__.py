"""
IPC 传输模块

该模块提供了客户端与核心服务之间的 TCP 通信机制，实现了基于 JSON-RPC 2.0 的进程间通信（IPC）。

核心组件：
- SocketServer: TCP 服务器，监听客户端连接并处理命令
- SocketClient: TCP 客户端，连接到核心服务并发送命令
- IpcEventBroadcaster: 事件广播器，将服务器端事件推送到所有订阅客户端

通信协议：
- 使用 JSON-RPC 2.0 协议进行命令请求和响应
- 使用换行符分隔的 JSON 行格式（JSON Lines）进行事件推送
- 支持最大 64MB 的单行消息，兼容 MCP 大文件工具结果

设计要点：
- 使用 asyncio StreamReader/StreamWriter 实现异步 TCP 通信
- 支持命令响应模式（request-response）和事件推送模式（publish-subscribe）
- 使用 ContextVar 在 handler 中传递连接上下文
- 支持 topic 和 scope 过滤，实现事件的精确推送
"""

from iwan_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster
from iwan_claude.core.transport.socket_client import SocketClient
from iwan_claude.core.transport.socket_server import SocketServer, get_connection_writer

__all__ = ["SocketServer", "SocketClient", "IpcEventBroadcaster", "get_connection_writer"]