"""
MCP 客户端模块 - 实现与 MCP Server 的通信

【学习要点】
1. MCP 协议：Model Context Protocol，Anthropic 定义的工具服务器协议
2. JSON-RPC 2.0：基于 JSON 的远程过程调用协议
3. 双传输模式：支持 stdio（子进程）和 TCP（网络）两种通信方式
4. 并发安全：使用 asyncio.Lock 保证并发写入安全
5. 管道处理：后台任务持续读取 stderr，防止缓冲区满导致阻塞

【核心类】
- McpClient: MCP 客户端，负责连接、握手、工具调用
- McpToolDef: 工具定义数据类
- McpServerUnavailableError: 服务器不可用异常
- McpToolError: 工具调用错误异常

【协议流程】
1. 连接建立（stdio 或 TCP）
2. 发送 initialize 请求（握手）
3. 发送 notifications/initialized 通知
4. 调用 tools/list 获取工具列表
5. 调用 tools/call 执行工具
6. 关闭连接
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


class McpServerUnavailableError(Exception):
    """
    MCP Server 不可用异常

    【触发场景】
    - 连接失败（子进程启动失败、TCP 连接超时）
    - 连接断开（EOF）
    - 读取超时
    - 响应过大（超过流限制）

    【设计目的】
    区分服务器级别的错误（连接问题）和工具级别的错误（业务问题）
    """
    pass


class McpToolError(Exception):
    """
    MCP Server 返回的应用层错误

    【触发场景】
    - 连接正常，但工具调用失败
    - JSON-RPC 响应中包含 error 字段

    【设计目的】
    区分服务器级别的错误和工具级别的错误，便于上层处理
    """
    pass


@dataclass
class McpToolDef:
    """
    MCP 工具定义数据类

    【字段说明】
    - name: str - 工具名称（唯一标识）
    - description: str - 工具描述（用于 LLM 理解工具功能）
    - input_schema: dict[str, Any] - 输入参数的 JSON Schema（用于参数校验和生成）

    【使用场景】
    从 MCP Server 获取工具列表后，解析为 McpToolDef 对象，供 ToolRegistry 注册

    【示例】
    ```python
    tool_def = McpToolDef(
        name="database_query",
        description="查询数据库",
        input_schema={
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL 查询语句"}
            },
            "required": ["sql"]
        }
    )
    ```
    """
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


class McpClient:
    """
    MCP 客户端 - 通过 stdio 或 TCP 与 MCP Server 通信

    【学习要点】
    1. JSON-RPC 2.0：基于 JSON 的远程过程调用协议，每行一个 JSON 对象
    2. 双传输模式：支持 stdio（子进程）和 TCP（网络）两种通信方式
    3. 并发安全：使用 asyncio.Lock 保证多个协程同时写入时不会交错
    4. 管道处理：后台任务持续读取 stderr，防止缓冲区满导致子进程阻塞
    5. 流限制：设置 64MB 流限制，防止恶意服务器发送过大响应

    【核心方法】
    - connect_stdio(): 通过 stdio 连接子进程
    - connect_tcp(): 通过 TCP 连接远程服务器
    - list_tools(): 获取工具列表
    - call_tool(): 调用工具
    - close(): 关闭连接

    【状态管理】
    - _id: JSON-RPC 请求 ID（自增）
    - _proc: stdio 子进程对象
    - _reader: 读取流（统一接口，兼容 stdio 和 TCP）
    - _transport: 传输类型标识
    - _lock: 并发写入锁
    - _stderr_task: 后台读取 stderr 的任务

    【协议实现】
    遵循 MCP 协议规范（2024-11-05 版本）：
    1. 发送 initialize 请求完成握手
    2. 发送 notifications/initialized 通知
    3. 调用 tools/list 获取工具定义
    4. 调用 tools/call 执行工具
    """
    # 流限制：64MB，防止大响应触发 LimitOverrunError
    _STREAM_LIMIT = 64 * 1024 * 1024

    def __init__(self) -> None:
        """
        初始化 MCP 客户端

        【字段说明】
        - _id: int - JSON-RPC 请求 ID（自增），用于匹配请求和响应
        - _proc: asyncio.subprocess.Process | None - stdio 子进程对象（仅 stdio 模式）
        - _reader: asyncio.StreamReader | None - 读取流（统一接口，兼容 stdio 和 TCP）
        - _writer_proc: asyncio.StreamWriter | None - stdio 写入流
        - _tcp_writer: asyncio.StreamWriter | None - TCP 写入流
        - _transport: str - 传输类型："stdio" 或 "tcp"
        - _lock: asyncio.Lock - 并发写入锁（防止多个协程同时写入时 JSON 行交错）
        - _stderr_task: asyncio.Task | None - 后台读取 stderr 的任务（防止缓冲区满）

        【设计要点】
        - 使用统一的 _reader 接口，简化读取逻辑
        - 写入流根据传输类型分别存储，在 _write_line 中处理
        - 使用 asyncio.Lock 保证并发写入安全
        - 后台任务持续读取 stderr，防止子进程阻塞
        """
        # JSON-RPC 请求 ID（自增）
        self._id = 0
        # stdio 子进程对象（仅 stdio 模式）
        self._proc: asyncio.subprocess.Process | None = None
        # 读取流（统一接口，兼容 stdio 和 TCP）
        self._reader: asyncio.StreamReader | None = None
        # 传输类型："stdio" 或 "tcp"
        self._transport = ""
        # 并发写入锁（防止多个协程同时写入时 JSON 行交错）
        self._lock = asyncio.Lock()
        # 后台读取 stderr 的任务（防止缓冲区满导致子进程阻塞）
        self._stderr_task: asyncio.Task[None] | None = None

    async def connect_stdio(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        """
        通过 stdio 连接子进程并完成 MCP 握手

        【参数说明】
        - command: str - 可执行文件路径（如 "python", "node"）
        - args: list[str] - 命令行参数（如 ["-m", "my_mcp_server"]）
        - env: dict[str, str] | None - 额外的环境变量（会合并到当前环境）

        【执行流程】
        1. 合并环境变量（当前环境 + 传入的环境变量）
        2. 创建子进程，将 stdin/stdout/stderr 重定向到管道
        3. 设置读取流和写入流
        4. 创建后台任务读取 stderr（防止缓冲区满）
        5. 发送 initialize 请求完成 MCP 握手

        【关键技术点】
        - 使用 asyncio.create_subprocess_exec 创建异步子进程
        - 设置 limit=self._STREAM_LIMIT 防止大响应触发 LimitOverrunError
        - 后台任务持续读取 stderr，防止子进程阻塞

        【注意事项】
        - command 必须是可执行文件的绝对路径或在 PATH 中
        - args 必须是列表，每个元素是一个参数
        - 子进程启动后，如果不读取 stderr，缓冲区满了会导致子进程阻塞

        【示例】
        ```python
        client = McpClient()
        await client.connect_stdio(
            command="python",
            args=["-m", "my_mcp_server"],
            env={"MY_VAR": "value"}
        )
        ```
        """
        # 导入 os 模块（放在函数内部避免循环导入）
        import os
        # 合并环境变量：当前环境 + 传入的环境变量
        merged_env = {**os.environ, **(env or {})}
        # 创建子进程，标准输入输出重定向到管道
        self._proc = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,     # 标准输入重定向到管道
            stdout=asyncio.subprocess.PIPE,    # 标准输出重定向到管道
            stderr=asyncio.subprocess.PIPE,    # 标准错误重定向到管道
            env=merged_env,                    # 环境变量
            limit=self._STREAM_LIMIT,          # 流限制（防止大响应）
        )
        # 设置读取流（子进程的标准输出）
        self._reader = self._proc.stdout
        # 设置写入流（子进程的标准输入）
        self._writer_proc = self._proc.stdin
        # 设置传输类型为 stdio
        self._transport = "stdio"
        # 创建后台任务持续读取 stderr（防止缓冲区满导致子进程阻塞）
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        # 完成 MCP 握手（发送 initialize 请求）
        await self._initialize()

    async def connect_tcp(self, host: str, port: int) -> None:
        """
        通过 TCP 连接到 MCP Server 并完成握手

        【参数说明】
        - host: str - 服务器地址（如 "localhost", "127.0.0.1", "example.com"）
        - port: int - 服务器端口（如 8080）

        【执行流程】
        1. 使用 asyncio.open_connection 建立 TCP 连接
        2. 设置读取流和写入流
        3. 设置传输类型为 tcp
        4. 发送 initialize 请求完成 MCP 握手

        【关键技术点】
        - 使用 asyncio.open_connection 建立异步 TCP 连接
        - 设置 limit=self._STREAM_LIMIT 防止大响应触发 LimitOverrunError

        【注意事项】
        - 服务器必须已启动并监听指定端口
        - 网络连接可能因防火墙、网络问题失败
        - TCP 连接有超时限制（默认由操作系统决定）

        【示例】
        ```python
        client = McpClient()
        await client.connect_tcp(host="localhost", port=8080)
        ```
        """
        # 建立 TCP 连接，获取读取流和写入流
        self._reader, tcp_writer = await asyncio.open_connection(
            host, port, 
            limit=self._STREAM_LIMIT  # 流限制（防止大响应）
        )
        # 设置 TCP 写入流
        self._tcp_writer = tcp_writer
        # 设置传输类型为 tcp
        self._transport = "tcp"
        # 完成 MCP 握手（发送 initialize 请求）
        await self._initialize()

    async def _initialize(self) -> None:
        """
        发送 initialize 请求完成 MCP 握手

        【MCP 协议规范】
        MCP 协议要求客户端先发送 initialize 请求，服务器返回 capabilities。
        客户端收到响应后，发送 notifications/initialized 通知，告诉服务器客户端已就绪。

        【请求参数】
        - protocolVersion: str - MCP 协议版本（固定为 "2024-11-05"）
        - capabilities: dict - 客户端能力（空字典表示默认能力）
        - clientInfo: dict - 客户端信息（name, version）

        【执行流程】
        1. 发送 initialize 请求（带 id，期望响应）
        2. 发送 notifications/initialized 通知（不带 id，不需要响应）

        【设计目的】
        - 验证服务器支持的协议版本
        - 告知服务器客户端的能力
        - 完成握手后，服务器才会处理后续的工具调用请求

        【注意事项】
        - 必须先完成握手才能调用工具
        - notifications/initialized 是通知（notification），不需要响应
        """
        # 发送 initialize 请求（带 id，期望响应）
        await self._call("initialize", {
            "protocolVersion": "2024-11-05",  # MCP 协议版本（固定值）
            "capabilities": {},               # 客户端能力（空表示默认）
            "clientInfo": {"name": "iwan-claude", "version": "0.1"},  # 客户端信息
        })
        # 发送 notifications/initialized 通知（不带 id，不需要响应）
        await self._notify("notifications/initialized", {})

    async def list_tools(self) -> list[McpToolDef]:
        """
        获取 MCP Server 提供的工具列表

        【返回值】
        - list[McpToolDef]: 工具定义列表

        【执行流程】
        1. 调用 tools/list 方法
        2. 解析响应中的 tools 数组
        3. 将每个工具转换为 McpToolDef 对象
        4. 返回工具列表

        【响应格式】
        ```json
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {
                        "name": "tool_name",
                        "description": "工具描述",
                        "inputSchema": { ... }
                    }
                ]
            }
        }
        ```

        【使用场景】
        在连接建立后，调用此方法获取工具列表，然后注册到 ToolRegistry

        【注意事项】
        - 必须先完成 initialize 握手才能调用
        - 如果服务器没有提供任何工具，返回空列表
        """
        # 调用 tools/list 方法
        response = await self._call("tools/list", {})
        tools = []
        # 遍历工具列表
        for t in response.get("tools", []):
            # 将工具定义转换为 McpToolDef 对象
            tools.append(McpToolDef(
                name=t.get("name", ""),           # 工具名称
                description=t.get("description", ""),  # 工具描述
                input_schema=t.get("inputSchema", {}),  # 输入参数 Schema
            ))
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """
        调用 MCP Server 上的工具

        【参数说明】
        - name: str - 工具名称（必须是 list_tools 返回的工具之一）
        - arguments: dict[str, Any] - 工具参数（必须符合工具的 input_schema）

        【返回值】
        - str: 工具执行结果（所有 text 类型内容的拼接）

        【异常处理】
        - McpServerUnavailableError: 服务器不可用（连接问题）
        - McpToolError: 工具执行失败（业务问题）

        【响应格式】
        ```json
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [
                    {"type": "text", "text": "结果文本1"},
                    {"type": "text", "text": "结果文本2"}
                ]
            }
        }
        ```

        【执行流程】
        1. 调用 tools/call 方法
        2. 解析响应中的 content 数组
        3. 提取所有 type="text" 的内容
        4. 将内容拼接为字符串返回

        【设计要点】
        - 只提取 text 类型的内容，忽略其他类型（如 image、file）
        - 使用 \n 连接多个内容块

        【注意事项】
        - 参数必须符合工具的 input_schema
        - 如果工具返回 error，会抛出 McpToolError
        """
        # 调用 tools/call 方法
        response = await self._call("tools/call", {"name": name, "arguments": arguments})
        parts: list[str] = []
        # MCP 响应内容可能包含多个块，只提取 text 类型的内容
        for item in response.get("content", []):
            if item.get("type") == "text":
                parts.append(str(item["text"]))
        # 将多个 text 块拼接为字符串
        return "\n".join(parts)

    async def _drain_stderr(self) -> None:
        """
        后台任务：持续读取 stderr 并记录日志

        【设计目的】
        如果不读取 stderr，子进程的 stderr 缓冲区满了会导致子进程阻塞。
        这个后台任务持续读取 stderr 并记录到日志，防止缓冲区满。

        【执行流程】
        1. 检查子进程是否存在且有 stderr
        2. 循环读取 stderr 行
        3. 如果读到空行（EOF），退出循环
        4. 将每行解码后记录到 debug 日志
        5. 如果任务被取消或发生异常，优雅退出

        【关键技术点】
        - 使用 await self._proc.stderr.readline() 异步读取
        - 使用 decode(errors="replace") 处理编码错误
        - 使用 log.debug 记录日志（仅在 debug 级别输出）

        【注意事项】
        - 仅在 stdio 模式下需要此任务
        - 任务会在 close() 时被取消
        """
        # 检查子进程是否存在且有 stderr
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            # 循环读取 stderr 行
            while True:
                line = await self._proc.stderr.readline()
                # 如果读到空行（EOF），退出循环
                if not line:
                    break
                # 解码并去除末尾换行符
                stderr_line = line.decode(errors="replace").rstrip()
                # 如果行不为空，记录到 debug 日志
                if stderr_line:
                    log.debug("mcp stderr: %s", stderr_line)
        except asyncio.CancelledError:
            # 任务被取消，优雅退出
            pass
        except Exception:
            # 发生异常，记录到 debug 日志
            log.debug("mcp stderr drain stopped", exc_info=True)

    async def close(self) -> None:
        """
        关闭连接并清理资源

        【执行流程】
        1. 取消 stderr 读取任务
        2. 如果是 stdio 模式：
           - 发送 terminate 信号终止子进程
           - 等待子进程结束（超时 5 秒）
           - 如果超时，发送 kill 信号强制终止
        3. 如果是 TCP 模式：
           - 关闭 TCP 连接
           - 等待连接关闭

        【资源清理】
        - 取消后台任务
        - 关闭子进程或 TCP 连接
        - 释放文件句柄

        【设计要点】
        - 使用 terminate() 先优雅终止，再使用 kill() 强制终止
        - 设置 5 秒超时，防止无限等待
        - 使用 getattr 获取 _tcp_writer，避免属性不存在的错误

        【注意事项】
        - 必须在不再使用客户端时调用 close()
        - 调用 close() 后，客户端不能再使用
        """
        # 1. 取消 stderr 读取任务
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None
        
        # 2. 如果是 stdio 模式，终止子进程
        if self._transport == "stdio" and self._proc is not None:
            try:
                # 发送 terminate 信号（优雅终止）
                self._proc.terminate()
                # 等待子进程结束（超时 5 秒）
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:
                # 如果超时或其他异常，发送 kill 信号（强制终止）
                try:
                    self._proc.kill()
                except Exception:
                    pass
        
        # 3. 如果是 TCP 模式，关闭连接
        elif self._transport == "tcp":
            # 使用 getattr 获取 _tcp_writer（避免属性不存在的错误）
            writer = getattr(self, "_tcp_writer", None)
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        发送 JSON-RPC 请求并等待响应

        【参数说明】
        - method: str - JSON-RPC 方法名（如 "initialize", "tools/list", "tools/call"）
        - params: dict[str, Any] - 方法参数

        【返回值】
        - dict[str, Any]: 响应结果

        【JSON-RPC 请求格式】
        ```json
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {...}}
        ```

        【JSON-RPC 响应格式】
        ```json
        {"jsonrpc": "2.0", "id": 1, "result": {...}}
        ```

        【执行流程】
        1. 自增请求 ID
        2. 构建 JSON-RPC 请求
        3. 获取写入锁（防止并发写入交错）
        4. 写入请求行
        5. 循环读取响应行
        6. 解析 JSON（跳过非 JSON 行）
        7. 忽略服务器通知（id 为 None）
        8. 匹配响应 ID（转为字符串比较）
        9. 如果响应包含 error，抛出 McpToolError
        10. 返回 result

        【设计要点】
        - 使用 asyncio.Lock 保证并发写入安全
        - 将 ID 转为字符串比较，兼容服务端返回字符串 ID 的情况
        - 跳过非 JSON 行（可能是日志或其他输出）
        - 忽略服务器通知（server-initiated notification）

        【异常处理】
        - McpServerUnavailableError: 服务器不可用（连接断开、超时等）
        - McpToolError: 工具执行失败（响应包含 error 字段）

        【注意事项】
        - 必须在锁内读取响应，防止多个请求的响应交错
        """
        # 自增请求 ID
        self._id += 1
        req_id = self._id
        req_id_str = str(req_id)
        # 构建 JSON-RPC 请求
        request = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        
        # 获取写入锁（防止并发写入交错）
        async with self._lock:
            # 写入请求行
            await self._write_line(json.dumps(request))
            
            # 循环读取响应行
            while True:
                line = await self._read_line()
                try:
                    # 解析 JSON
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    # 跳过非 JSON 行（可能是日志或其他输出）
                    log.debug("mcp: ignoring non-JSON line: %r", line[:200])
                    continue
                
                msg_id = msg.get("id")
                if msg_id is None:
                    # 服务器通知（server-initiated notification），忽略
                    log.debug("mcp: received server notification: %s", msg.get("method"))
                    continue
                
                # 匹配响应的 ID（转为字符串比较，兼容不同类型的 ID）
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

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """
        发送 JSON-RPC 通知（无响应）

        【参数说明】
        - method: str - JSON-RPC 方法名
        - params: dict[str, Any] - 方法参数

        【JSON-RPC 通知格式】
        ```json
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {...}}
        ```

        【设计目的】
        通知不需要响应，用于告知服务器客户端状态变化。
        例如：notifications/initialized 通知服务器客户端已就绪。

        【与 _call 的区别】
        - _call: 带 id，期望响应
        - _notify: 不带 id，不需要响应

        【注意事项】
        - 通知不会等待响应
        - 通知可能会丢失（如果连接断开）
        """
        # 构建 JSON-RPC 通知（不带 id）
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        # 写入通知行
        await self._write_line(json.dumps(notification))

    async def _write_line(self, line: str) -> None:
        """
        向 MCP Server 写入一行 JSON

        【参数说明】
        - line: str - 要写入的 JSON 字符串

        【执行流程】
        1. 将字符串编码为字节（添加换行符）
        2. 根据传输类型选择写入方式
        3. 如果写入流不可用，抛出 McpServerUnavailableError
        4. 写入数据并刷新缓冲区

        【传输方式】
        - stdio: 写入子进程的 stdin
        - tcp: 写入 TCP 连接的 writer

        【设计要点】
        - 使用 getattr 获取 _tcp_writer，避免属性不存在的错误
        - 调用 drain() 确保数据已写入底层缓冲区

        【异常处理】
        - McpServerUnavailableError: 写入流不可用
        """
        # 将字符串编码为字节（添加换行符）
        data = (line + "\n").encode()
        
        # 根据传输类型选择写入方式
        if self._transport == "stdio":
            # stdio 模式：写入子进程的 stdin
            w = self._proc.stdin if self._proc else None
            if w is None:
                raise McpServerUnavailableError("stdio writer unavailable")
            w.write(data)
            # 刷新缓冲区（确保数据已写入）
            await w.drain()
        elif self._transport == "tcp":
            # TCP 模式：写入 TCP 连接的 writer
            w = getattr(self, "_tcp_writer", None)
            if w is None:
                raise McpServerUnavailableError("tcp writer unavailable")
            w.write(data)
            # 刷新缓冲区（确保数据已发送）
            await w.drain()

    async def _read_line(self) -> str:
        """
        从 MCP Server 读取一行 JSON

        【返回值】
        - str: 读取的行（已去除首尾空白）

        【执行流程】
        1. 检查读取流是否可用
        2. 循环读取行：
           - 设置 30 秒超时
           - 处理 LimitOverrunError（响应过大）
           - 如果读到空字节（EOF），抛出 McpServerUnavailableError
           - 解码并去除首尾空白
           - 如果行不为空，返回
           - 如果行为空，继续读取

        【异常处理】
        - McpServerUnavailableError: 读取流不可用、超时、连接断开、响应过大

        【设计要点】
        - 设置 30 秒超时，防止无限等待
        - 使用 asyncio.LimitOverrunError 处理过大响应
        - 跳过空行，仅 EOF（b""）才视为连接断开
        - 使用 decode(errors="replace") 处理编码错误

        【注意事项】
        - 必须在锁内调用此方法（由 _call 保证）
        - 超时时间可以根据实际情况调整
        """
        # 检查读取流是否可用
        if self._reader is None:
            raise McpServerUnavailableError("reader unavailable")
        
        # 循环读取行（跳过空行）
        while True:
            try:
                # 读取一行（30 秒超时）
                data = await asyncio.wait_for(self._reader.readline(), timeout=30.0)
            except TimeoutError:
                # 读取超时
                raise McpServerUnavailableError("MCP server read timeout")
            except asyncio.LimitOverrunError as exc:
                # 响应过大（超过流限制）
                raise McpServerUnavailableError(
                    f"MCP response too large (>{self._STREAM_LIMIT // 1024 // 1024}MB): {exc}"
                ) from exc
            
            # 如果读到空字节（EOF），视为连接断开
            if data == b"":
                raise McpServerUnavailableError("MCP server closed connection")
            
            # 解码并去除首尾空白
            line = data.decode(errors="replace").strip()
            
            # 如果行不为空，返回
            if line:
                return line