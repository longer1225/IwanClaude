"""
内存/上下文模块

该模块提供了项目上下文管理功能，支持加载项目配置和上下文文件。

核心功能：
- 加载 CLAUDE.md 项目配置文件
- 加载 context.md 上下文文件
- 长期记忆存储（跨会话的用户偏好、历史决策）
- 向量记忆存储（历史对话的语义检索）
- 统一记忆管理器（整合三层记忆）

三层记忆架构：
1. 项目级记忆：CLAUDE.md（项目描述、技术栈、规则）
2. 长期记忆：LongTermMemory（用户偏好、决策，关键词搜索）
3. 向量记忆：VectorMemory（历史对话，语义搜索）
4. 短期记忆：当前会话上下文（在 SessionManager 中管理）

设计要点：
- CLAUDE.md 包含项目描述、技术栈、代码风格、架构、规则等信息
- 这些信息会被注入到系统提示词中，帮助 AI 理解项目
- 支持中英文混合的配置项解析
- 长期记忆用 JSONL 持久化，向量记忆复用 RAG 基础设施
"""

from iwan_claude.core.memory.claude_md import (
    ClaudeMdConfig,
    load_claude_md,
    generate_default_claude_md,
    render_claude_md_prompt,
)
from iwan_claude.core.memory.loader import load_context_file
from iwan_claude.core.memory.long_term import LongTermMemory, MemoryEntry
from iwan_claude.core.memory.manager import MemoryManager
from iwan_claude.core.memory.vector_memory import VectorMemory

__all__ = [
    "ClaudeMdConfig",
    "load_claude_md",
    "generate_default_claude_md",
    "render_claude_md_prompt",
    "load_context_file",
    "LongTermMemory",
    "MemoryEntry",
    "MemoryManager",
    "VectorMemory",
]
