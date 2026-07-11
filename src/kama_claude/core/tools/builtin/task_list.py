# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 TaskManager：任务管理器（负责 CRUD 和持久化）
from kama_claude.core.task.manager import TaskManager
# 导入工具基类：所有工具都继承自 BaseTool
from kama_claude.core.tools.base import BaseTool, ToolResult


# TaskListTool：列出所有任务的工具
# 什么是列出任务？就是显示所有任务的当前状态和依赖关系
# 为什么需要这个工具？因为 LLM 需要查看任务列表来决定下一步做什么
class TaskListTool(BaseTool):
    # 工具名称：供 LLM 调用时使用
    name = "task_list"
    # 工具描述：告诉 LLM 这个工具的用途和使用方式
    description = (
        "List all tasks with their current status and blocking dependencies. "
        "Use this to check what work remains and what can be started next."
    )
    # 输入参数 schema：定义 LLM 需要提供的参数
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},  # 没有参数
        "required": [],    # 没有必填参数
    }

    # 持有 TaskManager 实例，供 invoke 调用
    def __init__(self, task_manager: TaskManager) -> None:
        self._manager = task_manager

    # 返回格式化的任务列表摘要
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 调用 TaskManager 的 format_list() 方法
        # 这个方法返回专为 LLM 设计的紧凑格式
        return ToolResult(content=self._manager.format_list())
