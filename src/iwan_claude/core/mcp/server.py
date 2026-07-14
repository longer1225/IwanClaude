from __future__ import annotations

import logging

from iwan_claude.core.config import McpServerConfig
from iwan_claude.core.mcp.client import McpClient
from iwan_claude.core.mcp.tool import McpTool
from iwan_claude.core.tools.registry import ToolRegistry

log = logging.getLogger(__name__)


# 【s7 核心】管理所有 MCP server 连接的生命周期：启动、工具发现、注册、关闭
# MCP（Model Context Protocol）是 Anthropic 定义的协议，允许外部工具服务器向 LLM 暴露工具
# 设计目的：将工具实现从 core daemon 中分离出去，实现插件化扩展
# 典型场景：
#   - 通过 stdio 连接本地脚本（如 Python 脚本）
#   - 通过 TCP 连接远程工具服务器（如数据库查询服务、API 代理）
class McpServerManager:
    def __init__(self) -> None:
        self._clients: dict[str, McpClient] = {}  # 存储所有 MCP client（key=server_name）
        self._tools: list[McpTool] = []            # 存储所有已发现的 MCP 工具

    # 依次连接每个 MCP server，发现工具后缓存供后续 registry 使用；失败时记录日志并跳过
    # servers: MCP server 配置列表（来自 config.yaml）
    async def start_all(self, servers: list[McpServerConfig]) -> None:
        for cfg in servers:
            try:
                # 建立连接（stdio 或 TCP）
                client = await self._connect(cfg)
                # 发现工具（调用 tools/list）
                tool_defs = await client.list_tools()
                # 将每个工具包装为 McpTool（使 ToolRegistry 可透明调用）
                for tool_def in tool_defs:
                    self._tools.append(McpTool(client, cfg.name, tool_def))
                # 缓存 client（用于后续工具调用）
                self._clients[cfg.name] = client
                log.info(
                    "mcp: server '%s' connected, %d tool(s) discovered",
                    cfg.name, len(tool_defs),
                )
            except Exception:
                # 单个 MCP server 启动失败不影响其他 server
                log.exception("mcp: server '%s' failed to start, skipping", cfg.name)

    # 将所有已发现的 MCP 工具注册到指定 registry
    # 这样 agent 就可以像调用内置工具一样调用 MCP 工具
    def register_tools(self, registry: ToolRegistry) -> None:
        for tool in self._tools:
            registry.register(tool)

    # 返回已发现的 MCP 工具列表（用于 runner 每次 run 时注入新 registry）
    def get_tools(self) -> list[McpTool]:
        return list(self._tools)

    # 关闭所有 MCP 连接并终止 stdio 子进程
    async def stop_all(self) -> None:
        for name, client in list(self._clients.items()):
            try:
                await client.close()
                log.info("mcp: server '%s' closed", name)
            except Exception:
                log.warning("mcp: error closing server '%s'", name)
        self._clients.clear()

    # 根据 transport 类型建立连接
    async def _connect(self, cfg: McpServerConfig) -> McpClient:
        client = McpClient()
        if cfg.transport == "stdio":
            # stdio 模式：启动子进程，通过管道通信
            if not cfg.command:
                raise ValueError(f"mcp server '{cfg.name}': stdio transport requires 'command'")
            await client.connect_stdio(cfg.command, cfg.args, cfg.env or None)
        elif cfg.transport == "tcp":
            # TCP 模式：连接远程服务器
            await client.connect_tcp(cfg.host, cfg.port)
        else:
            raise ValueError(f"mcp server '{cfg.name}': unknown transport '{cfg.transport}'")
        return client