"""
MCP 工具包装模块 - 将 MCP 工具适配为 BaseTool

【学习要点】
1. 适配器模式：将 MCP 工具适配为 BaseTool 接口
2. 命名冲突解决：使用 {server_name}__{tool_name} 格式防止命名冲突
3. 错误处理：统一返回 ToolResult，区分服务器错误和工具错误
4. 透明调用：让 agent 调用 MCP 工具和调用内置工具完全一致

【核心类】
- McpTool: MCP 工具包装器，继承自 BaseTool

【设计目的】
将外部 MCP Server 提供的工具集成到 ToolRegistry，使 agent 可以透明地调用这些工具，
无需关心工具是内置的还是来自外部服务器。

【工具命名规范】
工具名格式：{server_name}__{tool_name}
例如："database__query", "api__fetch"

【错误处理策略】
- McpServerUnavailableError: 服务器不可用（连接问题）
- McpToolError: 工具执行失败（业务问题）
- Exception: 其他未预期的错误

所有错误统一返回 ToolResult，is_error=True，方便上层处理。
"""
from __future__ import annotations

from typing import Any

from iwan_claude.core.mcp.client import McpClient, McpServerUnavailableError, McpToolDef, McpToolError
from iwan_claude.core.tools.base import BaseTool, ToolResult


class McpTool(BaseTool):
    """
    MCP 工具包装器 - 将 MCP 工具适配为 BaseTool

    【学习要点】
    1. 适配器模式：实现 BaseTool 接口，适配 MCP 工具
    2. 命名冲突解决：使用 {server_name}__{tool_name} 格式
    3. 错误处理：统一返回 ToolResult，区分不同类型错误
    4. 透明调用：让 agent 无需关心工具来源

    【核心属性】
    - name: str - 工具名称（格式：{server_name}__{tool_name}）
    - description: str - 工具描述
    - input_schema: dict[str, Any] - 输入参数 Schema
    - _client: McpClient - MCP 客户端
    - _server_name: str - MCP Server 名称
    - _tool_def: McpToolDef - 工具定义

    【设计目的】
    将外部 MCP Server 提供的工具集成到 ToolRegistry，使 agent 可以透明地调用这些工具。

    【与 BaseTool 的区别】
    - BaseTool 使用 params_model（Pydantic 模型）进行参数校验
    - McpTool 使用 input_schema（JSON Schema）进行参数校验
    """
    # 不使用 Pydantic 模型进行参数校验，input_schema 来自 MCP tool_def
    params_model = None

    def __init__(self, client: McpClient, server_name: str, tool_def: McpToolDef) -> None:
        """
        初始化 MCP 工具包装器

        【参数说明】
        - client: McpClient - MCP 客户端（已连接到 Server）
        - server_name: str - MCP Server 名称（用于工具名前缀）
        - tool_def: McpToolDef - MCP 工具定义（包含 name、description、input_schema）

        【工具命名规范】
        工具名格式：{server_name}__{tool_name}
        例如："database__query", "api__fetch"

        【设计目的】
        使用 server_name 作为前缀，防止不同 MCP Server 的工具名冲突。

        【示例】
        ```python
        client = McpClient()
        await client.connect_stdio("python", ["-m", "my_mcp_server"])
        tool_def = McpToolDef(
            name="query",
            description="查询数据库",
            input_schema={"type": "object", "properties": {"sql": {"type": "string"}}}
        )
        mcp_tool = McpTool(client, "database", tool_def)
        # mcp_tool.name = "database__query"
        ```
        """
        # MCP 客户端（已连接到 Server）
        self._client = client
        # MCP Server 名称（用于工具名前缀）
        self._server_name = server_name
        # MCP 工具定义
        self._tool_def = tool_def
        # 工具名格式：{server_name}__{tool_name}（防止命名冲突）
        self.name = f"{server_name}__{tool_def.name}"
        # 工具描述（如果没有，使用默认描述）
        self.description = tool_def.description or f"MCP tool from {server_name}"
        # 输入参数 Schema（如果没有，使用空对象 Schema）
        self.input_schema: dict[str, Any] = (
            tool_def.input_schema or {"type": "object", "properties": {}}
        )

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        调用 MCP Server 上的工具

        【参数说明】
        - params: dict[str, object] - 用户传入的参数

        【返回值】
        - ToolResult: 工具调用结果
          - 成功：content 为工具输出
          - 失败：is_error=True，content 为错误信息，error_type 为错误类型

        【执行流程】
        1. 通过 MCP Client 调用远程工具
        2. 如果成功，返回 ToolResult(content=content)
        3. 如果失败，根据错误类型返回相应的 ToolResult

        【错误类型】
        - McpServerUnavailableError: 服务器不可用（连接失败、超时、断开）
        - McpToolError: 工具执行失败（连接正常，但工具调用失败）
        - Exception: 其他未预期的错误

        【错误处理策略】
        所有错误统一返回 ToolResult，is_error=True，方便上层处理。

        【示例】
        ```python
        result = await mcp_tool.invoke({"sql": "SELECT * FROM users"})
        if result.is_error:
            print(f"工具调用失败: {result.content}")
        else:
            print(f"工具调用成功: {result.content}")
        ```
        """
        try:
            # 通过 MCP Client 调用远程工具
            content = await self._client.call_tool(self._tool_def.name, dict(params))
            # 成功：返回 ToolResult
            return ToolResult(content=content)
        except McpServerUnavailableError as exc:
            # MCP Server 不可用（连接失败、超时、断开）
            return ToolResult(
                content=f"mcp server '{self._server_name}' unavailable: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        except McpToolError as exc:
            # MCP Server 返回应用层错误（连接正常，但工具调用失败）
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