"""
事件总线模块 - 定义事件、命令和 JSON-RPC 协议封装

【学习要点】
1. 事件系统：定义系统中所有事件类型
2. 命令系统：定义客户端向服务器发送的命令
3. JSON-RPC 协议：实现 JSON-RPC 2.0 协议的请求和响应封装
4. 判别联合：使用 Pydantic 的 Discriminator 实现多态类型

【核心组件】
- Event: 事件判别联合类型（根据 type 字段区分）
- Command: 命令判别联合类型（根据 type 字段区分）
- JsonRpcRequest: JSON-RPC 请求
- JsonRpcSuccess: JSON-RPC 成功响应
- JsonRpcError: JSON-RPC 错误响应

【事件类型】
- CoreStartedEvent: 核心服务启动
- RunStartedEvent/RunFinishedEvent: 运行开始/结束
- StepStartedEvent/StepFinishedEvent: 步骤开始/结束
- ToolCallStartedEvent/ToolCallFinishedEvent/ToolCallFailedEvent: 工具调用
- LlmTokenEvent/LlmUsageEvent/LlmModelSelectedEvent: LLM 相关事件
- LogLineEvent: 日志事件
- SessionCreatedEvent/SessionClosedEvent 等: 会话相关事件
- PermissionRequestedEvent/PermissionGrantedEvent/PermissionDeniedEvent: 权限相关事件
- SubagentStartedEvent/SubagentFinishedEvent: 子 Agent 相关事件
- SkillInvokedEvent: Skill 调用事件

【命令类型】
- PingCommand: 心跳检测
- AgentRunCommand: 运行 Agent
- EventSubscribeCommand: 订阅事件
- SessionCreateCommand/SessionSendMessageCommand/SessionCloseCommand: 会话管理
- PermissionRespondCommand: 权限响应
- SessionCompactCommand: 上下文压缩
- SessionCheckpointListCommand/SessionCheckpointRestoreCommand: 检查点管理

【JSON-RPC 错误码】
- PARSE_ERROR (-32700): 解析错误
- INVALID_REQUEST (-32600): 请求格式错误
- METHOD_NOT_FOUND (-32601): 方法不存在
- INVALID_PARAMS (-32602): 参数错误
- INTERNAL_ERROR (-32603): 服务器内部错误

【设计目的】
提供统一的事件和命令定义，
便于客户端和服务器之间的通信。
"""
from iwan_claude.core.bus.commands import Command, PingCommand, PongResult
from iwan_claude.core.bus.envelope import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JsonRpcError,
    JsonRpcErrorObject,
    JsonRpcRequest,
    JsonRpcSuccess,
    make_error,
)
from iwan_claude.core.bus.events import (
    CoreStartedEvent,
    Event,
    LlmModelSelectedEvent,
    LlmTokenEvent,
    LlmUsageEvent,
    LogLineEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    ToolCallFailedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)

# 统一导出的公共 API
__all__ = [
    "Command",
    "CoreStartedEvent",
    "Event",
    "LogLineEvent",
    "LlmModelSelectedEvent",
    "LlmTokenEvent",
    "LlmUsageEvent",
    "RunFinishedEvent",
    "RunStartedEvent",
    "StepFinishedEvent",
    "StepStartedEvent",
    "ToolCallFailedEvent",
    "ToolCallFinishedEvent",
    "ToolCallStartedEvent",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JsonRpcError",
    "JsonRpcErrorObject",
    "JsonRpcRequest",
    "JsonRpcSuccess",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PingCommand",
    "PongResult",
    "make_error",
]
