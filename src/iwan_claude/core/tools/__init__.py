"""
工具模块入口 - 统一导出工具系统的核心组件

【学习要点】
1. 模块聚合模式：将分散在多个文件中的核心类统一在 __init__.py 中导出
2. __all__ 控制公开接口：明确列出可被外部导入的组件，隐藏内部实现细节
3. 工具系统四件套：
   - BaseTool：所有工具的抽象基类
   - ToolResult：工具执行结果的标准返回格式
   - ToolRegistry：工具注册表，管理所有可用工具
   - invoke_tool：工具调用的统一入口，包含权限检查和重试逻辑

【使用示例】
```python
from iwan_claude.core.tools import BaseTool, ToolRegistry, invoke_tool

# 创建注册表
registry = ToolRegistry()

# 注册工具
registry.register(MyTool())

# 调用工具
result = await invoke_tool(registry, tool_call, bus, run_id)
```
"""
from iwan_claude.core.tools.base import BaseTool, ToolResult
from iwan_claude.core.tools.invocation import invoke_tool
from iwan_claude.core.tools.registry import ToolRegistry

__all__ = ["BaseTool", "ToolResult", "ToolRegistry", "invoke_tool"]
