"""
工具注册表 - 管理所有可用工具的注册和查询

【学习要点】
1. 注册表模式：使用字典存储工具，提供注册、查询、遍历等操作
2. 封装性：使用 _tools 私有变量，通过公共方法暴露操作接口
3. 适配器模式：to_langchain_tools 将自定义工具转换为 LangChain 兼容格式
4. 闭包技巧：_bridge 函数使用默认参数捕获循环变量，避免 late binding 问题

【核心职责】
- 工具注册：将工具实例添加到注册表
- 工具查找：根据名称获取工具实例
- Schema 生成：为 LLM 提供所有工具的 JSON Schema 描述
- LangChain 适配：将自定义工具包装为 LangChain 的 StructuredTool
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from iwan_claude.core.tools.base import BaseTool
from iwan_claude.core.llm.types import ToolCallBlock


class ToolRegistry:
    """
    工具注册表 - 集中管理所有工具的注册和查询

    【设计思路】
    使用单例模式的思想（虽然这里不是严格单例），将所有工具集中管理，
    方便在 Agent 执行过程中动态查找和调用工具。

    【核心数据结构】
    - _tools: dict[str, BaseTool] - 工具名称到工具实例的映射
    """
    def __init__(self) -> None:
        """
        初始化注册表

        创建一个空的工具字典，用于存储工具实例。
        """
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """
        注册工具

        【参数说明】
        - tool: BaseTool - 要注册的工具实例

        【注意事项】
        - 工具名称必须唯一，重复注册会覆盖之前的工具
        - 注册后工具可通过 get() 方法获取

        【示例】
        ```python
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        ```
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """
        根据名称获取工具

        【参数说明】
        - name: str - 工具名称

        【返回值】
        - BaseTool | None: 工具实例，如果不存在则返回 None

        【示例】
        ```python
        tool = registry.get("read_file")
        if tool:
            result = await tool.invoke({"path": "/file.txt"})
        ```
        """
        return self._tools.get(name)

    def tool_schemas(self) -> list[dict[str, object]]:
        """
        生成所有工具的 JSON Schema 列表

        【返回值】
        - list[dict]: 每个元素包含 name、description、input_schema 三个字段

        【用途】
        将工具 Schema 传递给 LLM，让 LLM 知道可以调用哪些工具以及如何调用

        【示例输出】
        ```python
        [
            {
                "name": "read_file",
                "description": "读取文件内容",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}
            }
        ]
        ```
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def to_langchain_tools(self) -> list[StructuredTool]:
        """
        将注册表中的工具转换为 LangChain 兼容的工具列表

        【学习要点】
        1. 适配器模式：将自定义工具接口适配为 LangChain 的工具接口
        2. 闭包捕获：使用 _t=tool, _name=name 默认参数避免循环变量引用问题
        3. 桥接函数：_bridge 函数作为适配器，将 LangChain 的调用格式转换为内部格式

        【返回值】
        - list[StructuredTool]: LangChain 结构化工具列表

        【用途】
        当使用 LangGraph 执行引擎时，需要将工具转换为 LangChain 格式

        【实现细节】
        - 为每个工具创建一个桥接函数 _bridge
        - 生成临时的 ToolCallBlock，模拟工具调用请求
        - 调用 invoke_tool 执行工具，返回结果内容
        """
        from iwan_claude.core.tools.invocation import invoke_tool
        import uuid

        tools: list[StructuredTool] = []
        for name, tool in self._tools.items():

            async def _bridge(params: dict[str, Any], *, _t=tool, _name=name) -> str:
                """
                桥接函数 - 将 LangChain 调用格式转换为内部调用格式

                【参数说明】
                - params: dict - LangChain 传入的参数
                - _t: BaseTool - 闭包捕获的工具实例（使用默认参数避免 late binding）
                - _name: str - 闭包捕获的工具名称

                【返回值】
                - str: 工具执行结果的内容
                """
                # 创建临时的 ToolCallBlock，模拟工具调用请求
                fake_tc = ToolCallBlock(
                    id=f"lg_{uuid.uuid4().hex[:8]}",  # 生成唯一的调用 ID
                    name=_name,
                    input=params,
                )
                # 调用内部的 invoke_tool 执行工具
                result = await invoke_tool(
                    self,
                    fake_tc,
                    None,  # bus 为 None，不发布事件
                    "",    # run_id 为空
                    permission_manager=None,  # 不检查权限
                    session_id="",            # session_id 为空
                )
                return result.content

            # 使用 LangChain 的 StructuredTool 包装桥接函数
            fn = StructuredTool.from_function(
                func=_bridge,
                name=name,
                description=tool.description,
            )
            tools.append(fn)
        return tools
