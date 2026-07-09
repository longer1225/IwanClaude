# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入 BaseTool 基类，用于类型注解和类型检查
from kama_claude.core.tools.base import BaseTool


# ToolRegistry 类：工具注册表，用于管理所有可用的工具
# 什么是注册表？它就像一个"工具箱"，把所有工具放在一起，方便查找和使用
# 提供三个核心功能：注册工具、查找工具、生成工具 Schema
class ToolRegistry:
    # 初始化方法：创建一个空的工具字典
    # 函数作用：创建 ToolRegistry 实例，初始化工具存储
    # 返回值：None
    def __init__(self) -> None:
        # 使用字典存储工具，键是工具名称（如 "read_file"），值是工具实例（如 ReadFileTool）
        # 为什么用字典？因为通过名称查找工具的时间复杂度是 O(1)，非常快
        self._tools: dict[str, BaseTool] = {}

    # 注册工具；同名覆盖
    # 函数作用：将工具添加到注册表中
    # 传参：tool - BaseTool 子类的实例（如 ReadFileTool()）
    # 返回值：None
    # 注意：如果注册同名工具，会覆盖之前的注册（这是有意设计的，允许动态替换工具）
    def register(self, tool: BaseTool) -> None:
        # 以工具的 name 属性作为键，将工具实例存入字典
        self._tools[tool.name] = tool

    # 按名称查找工具，不存在返回 None
    # 函数作用：根据工具名称查找工具实例
    # 传参：name - 工具名称（如 "read_file"）
    # 返回值：BaseTool | None - 找到返回工具实例，找不到返回 None
    def get(self, name: str) -> BaseTool | None:
        # 使用字典的 get 方法安全查找，不存在返回 None
        return self._tools.get(name)

    # 返回所有工具的 Anthropic 格式 schema 列表
    # 函数作用：生成所有工具的 Schema 列表，用于发送给 LLM
    # 返回值：list[dict[str, object]] - 工具 Schema 列表，每个元素是一个字典
    # 什么是 Anthropic 格式？就是 Anthropic API 要求的工具描述格式
    # LLM 需要这个 Schema 来了解有哪些工具可用，以及如何调用
    def tool_schemas(self) -> list[dict[str, object]]:
        # 使用列表推导式遍历所有工具，生成 Schema 列表
        return [
            {
                "name": tool.name,           # 工具名称，如 "read_file"
                "description": tool.description,  # 工具描述，如 "读取文件内容"
                "input_schema": tool.input_schema,  # 输入参数的 JSON Schema
            }
            for tool in self._tools.values()  # 遍历字典的值（所有工具实例）
        ]
