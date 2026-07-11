# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 dataclasses：用于定义数据类
from dataclasses import dataclass
# 导入 Any 和 Literal：类型注解
from typing import Any, Literal

# TaskStatus：任务状态的字面量类型
# Literal 表示只能取这三个值中的一个，保证类型安全
TaskStatus = Literal["pending", "in_progress", "completed"]


# Task 数据类：定义任务的结构
# @dataclass 装饰器自动生成 __init__、__repr__、__eq__ 等方法
@dataclass
class Task:
    # 任务 ID（唯一标识）
    id: int
    # 任务主题（简短描述）
    subject: str
    # 任务描述（详细说明）
    description: str
    # 任务状态（pending / in_progress / completed）
    status: TaskStatus
    # 依赖的任务 ID 列表（当前任务需要等待这些任务完成才能开始）
    blocked_by: list[int]
    # 创建时间（ISO 格式字符串）
    created_at: str
    # 更新时间（ISO 格式字符串）
    updated_at: str

    # 序列化为 dict，字段名与 JSON 文件格式一致
    # 什么是序列化？就是把对象转换为可以存储或传输的格式（这里是字典）
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "blocked_by": self.blocked_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # 从 dict 构造 Task（反序列化）
    # 什么是反序列化？就是把存储的格式（字典）转换回对象
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=int(data["id"]),
            subject=str(data["subject"]),
            description=str(data.get("description", "")),
            status=data.get("status", "pending"),  # 默认 pending
            blocked_by=[int(x) for x in data.get("blocked_by", [])],
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )
