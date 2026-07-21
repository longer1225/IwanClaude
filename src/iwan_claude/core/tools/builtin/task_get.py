"""
任务查询工具模块 - 根据 ID 获取任务详情

【学习要点】
1. 任务查询：通过 TaskManager.get() 根据 ID 查询任务
2. ID 转换：将参数转换为整数类型
3. JSON 序列化：将任务对象转换为 JSON 字符串返回
4. 错误处理：任务不存在时返回错误

【参数说明】
- task_id: int - 任务 ID（必填）

【返回值】
- JSON 字符串：包含任务的完整信息（ID、标题、描述、状态、创建时间等）
- 错误信息：任务不存在时返回错误消息

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


class TaskGetTool(BaseTool):
    """
    任务查询工具 - 根据 ID 获取任务详情

    【学习要点】
    1. 任务查询：调用 TaskManager.get() 根据 ID 查询任务
    2. 参数解析：手动解析 task_id 参数并转换为整数
    3. JSON 序列化：将任务对象转换为 JSON 字符串
    4. 错误处理：任务不存在时返回错误

    【使用示例】
    ```python
    from iwan_claude.core.task.manager import TaskManager
    
    task_manager = TaskManager()
    tool = TaskGetTool(task_manager)
    
    # 查询任务详情
    result = await tool.invoke({"task_id": 1})
    
    # 返回示例：
    # {"id": 1, "subject": "完成代码审查", "description": "", "status": "pending", ...}
    ```

    【执行流程】
    1. 解析 task_id 参数并转换为整数
    2. 调用 TaskManager.get() 查询任务
    3. 将任务对象转换为 JSON 字符串
    4. 返回结果

    【注意事项】
    - task_id 必须是正整数
    - 任务不存在时会引发 ValueError
    - 返回的 JSON 包含任务的完整信息（ID、标题、描述、状态、创建时间等）
    """
    name = "task_get"
    description = "Get full details of a task by its integer ID. Returns the task as JSON."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to retrieve.",
            },
        },
        "required": ["task_id"],
    }

    def __init__(self, task_manager: TaskManager) -> None:
        """
        初始化任务查询工具

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
        执行任务查询操作

        【参数说明】
        - params: dict - 工具调用参数，包含：
          - task_id: int - 任务 ID（必填）

        【返回值】
        - ToolResult: 包含任务信息的 JSON 字符串，或错误信息

        【参数解析逻辑】
        - task_id: 必须存在，转换为字符串后再转换为整数
        """
        # 1. 解析 task_id 参数并转换为整数
        task_id = int(str(params["task_id"]))

        try:
            # 2. 调用任务管理器查询任务
            task = self._manager.get(task_id)

            # 3. 将任务对象转换为 JSON 字符串并返回
            return ToolResult(content=json.dumps(task.to_dict(), ensure_ascii=False))
        except ValueError as exc:
            # 4. 处理查询失败的情况（如任务不存在）
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
