from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from iwan_claude.core.mcp.client import (
    McpClient,
    McpServerUnavailableError,
    McpToolDef,
    McpToolError,
)
from iwan_claude.core.mcp.tool import McpTool


def _make_tool(
    tool_name: str = "read_file",
    server_name: str = "filesystem",
) -> tuple[McpTool, AsyncMock]:
    client = AsyncMock(spec=McpClient)
    tool_def = McpToolDef(
        name=tool_name,
        description=f"Read a file via {server_name}",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    tool = McpTool(client, server_name, tool_def)
    return tool, client


# 功能：McpTool.invoke 应调用 client.call_tool 并将返回值封装为 ToolResult
# 设计：mock client.call_tool 返回固定字符串，验证 ToolResult.content 一致
@pytest.mark.asyncio
async def test_invoke_calls_mcp_client() -> None:
    tool, client = _make_tool()
    client.call_tool = AsyncMock(return_value="file content here")
    result = await tool.invoke({"path": "/tmp/test.txt"})
    assert not result.is_error
    assert result.content == "file content here"
    client.call_tool.assert_called_once_with("read_file", {"path": "/tmp/test.txt"})


# 功能：工具全名格式必须是 mcp__{server}__{tool}（对齐 Claude Code 命名空间惯例）
# 设计：mcp__ 前缀不只是好看——权限引擎靠它表达 `mcp__*` 一刀切管控外部工具，测试锁死契约防回退
def test_tool_name_prefixed() -> None:
    tool, _ = _make_tool("read_file", "filesystem")
    assert tool.name == "mcp__filesystem__read_file"


# 功能：client 抛 McpServerUnavailableError 时应返回 is_error=True 且不重新抛出
# 设计：mock client.call_tool 抛该异常，断言 ToolResult.is_error=True 且消息含 server 名称
@pytest.mark.asyncio
async def test_unavailable_returns_error() -> None:
    tool, client = _make_tool()
    client.call_tool = AsyncMock(side_effect=McpServerUnavailableError("process died"))
    result = await tool.invoke({"path": "/tmp/x.txt"})
    assert result.is_error
    assert result.error_type == "server_offline"
    assert "filesystem" in result.content


# 功能：不可用异常含 timeout 字样时 error_type 应为 timeout 而非 server_offline
# 设计：超时语义是"结果未知、副作用可能已发生"，与"连接已死"对模型的重试决策完全不同，分型必须测
@pytest.mark.asyncio
async def test_timeout_error_type_split() -> None:
    tool, client = _make_tool()
    client.call_tool = AsyncMock(
        side_effect=McpServerUnavailableError("MCP server read timeout after 30s")
    )
    result = await tool.invoke({"path": "/tmp/x.txt"})
    assert result.is_error
    assert result.error_type == "timeout"


# 功能：client 抛 McpToolError（工具业务失败）时 error_type 应为 tool_error
# 设计：tool_error 暗示"改参数再试"是合理路径，与 server_offline（重试无用）区分开
@pytest.mark.asyncio
async def test_tool_error_type_split() -> None:
    tool, client = _make_tool()
    client.call_tool = AsyncMock(side_effect=McpToolError("query failed: no such table"))
    result = await tool.invoke({"path": "/tmp/x.txt"})
    assert result.is_error
    assert result.error_type == "tool_error"
    assert "no such table" in result.content


# 功能：client 抛其他异常时应返回 runtime_error 类型的 ToolResult
# 设计：mock client.call_tool 抛 RuntimeError，断言 ToolResult 被正确包装
@pytest.mark.asyncio
async def test_runtime_error_caught() -> None:
    tool, client = _make_tool()
    client.call_tool = AsyncMock(side_effect=RuntimeError("unexpected failure"))
    result = await tool.invoke({"path": "/tmp/y.txt"})
    assert result.is_error
    assert result.error_type == "runtime_error"
    assert "unexpected failure" in result.content


# 功能：input_schema 应直接使用 MCP tool_def 中的 schema，而非 pydantic model
# 设计：验证 params_model 为 None，input_schema 与 tool_def.input_schema 一致
def test_input_schema_from_tool_def() -> None:
    tool, _ = _make_tool()
    assert McpTool.params_model is None
    assert "path" in tool.input_schema.get("properties", {})
