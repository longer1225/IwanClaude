"""
事件定义模块 - 定义系统中所有事件类型

【学习要点】
1. 事件类型：定义系统中所有事件类型
2. 判别联合：使用 Pydantic 的 Discriminator 实现多态类型
3. 事件字段：每个事件类型有特定的字段

【事件分类】
- 核心事件：CoreStartedEvent
- 运行事件：RunStartedEvent, RunFinishedEvent
- 步骤事件：StepStartedEvent, StepFinishedEvent
- 工具调用事件：ToolCallStartedEvent, ToolCallFinishedEvent, ToolCallFailedEvent
- LLM 事件：LlmTokenEvent, LlmUsageEvent, LlmModelSelectedEvent
- 日志事件：LogLineEvent
- 会话事件：SessionCreatedEvent, SessionMessageReceivedEvent, SessionWaitingForInputEvent, SessionResumedEvent, SessionClosedEvent
- 上下文事件：ContextCompactedEvent
- 权限事件：PermissionRequestedEvent, PermissionGrantedEvent, PermissionDeniedEvent
- 子 Agent 事件：SubagentStartedEvent, SubagentFinishedEvent
- Skill 事件：SkillInvokedEvent

【判别联合】
使用 Pydantic 的 Discriminator("type") 实现多态类型，
根据 type 字段自动推断事件类型。

【设计目的】
提供统一的事件定义，
便于服务器向客户端推送事件和客户端处理事件。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator


class CoreStartedEvent(BaseModel):
    """
    核心服务启动事件 - 核心服务启动时发送

    【字段说明】
    - type: Literal["core.started"] - 事件类型
    - listen_addr: str - 监听地址（如 "127.0.0.1:7437"）
    - version: str - 服务版本

    【设计目的】
    通知客户端核心服务已启动，提供监听地址和版本信息。
    """
    type: Literal["core.started"] = "core.started"
    listen_addr: str
    version: str


class RunStartedEvent(BaseModel):
    """
    运行开始事件 - Agent 运行开始时发送

    【字段说明】
    - type: Literal["run.started"] - 事件类型
    - run_id: str - 运行 ID
    - goal: str - 运行目标
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端 Agent 运行已开始，提供运行 ID 和目标。
    """
    type: Literal["run.started"] = "run.started"
    run_id: str
    goal: str
    ts: str


class RunFinishedEvent(BaseModel):
    """
    运行结束事件 - Agent 运行结束时发送

    【字段说明】
    - type: Literal["run.finished"] - 事件类型
    - run_id: str - 运行 ID
    - status: str - 运行状态（"success" | "failed"）
    - reason: str | None - 结束原因（"exceeded_max_steps" | "cancelled" | "llm_error" | ...）
    - steps: int - 执行步骤数
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端 Agent 运行已结束，提供运行状态和结束原因。
    """
    type: Literal["run.finished"] = "run.finished"
    run_id: str
    status: str
    reason: str | None = None
    steps: int
    ts: str


class StepStartedEvent(BaseModel):
    """
    步骤开始事件 - 执行步骤开始时发送

    【字段说明】
    - type: Literal["step.started"] - 事件类型
    - run_id: str - 运行 ID
    - step: int - 步骤编号
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端执行步骤已开始，提供步骤编号。
    """
    type: Literal["step.started"] = "step.started"
    run_id: str
    step: int
    ts: str


class StepFinishedEvent(BaseModel):
    """
    步骤结束事件 - 执行步骤结束时发送

    【字段说明】
    - type: Literal["step.finished"] - 事件类型
    - run_id: str - 运行 ID
    - step: int - 步骤编号
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端执行步骤已结束，提供步骤编号。
    """
    type: Literal["step.finished"] = "step.finished"
    run_id: str
    step: int
    ts: str


class ToolCallStartedEvent(BaseModel):
    """
    工具调用开始事件 - 工具调用开始时发送

    【字段说明】
    - type: Literal["tool.call_started"] - 事件类型
    - run_id: str - 运行 ID
    - tool_use_id: str - 工具调用 ID
    - tool_name: str - 工具名称
    - params: dict[str, Any] - 工具参数
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端工具调用已开始，提供工具名称和参数。
    """
    type: Literal["tool.call_started"] = "tool.call_started"
    run_id: str
    tool_use_id: str
    tool_name: str
    params: dict[str, Any]
    ts: str


class ToolCallFinishedEvent(BaseModel):
    """
    工具调用结束事件 - 工具调用成功结束时发送

    【字段说明】
    - type: Literal["tool.call_finished"] - 事件类型
    - run_id: str - 运行 ID
    - tool_use_id: str - 工具调用 ID
    - tool_name: str - 工具名称
    - elapsed_ms: int - 执行耗时（毫秒）
    - output: str - 工具输出内容（用于 TUI 显示）
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端工具调用已成功结束，提供执行耗时和输出内容。
    """
    type: Literal["tool.call_finished"] = "tool.call_finished"
    run_id: str
    tool_use_id: str
    tool_name: str
    elapsed_ms: int
    output: str = ""
    ts: str


class ToolCallFailedEvent(BaseModel):
    """
    工具调用失败事件 - 工具调用失败时发送

    【字段说明】
    - type: Literal["tool.call_failed"] - 事件类型
    - run_id: str - 运行 ID
    - tool_use_id: str - 工具调用 ID
    - tool_name: str - 工具名称
    - error_class: str - 错误类型（"runtime_error" | "timeout" | "schema_error" | "permission_denied" | "rate_limited"）
    - error_message: str - 错误消息
    - elapsed_ms: int - 执行耗时（毫秒）
    - attempt: int - 尝试次数（1=首次尝试，2=首次重试，3=第二次重试）
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端工具调用已失败，提供错误类型和错误消息。
    """
    type: Literal["tool.call_failed"] = "tool.call_failed"
    run_id: str
    tool_use_id: str
    tool_name: str
    error_class: str
    error_message: str
    elapsed_ms: int
    attempt: int = 1
    ts: str


class LlmTokenEvent(BaseModel):
    """
    LLM 令牌事件 - LLM 生成令牌时发送（流式输出）

    【字段说明】
    - type: Literal["llm.token"] - 事件类型
    - run_id: str - 运行 ID
    - token: str - 生成的令牌
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    流式通知客户端 LLM 正在生成令牌，实现实时输出。
    """
    type: Literal["llm.token"] = "llm.token"
    run_id: str
    token: str
    ts: str


class LlmUsageEvent(BaseModel):
    """
    LLM 使用事件 - LLM 请求完成后发送（包含令牌使用统计）

    【字段说明】
    - type: Literal["llm.usage"] - 事件类型
    - run_id: str - 运行 ID
    - input_tokens: int - 输入令牌数
    - output_tokens: int - 输出令牌数
    - cache_read_input_tokens: int - 缓存读取的输入令牌数
    - cache_creation_input_tokens: int - 缓存创建的输入令牌数
    - context_pct: float - 上下文使用率（0.0 到 1.0）
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端 LLM 令牌使用情况，用于成本统计和监控。
    """
    type: Literal["llm.usage"] = "llm.usage"
    run_id: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    context_pct: float = 0.0
    ts: str


class LlmModelSelectedEvent(BaseModel):
    """
    LLM 模型选择事件 - LLM 模型选择完成后发送

    【字段说明】
    - type: Literal["llm.model_selected"] - 事件类型
    - run_id: str - 运行 ID
    - model: str - 选择的模型名称
    - strategy: str - 选择策略（"static" | "rule_based" | "cost_budget"）
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端选择了哪个 LLM 模型，提供选择策略。
    """
    type: Literal["llm.model_selected"] = "llm.model_selected"
    run_id: str
    model: str
    strategy: str
    ts: str


class LogLineEvent(BaseModel):
    """
    日志事件 - 系统产生日志时发送

    【字段说明】
    - type: Literal["log.line"] - 事件类型
    - run_id: str - 运行 ID（可能为空）
    - level: str - 日志级别（"DEBUG" | "INFO" | "WARNING" | "ERROR"）
    - source: str - 日志来源（模块名）
    - message: str - 日志消息
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端系统日志，便于调试和监控。
    """
    type: Literal["log.line"] = "log.line"
    run_id: str
    level: str
    source: str
    message: str
    ts: str


class SessionCreatedEvent(BaseModel):
    """
    会话创建事件 - 会话创建时发送

    【字段说明】
    - type: Literal["session.created"] - 事件类型
    - session_id: str - 会话 ID
    - mode: str - 会话模式
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端会话已创建，提供会话 ID 和模式。
    """
    type: Literal["session.created"] = "session.created"
    session_id: str
    mode: str
    ts: str


class SessionMessageReceivedEvent(BaseModel):
    """
    会话消息接收事件 - 会话收到消息时发送

    【字段说明】
    - type: Literal["session.message_received"] - 事件类型
    - session_id: str - 会话 ID
    - content: str - 消息内容
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端会话收到消息，提供消息内容。
    """
    type: Literal["session.message_received"] = "session.message_received"
    session_id: str
    content: str
    ts: str


class SessionWaitingForInputEvent(BaseModel):
    """
    会话等待输入事件 - 会话等待用户输入时发送

    【字段说明】
    - type: Literal["session.waiting_for_input"] - 事件类型
    - session_id: str - 会话 ID
    - last_run_id: str - 最后一个运行 ID
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端会话正在等待用户输入，
    客户端可以显示输入提示。
    """
    type: Literal["session.waiting_for_input"] = "session.waiting_for_input"
    session_id: str
    last_run_id: str
    ts: str


class SessionResumedEvent(BaseModel):
    """
    会话恢复事件 - 会话恢复时发送

    【字段说明】
    - type: Literal["session.resumed"] - 事件类型
    - session_id: str - 会话 ID
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端会话已恢复，
    客户端可以更新界面状态。
    """
    type: Literal["session.resumed"] = "session.resumed"
    session_id: str
    ts: str


class SessionClosedEvent(BaseModel):
    """
    会话关闭事件 - 会话关闭时发送

    【字段说明】
    - type: Literal["session.closed"] - 事件类型
    - session_id: str - 会话 ID
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端会话已关闭，
    客户端可以清理资源。
    """
    type: Literal["session.closed"] = "session.closed"
    session_id: str
    ts: str


class SessionAutoModeChangedEvent(BaseModel):
    """
    会话自动模式变更事件 - 自动模式切换时发送

    【字段说明】
    - type: Literal["session.auto_mode_changed"] - 事件类型
    - session_id: str - 会话 ID
    - mode: str - 新的自动模式
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端自动模式已变更，
    客户端可以更新状态栏显示。
    """
    type: Literal["session.auto_mode_changed"] = "session.auto_mode_changed"
    session_id: str
    mode: str
    ts: str


class SessionEffortLevelChangedEvent(BaseModel):
    """
    会话努力等级变更事件 - 努力等级切换时发送

    【字段说明】
    - type: Literal["session.effort_level_changed"] - 事件类型
    - session_id: str - 会话 ID
    - level: str - 新的努力等级（minimal / low / medium / high / max）
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端努力等级已变更，
    客户端可以更新状态栏显示。
    """
    type: Literal["session.effort_level_changed"] = "session.effort_level_changed"
    session_id: str
    level: str
    ts: str


class SessionModelChangedEvent(BaseModel):
    """
    会话模型预设变更事件 - 模型预设切换时发送

    【字段说明】
    - type: Literal["session.model_changed"] - 事件类型
    - session_id: str - 会话 ID
    - preset: str - 新的模型预设（fast / balanced / powerful）
    - model: str - 对应的模型名称
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端模型预设已变更，
    客户端可以更新状态栏显示。
    """
    type: Literal["session.model_changed"] = "session.model_changed"
    session_id: str
    preset: str
    model: str
    ts: str


class SessionEngineChangedEvent(BaseModel):
    """
    会话引擎变更事件 - Agent 引擎切换时发送

    【字段说明】
    - type: Literal["session.engine_changed"] - 事件类型
    - session_id: str - 会话 ID
    - engine: str - 新的引擎名称（legacy / langgraph / plan_execute / debate / pipeline）
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端 Agent 引擎已变更，
    客户端可以更新状态栏显示并刷新检查点可用性。
    多客户端场景下确保所有连接的 TUI 状态同步。
    """
    type: Literal["session.engine_changed"] = "session.engine_changed"
    session_id: str
    engine: str
    ts: str


class SessionRenamedEvent(BaseModel):
    """
    会话重命名事件 - 会话标题变更时发送

    【字段说明】
    - type: Literal["session.renamed"] - 事件类型
    - session_id: str - 会话 ID
    - title: str - 新的会话标题
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端会话标题已变更，
    客户端可以更新标签页显示。
    """
    type: Literal["session.renamed"] = "session.renamed"
    session_id: str
    title: str
    ts: str


class ContextCompactedEvent(BaseModel):
    """
    上下文压缩事件 - 上下文压缩完成后发送

    【字段说明】
    - type: Literal["context.compacted"] - 事件类型
    - session_id: str - 会话 ID
    - run_id: str - 运行 ID
    - original_tokens: int - 原始令牌数
    - summary_tokens: int - 压缩后的令牌数
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端上下文已压缩，
    提供压缩前后的令牌数对比。
    """
    type: Literal["context.compacted"] = "context.compacted"
    session_id: str
    run_id: str
    original_tokens: int
    summary_tokens: int
    ts: str


class PermissionRequestedEvent(BaseModel):
    """
    权限请求事件 - 需要用户审批权限时发送

    【字段说明】
    - type: Literal["permission.requested"] - 事件类型
    - run_id: str - 运行 ID
    - tool_use_id: str - 工具调用 ID
    - tool_name: str - 工具名称
    - params: dict[str, Any] - 工具参数
    - param_preview: str - 参数预览
    - session_id: str - 会话 ID
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端需要用户审批权限，
    客户端可以显示审批对话框。
    """
    type: Literal["permission.requested"] = "permission.requested"
    run_id: str
    tool_use_id: str
    tool_name: str
    params: dict[str, Any]
    param_preview: str
    session_id: str
    ts: str


class PermissionGrantedEvent(BaseModel):
    """
    权限授予事件 - 用户授予权限后发送

    【字段说明】
    - type: Literal["permission.granted"] - 事件类型
    - run_id: str - 运行 ID
    - tool_use_id: str - 工具调用 ID
    - decision: str - 决策类型（"allow_once" | "always_allow" | "auto_allow"）
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端权限已授予，
    客户端可以更新权限状态。
    """
    type: Literal["permission.granted"] = "permission.granted"
    run_id: str
    tool_use_id: str
    decision: str
    ts: str


class PermissionDeniedEvent(BaseModel):
    """
    权限拒绝事件 - 用户拒绝权限后发送

    【字段说明】
    - type: Literal["permission.denied"] - 事件类型
    - run_id: str - 运行 ID
    - tool_use_id: str - 工具调用 ID
    - decision: str - 决策类型（"deny_once" | "always_deny" | "auto_deny"）
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端权限已拒绝，
    客户端可以更新权限状态。
    """
    type: Literal["permission.denied"] = "permission.denied"
    run_id: str
    tool_use_id: str
    decision: str
    ts: str


class SubagentStartedEvent(BaseModel):
    """
    子 Agent 开始事件 - 子 Agent 开始运行时发送

    【字段说明】
    - type: Literal["subagent.started"] - 事件类型
    - run_id: str - 子 Agent 运行 ID
    - parent_run_id: str - 父运行 ID
    - description: str - 子 Agent 描述
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端子 Agent 已开始运行，
    提供父运行 ID 和描述。
    """
    type: Literal["subagent.started"] = "subagent.started"
    run_id: str
    parent_run_id: str
    description: str
    ts: str


class SubagentFinishedEvent(BaseModel):
    """
    子 Agent 结束事件 - 子 Agent 运行结束时发送

    【字段说明】
    - type: Literal["subagent.finished"] - 事件类型
    - run_id: str - 子 Agent 运行 ID
    - parent_run_id: str - 父运行 ID
    - status: str - 运行状态（"success" | "failed"）
    - ts: str - 时间戳（ISO 8601）

    【设计目的】
    通知客户端子 Agent 已运行结束，
    提供运行状态。
    """
    type: Literal["subagent.finished"] = "subagent.finished"
    run_id: str
    parent_run_id: str
    status: str
    ts: str


class SkillInvokedEvent(BaseModel):
    """
    Skill 调用事件 - Skill 被调用时发送

    【字段说明】
    - type: Literal["skill.invoked"] - 事件类型
    - skill_name: str - Skill 名称
    - arguments: str - Skill 参数
    - run_id: str - 运行 ID
    - ts: str - 时间戳（ISO 8601）
    - auto_triggered: bool - 是否自动触发（默认 False）
    - match_score: float - 匹配分数（默认 0.0）

    【设计目的】
    通知客户端 Skill 已被调用，
    提供 Skill 名称、参数和匹配信息。
    """
    type: Literal["skill.invoked"] = "skill.invoked"
    skill_name: str
    arguments: str
    run_id: str
    ts: str
    auto_triggered: bool = False
    match_score: float = 0.0


# 根据 type 字段决定事件类型的判别联合
# 使用 Pydantic 的 Discriminator 实现多态类型，根据 type 字段自动推断事件类型
Event = Annotated[
    CoreStartedEvent
    | RunStartedEvent
    | RunFinishedEvent
    | StepStartedEvent
    | StepFinishedEvent
    | ToolCallStartedEvent
    | ToolCallFinishedEvent
    | ToolCallFailedEvent
    | LlmTokenEvent
    | LlmUsageEvent
    | LlmModelSelectedEvent
    | LogLineEvent
    | SessionCreatedEvent
    | SessionMessageReceivedEvent
    | SessionWaitingForInputEvent
    | SessionResumedEvent
    | SessionClosedEvent
    | SessionAutoModeChangedEvent
    | SessionEffortLevelChangedEvent
    | SessionModelChangedEvent
    | SessionRenamedEvent
    | ContextCompactedEvent
    | PermissionRequestedEvent
    | PermissionGrantedEvent
    | PermissionDeniedEvent
    | SubagentStartedEvent
    | SubagentFinishedEvent
    | SkillInvokedEvent,
    Discriminator("type"),
]
