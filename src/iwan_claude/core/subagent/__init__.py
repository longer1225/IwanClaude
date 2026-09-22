"""
子 Agent 模块

该模块提供了子 Agent 的管理和调用功能。

核心组件：
- BackgroundTaskRegistry: 后台任务注册表，管理并发子 Agent 的生命周期
- SpawnAgentTool: 生成子 Agent 的工具，支持前台和后台模式
- AgentResultTool: 获取子 Agent 执行结果的工具
- SpawnAgentsTool: 批量生成多个子 Agent 的工具（并行执行）
- BatchResultTool: 获取批量子 Agent 执行结果的工具
- CancelAgentTool: 取消正在运行的子 Agent 的工具

设计要点：
- 支持子 Agent 的嵌套调用（最多 2 层）
- 支持后台异步执行，通过 run_id 异步获取结果
- 支持批量并行执行，使用 Semaphore 控制并发数
- 支持任务取消和超时控制
- 支持任务分组（batch）管理
"""

from iwan_claude.core.subagent.registry import BackgroundTaskRegistry
from iwan_claude.core.subagent.tool import (
    AgentResultTool,
    BatchResultTool,
    CancelAgentTool,
    SpawnAgentTool,
    SpawnAgentsTool,
)

__all__ = [
    "BackgroundTaskRegistry",
    "SpawnAgentTool",
    "SpawnAgentsTool",
    "AgentResultTool",
    "BatchResultTool",
    "CancelAgentTool",
]
