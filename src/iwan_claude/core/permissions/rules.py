"""
权限规则引擎 - 对齐 Claude Code 的 deny→ask→allow 声明式规则模型

【学习要点】
1. 为什么不用正则：正则黑名单是"猜哪些危险"（best-effort，方向反了），
   规则模型是"声明哪些安全 + 其余一律问"（default-ask）。正则里 `.` 能
   匹配任何字符导致误放/误杀都难以推理；前缀/glob 规则的语义可以逐字
   审查——这是权限系统从"字符串玄学"升级为"可审计契约"的关键一步。
2. 三类规则（deny/ask/allow）共享同一套匹配语义，只有结果档位不同：
   deny 是地板（任何模式/缓存/allow 都翻不了），ask 盖 allow（预批准
   ≠ 免确认），allow 只在"所有子命令都被覆盖"时才生效。先匹配先赢。
3. 复合命令逐段求值：`git status && rm -rf /` 不能被 `git *` 规则放行。
   && || ; | 拆成子命令，每段各自过规则——这是官方文档 issue #29491
   确认的语义，也是前缀规则最常见的绕过点。
4. 两类 fail-closed 形态拒绝 allow 资格（宁可弹问不可假放）：
   a) 动态语法：$()、反引号、大括号扩展、不平衡引号——静态分析看不见
      真正会执行什么（社区实测这些可绕过前缀规则）；
   b) exec wrapper（watch/setsid/nohup/xargs…、find -exec/-delete）：
      前缀匹配放行 wrapper 本身 = 放行了它里面的任意命令。
"""
from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field

from iwan_claude.core.permissions.policy import PermissionDecision

log = logging.getLogger(__name__)

# 规则字符串形态：tool 或 tool(specifier)，如 "bash" / "bash(git status:*)"
# 工具名允许尾部/中间通配（"mcp__*"、"mcp__github__*"），首字符必须是字母/下划线
# ——裸 "*" 与 "*foo" 非法：从固定锚点开始的通配才可逐字审查
_RULE_SHAPE_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_*]*)(?:\((.+)\))?$")

# 允许出现在 allow 规则里但仍"不可前缀放行"的 exec 包装器：
# 放行了 `watch rm …` 的前缀就等于放行了任意命令，官方对这类命令
# 在 Manual 模式必弹（前缀规则永远不能自动批准）
_EXEC_WRAPPERS: frozenset[str] = frozenset({
    "watch", "setsid", "ionice", "nice", "nohup", "flock", "xargs", "env",
    "sudo", "runas", "start", "start-process", "start-job", "invoke-command",
})

# 动态语法标记：命中任一 → 整条命令失去被 allow 规则覆盖的资格
_DYNAMIC_SYNTAX_RES: list[re.Pattern[str]] = [
    re.compile(r"\$\("),          # 命令替换 $( )
    re.compile(r"`[^`]*`"),       # 反引号命令替换
    re.compile(r"\{[^}]*\}"),     # 大括号展开 {a,b}（保守：连字典字面量一起禁）
    re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*"),  # 变量展开 $VAR / ${VAR}
    re.compile(r"[<>]"),          # 重定向（CC 把 > 视为写；< 一并保守拒绝）
]

# 复合命令分隔符：&& || ; | 以及换行（引号内的分隔符不算，见 _split_once）
_SEPARATOR_RE = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


@dataclass
class PermissionRules:
    """
    声明式规则集合（deny → ask → allow，先匹配先赢）

    每条规则形如 "bash"（工具级，盖过整个工具）或 "bash(git status:*)"
    （带 specifier 的命令级规则）。非 bash 工具本期只支持工具级——
    文件工具的 gitignore 语法（Read(./.env)）在规划中，解析时直接报错
    是为了让配置笔误在启动时暴露，而不是运行时静默不生效。
    """
    deny: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.deny or self.ask or self.allow)


@dataclass
class RuleOutcome:
    """
    规则引擎对一次工具调用的裁定

    字段：
        decision: ALLOW/DENY/ASK
        forced: True 表示"缓存/模式不可绕过"（deny 与显式 ask 规则）
        detail: 命中说明（规则原文/段名），供日志与审批弹窗展示
        matched: 是否真的有规则参与了裁定（False = 规则引擎弃权，
                 交给后续层级：指纹缓存、legacy patterns、默认策略）
    """
    decision: PermissionDecision
    forced: bool
    detail: str
    matched: bool


def parse_rule(rule: str) -> tuple[str, str | None]:
    """
    解析规则字符串为 (工具名, specifier|None)，非法时抛 ValueError

    合法形态与语义（对齐 Claude Code）：
    - "bash"                 工具级规则
    - "mcp__*"               工具名 glob（仅 deny/ask 合法，allow 必须精确点名）
    - "bash(git status)"     specifier 无通配符 = 整串精确匹配
    - "bash(git status:*)"   遗留前缀语法 = 匹配 "git status" 本身或以其为
                             第一个词起点的任意命令
    - "bash(git * diff)"     位置通配：* 匹配任意文本（含空格），但 * 前后
                             必须是空格边界——"git*diff" 这种粘连形态非法
    """
    m = _RULE_SHAPE_RE.match(rule.strip())
    if not m:
        raise ValueError(f"规则格式非法: {rule!r}（期望 tool 或 tool(specifier)）")
    tool, spec = m.group(1).lower(), m.group(2)
    if "*" in tool and spec is not None:
        raise ValueError(
            f"规则 {rule!r}: 工具名通配只能整条生效（mcp__*），不能与 specifier 混用"
        )
    if tool != "bash" and spec is not None:
        raise ValueError(
            f"规则 {rule!r}: 目前仅 bash 支持 specifier，"
            "其他工具请用工具级规则（文件路径 gitignore 语法在规划中）"
        )
    if spec is not None:
        _validate_spec(spec, rule)
    return tool, spec


# specifier 静态校验：:* 遗留形态、* 的空格边界要求
def _validate_spec(spec: str, rule: str) -> None:
    if not spec.strip():
        raise ValueError(f"规则 {rule!r}: specifier 为空")
    # 尾部 :* 是遗留前缀语法，等价 " *"——归一化后不再出现在通配校验里
    core = spec[:-2] if spec.endswith(":*") else spec
    idx = core.find("*")
    while idx != -1:
        before_ok = idx == 0 or core[idx - 1] == " "
        after_ok = idx == len(core) - 1 or core[idx + 1] == " "
        if not (before_ok and after_ok):
            raise ValueError(
                f"规则 {rule!r}: 通配符 * 两侧必须是空格（'git*diff' 非法，"
                "'git * diff' 合法）"
            )
        idx = core.find("*", idx + 1)


# 编译 specifier 为匹配函数：对"归一化后的命令段"做 fullmatch
def _compile_spec(spec: str) -> re.Pattern[str]:
    # :* 尾部 → 展开成 "X *" 语义，并额外接受 X 本身（git commit:* 匹配 git commit）
    base = spec[:-2] if spec.endswith(":*") else spec
    legacy_prefix = spec.endswith(":*")
    parts = base.split(" ")
    # 每段按 * 再切，其余全部 re.escape——规则里没有别的花样
    piece_res: list[re.Pattern[str]] = []
    for p in parts:
        sub = ".*".join(re.escape(s) for s in p.split("*")) if "*" in p else re.escape(p)
        piece_res.append(re.compile(sub))
    body = r"\s+".join(pc.pattern for pc in piece_res)
    if legacy_prefix:
        # `X:*` 覆盖三形态：X 本身、"X <args>"（空格分隔）、"X:<suffix>"（npm run test:unit）
        pattern = rf"^{body}(?::.*|\s+.*\S.*)?$"
    else:
        pattern = rf"^{body}$"
    return re.compile(pattern)


# 归一化命令段：折叠空白、去首尾（规则匹配永远在归一化文本上进行）
def _normalize_segment(seg: str) -> str:
    return re.sub(r"\s+", " ", seg).strip()


# 命令中是否存在动态语法（命中即丧失 allow 资格，fail-closed 交给 ASK）
def has_dynamic_syntax(command: str) -> bool:
    return any(p.search(command) for p in _DYNAMIC_SYNTAX_RES)


def split_segments(command: str) -> list[str] | None:
    """
    按 shell 操作符拆子命令；引号跟踪失败（不平衡）返回 None 表示不可解析

    覆盖 && || ; | 与换行；引号与单引号内的操作符是字面量不是分隔符。
    返回 None 时调用方必须 fail-closed（宁可 ASK 不猜语义）。
    """
    segs: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        two = command[i:i + 2]
        if two in ("&&", "||"):
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in (";", "|", "\n"):
            # `|` 单字符：`||` 已在上一分支处理；PowerShell 的 `|` 同理
            if ch == "|" and i + 1 < n and command[i + 1] == "|":
                i += 2
                continue
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segs.append("".join(buf))
    if quote is not None:
        return None  # 引号不平衡：无法确定边界，fail-closed
    cleaned = [s for s in (_normalize_segment(x) for x in segs) if s]
    return cleaned or None


# 判断子命令段是否以"不可前缀放行"的形态开头（exec wrapper / find -exec|-delete）
def is_wrapper_segment(seg: str) -> bool:
    tokens = seg.split(" ")
    first = tokens[0].lower()
    if first in _EXEC_WRAPPERS:
        return True
    if first == "find" and any(t in ("-exec", "-execdir", "-ok", "-delete") for t in tokens):
        return True
    return False


def evaluate_bash_rules(command: str, rules: PermissionRules) -> RuleOutcome:
    """
    对一条 bash 命令执行 deny→ask→allow 规则裁定（逐段求值）

    返回 matched=False 表示规则引擎弃权（没有规则与该工具相关），调用方
    继续走指纹缓存/legacy patterns/默认策略。裁定语义：
    - 任一段命中 deny → DENY（forced，地板）
    - 任一段命中 ask → ASK（forced：显式 ask 规则盖过 allow 与缓存）
    - 全段都命中 allow 且无可前缀放行的 wrapper/动态语法 → ALLOW
    - 其余（有段没规则管）→ ASK（非 forced：默认问，但"整条命令"的
      always-allow 缓存仍可覆盖——比官方逐子命令存规则更严格更安全）
    """
    # 工具级规则先短路（bash 裸规则盖过整个工具，不看内容）
    tool_level: dict[str, list[str]] = {"deny": [], "ask": [], "allow": []}
    specs: dict[str, list[tuple[str, re.Pattern[str]]]] = {"deny": [], "ask": [], "allow": []}
    for kind, bucket in (("deny", rules.deny), ("ask", rules.ask), ("allow", rules.allow)):
        for raw in bucket:
            tool, spec = parse_rule(raw)
            if tool != "bash":
                continue  # 本函数只处理 bash；其他工具的规则由调用方做工具级判断
            if spec is None:
                tool_level[kind].append(raw)
            else:
                specs[kind].append((raw, _compile_spec(spec)))
    for kind, decision in (("deny", PermissionDecision.DENY), ("ask", PermissionDecision.ASK),
                           ("allow", PermissionDecision.ALLOW)):
        if tool_level[kind]:
            forced = kind != "allow"
            return RuleOutcome(decision, forced, f"工具级规则 {tool_level[kind][0]!r}", True)

    if not any(specs[k] for k in ("deny", "ask", "allow")):
        return RuleOutcome(PermissionDecision.ASK, False, "", False)  # 弃权

    segs = split_segments(command)
    if segs is None:
        # 无法拆解 ≠ 一定危险，但没有拆解就没有逐段 allow：按"未覆盖"处理
        return RuleOutcome(
            PermissionDecision.ASK, False, "命令结构不可解析（逐段求值不可用）", True)
    dynamic = has_dynamic_syntax(command)

    any_ask_hit = False
    any_uncovered = dynamic  # 动态语法整条丧失 allow 资格
    uncovered_detail = (
        "含动态语法（$()/反引号/brace/$变量/重定向），拒绝静态放行" if dynamic else "")
    for seg in segs:
        for raw, pat in specs["deny"]:
            if pat.fullmatch(seg):
                return RuleOutcome(
                    PermissionDecision.DENY, True, f"deny 规则 {raw!r} 命中段 {seg!r}", True)
        for raw, pat in specs["ask"]:
            if pat.fullmatch(seg):
                any_ask_hit = True
        covered = any(pat.fullmatch(seg) for _, pat in specs["allow"])
        if covered and is_wrapper_segment(seg):
            covered = False
            detail = f"段 {seg!r} 以 exec wrapper 开头，拒绝前缀放行"
            uncovered_detail = uncovered_detail or detail
        if not covered:
            any_uncovered = True
            if not uncovered_detail:
                uncovered_detail = f"段 {seg!r} 未命中任何 allow 规则"

    if any_ask_hit:
        return RuleOutcome(
            PermissionDecision.ASK, True, "命中显式 ask 规则（盖过 allow 与缓存）", True)
    if any_uncovered:
        return RuleOutcome(PermissionDecision.ASK, False, uncovered_detail, True)
    return RuleOutcome(PermissionDecision.ALLOW, False, "所有子命令段均被 allow 规则覆盖", True)


# 工具名 glob 通配判定：无 * 时退化为精确相等，有 * 时按 fnmatchcase
def _tool_name_matches(pattern: str, tool_name: str) -> bool:
    # 统一小写再比较：规则名在 parse_rule 已 lower，Windows 的 fnmatch.fnmatch
    # 还会按 normcase 折大小写而 Linux 不会——跨平台不一致的匹配是安全审计的噩梦
    name = tool_name.lower()
    # 精确名（绝大多数规则）走 ==：比 fnmatch 快，也避免 fnmatch 把 [ ] 当字符类
    if "*" not in pattern:
        return name == pattern
    return fnmatch.fnmatchcase(name, pattern)


# 对非 bash 工具执行工具级规则（specifier 本期不支持，见 parse_rule 校验）
# 工具名 glob 通配（mcp__* / mcp__github__*）仅对 deny/ask 开放——allow 必须精确点名
def evaluate_tool_rules(tool_name: str, rules: PermissionRules) -> RuleOutcome:
    """
    工具级 deny/ask/allow 裁定：非 bash 工具的全部规则入口

    bash 工具的规则裁定在 evaluate_bash_rules 里（那里同时处理工具级
    短路，所以本函数只应被非 bash 工具调用）。无相关规则时 matched=False 弃权。

    【设计】allow 桶跳过含 * 的规则——宽 allow = 给"一类没逐个审过的工具"
    发免死金牌，正是官方明确禁止的绕过面；deny/ask 是收紧方向，glob 合法。
    配置加载期已拦一次，这里是纵深防御（规则可能来自 policy_file 等旁路）。
    """
    for kind, decision, bucket in (
        ("deny", PermissionDecision.DENY, rules.deny),
        ("ask", PermissionDecision.ASK, rules.ask),
        ("allow", PermissionDecision.ALLOW, rules.allow),
    ):
        for raw in bucket:
            tool, _ = parse_rule(raw)
            if kind == "allow" and "*" in tool:
                # 宽 allow 在运行期不可信（见上）：不参与裁定
                log.warning("permission: ignoring glob in allow rule %r (allow must be exact)", raw)
                continue
            if _tool_name_matches(tool, tool_name):
                forced = kind != "allow"
                return RuleOutcome(decision, forced, f"工具级规则 {raw!r}", True)
    return RuleOutcome(PermissionDecision.ASK, False, "", False)
