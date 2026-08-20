"""
widgets 包初始化模块

从各 widget 子模块中导入类，统一对外暴露。
"""

from iwan_claude.tui.widgets.llm_stream import LLMStreamBlock
from iwan_claude.tui.widgets.tool_call import ToolCallBlock
from iwan_claude.tui.widgets.permission import PermissionSelect, PermissionBlock
from iwan_claude.tui.widgets.slash_complete import SlashCompleteWidget
from iwan_claude.tui.widgets.chat_input import ChatTextArea
from iwan_claude.tui.widgets.skill_confirm import SkillConfirm

__all__ = [
    "LLMStreamBlock", "ToolCallBlock",
    "PermissionSelect", "PermissionBlock",
    "SlashCompleteWidget", "ChatTextArea",
    "SkillConfirm",
]