"""
hooks 包：PreToolUse/PostToolUse 生命周期钩子（外部裁判，不改 core 即可扩展审批/审计）

【学习要点】
分层：spec（启动期校验）→ runner（退出码协议）→ registry（匹配/聚合/广播）。
依赖方向严格单向 registry→runner→spec，且本包不 import permissions——
是权限链持有 hook，不是 hook 持有权限链，避免双向耦合与循环。
详见 docs/design/hooks.md。
"""
from iwan_claude.core.hooks.registry import HookRegistry, aggregate
from iwan_claude.core.hooks.runner import HookDecision, HookOutcome
from iwan_claude.core.hooks.spec import HookSpec, parse_hook_entries

__all__ = [
    "HookRegistry",
    "aggregate",
    "HookDecision",
    "HookOutcome",
    "HookSpec",
    "parse_hook_entries",
]
