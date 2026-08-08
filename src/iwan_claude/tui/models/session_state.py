"""
会话状态数据模型模块

本模块定义了 _SessionState 数据类，用于保存单个会话的 TUI UI 状态。
支持多会话（多标签页）切换时的状态保存与恢复。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from textual.widget import Widget


@dataclass
class _SessionState:
    """
    会话 UI 状态 - 保存单个会话的 TUI 状态

    【字段说明】
    - session_id: str - 会话唯一标识符，用于区分不同会话
    - title: str - 会话标题，显示在标签页上
    - widgets: list[Widget] - 该会话的日志 widget 列表，切换会话时用于恢复显示
    - auto_mode: str - 自动模式（off / read_only / on），控制 Agent 自主操作程度
    - effort_level: str - 努力等级（minimal / low / medium / high / max），影响 token 消耗
    - model_preset: str - 模型预设（fast / balanced / powerful），决定使用的模型
    - busy: bool - 是否正在运行（执行中），用于防止重复提交
    - last_context_pct: float - 上次上下文占用率百分比，用于压缩判断
    - current_llm: Any - 当前 LLM 流式输出块（LLMStreamBlock | None），跟踪正在生成的回复
    - pending_tool_blocks: dict - 待完成的工具调用块映射，键为 block_id
    - pending_permission_blocks: dict - 待处理的权限审批块映射，键为 block_id
    - subagent_run_ids: dict - 子 Agent 运行 ID 映射，跟踪子进程执行
    - subagent_start_times: dict - 子 Agent 开始时间映射，用于计算耗时

    【设计目的】
    每个会话有独立的 UI 状态，切换会话时保存当前状态并恢复目标会话状态。
    这样可以支持多标签页并行，每个会话独立运行互不干扰。

    【default_factory 使用说明】
    以下字段使用 default_factory=list/dict 而非直接赋值 [] 或 {}：
    - widgets: 使用 default_factory=list，避免可变默认值陷阱（mutable default argument trap）
    - pending_tool_blocks: 使用 default_factory=dict
    - pending_permission_blocks: 使用 default_factory=dict
    - subagent_run_ids: 使用 default_factory=dict
    - subagent_start_times: 使用 default_factory=dict

    【多会话切换流程】
    1. 切换前：将当前会话的 widgets、busy 等状态存入 _SessionState
    2. 切换时：从目标会话的 _SessionState 恢复 widgets 到界面
    3. 切换后：目标会话继续运行，互不影响
    """

    session_id: str
    title: str = ""
    widgets: list[Widget] = field(default_factory=list)
    auto_mode: str = "off"
    effort_level: str = "medium"
    model_preset: str = "balanced"
    busy: bool = False
    last_context_pct: float = 0.0
    current_llm: Any = None  # LLMStreamBlock | None
    pending_tool_blocks: dict[str, Any] = field(default_factory=dict)
    pending_permission_blocks: dict[str, Any] = field(default_factory=dict)
    subagent_run_ids: dict[str, str] = field(default_factory=dict)
    subagent_start_times: dict[str, float] = field(default_factory=dict)