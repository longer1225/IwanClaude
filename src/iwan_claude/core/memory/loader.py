"""
上下文文件加载器

该模块提供了上下文文件的加载功能，用于读取项目中的 context.md 文件。

核心功能：
- 加载指定路径的上下文文件
- 支持用户路径展开（~）
- 路径不存在时返回空字符串

设计要点：
- 使用 path.expanduser() 处理用户路径（如 ~/context.md）
- 使用 utf-8 编码读取文件，确保中文等非 ASCII 字符正确处理
- 文件不存在或内容为空时返回空字符串，避免抛出异常
"""

from __future__ import annotations

from pathlib import Path


def load_context_file(path: Path) -> str:
    """
    加载上下文文件

    读取指定路径的上下文文件，支持用户路径展开。

    参数：
        path: 上下文文件的路径，可以包含 ~ 表示用户目录

    返回：
        str: 文件内容，如果文件不存在则返回空字符串

    实现步骤：
    1. 使用 expanduser() 展开用户路径（如 ~/context.md → /home/user/context.md）
    2. 检查文件是否存在
    3. 如果不存在，返回空字符串
    4. 如果存在，读取文件内容并去除首尾空白

    容错设计：
    - 文件不存在时不抛出异常，返回空字符串
    - 使用 utf-8 编码，确保中文等非 ASCII 字符正确处理

    使用示例：
        >>> from pathlib import Path
        >>> content = load_context_file(Path("~/context.md"))
        >>> print(content)
        "项目上下文信息..."
    """
    p = path.expanduser()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()
