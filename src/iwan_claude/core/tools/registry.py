from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from iwan_claude.core.tools.base import BaseTool
from iwan_claude.core.llm.types import ToolCallBlock


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def tool_schemas(self) -> list[dict[str, object]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def to_langchain_tools(self) -> list[StructuredTool]:
        from iwan_claude.core.tools.invocation import invoke_tool
        import uuid

        tools: list[StructuredTool] = []
        for name, tool in self._tools.items():

            async def _bridge(params: dict[str, Any], *, _t=tool, _name=name) -> str:
                fake_tc = ToolCallBlock(
                    id=f"lg_{uuid.uuid4().hex[:8]}",
                    name=_name,
                    input=params,
                )
                result = await invoke_tool(
                    self,
                    fake_tc,
                    None,
                    "",
                    permission_manager=None,
                    session_id="",
                )
                return result.content

            fn = StructuredTool.from_function(
                func=_bridge,
                name=name,
                description=tool.description,
            )
            tools.append(fn)
        return tools
