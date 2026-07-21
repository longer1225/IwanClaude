"""
任务创建工具模块 - 创建新任务

【学习要点】
1. 任务管理：通过 TaskManager 管理任务的创建、查询和状态跟踪
2. 依赖关系：支持任务之间的阻塞关系（blocked_by）
3. JSON 序列化：将任务对象转换为 JSON 字符串返回
4. 参数处理：手动解析参数（未使用 Pydantic）

【任务字段说明】
- subject: str - 任务标题（必填）
- description: str - 任务描述（可选）
- blocked_by: list[int] - 依赖的任务 ID 列表（可选）

【设计特点】
- 构造函数注入：通过 __init__ 注入 TaskManager 实例
- 手动参数解析：没有使用 Pydantic 模型验证参数
- JSON 返回：返回任务的完整信息（JSON 格式）

【依赖关系】
- 依赖 iwan_claude.core.task.manager.TaskManager
- 依赖 iwan_claude.core.tools.base.BaseTool, ToolResult
"""
from __future__ import annotations

import json

from iwan_claude.core.task.manager import TaskManager
from iwan_claude.core.tools.base import BaseTool, ToolResult


class TaskCreateTool(BaseTool):
    """
    任务创建工具 - 创建新任务

    【学习要点】
    1. 任务创建：调用 TaskManager.create() 创建任务
    2. 参数解析：手动解析 subject、description、blocked_by 参数
    3. 依赖处理：支持任务之间的阻塞关系
    4. JSON 序列化：将任务对象转换为 JSON 字符串

    【使用示例】
    ```python
    from iwan_claude.core.task.manager import TaskManager
    
    task_manager = TaskManager()
    tool = TaskCreateTool(task_manager)
    
    # 创建简单任务
    result = await tool.invoke({"subject": "完成代码审查"})
    
    # 创建带描述的任务
    result = await tool.invoke({
        "subject": "修复 bug",
        "description": "修复用户登录时的认证问题"
    })
    
    # 创建有依赖关系的任务
    result = await tool.invoke({
        "subject": "编写测试",
        "blocked_by": [1, 2]
    })
    ```

    【执行流程】
    1. 解析 subject（必填）
    2. 解析 description（可选，默认为空）
    3. 解析 blocked_by（可选，默认为空列表）
    4. 调用 TaskManager.create() 创建任务
    5. 将任务对象转换为 JSON 字符串
    6. 返回结果

    【注意事项】
    - blocked_by 中的任务 ID 必须是已存在的任务
    - 无效的任务 ID 会引发 ValueError
    - 返回的 JSON 包含任务的完整信息（ID、标题、描述、状态等）
    """
    name = "task_create"
    description = (
        "Create a new task to track a unit of work. "
        "Use this to break down a complex goal into smaller, trackable steps. "
        "Returns the created task as JSON."
    )
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
        "required": ["subject"],
    }

    def __init__(self, task_manager: TaskManager) -> None:
        """
        初始化任务创建工具

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
        执行任务创建操作

        【参数说明】
        - params: dict - 工具调用参数，包含：
          - subject: str - 任务标题（必填）
          - description: str - 任务描述（可选）
          - blocked_by: list[int] - 依赖的任务 ID 列表（可选）

        【返回值】
        - ToolResult: 包含任务信息的 JSON 字符串，或错误信息

        【参数解析逻辑】
        - subject: 必须存在，转换为字符串
        - description: 可选，默认为空字符串
        - blocked_by: 可选，默认为空列表，需要将每个元素转换为整数
        """
        # 1. 解析必填参数 subject
        subject = str(params["subject"])

        # 2. 解析可选参数 description（默认为空）
        description = str(params.get("description") or "")

        # 3. 解析可选参数 blocked_by（默认为空列表）
        # 需要将每个元素转换为整数
        raw_blocked: list[object] = list(params.get("blocked_by") or [])  # type: ignore[call-overload]
        blocked_by = [int(str(x)) for x in raw_blocked]

        try:
            # 4. 调用任务管理器创建任务
            task = self._manager.create(subject, description, blocked_by)

            # 5. 将任务对象转换为 JSON 字符串并返回
            return ToolResult(content=json.dumps(task.to_dict(), ensure_ascii=False))
        except ValueError as exc:
            # 6. 处理创建失败的情况（如无效的依赖任务 ID）
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
