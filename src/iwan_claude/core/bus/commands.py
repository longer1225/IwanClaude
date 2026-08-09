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
    - auto_mode: str - 当前自动模式（"off" / "read_only" / "on"）
    - effort_level: str - 当前努力等级（"minimal" / "low" / "medium" / "high" / "max"）
    - model_preset: str - 当前模型预设（"fast" / "balanced" / "powerful"）

    【设计目的】
    返回会话 ID、状态和当前配置，用于客户端管理会话。
    """
    session_id: str
    status: SessionStatus
    auto_mode: str = "off"
    effort_level: str = "medium"
    model_preset: str = "balanced"


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


class SessionSetAutoModeCommand(BaseModel):
    """
    设置自动模式命令 - 客户端请求设置会话的自动模式

    【字段说明】
    - type: Literal["session.set_auto_mode"] - 命令类型
    - session_id: str - 会话 ID
    - mode: str - 自动模式（"off" / "read_only" / "on"）

    【设计目的】
    允许客户端动态切换自动模式，控制是否自动批准低风险工具调用。

    【响应】
    SessionSetAutoModeResult - 包含设置后的模式
    """
    type: Literal["session.set_auto_mode"] = "session.set_auto_mode"
    session_id: str
    mode: str


class SessionSetAutoModeResult(BaseModel):
    """
    设置自动模式响应 - 服务器返回的设置结果

    【字段说明】
    - mode: str - 当前自动模式

    【设计目的】
    返回设置后的自动模式，用于客户端同步状态。
    """
    mode: str


class SessionSetEffortLevelCommand(BaseModel):
    """
    设置努力等级命令 - 客户端请求设置会话的努力等级

    【字段说明】
    - type: Literal["session.set_effort_level"] - 命令类型
    - session_id: str - 会话 ID
    - level: str - 努力等级（"minimal" / "low" / "medium" / "high" / "max"）

    【设计目的】
    允许客户端动态切换努力等级，控制 Agent 执行深度。
    等级越高，Agent 会读更多文件、做更多验证、搜索更深。

    【响应】
    SessionSetEffortLevelResult - 包含设置后的等级
    """
    type: Literal["session.set_effort_level"] = "session.set_effort_level"
    session_id: str
    level: str


class SessionSetEffortLevelResult(BaseModel):
    """
    设置努力等级响应 - 服务器返回的设置结果

    【字段说明】
    - level: str - 当前努力等级

    【设计目的】
    返回设置后的努力等级，用于客户端同步状态。
    """
    level: str


class SessionSetModelCommand(BaseModel):
    """
    设置模型预设命令 - 客户端请求设置会话的模型预设

    【字段说明】
    - type: Literal["session.set_model"] - 命令类型
    - session_id: str - 会话 ID
    - preset: str - 模型预设（"fast" / "balanced" / "powerful"）

    【设计目的】
    允许客户端动态切换模型预设，控制 Agent 使用哪个 LLM 模型。
    切换后，下一次 Agent run 会使用新预设对应的模型。

    【响应】
    SessionSetModelResult - 包含设置后的预设
    """
    type: Literal["session.set_model"] = "session.set_model"
    session_id: str
    preset: str


class SessionSetModelResult(BaseModel):
    """
    设置模型预设响应 - 服务器返回的设置结果

    【字段说明】
    - preset: str - 当前模型预设

    【设计目的】
    返回设置后的模型预设，用于客户端同步状态。
    """
    preset: str


class SessionSetEngineCommand(BaseModel):
    """
    设置 Agent 引擎命令 - 客户端请求动态切换执行引擎

    【字段说明】
    - type: Literal["session.set_engine"] - 命令类型
    - session_id: str - 会话 ID
    - engine: str - 引擎名称（legacy / langgraph / plan_execute / debate / pipeline）

    【设计目的】
    允许客户端在运行时动态切换 Agent 引擎，无需重启 core。
    不同引擎适合不同任务：legacy（简单）、langgraph（ReAct）、plan_execute（规划执行）、
    debate（辩论）、pipeline（多角色流水线）。

    【响应】
    SessionSetEngineResult - 包含设置后的引擎名称
    """
    type: Literal["session.set_engine"] = "session.set_engine"
    session_id: str
    engine: str


class SessionSetEngineResult(BaseModel):
    """
    设置引擎响应 - 服务器返回的设置结果

    【字段说明】
    - engine: str - 当前引擎名称
    """
    engine: str


class SessionListCommand(BaseModel):
    """
    会话列表命令 - 客户端请求列出所有会话

    【字段说明】
    - type: Literal["session.list"] - 命令类型

    【设计目的】
    允许客户端获取所有会话的列表，
    用于 TUI 标签页显示和会话切换。

    【响应】
    SessionListResult - 包含会话列表
    """
    type: Literal["session.list"] = "session.list"


class SessionInfo(BaseModel):
    """
    会话信息 - 会话列表中的单个会话信息

    【字段说明】
    - id: str - 会话 ID
    - title: str - 会话标题
    - status: str - 会话状态（active / waiting_for_input / closed）
    - mode: str - 会话模式（one_shot / chat）
    - updated_at: str - 最后更新时间

    【设计目的】
    轻量级的会话信息，用于列表展示，
    不包含完整消息历史以减少传输量。
    """
    id: str
    title: str
    status: str
    mode: str
    updated_at: str


class SessionListResult(BaseModel):
    """
    会话列表响应 - 服务器返回的会话列表

    【字段说明】
    - sessions: list[SessionInfo] - 会话列表，按更新时间倒序排列

    【设计目的】
    返回所有会话的摘要信息，
    用于 TUI 标签页显示和会话切换。
    """
    sessions: list[SessionInfo]


class SessionRenameCommand(BaseModel):
    """
    重命名会话命令 - 客户端请求重命名会话标题

    【字段说明】
    - type: Literal["session.rename"] - 命令类型
    - session_id: str - 会话 ID
    - title: str - 新的会话标题

    【设计目的】
    允许用户自定义会话标题，
    便于在多标签中识别不同会话。

    【响应】
    SessionRenameResult - 包含重命名后的会话信息
    """
    type: Literal["session.rename"] = "session.rename"
    session_id: str
    title: str


class SessionRenameResult(BaseModel):
    """
    重命名会话响应 - 服务器返回的重命名结果

    【字段说明】
    - session_id: str - 会话 ID
    - title: str - 新的会话标题

    【设计目的】
    返回重命名后的会话信息，
    用于客户端同步标签页标题。
    """
    session_id: str
    title: str


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
    | SessionSetAutoModeCommand
    | SessionSetEffortLevelCommand
    | SessionSetModelCommand
    | SessionListCommand
    | SessionRenameCommand
    | SessionCompactCommand
    | SessionCheckpointListCommand
    | SessionCheckpointRestoreCommand,
    Discriminator("type"),
]
