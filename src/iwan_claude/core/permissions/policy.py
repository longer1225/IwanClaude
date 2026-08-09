"""
权限策略模块 - 定义权限策略和评估逻辑

【学习要点】
1. 权限决策枚举：ALLOW（允许）、DENY（拒绝）、ASK（询问用户）
2. 启发式规则：检测 bash 命令是否操作 cwd 之外的路径
3. 工具策略：定义每个工具的默认策略和模式匹配规则
4. 策略评估：4 层静态策略评估流程
5. 沙箱检查：验证文件操作是否在沙箱允许范围内

【核心组件】
- PermissionDecision: 权限决策枚举
- ToolPolicy: 工具策略数据类
- OUTSIDE_CWD_HEURISTICS: 启发式规则列表
- DEFAULT_POLICIES: 默认工具策略
- evaluate(): 策略评估函数

【策略评估流程】
1. deny_patterns: 拒绝模式匹配（bash only）
2. OUTSIDE_CWD_HEURISTICS: 检测操作 cwd 之外路径（强制 ASK）
3. sandbox path check: 沙箱路径检查（强制 ASK）
4. allow_patterns: 允许模式匹配（bash only）
5. tool default: 工具默认策略

【安全设计】
- 强制 ASK 规则不可被 allow_patterns 绕过
- 沙箱路径检查确保文件操作在允许范围内
- 默认策略为 ASK，确保安全
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from iwan_claude.core.sandbox import get_sandbox


class PermissionDecision(StrEnum):
    """
    权限决策枚举 - 定义权限审批的三种结果

    【枚举值】
    - ALLOW: 允许工具调用
    - DENY: 拒绝工具调用
    - ASK: 询问用户是否允许

    【设计目的】
    提供统一的权限决策类型，便于策略评估和权限管理。

    【使用场景】
    - 策略评估函数返回权限决策
    - 权限管理器根据决策执行相应操作
    """
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# 检测 bash/PowerShell 命令是否操作 cwd 之外路径的正则规则列表（强制触发 ASK，不可被 allow_patterns 绕过）
# 同时覆盖 Unix 和 Windows 路径模式
OUTSIDE_CWD_HEURISTICS: list[str] = [
    # === Unix 路径 ===
    r"(^|\s)/[^\s]",              # Unix 绝对路径（如 /etc/passwd）
    r"(^|\s)~",                   # 波浪号 home（如 ~/.bashrc）
    r"(^|\s)\.\.(/|$|\s)",        # 父目录遍历（如 ../etc/passwd）
    r"\$\{?HOME\b",               # $HOME 环境变量
    r"\$\{?PWD\b",                # $PWD 环境变量
    r"(^|\s|;|&&|\|\|)cd(\s|$)",  # Unix cd 命令
    # === Windows 路径 ===
    r"(^|\s)[A-Za-z]:[\\/]",      # Windows 盘符绝对路径（如 C:\Users、D:\data）
    r"(^|\s)\\\\[^\s]",           # UNC 路径（如 \\server\share）
    r"(?i)%USERPROFILE%",         # %USERPROFILE% 环境变量
    r"(?i)%TEMP%",                # %TEMP% / %TMP%
    r"(?i)%APPDATA%",             # %APPDATA%
    r"(?i)%LOCALAPPDATA%",        # %LOCALAPPDATA%
    r"(?i)%WINDIR%",              # %WINDIR% / %SystemRoot%
    r"(?i)%SYSTEMROOT%",          # %SYSTEMROOT%
    r"(?i)%PROGRAMFILES%",        # %PROGRAMFILES%
    # === PowerShell 目录切换命令 ===
    r"(?i)(^|\s|;|&&|\|\|)Set-Location(\s|$)",   # PowerShell cd 等价
    r"(?i)(^|\s|;|&&|\|\|)Push-Location(\s|$)",  # PowerShell pushd
    r"(?i)(^|\s|;|&&|\|\|)Pop-Location(\s|$)",   # PowerShell popd
    # === 通用父目录遍历（Windows 反斜杠）===
    r"(^|\s)\.\.[\\/]",           # Windows 父目录遍历（如 ..\..\secret）
]

# 编译正则表达式列表（预编译提高性能）
_OUTSIDE_CWD_RE: list[re.Pattern[str]] = [re.compile(p) for p in OUTSIDE_CWD_HEURISTICS]


# 网络外传命令正则（block_network_commands=True 时由 bash 工具二次校验阻断）
# 覆盖：Unix 网络工具 + PowerShell 网络命令
NETWORK_COMMAND_PATTERNS: list[str] = [
    r"(?i)\bcurl\b",                # curl
    r"(?i)\bwget\b",                # wget
    r"(?i)\bnc\b",                  # netcat
    r"(?i)\bssh\b",                 # ssh
    r"(?i)\bscp\b",                 # scp
    r"(?i)\bftp\b",                 # ftp
    r"(?i)\btelnet\b",              # telnet
    r"(?i)\bInvoke-WebRequest\b",   # PowerShell iwr
    r"(?i)\bInvoke-RestMethod\b",   # PowerShell irm
    r"(?i)\bStart-BitsTransfer\b",  # PowerShell Bits
]

# 预编译网络命令正则（提高匹配性能）
_NETWORK_CMD_RE: list[re.Pattern[str]] = [re.compile(p) for p in NETWORK_COMMAND_PATTERNS]


def matches_network_command(command: str) -> bool:
    """
    判断 bash 命令是否包含网络外传命令

    【参数说明】
    - command: str - bash/PowerShell 命令字符串

    【返回值】
    - bool: True 表示命令包含网络外传命令，False 表示不包含

    【设计目的】
    供 bash 工具在 block_network_commands=True 时调用，
    阻断 curl/wget/nc/ssh 等网络命令，防止数据外传。

    【与命令黑名单的区别】
    - command_blacklist: 硬 DENY（不可被用户批准绕过），覆盖破坏性命令
    - NETWORK_COMMAND_PATTERNS: 工具内阻断（返回 permission_denied），
      可通过 sandbox.block_network_commands=false 关闭
    """
    return any(pat.search(command) for pat in _NETWORK_CMD_RE)


def matches_outside_cwd(command: str) -> bool:
    """
    判断 bash 命令是否命中 outside-cwd 启发式规则

    【参数说明】
    - command: str - bash 命令字符串

    【返回值】
    - bool: True 表示命令可能操作 cwd 之外的路径，False 表示命令在 cwd 内操作

    【设计目的】
    检测危险命令，强制触发用户确认，防止恶意操作。

    【启发式规则】
    Unix:
    - 绝对路径：/etc/passwd
    - 波浪号 home：~/.bashrc
    - 父目录遍历：../etc/passwd
    - 环境变量：$HOME, $PWD
    - 显式 cd：cd /etc

    Windows:
    - 盘符绝对路径：C:\\Users\\...
    - UNC 路径：\\\\server\\share
    - 环境变量：%USERPROFILE%, %TEMP%, %APPDATA%, %WINDIR% 等
    - PowerShell 目录切换：Set-Location, Push-Location, Pop-Location
    - Windows 父目录遍历：..\\..\\secret

    【安全设计】
    命中规则的命令强制触发 ASK，不可被 allow_patterns 绕过。

    【示例】
    ```python
    matches_outside_cwd("ls")              # False
    matches_outside_cwd("ls /etc")         # True
    matches_outside_cwd("cd ..")           # True
    matches_outside_cwd("type C:\\\\...")  # True
    matches_outside_cwd("echo %TEMP%")     # True
    ```
    """
    # 检查命令是否匹配任何 outside-cwd 规则
    return any(pat.search(command) for pat in _OUTSIDE_CWD_RE)


@dataclass
class ToolPolicy:
    """
    工具策略数据类 - 定义单个工具的权限策略

    【字段说明】
    - default: PermissionDecision - 默认权限决策
    - allow_patterns: list[str] - 允许模式列表（bash only）
    - deny_patterns: list[str] - 拒绝模式列表（bash only）

    【设计目的】
    为每个工具定义独立的权限策略，支持模式匹配。

    【模式匹配】
    - allow_patterns: 匹配成功则允许工具调用（bash only）
    - deny_patterns: 匹配成功则拒绝工具调用（bash only）
    - 模式使用正则表达式

    【示例】
    ```python
    policy = ToolPolicy(
        default=PermissionDecision.ASK,
        allow_patterns=["^ls\\s+"],
        deny_patterns=["rm\\s+-rf"]
    )
    ```
    """
    # 默认权限决策
    default: PermissionDecision
    # 允许模式列表（bash only）
    allow_patterns: list[str] = field(default_factory=list)
    # 拒绝模式列表（bash only）
    deny_patterns: list[str] = field(default_factory=list)


# 默认工具策略映射
DEFAULT_POLICIES: dict[str, ToolPolicy] = {
    "bash":              ToolPolicy(default=PermissionDecision.ASK),
    "write_file":        ToolPolicy(default=PermissionDecision.ASK),
    "read_file":         ToolPolicy(default=PermissionDecision.ALLOW),
    "list_dir":          ToolPolicy(default=PermissionDecision.ALLOW),
    "note_save":         ToolPolicy(default=PermissionDecision.ALLOW),
    "list_checkpoints":  ToolPolicy(default=PermissionDecision.ALLOW),
    "restore_checkpoint": ToolPolicy(default=PermissionDecision.ALLOW),
}

# 未在 DEFAULT_POLICIES 中登记的工具的兜底策略
_UNKNOWN_TOOL_DEFAULT = PermissionDecision.ASK

# Auto Mode 下自动批准的只读工具集合（read_only / on 模式都适用）
AUTO_MODE_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "list_dir",
    "search",
    "file_exists",
    "file_stat",
    "find_files",
    "git_status",
    "git_diff",
    "git_log",
    "note_save",
    "list_checkpoints",
    "restore_checkpoint",
    "task_get",
    "task_list",
    "cache_get",
    "cache_stats",
})

# Auto Mode 为 on 时额外自动批准的写工具集合（保守白名单）
AUTO_MODE_WRITE_ALLOW_TOOLS: frozenset[str] = frozenset({
    "write_file",
    "edit_by_search",
    "edit_by_lines",
    "insert_at_line",
    "delete_lines",
    "generate_docs",
    "changelog",
})

# bash 参数中展示用的关键字段映射（用于生成审批提示）
_PREVIEW_KEY: dict[str, str] = {
    "bash":       "command",
    "read_file":  "path",
    "write_file": "path",
    "list_dir":   "path",
    "note_save":  "content",
}
# 参数预览的最大长度
_PREVIEW_MAX = 60


def param_preview(tool_name: str, params: dict[str, Any]) -> str:
    """
    为权限审批事件生成人类可读的参数摘要

    【参数说明】
    - tool_name: str - 工具名称
    - params: dict[str, Any] - 工具参数

    【返回值】
    - str: 参数摘要（最大 60 字符）

    【设计目的】
    生成简洁的参数摘要，用于权限审批提示。

    【预览规则】
    1. 如果工具在 _PREVIEW_KEY 中有映射，使用对应字段
    2. 否则使用整个参数字典
    3. 超过 60 字符时截断并添加省略号

    【示例】
    ```python
    param_preview("bash", {"command": "ls -la"})
    # 返回: "command='ls -la'"

    param_preview("read_file", {"path": "/etc/passwd"})
    # 返回: "path='/etc/passwd'"

    param_preview("unknown_tool", {"key1": "value1", "key2": "value2"})
    # 返回: "{'key1': 'value1', 'key2': 'value2'}"（如果超过 60 字符则截断）
    ```
    """
    # 获取工具对应的预览关键字段
    key = _PREVIEW_KEY.get(tool_name)
    if key and key in params:
        # 如果有关键字段，使用该字段的值
        val = str(params[key])
        # 如果值超过最大长度，截断并添加省略号
        if len(val) > _PREVIEW_MAX:
            val = val[:_PREVIEW_MAX] + "…"
        return f"{key}={val!r}"
    # 如果没有关键字段，使用整个参数字典
    snippet = str(params)
    return snippet[:_PREVIEW_MAX] if len(snippet) > _PREVIEW_MAX else snippet


def _check_sandbox_path(tool_name: str, params: dict[str, Any]) -> bool:
    """
    检查文件操作工具的路径是否在沙箱允许范围内

    【参数说明】
    - tool_name: str - 工具名称
    - params: dict[str, Any] - 工具参数

    【返回值】
    - bool: True 表示路径不在沙箱允许范围内，False 表示路径在允许范围内

    【设计目的】
    确保文件操作工具只在沙箱允许的目录内操作，防止越权访问。

    【支持的工具】
    - read_file, write_file, list_dir, delete_file
    - rename_file, copy_file, mkdir
    - file_stat, file_exists, view_file
    - edit_by_lines, edit_by_search, insert_at_line, delete_lines

    【检查流程】
    1. 获取沙箱实例
    2. 如果沙箱未启用，返回 False
    3. 根据工具名称获取路径参数名
    4. 如果路径参数存在，检查路径是否在沙箱允许范围内
    5. 返回检查结果

    【注意事项】
    - 沙箱未启用时返回 False（表示无需检查）
    - 返回 True 表示路径不在允许范围内，应触发 ASK
    """
    # 获取沙箱实例
    sandbox = get_sandbox()
    # 如果沙箱未启用，返回 False（表示无需检查）
    if not sandbox.enabled:
        return False

    # 文件操作工具到路径参数名的映射
    path_params = {
        "read_file": "path",
        "write_file": "path",
        "list_dir": "path",
        "delete_file": "path",
        "rename_file": "path",
        "copy_file": "path",
        "mkdir": "path",
        "file_stat": "path",
        "file_exists": "path",
        "view_file": "path",
        "edit_by_lines": "path",
        "edit_by_search": "path",
        "insert_at_line": "path",
        "delete_lines": "path",
    }

    # 获取工具对应的路径参数名
    path_key = path_params.get(tool_name)
    if path_key and path_key in params:
        # 获取路径字符串
        path_str = str(params[path_key])
        # 检查路径是否在沙箱允许范围内（返回 True 表示不在允许范围内）
        return not sandbox.is_path_allowed(path_str)

    # 工具不涉及文件操作或路径参数不存在
    return False


def evaluate(
    tool_name: str,
    params: dict[str, Any],
    policy: ToolPolicy | None = None,
) -> PermissionDecision:
    """
    对工具 + 参数执行 4 层静态策略评估，返回 ALLOW/DENY/ASK

    【参数说明】
    - tool_name: str - 工具名称
    - params: dict[str, Any] - 工具参数
    - policy: ToolPolicy | None - 工具策略（默认为 None，使用 DEFAULT_POLICIES）

    【返回值】
    - PermissionDecision: 权限决策（ALLOW/DENY/ASK）

    【评估流程】
    Tier 1: deny_patterns（bash only）→ DENY
    Tier 2: OUTSIDE_CWD_HEURISTICS（bash only）→ ASK（强制，不可绕过）
    Tier 2.5: sandbox path check → ASK（强制，不可绕过）
    Tier 3: allow_patterns（bash only）→ ALLOW
    Tier 4: tool default → 默认决策

    【安全设计】
    - deny_patterns 优先级最高，匹配成功直接拒绝
    - OUTSIDE_CWD_HEURISTICS 和 sandbox path check 强制 ASK，不可被 allow_patterns 绕过
    - allow_patterns 优先级低于强制 ASK 规则
    - 默认策略为 ASK，确保安全

    【示例】
    ```python
    evaluate("bash", {"command": "ls"})
    # 返回: PermissionDecision.ASK（默认策略）

    evaluate("read_file", {"path": "/etc/passwd"})
    # 返回: PermissionDecision.ALLOW（默认策略）

    evaluate("bash", {"command": "rm -rf /"})
    # 返回: PermissionDecision.ASK（命中 OUTSIDE_CWD_HEURISTICS）
    ```
    """
    # 如果没有指定策略，使用默认策略
    if policy is None:
        policy = DEFAULT_POLICIES.get(tool_name)

    # 如果工具不在默认策略中，使用兜底策略
    if policy is None:
        return _UNKNOWN_TOOL_DEFAULT

    # 获取 bash 命令（非 bash 工具为空字符串）
    command = str(params.get("command", "")) if tool_name == "bash" else ""

    # Tier 1: deny_patterns（bash only）- 拒绝模式匹配
    # 合并两个来源：policy.deny_patterns + sandbox.command_blacklist（硬 DENY，不可被用户批准绕过）
    if command:
        # 1a. 策略文件中定义的 deny_patterns
        for pat in policy.deny_patterns:
            if re.search(pat, command):
                return PermissionDecision.DENY
        # 1b. 沙箱配置的 command_blacklist（进程内强化新增）
        # 从沙箱动态读取，确保配置变更后立即生效
        sandbox = get_sandbox()
        if sandbox.enabled:
            for pat in sandbox.command_blacklist:
                if re.search(pat, command):
                    return PermissionDecision.DENY

    # Tier 2: OUTSIDE_CWD_HEURISTICS — 强制 ASK，不可被 allow_patterns 绕过
    if command and matches_outside_cwd(command):
        return PermissionDecision.ASK

    # Tier 2.5: sandbox path check — 如果路径不在沙箱允许范围内，强制 ASK
    if _check_sandbox_path(tool_name, params):
        return PermissionDecision.ASK

    # Tier 3: allow_patterns（bash only）- 允许模式匹配
    if command:
        for pat in policy.allow_patterns:
            if re.search(pat, command):
                return PermissionDecision.ALLOW

    # Tier 4: tool default - 工具默认策略
    return policy.default
