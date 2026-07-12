# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 dataclass 和 field：用于定义数据类
from dataclasses import dataclass, field
# 导入 Any 和 Literal：类型提示
from typing import Any, Literal

# SessionStatus：会话状态的字面量类型
# - active：会话刚创建，尚未处理任何消息
# - waiting_for_input：会话正在等待用户输入（chat 模式）
# - closed：会话已关闭（one_shot 模式完成后自动关闭）
SessionStatus = Literal["active", "waiting_for_input", "closed"]

# SessionMode：会话模式的字面量类型
# - one_shot：一次性模式，执行完一个任务后自动关闭（run 命令使用）
# - chat：聊天模式，持续接收用户消息，保持会话活跃（chat 命令使用）
SessionMode = Literal["one_shot", "chat"]


# Session 数据类：表示一个聊天会话的元数据
# 什么是会话？会话是用户与 agent 之间的一次连续交互
# 包含对话历史、运行记录、笔记等信息
@dataclass
class Session:
    # 会话唯一标识（格式：sess-xxxxxxxxxxxx）
    id: str
    # 会话模式（one_shot 或 chat）
    mode: SessionMode
    # 会话状态（active、waiting_for_input、closed）
    status: SessionStatus
    # 会话标题（通常是第一条消息的前 40 个字符）
    title: str
    # 创建时间（ISO 8601 格式）
    created_at: str
    # 更新时间（ISO 8601 格式）
    updated_at: str
    # 关联的 run ID 列表（每次发送消息会创建一个 run）
    run_ids: list[str] = field(default_factory=list)

    # 将 Session 对象转换为字典，用于写入 meta.json 文件
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "run_ids": list(self.run_ids),
        }

    # 从字典创建 Session 对象（工厂方法）
    # 用于从 meta.json 文件读取会话信息
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            id=str(data["id"]),
            mode=data["mode"],
            status=data["status"],
            title=str(data.get("title", "")),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            run_ids=[str(x) for x in data.get("run_ids", [])],
        )
