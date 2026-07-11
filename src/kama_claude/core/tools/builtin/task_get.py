# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 json：用于序列化任务为 JSON 字符串
import json

# 导入 TaskManager：任务管理器（负责 CRUD 和持久化）
from kama_claude.core.task.manager import TaskManager
# 导入工具基类：所有工具都继承自 BaseTool
from kama_claude.core.tools.base import BaseTool, ToolResult


# TaskGetTool：获取单个任务详情的工具
# 什么是获取任务详情？就是获取某个任务的完整信息（包括描述、状态、依赖等）
# 为什么需要这个工具？因为 task_list 只显示摘要，LLM 需要查看完整信息时使用这个工具
class TaskGetTool(BaseTool):
    # 工具名称：供 LLM 调用时使用
    name = "task_get"
    # 工具描述：告诉 LLM 这个工具的用途和使用方式
    description = "Get full details of a task by its integer ID. Returns the task as JSON."
    # 输入参数 schema：定义 LLM 需要提供的参数
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to retrieve.",
            },
        },
        "required": ["task_id"],  # task_id 是必填参数
    }

    # 持有 TaskManager 实例，供 invoke 调用
    def __init__(self, task_manager: TaskManager) -> None:
        self._manager = task_manager

    # 获取任务详情并返回 JSON 字符串
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 获取任务 ID 参数（转换为整数）
        task_id = int(str(params["task_id"]))
        
        try:
            # 调用 TaskManager 获取任务
            task = self._manager.get(task_id)
            # 返回任务的 JSON 字符串
            return ToolResult(content=json.dumps(task.to_dict(), ensure_ascii=False))
        except ValueError as exc:
            # 如果任务不存在，返回错误
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
