from __future__ import annotations

from typing import Any

from iwan_claude.core.mcp.client import McpClient, McpServerUnavailableError, McpToolDef, McpToolError
from iwan_claude.core.tools.base import BaseTool, ToolResult


# 【s7 核心】将 MCP 工具包装为 BaseTool，使 ToolRegistry 可透明调用
# 设计目的：让 agent 调用 MCP 工具和调用内置工具完全一致
# 工具名格式：{server_name}__{tool_name}（防止不同 MCP server 的工具名冲突）
class McpTool(BaseTool):
    params_model = None  # input_schema 来自 MCP tool_def，不使用 pydantic model

    # 初始化 MCP 工具包装器，工具名以 server_name__ 为前缀防止命名冲突
    # client: MCP 客户端（已连接到 server）
    # server_name: MCP server 名称（用于工具名前缀）
    # tool_def: MCP 工具定义（包含 name、description、input_schema）
    def __init__(self, client: McpClient, server_name: str, tool_def: McpToolDef) -> None:
        self._client = client
        self._server_name = server_name
        self._tool_def = tool_def
        # 工具名格式：{server_name}__{tool_name}
        # 例如："database__query", "api__fetch"
        self.name = f"{server_name}__{tool_def.name}"
        self.description = tool_def.description or f"MCP tool from {server_name}"
        self.input_schema: dict[str, Any] = (
            tool_def.input_schema or {"type": "object", "properties": {}}
        )

    # 调用 MCP server 上的工具，连接不可用或工具执行失败时返回 is_error=True
    # 参数：params（用户传入的参数，转为 dict）
    # 返回：ToolResult（成功时 content 为工具输出，失败时 is_error=True）
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        try:
            # 通过 MCP client 调用远程工具
            content = await self._client.call_tool(self._tool_def.name, dict(params))
            return ToolResult(content=content)
        except McpServerUnavailableError as exc:
            # MCP server 不可用（连接失败、超时、断开）
            return ToolResult(
                content=f"mcp server '{self._server_name}' unavailable: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        except McpToolError as exc:
            # MCP server 返回应用层错误（连接正常，但工具调用失败）
            return ToolResult(
                content=f"mcp tool '{self.name}' error: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        except Exception as exc:
            # 其他未预期的错误
            return ToolResult(
                content=f"mcp tool '{self.name}' unexpected error: {exc}",
                is_error=True,
                error_type="runtime_error",
            )