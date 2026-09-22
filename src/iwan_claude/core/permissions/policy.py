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


# 检测 bash/PowerShell 命令是否操作 cwd 之外路径的正则规则列表
# （强制触发 ASK，不可被 allow_patterns 绕过）
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

# trust=deny（Layer 0 未信任目录）时被强制 DENY 的工具集：文件变更类 + 任意执行类。
# 显式枚举而非"非只读即禁"：todo_write/task_* 这类内部记账工具被禁会把会话
# 弄瘫，而信任模型只该锁"能碰到文件系统和解释器"的动作。新增文件写工具时
# 必须同步登记此集合（tests/unit/test_trust.py 有一条守护用例防漏）。
TRUST_DENY_FORBIDDEN_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_by_lines", "edit_by_search", "multi_edit",
    "insert_at_line", "delete_lines", "delete_file", "rename_file",
    "copy_file", "mkdir", "bash", "run_python",
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
    - git_*（仓库路径 + git_diff 的目标文件路径）

    【检查流程】
    1. 获取当前上下文生效的沙箱实例（会话隔离）
    2. 如果沙箱未启用，返回 False
    3. 对工具涉及的每个路径参数逐一检查
    4. 任一路径越界 → True（应触发强制 ASK）

    【注意事项】
    - 沙箱未启用时返回 False（表示无需检查）
    - 返回 True 表示路径不在允许范围内，应触发 ASK
    """
    # 获取沙箱实例（contextvar 优先，即当前会话的沙箱）
    sandbox = get_sandbox()
    # 如果沙箱未启用，返回 False（表示无需检查）
    if not sandbox.enabled:
        return False

    # 文件操作工具到路径参数名的映射（一个工具可能涉及多个路径参数）
    path_params: dict[str, list[str]] = {
        "read_file": ["path"],
        "write_file": ["path"],
        "list_dir": ["path"],
        "delete_file": ["path"],
        "rename_file": ["path"],
        "copy_file": ["path"],
        "mkdir": ["path"],
        "file_stat": ["path"],
        "file_exists": ["path"],
        "view_file": ["path"],
        "edit_by_lines": ["path"],
        "edit_by_search": ["path"],
        "insert_at_line": ["path"],
        "delete_lines": ["path"],
        # git 工具：仓库路径 + diff 的目标文件（file 参数可注入 --output 等写出路径）
        "git_status": ["path"],
        "git_log": ["path"],
        "git_diff": ["path", "file"],
        "git_commit": ["path"],
        "git_checkout": ["path"],
    }

    # 获取工具对应的路径参数名列表
    path_keys = path_params.get(tool_name)
    if not path_keys:
        return False

    for path_key in path_keys:
        if path_key not in params:
            continue
        path_str = str(params[path_key])
        if not path_str:
            continue
        # 检查路径是否在沙箱允许范围内（is_path_allowed=False 表示越界）
        if not sandbox.is_path_allowed(path_str):
            return True

    # 所有涉及的路径都在允许范围内
    return False


# 工具参数中用于生成审批缓存指纹的"关键字段"映射（未列出的工具用整个 params 的稳定序列化）
_FINGERPRINT_KEY: dict[str, str] = {
    "bash": "command",
    "run_python": "code",
}


# 路径归一化：反斜杠转正斜杠、折叠重复分隔符、去尾部分隔符、统一小写（Windows 大小写不敏感）
def _norm_path(value: str) -> str:
    norm = value.replace("\\", "/")
    norm = re.sub(r"/{2,}", "/", norm)
    return norm.rstrip("/").lower()


def param_fingerprint(tool_name: str, params: dict[str, Any]) -> str:
    """
    为工具参数生成归一化摘要（审批缓存键），实现"always allow 此命令/此路径"
    而非"always allow 此工具"

    【参数说明】
    - tool_name: str - 工具名称
    - params: dict[str, Any] - 工具参数

    【返回值】
    - str: 参数指纹（sha256 前 16 位十六进制）

    【归一化规则】
    - bash: 命令字符串折叠连续空白并去首尾空白
    - run_python: 代码原文（不折叠大小写）
    - git_diff: 仓库路径 + 目标文件路径联合归一化
    - 路径类工具: path 参数走路径归一化（转正斜杠、小写）
    - 其他: 对整个 params 按键排序后的稳定序列化
    """
    import hashlib

    raw: str
    if tool_name == "bash":
        raw = re.sub(r"\s+", " ", str(params.get("command", ""))).strip()
    elif tool_name == "run_python":
        raw = str(params.get("code", ""))
    elif tool_name == "git_diff":
        raw = (
            _norm_path(str(params.get("path", ".")))
            + "\x1f"
            + _norm_path(str(params.get("file", "")))
        )
    elif "path" in params:
        raw = _norm_path(str(params["path"]))
    else:
        key = _FINGERPRINT_KEY.get(tool_name)
        if key is not None:
            raw = str(params.get(key, ""))
        else:
            # 无已知关键字段：对全部参数做稳定序列化（按键排序，保证同参数恒等）
            raw = repr(sorted((str(k), str(v)) for k, v in params.items()))

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# run_python 代码中是否存在"写路径无法静态确定"的操作（AST 判定，需强制 ASK）
def run_python_dynamic_write(code: str) -> bool:
    from iwan_claude.core.tools.builtin.run_python import analyze_python_writes
    # 无法静态确定（含语法解析失败）→ fail-closed，按存在动态写处理
    _, dynamic = analyze_python_writes(code)
    return dynamic


# 沙箱维度的强制 ASK 判定：路径越界 或 run_python 存在无法静态验证的写操作
# （缓存/auto 模式均不可绕过）
def sandbox_forces_ask(tool_name: str, params: dict[str, Any]) -> bool:
    # 规则一：文件/git 工具的路径参数越出沙箱
    if _check_sandbox_path(tool_name, params):
        return True
    # 规则二：run_python 写路径无法静态确定（AST 判定，fail-closed）
    if tool_name == "run_python":
        try:
            return run_python_dynamic_write(str(params.get("code", "")))
        except Exception:
            # AST 分析本身异常（极端畸形代码）→ fail-closed
            return True
    return False


def evaluate(
    tool_name: str,
    params: dict[str, Any],
    policy: ToolPolicy | None = None,
    rules: Any = None,
) -> PermissionDecision:
    """
    对工具 + 参数执行完整静态策略评估，返回 ALLOW/DENY/ASK

    rules 为 PermissionRules（可为 None = 关闭规则引擎），完整评估序见
    evaluate_pre_cache / evaluate_post_cache 两个函数——本函数是"无缓存
    视角"的组合，与 PermissionManager.check_and_wait 共享同一实现，
    保证"静态评估"与"实际审批链"永不漂移。
    """
    from iwan_claude.core.permissions.rules import PermissionRules
    _rules: PermissionRules | None = rules
    pre = evaluate_pre_cache(tool_name, params, policy, _rules)
    if pre is not None:
        return pre.decision
    return evaluate_post_cache(tool_name, params, policy, _rules).decision


# 缓存之前的评估层级：deny 地板与一切"强制 ASK"——命中即返回，永不回头
def evaluate_pre_cache(
    tool_name: str,
    params: dict[str, Any],
    policy: ToolPolicy | None,
    rules: Any = None,
) -> TierVerdict | None:
    """
    评估 Tier 1（deny 类）与 Tier 2（强制 ASK 类）；None 表示未定、可进缓存

    【学习要点】规则引擎的三档结果在这两层里的落点不同：
    - deny 规则 = Tier 1 地板（与 legacy deny/blacklist 同档，先匹配先赢）
    - 显式 ask 规则 = Tier 2 强制（盖过指纹缓存——对齐官方"ask 盖 allow"）
    - 动态语法/wrapper/未覆盖段的 ASK = 非强制：不进这里，留给 post 层，
      这样用户对整条命令的 always-allow 仍能生效（比官方逐子命令存规则
      更保守：没有部分覆盖的捷径）。
    """
    from iwan_claude.core.permissions.rules import (
        PermissionRules,
        evaluate_bash_rules,
        evaluate_tool_rules,
    )
    _rules: PermissionRules | None = rules
    command = str(params.get("command", "")) if tool_name == "bash" else ""

    # Tier 1: deny 类（DENY 地板：缓存/auto/allow 规则都翻不了）
    if command and policy:
        for pat in policy.deny_patterns:
            if re.search(pat, command):
                return TierVerdict(
                    PermissionDecision.DENY, True, "legacy deny_pattern", False)
    sandbox = get_sandbox()
    if command and sandbox.enabled:
        for pat in sandbox.command_blacklist:
            if re.search(pat, command):
                return TierVerdict(
                    PermissionDecision.DENY, True, "sandbox command_blacklist", False)
    if _rules is not None and not _rules.is_empty():
        outcome = (
            evaluate_bash_rules(command, _rules) if tool_name == "bash"
            else evaluate_tool_rules(tool_name, _rules)
        )
        if outcome.matched and outcome.decision == PermissionDecision.DENY:
            return TierVerdict(PermissionDecision.DENY, True, outcome.detail, True)
        if outcome.matched and outcome.forced and outcome.decision == PermissionDecision.ASK:
            return TierVerdict(PermissionDecision.ASK, True, outcome.detail, True)

    # Tier 2: 强制 ASK（不可被缓存/auto 绕过）
    if command and matches_outside_cwd(command):
        return TierVerdict(PermissionDecision.ASK, True, "outside-cwd 启发式", False)
    if sandbox_forces_ask(tool_name, params):
        return TierVerdict(PermissionDecision.ASK, True, "沙箱强制 ASK", False)

    return None


# 缓存未命中之后的评估层级：allow 类与工具默认策略
def evaluate_post_cache(
    tool_name: str,
    params: dict[str, Any],
    policy: ToolPolicy | None,
    rules: Any = None,
) -> TierVerdict:
    """
    评估 Tier 3（allow 类）与 Tier 4（工具默认）

    规则引擎在此层的两条铁律（对齐官方并更保守）：
    1. allow 规则只对"每一段都被覆盖"的命令生效（逐段求值）；
    2. 一旦规则引擎参与过裁定（matched=True），legacy 正则 allow 让位——
       配置了现代规则还让旧正则兜底放行，等于两套规则互相越权。
    另外 legacy allow_patterns 现在只允许命中"单段命令"：复合命令
    （&&/;/| 拆分后多段）不再被单个正则放行——这正是旧方案最大的绕过面。
    """
    from iwan_claude.core.permissions.rules import (
        PermissionRules,
        evaluate_bash_rules,
        evaluate_tool_rules,
        split_segments,
    )
    _rules: PermissionRules | None = rules
    command = str(params.get("command", "")) if tool_name == "bash" else ""

    if policy is None:
        policy = DEFAULT_POLICIES.get(tool_name)

    rule_matched = False
    if _rules is not None and not _rules.is_empty():
        outcome = (
            evaluate_bash_rules(command, _rules) if tool_name == "bash"
            else evaluate_tool_rules(tool_name, _rules)
        )
        rule_matched = outcome.matched
        if outcome.matched and outcome.decision == PermissionDecision.ALLOW:
            return TierVerdict(PermissionDecision.ALLOW, False, outcome.detail, True)

    if not rule_matched:
        # Tier 3: legacy allow_patterns（bash only）——仅单段命令有资格
        if command and policy:
            segs = split_segments(command)
            if segs is not None and len(segs) <= 1:
                for pat in policy.allow_patterns:
                    if re.search(pat, command):
                        return TierVerdict(
                            PermissionDecision.ALLOW, False, "legacy allow_pattern", False)

    # Tier 4: 工具默认策略
    if policy is not None:
        return TierVerdict(policy.default, False, "tool default", rule_matched)
    return TierVerdict(_UNKNOWN_TOOL_DEFAULT, False, "unknown tool default", rule_matched)


# 单层评估裁定结果（forced=True 表示缓存/模式不可绕过）
@dataclass
class TierVerdict:
    """
    权限评估链的单次裁定

    字段：
        decision: ALLOW/DENY/ASK
        forced: True = 该结果不可被指纹缓存与 auto 模式改写
        detail: 人类可读的命中原因（日志/审批弹窗/审计）
        from_rules: True = 来自声明式规则引擎（参与"规则在场时 legacy 让位"的裁决）
    """
    decision: PermissionDecision
    forced: bool
    detail: str
    from_rules: bool

