from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


# MCP server 不可用异常（连接失败、超时、断开）
class McpServerUnavailableError(Exception):
    pass


# MCP server 返回的应用层错误（连接正常，但工具调用失败）
class McpToolError(Exception):
    """MCP server 返回的应用层错误（连接正常，但工具调用失败）"""
    pass


# MCP 工具定义数据类：name（工具名）、description（描述）、input_schema（输入参数 schema）
@dataclass
class McpToolDef:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


# 【s7 核心】通过 stdio 或 TCP 与 MCP server 通信的 JSON-RPC 2.0 客户端
# MCP（Model Context Protocol）是 Anthropic 定义的协议，允许外部工具服务器向 LLM 暴露工具
# 设计目的：将工具实现从 core daemon 中分离出去，实现插件化扩展
class McpClient:
    def __init__(self) -> None:
        self._id = 0                    # JSON-RPC 请求 ID（自增）
        self._proc: asyncio.subprocess.Process | None = None  # stdio 子进程
        self._reader: asyncio.StreamReader | None = None     # 读取流
        self._writer: asyncio.StreamWriter | None = None     # 写入流
        self._transport = ""            # 传输类型："stdio" 或 "tcp"
        self._lock = asyncio.Lock()     # 并发写入锁（防止多个协程同时写）
        self._stderr_task: asyncio.Task[None] | None = None  # 后台读取 stderr 的任务

    _STREAM_LIMIT = 64 * 1024 * 1024  # 64 MB，防止大响应触发 LimitOverrunError

    # 启动 stdio 子进程并完成 MCP initialize 握手
    # command: 可执行文件路径
    # args: 命令行参数
    # env: 环境变量
    async def connect_stdio(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        import os
        merged_env = {**os.environ, **(env or {})}
        # 创建子进程，标准输入输出重定向到管道
        self._proc = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
            limit=self._STREAM_LIMIT,
        )
        self._reader = self._proc.stdout
        self._writer_proc = self._proc.stdin
        self._transport = "stdio"
        # 后台持续读取 stderr，防止管道缓冲区满导致子进程阻塞
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        # 完成 MCP 握手（发送 initialize 请求）
        await self._initialize()

    # 通过 TCP 连接到 MCP server 并完成 initialize 握手
    # host: 服务器地址
    # port: 服务器端口
    async def connect_tcp(self, host: str, port: int) -> None:
        self._reader, tcp_writer = await asyncio.open_connection(host, port, limit=self._STREAM_LIMIT)
        self._tcp_writer = tcp_writer
        self._transport = "tcp"
        await self._initialize()

    # 发送 initialize 请求完成 MCP 握手
    # MCP 协议要求客户端先发送 initialize 请求，服务器返回 capabilities
    async def _initialize(self) -> None:
        await self._call("initialize", {
            "protocolVersion": "2024-11-05",  # MCP 协议版本
            "capabilities": {},               # 客户端能力（空表示默认）
            "clientInfo": {"name": "iwan-claude", "version": "0.1"},  # 客户端信息
        })
        # 发送 notifications/initialized 通知（告诉服务器客户端已就绪）
        await self._notify("notifications/initialized", {})

    # 列出 MCP server 提供的工具定义
    # 返回 McpToolDef 列表，供 ToolRegistry 注册
    async def list_tools(self) -> list[McpToolDef]:
        response = await self._call("tools/list", {})
        tools = []
        for t in response.get("tools", []):
            tools.append(McpToolDef(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            ))
        return tools

    # 调用 MCP server 上的工具，返回所有 text 内容拼接
    # 连接异常抛 McpServerUnavailableError，工具错误抛 McpToolError
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        response = await self._call("tools/call", {"name": name, "arguments": arguments})
        parts: list[str] = []
        # MCP 响应内容可能包含多个块，只提取 text 类型的内容
        for item in response.get("content", []):
            if item.get("type") == "text":
                parts.append(str(item["text"]))
        return "\n".join(parts)

    # 后台任务：持续读取 stderr 并记录日志，防止管道缓冲区满
    # 如果不读取 stderr，子进程的 stderr 缓冲区满了会导致子进程阻塞
    async def _drain_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                stderr_line = line.decode(errors="replace").rstrip()
                if stderr_line:
                    log.debug("mcp stderr: %s", stderr_line)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("mcp stderr drain stopped", exc_info=True)

    # 关闭连接并终止 stdio 子进程
    async def close(self) -> None:
        # 先取消 stderr 读取任务
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None
        # 如果是 stdio 模式，终止子进程
        if self._transport == "stdio" and self._proc is not None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        # 如果是 TCP 模式，关闭连接
        elif self._transport == "tcp":
            writer = getattr(self, "_tcp_writer", None)
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    # 发送 JSON-RPC 请求并等待响应
    # id 比较用字符串兼容服务端返回字符串 id 的情况
    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        req_id = self._id
        req_id_str = str(req_id)
        # 构建 JSON-RPC 请求
        request = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        async with self._lock:
            await self._write_line(json.dumps(request))
            while True:
                line = await self._read_line()
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("mcp: ignoring non-JSON line: %r", line[:200])
                    continue
                msg_id = msg.get("id")
                if msg_id is None:
                    # server-initiated notification，忽略（服务器主动推送的消息）
                    log.debug("mcp: received server notification: %s", msg.get("method"))
                    continue
                # 匹配响应的 id（转为字符串比较，兼容不同类型的 id）
                if str(msg_id) == req_id_str:
                    # 如果响应包含 error 字段，抛出异常
                    if "error" in msg:
                        err = msg["error"]
                        raise McpToolError(
                            f"{err.get('message', str(err))} (code={err.get('code')})"
                        )
                    # 返回结果
                    result: dict[str, Any] = msg.get("result", {})
                    return result

    # 发送 JSON-RPC 通知（无响应）
    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._write_line(json.dumps(notification))

    # 向 MCP server 写入一行 JSON
    async def _write_line(self, line: str) -> None:
        data = (line + "\n").encode()
        if self._transport == "stdio":
            w = self._proc.stdin if self._proc else None
            if w is None:
                raise McpServerUnavailableError("stdio writer unavailable")
            w.write(data)
            await w.drain()
        elif self._transport == "tcp":
            w = getattr(self, "_tcp_writer", None)
            if w is None:
                raise McpServerUnavailableError("tcp writer unavailable")
            w.write(data)
            await w.drain()

    # 从 MCP server 读取一行 JSON；跳过空行，仅 EOF（b""）才视为连接断开
    async def _read_line(self) -> str:
        if self._reader is None:
            raise McpServerUnavailableError("reader unavailable")
        while True:
            try:
                data = await asyncio.wait_for(self._reader.readline(), timeout=30.0)
            except TimeoutError:
                raise McpServerUnavailableError("MCP server read timeout")
            except asyncio.LimitOverrunError as exc:
                raise McpServerUnavailableError(
                    f"MCP response too large (>{self._STREAM_LIMIT // 1024 // 1024}MB): {exc}"
                ) from exc
            if data == b"":
                raise McpServerUnavailableError("MCP server closed connection")
            line = data.decode(errors="replace").strip()
            if line:
                return line