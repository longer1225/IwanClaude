"""
任务更新工具模块 - 更新任务状态或依赖关系

【学习要点】
1. 任务更新：通过 TaskManager.update() 更新任务状态或依赖关系
2. 状态管理：支持 pending、in_progress、completed 三种状态
3. 依赖管理：支持添加和移除阻塞依赖
4. JSON 序列化：将更新后的任务对象转换为 JSON 字符串返回

【参数说明】
- task_id: int - 任务 ID（必填）
- status: str - 任务状态（可选），支持：pending、in_progress、completed
- add_blocked_by: list[int] - 添加到阻塞依赖的任务 ID 列表（可选）
- remove_blocked_by: list[int] - 从阻塞依赖中移除的任务 ID 列表（可选）

【状态转换说明】
- pending: 待处理状态，任务尚未开始
- in_progress: 进行中状态，正在处理任务
- completed: 已完成状态，任务已完成，会自动从其他任务的 blocked_by 中移除

【设计特点】
- 构造函数注入：通过 __init__ 注入 TaskManager 实例
- 手动参数解析：没有使用 Pydantic 模型验证参数
- JSON 返回：返回更新后的任务完整信息（JSON 格式）

【依赖关系】
- 依赖 iwan_claude.core.task.manager.TaskManager
- 依赖 iwan_claude.core.task.model.TaskStatus
- 依赖 iwan_claude.core.tools.base.BaseTool, ToolResult
"""
from __future__ import annotations

import json

from iwan_claude.core.task.manager import TaskManager
from iwan_claude.core.task.model import TaskStatus
from iwan_claude.core.tools.base import BaseTool, ToolResult


class TaskUpdateTool(BaseTool):
    """
    任务更新工具 - 更新任务状态或依赖关系

    【学习要点】
    1. 任务更新：调用 TaskManager.update() 更新任务状态或依赖关系
    2. 参数解析：手动解析 task_id、status、add_blocked_by、remove_blocked_by 参数
    3. 状态管理：支持 pending、in_progress、completed 三种状态
    4. 依赖管理：支持添加和移除阻塞依赖
    5. JSON 序列化：将更新后的任务对象转换为 JSON 字符串

    【使用示例】
    ```python
    from iwan_claude.core.task.manager import TaskManager
    
    task_manager = TaskManager()
    tool = TaskUpdateTool(task_manager)
    
    # 更新任务状态为进行中
    result = await tool.invoke({"task_id": 1, "status": "in_progress"})
    
    # 更新任务状态为已完成
    result = await tool.invoke({"task_id": 1, "status": "completed"})
    
    # 添加阻塞依赖
    result = await tool.invoke({"task_id": 2, "add_blocked_by": [1]})
    
    # 移除阻塞依赖
    result = await tool.invoke({"task_id": 2, "remove_blocked_by": [1]})
    ```

    【执行流程】
    1. 解析 task_id 参数并转换为整数
    2. 解析 status 参数（可选）
    3. 解析 add_blocked_by 参数（可选）并转换为整数列表
    4. 解析 remove_blocked_by 参数（可选）并转换为整数列表
    5. 调用 TaskManager.update() 更新任务
    6. 将更新后的任务对象转换为 JSON 字符串
    7. 返回结果

    【注意事项】
    - task_id 必须是正整数
    - status 必须是枚举值之一：pending、in_progress、completed
    - 任务完成后会自动从其他任务的 blocked_by 中移除
    - 无效的任务 ID 或状态会引发 ValueError
    """
    name = "task_update"
    description = (
        "Update a task's status or dependency list. "
        "Set status to 'in_progress' when starting work on a task, "
        "'completed' when finished (automatically clears it from other tasks' blocked_by). "
        "Returns the updated task as JSON."
    )
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
        "required": ["task_id"],
    }

    def __init__(self, task_manager: TaskManager) -> None:
        """
        初始化任务更新工具

        【参数说明】
        - task_manager: TaskManager - 任务管理器实例

        【设计说明】
        使用构造函数注入 TaskManager，实现依赖注入
        这样可以在测试时替换为 Mock 对象
        """
        # 持有 TaskManager 实例，供 invoke 调用
        self._manager = task_manager

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行任务更新操作

        【参数说明】
        - params: dict - 工具调用参数，包含：
          - task_id: int - 任务 ID（必填）
          - status: str - 任务状态（可选），支持：pending、in_progress、completed
          - add_blocked_by: list[int] - 添加到阻塞依赖的任务 ID 列表（可选）
          - remove_blocked_by: list[int] - 从阻塞依赖中移除的任务 ID 列表（可选）

        【返回值】
        - ToolResult: 包含更新后任务信息的 JSON 字符串，或错误信息

        【参数解析逻辑】
        - task_id: 必须存在，转换为字符串后再转换为整数
        - status: 可选，直接传递给 TaskManager
        - add_blocked_by: 可选，默认为空列表，需要将每个元素转换为整数
        - remove_blocked_by: 可选，默认为空列表，需要将每个元素转换为整数
        """
        # 1. 解析必填参数 task_id
        task_id = int(str(params["task_id"]))

        # 2. 解析可选参数 status
        status: TaskStatus | None = params.get("status")  # type: ignore[assignment]

        # 3. 解析可选参数 add_blocked_by（默认为空列表）
        # 需要将每个元素转换为整数
        raw_add: list[object] = list(params.get("add_blocked_by") or [])  # type: ignore[call-overload]
        add_blocked = [int(str(x)) for x in raw_add]

        # 4. 解析可选参数 remove_blocked_by（默认为空列表）
        # 需要将每个元素转换为整数
        raw_rem: list[object] = list(params.get("remove_blocked_by") or [])  # type: ignore[call-overload]
        remove_blocked = [int(str(x)) for x in raw_rem]

        try:
            # 5. 调用任务管理器更新任务
            # 如果列表为空，传递 None 而不是空列表
            task = self._manager.update(
                task_id,
                status=status,
                add_blocked_by=add_blocked or None,
                remove_blocked_by=remove_blocked or None,
            )

            # 6. 将更新后的任务对象转换为 JSON 字符串并返回
            return ToolResult(content=json.dumps(task.to_dict(), ensure_ascii=False))
        except ValueError as exc:
            # 7. 处理更新失败的情况（如无效的任务 ID 或状态）
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
