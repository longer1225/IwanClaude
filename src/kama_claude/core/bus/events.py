# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入类型注解工具
from typing import Annotated, Any, Literal

# 导入 Pydantic 模型基类和判别器
from pydantic import BaseModel, Discriminator


# Core 守护进程启动事件：当 Core 服务启动并开始监听时发布
class CoreStartedEvent(BaseModel):
    # 事件类型标识，固定为 "core.started"
    type: Literal["core.started"] = "core.started"
    # Core 监听的地址，格式如 "127.0.0.1:7437"
    listen_addr: str
    # KamaClaude 的版本号，如 "0.1.0"
    version: str


# Agent Run 开始事件：当一次新的 agent run 开始时发布
class RunStartedEvent(BaseModel):
    # 事件类型标识，固定为 "run.started"
    type: Literal["run.started"] = "run.started"
    # 运行的唯一 ID，格式如 "20260511-161020-abc123"
    run_id: str
    # 用户指定的目标字符串，如 "总结 README.md"
    goal: str
    # 事件发生时间，ISO 8601 格式，如 "2026-05-11T07:31:14.022Z"
    ts: str


# Agent Run 结束事件：当一次 agent run 结束时发布（无论成功或失败）
class RunFinishedEvent(BaseModel):
    # 事件类型标识，固定为 "run.finished"
    type: Literal["run.finished"] = "run.finished"
    # 运行的唯一 ID，与 RunStartedEvent 对应
    run_id: str
    # 运行状态："success"（成功）或 "failed"（失败）
    status: str
    # 失败原因（可选）：如 "exceeded_max_steps"（超过最大步骤数）、"cancelled"（被取消）、"llm_error"（LLM 错误）
    reason: str | None = None
    # 执行的步骤总数
    steps: int
    # 事件发生时间，ISO 8601 格式
    ts: str


# 步骤开始事件：当 agent 进入新的执行步骤时发布
class StepStartedEvent(BaseModel):
    # 事件类型标识，固定为 "step.started"
    type: Literal["step.started"] = "step.started"
    # 运行的唯一 ID
    run_id: str
    # 当前步骤编号，从 1 开始
    step: int
    # 事件发生时间，ISO 8601 格式
    ts: str


# 步骤结束事件：当 agent 完成一个执行步骤时发布
class StepFinishedEvent(BaseModel):
    # 事件类型标识，固定为 "step.finished"
    type: Literal["step.finished"] = "step.finished"
    # 运行的唯一 ID
    run_id: str
    # 当前步骤编号
    step: int
    # 事件发生时间，ISO 8601 格式
    ts: str


# 工具调用开始事件：当 agent 开始调用工具时发布
class ToolCallStartedEvent(BaseModel):
    # 事件类型标识，固定为 "tool.call_started"
    type: Literal["tool.call_started"] = "tool.call_started"
    # 运行的唯一 ID
    run_id: str
    # 工具调用的唯一 ID，用于关联工具调用的开始、结束、失败事件
    tool_use_id: str
    # 工具名称，如 "read_file"、"write_file"
    tool_name: str
    # 工具调用参数，字典格式，包含工具所需的所有参数
    params: dict[str, Any]
    # 事件发生时间，ISO 8601 格式
    ts: str


# 工具调用完成事件：当工具调用成功完成时发布
class ToolCallFinishedEvent(BaseModel):
    # 事件类型标识，固定为 "tool.call_finished"
    type: Literal["tool.call_finished"] = "tool.call_finished"
    # 运行的唯一 ID
    run_id: str
    # 工具调用的唯一 ID，与 ToolCallStartedEvent 对应
    tool_use_id: str
    # 工具名称
    tool_name: str
    # 工具执行耗时，单位毫秒
    elapsed_ms: int
    # 事件发生时间，ISO 8601 格式
    ts: str


# 工具调用失败事件：当工具调用失败时发布
class ToolCallFailedEvent(BaseModel):
    # 事件类型标识，固定为 "tool.call_failed"
    type: Literal["tool.call_failed"] = "tool.call_failed"
    # 运行的唯一 ID
    run_id: str
    # 工具调用的唯一 ID，与 ToolCallStartedEvent 对应
    tool_use_id: str
    # 工具名称
    tool_name: str
    # 错误类型："runtime_error"（运行时错误）、"timeout"（超时）、"schema_error"（参数校验错误）
    error_type: str
    # 错误消息，详细描述错误原因
    error_message: str
    # 工具执行耗时（失败前的时间），单位毫秒
    elapsed_ms: int
    # 事件发生时间，ISO 8601 格式
    ts: str


# LLM Token 事件：当 LLM 流式输出一个 token 时发布
class LlmTokenEvent(BaseModel):
    # 事件类型标识，固定为 "llm.token"
    type: Literal["llm.token"] = "llm.token"
    # 运行的唯一 ID
    run_id: str
    # 单个 token 文本，如 "Hello"、" "、"World"
    token: str
    # 事件发生时间，ISO 8601 格式
    ts: str


# LLM 使用量事件：当一次 LLM 调用完成后发布，记录 token 使用情况
class LlmUsageEvent(BaseModel):
    # 事件类型标识，固定为 "llm.usage"
    type: Literal["llm.usage"] = "llm.usage"
    # 运行的唯一 ID
    run_id: str
    # 输入 token 数量（发送给 LLM 的文本长度）
    input_tokens: int
    # 输出 token 数量（LLM 返回的文本长度）
    output_tokens: int
    # 从缓存读取的输入 token 数量（用于计算缓存节省）
    cache_read_input_tokens: int
    # 缓存创建的输入 token 数量（首次处理时创建的缓存）
    cache_creation_input_tokens: int
    # 事件发生时间，ISO 8601 格式
    ts: str


# LLM 模型选择事件：当选择使用哪个 LLM 模型时发布
class LlmModelSelectedEvent(BaseModel):
    # 事件类型标识，固定为 "llm.model_selected"
    type: Literal["llm.model_selected"] = "llm.model_selected"
    # 运行的唯一 ID
    run_id: str
    # 选择的模型名称，如 "claude-3-sonnet-20240229"
    model: str
    # 选择策略："static"（静态配置）、"rule_based"（基于规则）、"cost_budget"（基于成本预算）
    strategy: str
    # 事件发生时间，ISO 8601 格式
    ts: str


# 日志行事件：当有日志输出时发布
class LogLineEvent(BaseModel):
    # 事件类型标识，固定为 "log.line"
    type: Literal["log.line"] = "log.line"
    # 运行的唯一 ID
    run_id: str
    # 日志级别："DEBUG"、"INFO"、"WARNING"、"ERROR"
    level: str
    # 日志来源，通常是模块名或类名，如 "core.loop"
    source: str
    # 日志消息内容
    message: str
    # 事件发生时间，ISO 8601 格式
    ts: str


# 事件判别联合类型：根据 type 字段自动选择对应的事件模型
# 作用：当存在多种事件类型时，通过 type 字段自动识别并验证为对应的模型
Event = Annotated[
    # 所有事件类型的联合
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
    | LogLineEvent,
    # 指定使用 "type" 字段作为判别器，Pydantic 会根据 type 值自动选择对应的模型
    Discriminator("type"),
]
