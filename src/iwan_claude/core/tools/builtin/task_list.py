"""
任务列表工具模块 - 列出所有任务

【学习要点】
1. 任务列表：通过 TaskManager.format_list() 获取格式化的任务列表
2. 无参数工具：不需要输入参数
3. 格式化输出：返回可读的任务列表摘要
4. 状态展示：显示任务的当前状态和阻塞依赖

【返回值】
- 格式化的任务列表字符串，包含任务 ID、标题、状态和依赖关系

【设计特点】
- 构造函数注入：通过 __init__ 注入 TaskManager 实例
- 无参数工具：不需要输入参数
- 格式化输出：委托给 TaskManager.format_list() 处理

【依赖关系】
- 依赖 iwan_claude.core.task.manager.TaskManager
- 依赖 iwan_claude.core.tools.base.BaseTool, ToolResult
"""
from __future__ import annotations

from iwan_claude.core.task.manager import TaskManager
from iwan_claude.core.tools.base import BaseTool, ToolResult


class TaskListTool(BaseTool):
    """
    任务列表工具 - 列出所有任务

    【学习要点】
    1. 任务列表：调用 TaskManager.format_list() 获取格式化的任务列表
    2. 无参数工具：不需要输入参数
    3. 格式化输出：返回可读的任务列表摘要
    4. 状态展示：显示任务的当前状态和阻塞依赖

    【使用示例】
    ```python
    from iwan_claude.core.task.manager import TaskManager
    
    task_manager = TaskManager()
    tool = TaskListTool(task_manager)
    
    # 获取任务列表
    result = await tool.invoke({})
    
    # 返回示例：
    # ID | Subject | Status | Blocked By
    # ---|---------|--------|-----------
    # 1  | 完成代码审查 | pending | 
    # 2  | 编写测试 | pending | 1
    # 3  | 修复 bug | completed | 
    ```

    【执行流程】
    1. 调用 TaskManager.format_list() 获取格式化的任务列表
    2. 返回结果

    【注意事项】
    - 此工具不需要输入参数
    - 返回所有任务的状态摘要
    - 显示任务之间的阻塞依赖关系
    """
    name = "task_list"
    description = (
        "List all tasks with their current status and blocking dependencies. "
        "Use this to check what work remains and what can be started next."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, task_manager: TaskManager) -> None:
        """
        初始化任务列表工具

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
        执行任务列表查询操作

        【参数说明】
        - params: dict - 工具调用参数（无必填参数）

        【返回值】
        - ToolResult: 包含格式化任务列表的字符串
        """
        # 1. 调用任务管理器获取格式化的任务列表
        # 2. 返回结果
        return ToolResult(content=self._manager.format_list())
