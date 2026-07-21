"""
内存/上下文模块

该模块提供了项目上下文管理功能，支持加载项目配置和上下文文件。

核心功能：
- 加载 CLAUDE.md 项目配置文件
- 加载 context.md 上下文文件

设计要点：
- CLAUDE.md 包含项目描述、技术栈、代码风格、架构、规则等信息
- 这些信息会被注入到系统提示词中，帮助 AI 理解项目
- 支持中英文混合的配置项解析
"""

from iwan_claude.core.memory.claude_md import (
    ClaudeMdConfig,
    load_claude_md,
    generate_default_claude_md,
    render_claude_md_prompt,
)
from iwan_claude.core.memory.loader import load_context_file

__all__ = [
    "ClaudeMdConfig",
    "load_claude_md",
    "generate_default_claude_md",
    "render_claude_md_prompt",
    "load_context_file",
]
