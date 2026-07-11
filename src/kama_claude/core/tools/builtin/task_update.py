# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 json：用于序列化任务为 JSON 字符串
import json

# 导入 TaskManager：任务管理器（负责 CRUD 和持久化）
from kama_claude.core.task.manager import TaskManager
# 导入 TaskStatus：任务状态类型
from kama_claude.core.task.model import TaskStatus
# 导入工具基类：所有工具都继承自 BaseTool
from kama_claude.core.tools.base import BaseTool, ToolResult


# TaskUpdateTool：更新任务的工具
# 什么是更新任务？就是修改任务的状态或依赖关系
# 为什么需要这个工具？因为 LLM 需要标记任务开始、完成，或者调整依赖
class TaskUpdateTool(BaseTool):
    # 工具名称：供 LLM 调用时使用
    name = "task_update"
    # 工具描述：告诉 LLM 这个工具的用途和使用方式
    description = (
        "Update a task's status or dependency list. "
        "Set status to 'in_progress' when starting work on a task, "
        "'completed' when finished (automatically clears it from other tasks' blocked_by). "
        "Returns the updated task as JSON."
    )
    # 输入参数 schema：定义 LLM 需要提供的参数
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to update.",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "New status for the task.",
            },
            "add_blocked_by": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Task IDs to add to blocked_by.",
            },
            "remove_blocked_by": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Task IDs to remove from blocked_by.",
            },
        },
        "required": ["task_id"],  # task_id 是必填参数
    }

    # 持有 TaskManager 实例，供 invoke 调用
    def __init__(self, task_manager: TaskManager) -> None:
        self._manager = task_manager

    # 更新任务并返回 JSON 字符串
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 获取任务 ID 参数（转换为整数）
        task_id = int(str(params["task_id"]))
        # 获取状态参数（可选）
        status: TaskStatus | None = params.get("status")
        # 获取要添加的依赖（可选）
        raw_add: list[object] = list(params.get("add_blocked_by") or [])
        # 获取要移除的依赖（可选）
        raw_rem: list[object] = list(params.get("remove_blocked_by") or [])
        # 转换为整数列表
        add_blocked = [int(str(x)) for x in raw_add]
        remove_blocked = [int(str(x)) for x in raw_rem]
        
        try:
            # 调用 TaskManager 更新任务
            task = self._manager.update(
                task_id,
                status=status,
                add_blocked_by=add_blocked or None,
                remove_blocked_by=remove_blocked or None,
            )
            # 返回更新后的任务 JSON 字符串
            return ToolResult(content=json.dumps(task.to_dict(), ensure_ascii=False))
        except ValueError as exc:
            # 如果更新失败（如状态无效），返回错误
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
