"""
命令定义模块 - 定义客户端向服务器发送的命令类型

【学习要点】
1. 命令类型：定义客户端向服务器发送的命令
2. 响应类型：定义服务器返回的响应
3. 判别联合：使用 Pydantic 的 Discriminator 实现多态类型
4. 命令分类：心跳检测、Agent 运行、事件订阅、会话管理、权限响应、上下文压缩、检查点管理

【命令分类】
- 心跳检测：PingCommand
- Agent 运行：AgentRunCommand
- 事件订阅：EventSubscribeCommand
- 会话管理：SessionCreateCommand, SessionSendMessageCommand, SessionGetHistoryCommand, SessionCloseCommand
- 权限响应：PermissionRespondCommand
- 上下文压缩：SessionCompactCommand
- 检查点管理：SessionCheckpointListCommand, SessionCheckpointRestoreCommand

【判别联合】
使用 Pydantic 的 Discriminator("type") 实现多态类型，
根据 type 字段自动推断命令类型。

【设计目的】
提供统一的命令定义，
便于客户端向服务器发送命令和服务器处理命令。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator

from iwan_claude.core.session.model import SessionMode, SessionStatus


class PingCommand(BaseModel):
    """
    心跳检测命令 - 客户端向服务器发送心跳检测

    【字段说明】
    - type: Literal["core.ping"] - 命令类型
    - client: str - 客户端标识

    【设计目的】
    检测服务器是否在线，获取服务器版本和运行时间。

    【响应】
    PongResult - 包含服务器版本、运行时间和接收时间
    """
    type: Literal["core.ping"] = "core.ping"
    client: str


class PongResult(BaseModel):
    """
    心跳检测响应 - 服务器返回的心跳响应

    【字段说明】
    - server_version: str - 服务器版本
    - uptime_ms: int - 服务器运行时间（毫秒）
    - received_at: str - 请求接收时间（ISO 8601）

    【设计目的】
    返回服务器状态信息，用于客户端健康检查。
    """
    server_version: str
    uptime_ms: int
    received_at: str


class AgentRunCommand(BaseModel):
    """
    Agent 运行命令 - 客户端请求运行 Agent

    【字段说明】
    - type: Literal["agent.run"] - 命令类型
    - goal: str - 运行目标

    【设计目的】
    请求服务器运行 Agent，执行指定目标。

    【响应】
    AgentRunResult - 包含运行 ID
    """
    type: Literal["agent.run"] = "agent.run"
    goal: str


class AgentRunResult(BaseModel):
    """
    Agent 运行响应 - 服务器返回的运行响应

    【字段说明】
    - run_id: str - 运行 ID

    【设计目的】
    返回运行 ID，用于后续查询运行状态和事件。
    """
    run_id: str


class EventSubscribeCommand(BaseModel):
    """
    事件订阅命令 - 客户端订阅服务器事件

    【字段说明】
    - type: Literal["event.subscribe"] - 命令类型
    - topics: list[str] - 订阅主题列表（fnmatch 模式，如 ["step.*", "tool.*"]）
    - scope: str - 订阅范围（"global" | "run:<run_id>"，默认 "global"）
    - replay_from_run: str | None - 回放起始运行 ID（设置则先从 events.jsonl 回放历史再接实时流）

    【设计目的】
    订阅服务器事件，实现实时事件推送和历史事件回放。

    【响应】
    EventSubscribeResult - 包含订阅 ID 和回放事件数
    """
    type: Literal["event.subscribe"] = "event.subscribe"
    topics: list[str]
    scope: str = "global"
    replay_from_run: str | None = None


class EventSubscribeResult(BaseModel):
    """
    事件订阅响应 - 服务器返回的订阅响应

    【字段说明】
    - subscription_id: str - 订阅 ID
    - replayed_count: int - 回放事件数（默认 0）

    【设计目的】
    返回订阅 ID 和回放事件数，用于客户端管理订阅。
    """
    subscription_id: str
    replayed_count: int = 0


class SessionCreateCommand(BaseModel):
    """
    会话创建命令 - 客户端请求创建会话

    【字段说明】
    - type: Literal["session.create"] - 命令类型
    - mode: SessionMode - 会话模式（默认 "chat"）
    - title: str - 会话标题（默认空字符串）

    【设计目的】
    创建新的会话，设置会话模式和标题。

    【响应】
    SessionCreateResult - 包含会话 ID 和状态
    """
    type: Literal["session.create"] = "session.create"
    mode: SessionMode = "chat"
    title: str = ""


class SessionCreateResult(BaseModel):
    """
    会话创建响应 - 服务器返回的会话创建响应

    【字段说明】
    - session_id: str - 会话 ID
    - status: SessionStatus - 会话状态

    【设计目的】
    返回会话 ID 和状态，用于客户端管理会话。
    """
    session_id: str
    status: SessionStatus


class SessionSendMessageCommand(BaseModel):
    """
    发送消息命令 - 客户端向会话发送消息

    【字段说明】
    - type: Literal["session.send_message"] - 命令类型
    - session_id: str - 会话 ID
    - content: str - 消息内容

    【设计目的】
    向指定会话发送消息，触发 Agent 运行。

    【响应】
    SessionSendMessageResult - 包含运行 ID
    """
    type: Literal["session.send_message"] = "session.send_message"
    session_id: str
    content: str


class SessionSendMessageResult(BaseModel):
    """
    发送消息响应 - 服务器返回的发送消息响应

    【字段说明】
    - run_id: str - 运行 ID

    【设计目的】
    返回运行 ID，用于后续查询运行状态和事件。
    """
    run_id: str


class SessionGetHistoryCommand(BaseModel):
    """
    获取历史命令 - 客户端获取会话历史消息

    【字段说明】
    - type: Literal["session.get_history"] - 命令类型
    - session_id: str - 会话 ID

    【设计目的】
    获取指定会话的历史消息，用于客户端显示聊天记录。

    【响应】
    SessionGetHistoryResult - 包含消息列表
    """
    type: Literal["session.get_history"] = "session.get_history"
    session_id: str


class SessionGetHistoryResult(BaseModel):
    """
    获取历史响应 - 服务器返回的历史消息响应

    【字段说明】
    - messages: list[dict[str, Any]] - 消息列表

    【设计目的】
    返回会话历史消息，用于客户端显示聊天记录。
    """
    messages: list[dict[str, Any]]


class SessionCloseCommand(BaseModel):
    """
    关闭会话命令 - 客户端请求关闭会话

    【字段说明】
    - type: Literal["session.close"] - 命令类型
    - session_id: str - 会话 ID

    【设计目的】
    关闭指定会话，释放相关资源。

    【响应】
    SessionCloseResult - 包含会话状态
    """
    type: Literal["session.close"] = "session.close"
    session_id: str


class SessionCloseResult(BaseModel):
    """
    关闭会话响应 - 服务器返回的关闭会话响应

    【字段说明】
    - status: SessionStatus - 会话状态

    【设计目的】
    返回会话状态，用于客户端确认会话已关闭。
    """
    status: SessionStatus


class PermissionRespondCommand(BaseModel):
    """
    权限响应命令 - 客户端响应权限请求

    【字段说明】
    - type: Literal["permission.respond"] - 命令类型
    - tool_use_id: str - 工具调用 ID
    - decision: str - 决策类型（"allow_once" | "always_allow" | "deny_once" | "always_deny"）

    【设计目的】
    响应服务器的权限请求，决定是否允许工具调用。

    【决策类型】
    - allow_once: 允许一次
    - always_allow: 始终允许（更新缓存）
    - deny_once: 拒绝一次
    - always_deny: 始终拒绝（更新缓存）

    【响应】
    PermissionRespondResult - 包含是否成功
    """
    type: Literal["permission.respond"] = "permission.respond"
    tool_use_id: str
    decision: str


class PermissionRespondResult(BaseModel):
    """
    权限响应结果 - 服务器返回的权限响应结果

    【字段说明】
    - ok: bool - 是否成功（默认 True）

    【设计目的】
    返回权限响应是否成功处理。
    """
    ok: bool = True


class SessionCompactCommand(BaseModel):
    """
    上下文压缩命令 - 客户端请求压缩会话上下文

    【字段说明】
    - type: Literal["session.compact"] - 命令类型
    - session_id: str - 会话 ID
    - focus: str - 压缩焦点（默认空字符串）

    【设计目的】
    压缩会话上下文，减少令牌消耗，延长对话长度。

    【响应】
    SessionCompactResult - 包含压缩后的令牌数和节省的令牌数
    """
    type: Literal["session.compact"] = "session.compact"
    session_id: str
    focus: str = ""


class SessionCompactResult(BaseModel):
    """
    上下文压缩响应 - 服务器返回的上下文压缩响应

    【字段说明】
    - summary_tokens: int - 压缩后的令牌数
    - saved_tokens: int - 节省的令牌数

    【设计目的】
    返回压缩结果，用于客户端显示压缩效果。
    """
    summary_tokens: int
    saved_tokens: int


class SessionCheckpointListCommand(BaseModel):
    """
    检查点列表命令 - 客户端获取会话检查点列表

    【字段说明】
    - type: Literal["session.checkpoint.list"] - 命令类型
    - session_id: str - 会话 ID

    【设计目的】
    获取会话的检查点列表，用于客户端显示和选择检查点。

    【响应】
    SessionCheckpointListResult - 包含检查点列表和线程 ID
    """
    type: Literal["session.checkpoint.list"] = "session.checkpoint.list"
    session_id: str


class CheckpointInfo(BaseModel):
    """
    检查点信息 - 包含单个检查点的详细信息

    【字段说明】
    - checkpoint_id: str - 检查点 ID
    - step: int - 步骤编号
    - timestamp: str - 时间戳
    - summary: str - 检查点摘要
    - node: str | None - 节点名称（可选）

    【设计目的】
    封装单个检查点的详细信息，用于客户端显示。
    """
    checkpoint_id: str
    step: int
    timestamp: str
    summary: str
    node: str | None = None


class SessionCheckpointListResult(BaseModel):
    """
    检查点列表响应 - 服务器返回的检查点列表响应

    【字段说明】
    - checkpoints: list[CheckpointInfo] - 检查点列表
    - thread_id: str - 线程 ID

    【设计目的】
    返回检查点列表和线程 ID，用于客户端显示和管理检查点。
    """
    checkpoints: list[CheckpointInfo]
    thread_id: str


class SessionCheckpointRestoreCommand(BaseModel):
    """
    检查点恢复命令 - 客户端请求恢复到指定检查点

    【字段说明】
    - type: Literal["session.checkpoint.restore"] - 命令类型
    - session_id: str - 会话 ID
    - checkpoint_id: str - 检查点 ID

    【设计目的】
    将会话恢复到指定检查点，实现回溯功能。

    【响应】
    SessionCheckpointRestoreResult - 包含恢复结果
    """
    type: Literal["session.checkpoint.restore"] = "session.checkpoint.restore"
    session_id: str
    checkpoint_id: str


class SessionCheckpointRestoreResult(BaseModel):
    """
    检查点恢复响应 - 服务器返回的检查点恢复响应

    【字段说明】
    - success: bool - 是否成功
    - checkpoint_id: str - 检查点 ID
    - step: int - 步骤编号
    - message: str - 消息

    【设计目的】
    返回恢复结果，用于客户端确认恢复是否成功。
    """
    success: bool
    checkpoint_id: str
    step: int
    message: str


# 根据 type 字段决定命令类型的判别联合
# 使用 Pydantic 的 Discriminator 实现多态类型，根据 type 字段自动推断命令类型
Command = Annotated[
    PingCommand
    | AgentRunCommand
    | EventSubscribeCommand
    | SessionCreateCommand
    | SessionSendMessageCommand
    | SessionGetHistoryCommand
    | SessionCloseCommand
    | PermissionRespondCommand
    | SessionCompactCommand
    | SessionCheckpointListCommand
    | SessionCheckpointRestoreCommand,
    Discriminator("type"),
]
