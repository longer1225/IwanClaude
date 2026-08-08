"""
格式化工具函数模块

本模块提供用于 TUI 文本格式化的辅助函数，包括：
- 字符串预览截断
- 参数字典格式化
- 工具参数摘要生成

这些函数从 app.py 中提取而来，职责单一，便于测试和复用。
"""

from __future__ import annotations

import json
from typing import Any


def _preview(s: str, n: int) -> str:
    """
    文本预览函数：将字符串截断到指定长度，超出部分用省略号表示。

    【设计选择】
    - 使用 "…" (U+2026) 作为省略号字符，比 "..." 更紧凑美观
    - 当原始字符串长度 <= n 时直接返回原字符串，不做修改
    - 当原始字符串长度 > n 时截断并追加省略号，总长度为 n+1

    【参数】
    s: str - 原始字符串
    n: int - 最大长度阈值（不含省略号）

    【返回值】
    str: 截断后的字符串。若 len(s) > n，返回 s[:n] + "…"；否则返回 s 本身。

    【边界情况】
    - n <= 0 时，s[:n] 返回空字符串，结果为 "…" 或空字符串
    - s 为空字符串时，len(s)=0 <= n，直接返回空字符串
    - 多字节字符（如中文）按 Python 字符串长度截断，可能截断半个字符（调用方需注意）

    【使用示例】
    >>> _preview("hello world", 5)
    'hello…'
    >>> _preview("hi", 5)
    'hi'
    """
    return s[:n] + "…" if len(s) > n else s


def _params_str(params: dict[str, Any]) -> str:
    """
    将工具参数字典转换为格式化的 JSON 字符串。

    【设计选择】
    - 使用 json.dumps 配合 ensure_ascii=False：保留中文字符不转义为 \\uXXXX
    - 使用 indent=2：生成人类可读的缩进格式，便于 TUI 中展示
    - 输入为空字典时返回 "{}"

    【参数】
    params: dict[str, Any] - 工具参数字典，键为参数名，值为参数值

    【返回值】
    str: 格式化后的 JSON 字符串，例如：
        '{\n  "path": "/etc/passwd"\n}'

    【边界情况】
    - params 为 None 时会抛出 TypeError（调用方需确保传入 dict）
    - 嵌套结构会被完整序列化
    - 非 JSON 标准类型（如 datetime）会抛出 TypeError

    【使用示例】
    >>> _params_str({"path": "/etc/passwd"})
    '{\n  "path": "/etc/passwd"\n}'
    """
    return json.dumps(params, ensure_ascii=False, indent=2)


def _param_summary(tool_name: str, params: dict[str, Any], max_len: int = 72) -> str:
    """
    从工具参数中提取最适合摘要展示的关键字段，生成简短的参数摘要。

    【设计选择】
    - 为每种工具预定义了关键参数名映射（keys_by_tool），优先展示工具最核心的参数
    - 当工具未在映射中定义时，回退取 params 中前 2 个键值对
    - 使用 repr() 格式化值，保留字符串引号，便于区分值的类型
    - 最终通过 _preview 截断到 max_len，防止摘要过长

    【参数】
    tool_name: str - 工具名称（如 "bash", "read_file" 等），用于查找关键参数
    params: dict[str, Any] - 工具参数字典
    max_len: int - 摘要最大长度，默认 72 字符（适配终端窄宽度场景）

    【返回值】
    str: 参数摘要字符串，格式如 "command='ls -la'" 或 "path='/etc/passwd'"

    【关键参数映射表】
    - read_file   → path
    - write_file  → path
    - list_dir    → path, max_depth
    - bash        → command
    - note_save   → content

    【边界情况】
    - params 中不含映射键时，回退取前 2 个键值对
    - params 为空字典时，返回空字符串
    - 生成的摘要超过 max_len 时自动截断并添加省略号

    【使用示例】
    >>> _param_summary("bash", {"command": "ls -la /etc"})
    "command='ls -la /etc'"
    >>> _param_summary("read_file", {"path": "/etc/passwd", "encoding": "utf-8"})
    "path='/etc/passwd'"
    """
    keys_by_tool = {
        "read_file": ("path",),
        "write_file": ("path",),
        "list_dir": ("path", "max_depth"),
        "bash": ("command",),
        "note_save": ("content",),
    }
    keys = keys_by_tool.get(tool_name, ())
    parts = [f"{key}={params[key]!r}" for key in keys if key in params]
    if not parts:
        parts = [f"{key}={value!r}" for key, value in list(params.items())[:2]]
    return _preview(", ".join(parts), max_len)