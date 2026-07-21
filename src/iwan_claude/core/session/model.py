"""
会话模型模块 - 定义会话的数据结构和类型

【学习要点】
1. 字面量类型：使用 Literal 定义有限枚举值类型
2. 数据类：使用 dataclass 定义会话数据模型
3. 序列化：to_dict() 方法将对象转换为可序列化的字典
4. 反序列化：from_dict() 类方法从字典还原对象

【类型定义】
- SessionStatus: 会话状态类型，支持：active、waiting_for_input、closed
- SessionMode: 会话模式类型，支持：one_shot、chat

【会话状态说明】
- active: 会话正在进行中
- waiting_for_input: 会话等待用户输入
- closed: 会话已关闭

【会话模式说明】
- one_shot: 一次性对话模式，用户发送一条消息后结束
- chat: 持续对话模式，支持多轮对话

【设计特点】
- 使用 dataclass 自动生成 __init__、__repr__ 等方法
- 使用 field(default_factory=list) 设置列表默认值
- to_dict() 和 from_dict() 支持 JSON 序列化/反序列化
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# 会话状态类型：字面量类型，只允许三个值
# - active: 会话正在进行中
# - waiting_for_input: 会话等待用户输入
# - closed: 会话已关闭
SessionStatus = Literal["active", "waiting_for_input", "closed"]

# 会话模式类型：字面量类型，只允许两个值
# - one_shot: 一次性对话模式
# - chat: 持续对话模式
SessionMode = Literal["one_shot", "chat"]


@dataclass
class Session:
    """
    会话数据模型 - 定义会话的基本信息

    【字段说明】
    - id: str - 会话唯一标识
    - mode: SessionMode - 会话模式（one_shot / chat）
    - status: SessionStatus - 会话状态（active / waiting_for_input / closed）
    - title: str - 会话标题
    - created_at: str - 创建时间（ISO 格式）
    - updated_at: str - 更新时间（ISO 格式）
    - run_ids: list[str] - 会话包含的运行 ID 列表

    【设计说明】
    使用 dataclass 装饰器自动生成：
    - __init__: 构造函数
    - __repr__: 字符串表示
    - __eq__: 相等比较
    - __hash__: 哈希值（如果所有字段都是不可变的）

    run_ids 使用 field(default_factory=list) 设置默认值，
    这样每个实例都会有独立的列表，不会共享同一个列表对象。

    【使用示例】
    ```python
    from iwan_claude.core.session.model import Session, SessionMode, SessionStatus
    
    # 创建会话
    session = Session(
        id="session_123",
        mode="chat",
        status="active",
        title="代码审查",
        created_at="2024-01-15T10:30:00",
        updated_at="2024-01-15T10:30:00",
    )
    
    # 添加运行 ID
    session.run_ids.append("run_456")
    
    # 转换为字典
    data = session.to_dict()
    
    # 从字典还原
    session2 = Session.from_dict(data)
    ```
    """
    id: str
    mode: SessionMode
    status: SessionStatus
    title: str
    created_at: str
    updated_at: str
    run_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        将 Session 对象转换为字典

        【返回值】
        - dict[str, Any]: 包含所有字段的字典，可用于 JSON 序列化

        【设计目的】
        将对象转换为普通字典，便于写入 meta.json 文件
        """
        return {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "run_ids": list(self.run_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        """
        从字典还原 Session 对象

        【参数说明】
        - data: dict[str, Any] - 包含会话数据的字典

        【返回值】
        - Session: 从字典还原的会话对象

        【设计目的】
        从 meta.json 文件读取的数据还原为 Session 对象
        """
        return cls(
            id=str(data["id"]),
            mode=data["mode"],
            status=data["status"],
            title=str(data.get("title", "")),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            run_ids=[str(x) for x in data.get("run_ids", [])],
        )
