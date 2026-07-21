"""
任务模型

该模块定义了任务的数据结构和序列化方法。

核心组件：
- TaskStatus: 任务状态类型，支持 pending/in_progress/completed
- Task: 任务数据类，存储任务信息

设计要点：
- 使用 dataclass 定义任务数据结构
- 支持序列化（to_dict）和反序列化（from_dict）
- 使用 Literal 类型约束任务状态
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# 任务状态类型
# pending: 待处理
# in_progress: 进行中
# completed: 已完成
TaskStatus = Literal["pending", "in_progress", "completed"]


@dataclass
class Task:
    """
    任务数据类

    存储任务的完整信息，包括 ID、主题、描述、状态、依赖关系和时间戳。

    属性：
        id: 任务唯一标识符，自动递增
        subject: 任务主题，简短描述
        description: 任务详细描述
        status: 任务状态，取值为 pending/in_progress/completed
        blocked_by: 依赖的任务 ID 列表，这些任务完成后才能执行当前任务
        created_at: 创建时间，ISO 8601 格式
        updated_at: 更新时间，ISO 8601 格式

    使用示例：
        >>> task = Task(
        ...     id=1,
        ...     subject="完成项目文档",
        ...     description="编写项目的 README 和 API 文档",
        ...     status="in_progress",
        ...     blocked_by=[2],
        ...     created_at="2026-07-21T10:00:00Z",
        ...     updated_at="2026-07-21T11:00:00Z"
        ... )
    """
    id: int
    subject: str
    description: str
    status: TaskStatus
    blocked_by: list[int]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """
        将任务序列化为字典

        返回与 JSON 文件格式一致的字典，用于持久化存储。

        返回：
            dict[str, Any]: 包含所有任务字段的字典

        使用示例：
            >>> task = Task(id=1, subject="test", ...)
            >>> data = task.to_dict()
            >>> print(data)
            {"id": 1, "subject": "test", ...}
        """
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "blocked_by": self.blocked_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        """
        从字典构造任务对象

        将 JSON 解析后的字典转换为 Task 对象。

        参数：
            data: 包含任务数据的字典

        返回：
            Task: 解析后的任务对象

        容错设计：
        - description: 默认空字符串
        - status: 默认 "pending"
        - blocked_by: 默认空列表
        - created_at/updated_at: 默认空字符串

        使用示例：
            >>> data = {"id": 1, "subject": "test"}
            >>> task = Task.from_dict(data)
            >>> print(task.status)
            "pending"
        """
        return cls(
            id=int(data["id"]),
            subject=str(data["subject"]),
            description=str(data.get("description", "")),
            status=data.get("status", "pending"),
            blocked_by=[int(x) for x in data.get("blocked_by", [])],
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )
