"""
CLAUDE.md 配置解析器

该模块实现了 CLAUDE.md 项目配置文件的加载、解析和渲染功能。

核心功能：
- 加载项目根目录下的 CLAUDE.md 文件
- 解析 Markdown 格式的配置项
- 将配置渲染为系统提示词格式

设计要点：
- 支持中英文混合的配置项标题识别
- 使用正则表达式按二级标题分割章节
- 如果没有找到项目描述，使用文件前 500 字符作为默认描述
- 配置项会被注入到系统提示词中，帮助 AI 理解项目上下文
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ClaudeMdConfig:
    """
    CLAUDE.md 配置数据类

    存储从 CLAUDE.md 文件中解析出的所有配置项。

    属性：
        project_description: 项目描述，帮助 AI 了解项目背景
        tech_stack: 技术栈列表，如 ["Python", "FastAPI", "SQLAlchemy"]
        code_style: 代码风格规范，如 PEP 8、文件名命名规则等
        architecture: 架构说明，描述项目的整体架构设计
        custom_prompt: 自定义提示词，会被添加到系统提示词中
        rules: 项目规则列表，如代码审查规则、安全规则等
        preferences: 偏好设置字典，key-value 形式的配置
    """
    project_description: str = ""
    tech_stack: list[str] = field(default_factory=list)
    code_style: str = ""
    architecture: str = ""
    custom_prompt: str = ""
    rules: list[str] = field(default_factory=list)
    preferences: dict[str, str] = field(default_factory=dict)


# CLAUDE.md 文件名常量
_CLAUDE_MD_NAME = "CLAUDE.md"


def load_claude_md(project_root: Path | None = None) -> ClaudeMdConfig:
    """
    加载 CLAUDE.md 配置文件

    从项目根目录加载 CLAUDE.md 文件，如果文件不存在则返回空配置。

    参数：
        project_root: 项目根目录路径，默认为当前工作目录

    返回：
        ClaudeMdConfig: 解析后的配置对象

    实现步骤：
    1. 如果未指定 project_root，使用当前工作目录
    2. 构建 CLAUDE.md 的完整路径
    3. 如果文件不存在，返回空配置
    4. 读取文件内容并调用 _parse_claude_md() 解析

    使用示例：
        >>> config = load_claude_md()
        >>> print(config.project_description)
        "这是一个 Python 项目..."
    """
    if project_root is None:
        project_root = Path.cwd()
    
    claude_md_path = project_root / _CLAUDE_MD_NAME
    if not claude_md_path.exists():
        return ClaudeMdConfig()
    
    content = claude_md_path.read_text(encoding="utf-8")
    return _parse_claude_md(content)


def _parse_claude_md(content: str) -> ClaudeMdConfig:
    """
    解析 CLAUDE.md 内容

    将 Markdown 格式的配置文件解析为 ClaudeMdConfig 对象。

    参数：
        content: CLAUDE.md 文件的文本内容

    返回：
        ClaudeMdConfig: 解析后的配置对象

    解析策略：
    1. 使用正则表达式按 "## " 分割章节
    2. 遍历每个章节，提取标题和正文
    3. 根据标题关键词匹配配置项
    4. 支持中英文标题识别（如 "Project Description" 和 "项目描述"）

    匹配规则：
    - description/项目 → project_description
    - tech/技术/栈 → tech_stack
    - style/规范 → code_style
    - architecture/架构 → architecture
    - prompt/提示 → custom_prompt
    - rules/规则 → rules
    - preferences/偏好 → preferences

    容错设计：
    - 如果没有找到项目描述，使用文件前 500 字符作为默认值
    - 空章节会被跳过
    - 空白行和空内容会被忽略
    """
    config = ClaudeMdConfig()
    
    # 按二级标题分割章节，保留标题内容
    sections = re.split(r'^##\s+', content, flags=re.MULTILINE)
    
    for section in sections:
        section = section.strip()
        if not section:
            continue
        
        # 分离标题和正文
        first_line_end = section.find('\n')
        if first_line_end == -1:
            title = section
            body = ""
        else:
            title = section[:first_line_end].strip()
            body = section[first_line_end:].strip()
        
        title_lower = title.lower()
        
        # 根据标题关键词匹配配置项
        if "description" in title_lower or "项目" in title:
            config.project_description = body
        elif "tech" in title_lower or "技术" in title_lower or "栈" in title:
            config.tech_stack = [line.strip() for line in body.split('\n') if line.strip()]
        elif "style" in title_lower or "规范" in title_lower:
            config.code_style = body
        elif "architecture" in title_lower or "架构" in title_lower:
            config.architecture = body
        elif "prompt" in title_lower or "提示" in title_lower:
            config.custom_prompt = body
        elif "rules" in title_lower or "规则" in title_lower:
            config.rules = [line.strip() for line in body.split('\n') if line.strip()]
        elif "preferences" in title_lower or "偏好" in title_lower:
            # 解析键值对格式：key: value
            for line in body.split('\n'):
                line = line.strip()
                if line and ':' in line:
                    key, value = line.split(':', 1)
                    config.preferences[key.strip()] = value.strip()
    
    # 如果没有找到项目描述，使用文件前 500 字符作为默认值
    if not config.project_description:
        config.project_description = content[:500].strip()
    
    return config


def generate_default_claude_md() -> str:
    """
    生成默认的 CLAUDE.md 模板

    返回一个包含所有配置项的 Markdown 模板字符串，供用户参考。

    返回：
        str: 默认的 CLAUDE.md 模板内容

    使用示例：
        >>> print(generate_default_claude_md())
        # CLAUDE.md
        ...
    """
    return """# CLAUDE.md

项目配置文件，用于指导 AI 理解项目结构和规范。

## Project Description

在此描述您的项目。

## Tech Stack

- Python 3.11+
- FastAPI
- SQLAlchemy

## Code Style

- 使用类型提示
- 遵循 PEP 8
- 文件名使用 snake_case
- 类名使用 PascalCase

## Architecture

在此描述您的项目架构。

## Custom Prompt

在此添加自定义提示词，AI 会在处理此项目时使用。

## Rules

- 规则 1
- 规则 2

## Preferences

key: value
"""


def render_claude_md_prompt(config: ClaudeMdConfig) -> str:
    """
    将配置渲染为系统提示词格式

    将 ClaudeMdConfig 对象转换为自然语言描述的提示词，
    用于注入到系统提示词中，帮助 AI 理解项目上下文。

    参数：
        config: CLAUDE.md 配置对象

    返回：
        str: 格式化的提示词字符串

    渲染逻辑：
    - 项目描述 → "项目描述：..."
    - 技术栈 → "技术栈：..."（逗号分隔）
    - 代码风格 → "代码风格规范：..."
    - 架构 → "架构说明：..."
    - 规则 → "项目规则：" + 列表项
    - 偏好 → "偏好设置：" + 键值对列表
    - 自定义提示 → "自定义提示：..."

    使用示例：
        >>> config = ClaudeMdConfig(
        ...     project_description="一个 Python Web 项目",
        ...     tech_stack=["Python", "FastAPI"]
        ... )
        >>> print(render_claude_md_prompt(config))
        项目描述：
        一个 Python Web 项目
        ...
    """
    parts = []
    
    if config.project_description:
        parts.append(f"项目描述：\n{config.project_description}\n")
    
    if config.tech_stack:
        parts.append(f"技术栈：\n{', '.join(config.tech_stack)}\n")
    
    if config.code_style:
        parts.append(f"代码风格规范：\n{config.code_style}\n")
    
    if config.architecture:
        parts.append(f"架构说明：\n{config.architecture}\n")
    
    if config.rules:
        parts.append("项目规则：")
        for rule in config.rules:
            parts.append(f"- {rule}")
        parts.append("")
    
    if config.preferences:
        parts.append("偏好设置：")
        for key, value in config.preferences.items():
            parts.append(f"- {key}: {value}")
        parts.append("")
    
    if config.custom_prompt:
        parts.append(f"自定义提示：\n{config.custom_prompt}\n")
    
    return "\n".join(parts).strip()
