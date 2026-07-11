# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 json：用于序列化任务为 JSON 字符串
import json

# 导入 TaskManager：任务管理器（负责 CRUD 和持久化）
from kama_claude.core.task.manager import TaskManager
# 导入工具基类：所有工具都继承自 BaseTool
from kama_claude.core.tools.base import BaseTool, ToolResult


# TaskCreateTool：创建任务的工具
# 什么是创建任务？就是把一个工作单元记录下来，分配一个唯一 ID
# 为什么需要这个工具？因为 LLM 需要把复杂目标拆解成小任务来追踪进度
class TaskCreateTool(BaseTool):
    # 工具名称：供 LLM 调用时使用
    name = "task_create"
    # 工具描述：告诉 LLM 这个工具的用途和使用方式
    description = (
        "Create a new task to track a unit of work. "
        "Use this to break down a complex goal into smaller, trackable steps. "
        "Returns the created task as JSON."
    )
    # 输入参数 schema：定义 LLM 需要提供的参数
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Short title for the task.",
            },
            "description": {
                "type": "string",
                "description": "Optional longer description of what needs to be done.",
            },
            "blocked_by": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "IDs of tasks that must be completed before this one.",
            },
        },
        "required": ["subject"],  # subject 是必填参数
    }

    # 持有 TaskManager 实例，供 invoke 调用
    # 什么是依赖注入？就是把依赖从外部传入，而不是自己创建
    # 为什么需要？因为多个任务工具需要共享同一个 TaskManager 实例
    def __init__(self, task_manager: TaskManager) -> None:
        self._manager = task_manager

    # 创建任务并返回 JSON 字符串
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 获取主题参数（必填）
        subject = str(params["subject"])
        # 获取描述参数（可选，默认为空）
        description = str(params.get("description") or "")
        # 获取依赖参数（可选，默认为空列表）
        raw_blocked: list[object] = list(params.get("blocked_by") or [])
        # 转换为整数列表
        blocked_by = [int(str(x)) for x in raw_blocked]
        
        try:
            # 调用 TaskManager 创建任务
            task = self._manager.create(subject, description, blocked_by)
            # 返回任务的 JSON 字符串
            return ToolResult(content=json.dumps(task.to_dict(), ensure_ascii=False))
        except ValueError as exc:
            # 如果创建失败（如依赖的任务不存在），返回错误
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
