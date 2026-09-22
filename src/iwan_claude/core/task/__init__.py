"""
任务管理模块

该模块提供了简单的任务管理功能，支持任务的创建、读取、更新和删除。

核心组件：
- Task: 任务数据类，存储任务信息
- TaskStatus: 任务状态类型，支持 pending/in_progress/completed
- TaskManager: 任务管理器，负责任务的 CRUD 操作

设计要点：
- 使用 JSON 文件存储任务，每个任务一个文件（task_{id}.json）
- 支持任务依赖关系（blocked_by）
- 任务完成时自动清理其他任务的依赖
- 任务 ID 自动递增

使用场景：
- SpawnAgentTool 创建子 Agent 时，子 Agent 可以使用任务管理工具
- 用户可以通过 task_create/task_update/task_list/task_get 工具管理任务
"""

from iwan_claude.core.task.manager import TaskManager
from iwan_claude.core.task.model import Task, TaskStatus

__all__ = ["Task", "TaskManager", "TaskStatus"]
