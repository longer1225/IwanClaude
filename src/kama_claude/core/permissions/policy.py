# 导入 Python 3.7+ 的类型注解特性
from __future__ import annotations

# 导入正则表达式模块（用于匹配命令模式）
import re

# 导入 dataclass 和 field（用于定义数据类，简化类定义）
from dataclasses import dataclass, field

# 导入 StrEnum（字符串枚举，值本身就是字符串）
from enum import StrEnum

# 导入 Any（表示任意类型）
from typing import Any


# 权限决策的枚举类（三种可能的结果）
# StrEnum 是字符串枚举，每个成员的值就是它的名字（字符串形式）
class PermissionDecision(StrEnum):
    ALLOW = "allow"  # 允许执行
    DENY = "deny"    # 拒绝执行
    ASK = "ask"      # 需要询问用户


# 检测 bash 命令是否操作当前工作目录（cwd）之外路径的正则规则列表
# 这些规则是"强制触发 ASK"的，即使有 allow_patterns 也不能绕过
# 目的：防止 LLM 执行危险操作（如删除系统文件、访问敏感目录）
OUTSIDE_CWD_HEURISTICS: list[str] = [
    r"(^|\s)/[^\s]",              # 绝对路径（以 / 开头），如: /etc/passwd
    r"(^|\s)~",                   # 波浪号 home 目录，如: ~/.ssh/
    r"(^|\s)\.\.(/|$|\s)",        # 父目录遍历，如: ../secret/
    r"\$\{?HOME\b",               # $HOME 环境变量，如: $HOME/.bashrc
    r"\$\{?PWD\b",                # $PWD 环境变量，如: $PWD/../
    r"(^|\s|;|&&|\|\|)cd(\s|$)",  # 显式 cd 命令，如: cd /tmp
]

# 将正则规则编译为正则表达式对象（编译一次，多次使用，提高性能）
_OUTSIDE_CWD_RE: list[re.Pattern[str]] = [re.compile(p) for p in OUTSIDE_CWD_HEURISTICS]


# 判断 bash 命令是否命中任何 outside-cwd 启发式规则
# 参数 command: bash 命令字符串
# 返回值: True（命中规则）或 False（未命中）
def matches_outside_cwd(command: str) -> bool:
    # 遍历所有正则模式，只要有一个匹配就返回 True
    # any() 是短路求值：找到第一个匹配的就停止
    return any(pat.search(command) for pat in _OUTSIDE_CWD_RE)


# 工具级别的权限策略数据类
# @dataclass 装饰器会自动生成 __init__、__repr__、__eq__ 等方法
@dataclass
class ToolPolicy:
    # 默认权限决策（ALLOW/DENY/ASK）
    default: PermissionDecision
    
    # 允许模式列表（仅适用于 bash 工具）
    # 如果命令匹配任何允许模式，直接返回 ALLOW
    allow_patterns: list[str] = field(default_factory=list)
    
    # 拒绝模式列表（仅适用于 bash 工具）
    # 如果命令匹配任何拒绝模式，直接返回 DENY（优先级最高）
    deny_patterns: list[str] = field(default_factory=list)


# 内置工具的默认权限策略字典
# key: 工具名，value: ToolPolicy 对象
DEFAULT_POLICIES: dict[str, ToolPolicy] = {
    "bash":       ToolPolicy(default=PermissionDecision.ASK),       # bash 命令默认询问用户
    "write_file": ToolPolicy(default=PermissionDecision.ASK),       # 写文件默认询问用户
    "read_file":  ToolPolicy(default=PermissionDecision.ALLOW),     # 读文件默认允许（安全）
    "list_dir":   ToolPolicy(default=PermissionDecision.ALLOW),     # 列目录默认允许（安全）
    "note_save":  ToolPolicy(default=PermissionDecision.ALLOW),     # 保存笔记默认允许（安全）
}

# 未在 DEFAULT_POLICIES 中登记的未知工具的兜底策略
# 默认 ASK（保守策略，未知工具需要用户确认）
_UNKNOWN_TOOL_DEFAULT = PermissionDecision.ASK

# 权限审批事件中展示参数时，每个工具对应的关键字段映射
# 用于生成人类可读的参数摘要（如：bash 显示 command，write_file 显示 path）
_PREVIEW_KEY: dict[str, str] = {
    "bash":       "command",   # bash 命令显示 command 字段
    "read_file":  "path",      # 读文件显示 path 字段
    "write_file": "path",      # 写文件显示 path 字段
    "list_dir":   "path",      # 列目录显示 path 字段
    "note_save":  "content",   # 保存笔记显示 content 字段
}

# 参数摘要的最大长度（超过则截断并添加省略号）
_PREVIEW_MAX = 60


# 为权限审批事件生成人类可读的参数摘要
# 参数 tool_name: 工具名
# 参数 params: 工具调用参数字典
# 返回值: 简洁的参数字符串（用于显示给用户）
def param_preview(tool_name: str, params: dict[str, Any]) -> str:
    # 获取该工具对应的预览关键字段
    key = _PREVIEW_KEY.get(tool_name)
    
    # 如果有关键字段且该字段存在于参数中
    if key and key in params:
        # 将参数值转换为字符串
        val = str(params[key])
        
        # 如果超过最大长度，截断并添加省略号
        if len(val) > _PREVIEW_MAX:
            val = val[:_PREVIEW_MAX] + "…"
        
        # 返回格式：key=value（带引号）
        return f"{key}={val!r}"
    
    # 如果没有关键字段，直接显示整个参数字典
    snippet = str(params)
    
    # 同样进行截断处理
    return snippet[:_PREVIEW_MAX] if len(snippet) > _PREVIEW_MAX else snippet


# 对工具 + 参数执行 4 层静态策略评估，返回权限决策
# 参数 tool_name: 工具名
# 参数 params: 工具调用参数字典
# 参数 policy: 可选的工具策略（默认为 None，使用 DEFAULT_POLICIES）
# 返回值: PermissionDecision（ALLOW/DENY/ASK）
# 
# 评估流程（按优先级从高到低）：
# Tier 1: deny_patterns → 如果匹配，直接 DENY
# Tier 2: OUTSIDE_CWD_HEURISTICS → 如果匹配，强制 ASK（不可被绕过）
# Tier 3: allow_patterns → 如果匹配，直接 ALLOW
# Tier 4: tool default → 使用工具的默认策略
def evaluate(
    tool_name: str,
    params: dict[str, Any],
    policy: ToolPolicy | None = None,
) -> PermissionDecision:
    # 如果没有提供策略，从 DEFAULT_POLICIES 中查找
    if policy is None:
        policy = DEFAULT_POLICIES.get(tool_name)

    # 如果还是找不到策略（未知工具），使用兜底策略
    if policy is None:
        return _UNKNOWN_TOOL_DEFAULT

    # 提取 bash 命令（仅 bash 工具需要，其他工具为空字符串）
    command = str(params.get("command", "")) if tool_name == "bash" else ""

    # Tier 1: deny_patterns（仅适用于 bash）
    # 如果命令匹配任何拒绝模式，直接返回 DENY（优先级最高）
    if command:
        for pat in policy.deny_patterns:
            if re.search(pat, command):
                return PermissionDecision.DENY

    # Tier 2: OUTSIDE_CWD_HEURISTICS（仅适用于 bash）
    # 如果命令操作 cwd 之外的路径，强制返回 ASK（不可被任何缓存或模式绕过）
    if command and matches_outside_cwd(command):
        return PermissionDecision.ASK

    # Tier 3: allow_patterns（仅适用于 bash）
    # 如果命令匹配任何允许模式，直接返回 ALLOW
    if command:
        for pat in policy.allow_patterns:
            if re.search(pat, command):
                return PermissionDecision.ALLOW

    # Tier 4: tool default
    # 使用工具的默认策略
    return policy.default
