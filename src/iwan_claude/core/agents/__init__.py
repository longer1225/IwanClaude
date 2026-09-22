"""
Agent 角色配置模块

该模块提供了 Agent 角色配置的加载和管理功能。

核心功能：
- 定义 AgentProfile 数据类，存储角色配置信息
- 实现 AgentProfileLoader，支持多级优先级查找角色配置

设计要点：
- 支持三级优先级：项目本地 > 用户全局 > 内建
- 配置文件使用 TOML 格式
- 角色配置包含名称、描述、系统提示词、允许的工具和模型

使用场景：
- SpawnAgentTool 根据 subagent_type 加载对应的角色配置
- 实现不同角色的 Agent，如 planner（规划师）、executor（执行者）、reviewer（审查者）
"""

from iwan_claude.core.agents.loader import AgentProfile, AgentProfileLoader

__all__ = ["AgentProfile", "AgentProfileLoader"]
