"""
【学习要点】声明式配置 → 可执行规格（HookSpec）的转换层：只做校验与 argv 化，不执行任何东西
【设计】配置笔误（event 拼错 / timeout 非法 / command 为空）全部在启动期抛 ValueError，
由 config 层转成 SystemExit；argv 化（shell=False）是防注入的关键——见 _to_argv 注释
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Any

# 支持的生命周期事件（本期两点；Notification/Stop 等留扩展位）
HOOK_EVENTS: tuple[str, ...] = ("PreToolUse", "PostToolUse")

# hook 子进程默认超时（秒）——挂死的 hook 绝不能拖死审批链
DEFAULT_HOOK_TIMEOUT_S = 10.0


@dataclass
class HookSpec:
    """
    单条 hook 的可执行规格（已经过校验的 [[hooks]] 条目）

    【字段说明】
    - event: HOOK_EVENTS 之一，决定挂载的生命周期点
    - matcher: 精确工具名 或 "*"（本期不做 regex/多值）
    - argv: 已拆分的命令行（shell=False 直接 exec）
    - timeout_s: 子进程超时（秒，>0）

    【设计目的】
    spec 层与执行层（runner.py）分离：校验在启动期集中完成，
    运行期不再有任何"配置是否合法"的分支——非法配置活不到运行时。
    """
    event: str
    matcher: str
    argv: list[str]
    timeout_s: float


# 把配置里的 shell 风格 command 字符串安全拆成 argv
def _to_argv(command: str, rule_desc: str) -> list[str]:
    """
    shlex 拆分 command 为 argv；拆不出可执行体时抛 ValueError

    【安全设计】hook 以 shell=False 执行拆分结果——如果直接
    `shell=True` 跑原字符串，配置里混入的 `guard.py && rm -rf /` 会真的执行
    rm；argv 化后这种拼接变成"找不到叫这个名字的可执行文件"的启动失败。
    官方 Claude Code 允许 shell 字符串是因为它有 OS 沙箱兜底，我们 Windows
    路线短期没有等价物，所以先收紧（docs/design/hooks.md 决策记录 3）。
    """
    text = command.strip()
    if not text:
        raise ValueError(f"hook {rule_desc}: command 不能为空")
    try:
        if os.name != "nt":
            argv = shlex.split(text, posix=True)
        else:
            # Windows 走 posix=False：反斜杠不是转义符（"C:\path" 原样保留），
            # 代价是 token 自带引号——剥掉外层成对引号即可模拟 shell 的取词结果
            argv = [
                t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
                for t in shlex.split(text, posix=False)
            ]
    except ValueError as exc:
        raise ValueError(f"hook {rule_desc}: command 无法解析（引号不平衡？）: {exc}") from exc
    argv = [a for a in argv if a]
    if not argv:
        raise ValueError(f"hook {rule_desc}: command 拆分后为空")
    return argv


# 校验并转换单个 [[hooks]] dict 条目（配置笔误在此拦截）
def _parse_one(entry: dict[str, Any], idx: int) -> HookSpec:
    desc = f"[[hooks]] 第 {idx + 1} 条"
    if not isinstance(entry, dict):
        raise ValueError(f"{desc}: 必须是 table")
    unknown = set(entry.keys()) - {"event", "matcher", "command", "timeout_s"}
    if unknown:
        raise ValueError(f"{desc}: 未知键 {', '.join(sorted(unknown))}")
    event = entry.get("event")
    if not isinstance(event, str) or event not in HOOK_EVENTS:
        raise ValueError(f"{desc}: event 必须是 {'/'.join(HOOK_EVENTS)} 之一，got {event!r}")
    matcher = entry.get("matcher", "*")
    if not isinstance(matcher, str) or not matcher.strip():
        raise ValueError(f"{desc}: matcher 必须是非空字符串（工具名或 '*'）")
    command = entry.get("command")
    if not isinstance(command, str):
        raise ValueError(f"{desc}: command 必须是字符串")
    timeout_s = entry.get("timeout_s", DEFAULT_HOOK_TIMEOUT_S)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ValueError(f"{desc}: timeout_s 必须是正数")
    return HookSpec(
        event=event,
        matcher=matcher.strip(),
        argv=_to_argv(command, desc),
        timeout_s=float(timeout_s),
    )


# 校验并转换整组 [[hooks]] 配置；返回可直接执行的 HookSpec 列表
def parse_hook_entries(entries: Any) -> list[HookSpec]:
    """
    [[hooks]] 数组 → list[HookSpec]；任何一条非法都整组拒绝（抛 ValueError）

    【设计】不做"跳过坏条目继续跑"：hook 常被用作审批守卫，静默丢掉一条
    守卫规则比启动失败危险得多——与 permission 规则的"笔误当场炸"同哲学。
    """
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError("[[hooks]] 必须是数组（TOML array of tables）")
    return [_parse_one(e, i) for i, e in enumerate(entries)]
