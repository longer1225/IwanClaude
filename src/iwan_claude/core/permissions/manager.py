"""
权限管理器模块 - 管理工具调用的权限审批

【学习要点】
1. 权限检查流程：6 层评估 + 用户审批
2. 缓存机制：session 级缓存（重启丢失）和持久化缓存（跨 session）
3. 用户审批：通过事件机制向客户端发送权限请求，等待响应
4. 超时处理：权限审批超时自动拒绝
5. 异常处理：客户端断连时取消所有待审批请求

【核心类】
- PermissionManager: 权限管理器主类
- _PendingRequest: 待审批请求数据类

【权限检查流程】（实现收敛在 policy.evaluate_pre_cache / evaluate_post_cache，
与静态评估共享同一函数——审批链与评估结果永不漂移）
Tier 0: PreToolUse hook（外部裁判，先于一切评估）——DENY 即地板（"hook_deny"）、
        ASK 并入强制档、ALLOW 在缓存全数未否决后终结链为"hook_allow"
Tier 1: deny 类地板——legacy deny_patterns + sandbox command_blacklist
        + 规则引擎 deny（bash 逐段求值）→ DENY（任何缓存/模式不可翻）
Tier 2: 强制 ASK 类——规则引擎显式 ask + OUTSIDE_CWD_HEURISTICS
        + 沙箱检查（路径越界 / run_python 动态写路径）→ 弹问，跳过缓存
Tier 3: session always 缓存（按 工具+参数指纹 键控，重启丢失）
Tier 4: persistent always（跨 session，键为 "tool|参数指纹"）
Tier 5: allow 类——规则引擎 allow（全段覆盖）+ legacy allow_patterns
        （仅单段命令）→ ALLOW
Tier 6: 工具默认策略；ASK 时检查 auto 模式（规则来源的 ASK 不豁免）
ASK 路径: 向客户端发送事件，等待响应

【审批决策类型】
- allow_once: 允许一次
- always_allow: 始终允许（更新 session 和 persistent 缓存）
- deny_once: 拒绝一次
- always_deny: 始终拒绝（更新 session 和 persistent 缓存）

【设计目的】
提供完整的权限管理功能，
确保工具调用的安全性和用户可控性。
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from iwan_claude.core.permissions.policy import (
    AUTO_MODE_READ_ONLY_TOOLS,
    AUTO_MODE_WRITE_ALLOW_TOOLS,
    DEFAULT_POLICIES,
    PermissionDecision,
    TRUST_DENY_FORBIDDEN_TOOLS,
    ToolPolicy,
    evaluate_post_cache,
    evaluate_pre_cache,
    param_fingerprint,
    param_preview,
)
from iwan_claude.core.permissions.storage import load_policy_file, save_policy_file

# 合法的自动模式值（legacy 三态；经 AUTO_TO_MODE 映射进五态模型，保留一版兼容）
AUTO_MODES = ("off", "read_only", "on")

# 权限模式五态（对齐 Claude Code，语义矩阵见 docs/design/permission-modes.md）
PERMISSION_MODES = ("default", "acceptEdits", "plan", "auto", "bypassPermissions")

# legacy auto_mode → 新 mode 映射：off=什么都不自动放，read_only=只读自动放(=auto)，
# on=读+白名单写自动放(=acceptEdits)
AUTO_TO_MODE: dict[str, str] = {
    "off": "default",
    "read_only": "auto",
    "on": "acceptEdits",
}

# 合法的努力等级值
EFFORT_LEVELS = ("minimal", "low", "medium", "high", "max")

# 合法的模型预设值
MODEL_PRESETS = ("fast", "balanced", "powerful")

# 日志记录器
logger = logging.getLogger(__name__)


def _now() -> str:
    """
    获取当前时间的 ISO 格式字符串

    【返回值】
    - str: 当前时间的 ISO 格式字符串（如 "2024-01-01T12:00:00Z"）

    【设计目的】
    为权限审批事件提供时间戳。

    【示例】
    ```python
    _now()
    # 返回: "2024-01-01T12:00:00Z"
    ```
    """
    return datetime.datetime.now(UTC).isoformat()


@dataclass
class _PendingRequest:
    """
    待审批请求数据类 - 存储待处理的权限审批请求

    【字段说明】
    - future: asyncio.Future[str] - 异步 Future，用于等待审批结果
    - session_id: str - session ID
    - tool_name: str - 工具名称

    【设计目的】
    在权限审批过程中存储待处理请求，
    当客户端返回审批决策时 resolve 对应的 Future。

    【使用场景】
    - check_and_wait 方法创建待审批请求
    - respond 方法处理审批决策并 resolve Future
    - cancel_session 方法取消待审批请求
    """
    # 异步 Future，用于等待审批结果
    future: asyncio.Future[str]
    # session ID
    session_id: str
    # 工具名称
    tool_name: str


class PermissionManager:
    """
    权限管理器 - 管理工具调用的权限审批

    【学习要点】
    1. 权限检查：6 层评估 + 用户审批
    2. 缓存机制：session 级缓存和持久化缓存
    3. 用户审批：通过事件机制向客户端发送权限请求
    4. 超时处理：权限审批超时自动拒绝
    5. 异常处理：客户端断连时取消所有待审批请求

    【核心字段】
    - _policies: dict[str, ToolPolicy] - 工具策略映射
    - _pending: dict[str, _PendingRequest] - 待审批请求映射
    - _session_always: dict[tuple[str, str, str], str] - session 级缓存（键含参数指纹）
    - _persistent_always: dict[str, str] - 持久化缓存（键为 "tool|参数指纹"）
    - _policy_file: Path | None - 策略文件路径
    - _timeout_s: float - 审批超时时间（秒）

    【核心方法】
    - evaluate(): 静态策略评估（不挂起）
    - check_and_wait(): 权限检查（如需 ASK 则挂起等待）
    - respond(): 处理客户端返回的审批决策
    - cancel_session(): 取消 session 的所有待审批请求
    """
    def __init__(
        self,
        policies: dict[str, ToolPolicy] | None = None,
        *,
        policy_file: Path | None = None,
        timeout_s: float = 60.0,
        rules: Any = None,  # PermissionRules（声明式 deny/ask/allow 规则）
        hooks: Any = None,  # HookRegistry（PreToolUse/PostToolUse 外部裁判）
        default_mode: str = "default",  # 权限模式五态的会话默认值
    ) -> None:
        """
        初始化权限管理器

        【参数说明】
        - policies: dict[str, ToolPolicy] | None - 工具策略映射（默认使用 DEFAULT_POLICIES）
        - policy_file: Path | None - 策略文件路径（用于持久化缓存）
        - timeout_s: float - 审批超时时间（秒，0 表示不超时，默认 60.0）
        - rules: PermissionRules | None - 声明式 deny/ask/allow 规则引擎输入
          （None 或空 = 关闭规则引擎，只走 legacy 评估链，保证旧配置零感知升级）
        - hooks: HookRegistry | None - 生命周期钩子注册表（None/空 = PreToolUse
          闸与 PostToolUse 广播都跳过，行为与 hook 上线前逐字节一致）

        【初始化流程】
        1. 初始化工具策略映射
        2. 初始化待审批请求映射
        3. 初始化 session 级缓存
        4. 初始化持久化缓存（从 policy_file 加载）
        5. 设置审批超时时间

        【缓存机制】
        - session_always: session 级缓存，重启丢失
        - persistent_always: 持久化缓存，从 policy_file 加载，跨 session

        【示例】
        ```python
        manager = PermissionManager(
            policy_file=Path("~/.iwan/policy.toml"),
            timeout_s=60.0
        )
        ```
        """
        # 工具策略映射（默认使用 DEFAULT_POLICIES）
        self._policies: dict[str, ToolPolicy] = policies or dict(DEFAULT_POLICIES)
        # 待审批请求映射（tool_use_id → _PendingRequest）
        self._pending: dict[str, _PendingRequest] = {}
        # session 级缓存（(session_id, tool_name, 参数指纹) → "allow" | "deny"，重启丢失）
        # 键含参数指纹：always_allow 只覆盖"同一工具+同一命令/路径"，不再一刀切放行整个工具
        self._session_always: dict[tuple[str, str, str], str] = {}
        # 策略文件路径（用于持久化缓存）
        self._policy_file = policy_file
        # 持久化缓存（"tool_name|参数指纹" → "allow" | "deny"，从 policy_file 加载，跨 session）
        loaded_always = load_policy_file(policy_file) if policy_file is not None else {}
        # 旧格式（仅工具名、无指纹后缀）的一刀切条目不再信任：加载时丢弃并提示重新审批
        self._persistent_always: dict[str, str] = {
            k: v for k, v in loaded_always.items() if "|" in k
        }
        dropped = len(loaded_always) - len(self._persistent_always)
        if dropped:
            logger.warning(
                "permission: dropped %d legacy blanket policy entries in %s "
                "(cache key now requires a param fingerprint)",
                dropped, policy_file,
            )
        # 审批超时时间（秒，0 表示不超时）
        self._timeout_s = timeout_s
        # 权限模式：会话默认值（五态；PERMISSION_MODES）+ per-session 覆盖表
        if default_mode not in PERMISSION_MODES:
            raise ValueError(
                f"default_mode must be one of {PERMISSION_MODES}, got {default_mode!r}")
        self._default_mode: str = default_mode
        self._modes: dict[str, str] = {}  # session_id → mode（显式切换过的会话）
        # 努力等级：minimal / low / medium / high / max
        self._effort_level: str = "medium"
        # 模型预设：fast / balanced / powerful
        self._model_preset: str = "balanced"
        # 声明式规则引擎（PermissionRules 或 None；见 permissions/rules.py）
        self._rules = rules
        # 生命周期钩子注册表（HookRegistry 或 None；见 core/hooks/registry.py）
        self._hooks = hooks
        # Layer 0 项目信任（session_id → "allow"/"deny"/"ask"）：
        # 未登记的会话按 "allow" 处理——ask 的"每次写走审批"由既有默认策略
        # （未知工具兜底 ASK）天然承担，无需在此层重复强制
        self._trust: dict[str, str] = {}

    # 暴露 hook 注册表给工具执行路径（invocation 在成功执行后调 PostToolUse）
    @property
    def hooks(self) -> Any:
        return self._hooks

    # PostToolUse 钩子直通：无注册表/无匹配时返回空串（调用方据此不改动 tool 输出）
    async def run_post_tool_use_hooks(
        self, tool_name: str, params: dict[str, Any], tool_output: str,
        session_id: str = "", run_id: str = "",
    ) -> str:
        """
        工具成功执行后的 hook 广播；返回应追加进 tool 结果、回灌给模型的警告文本
        """
        if self._hooks is None or self._hooks.is_empty():
            return ""
        warn: str = await self._hooks.run_post_tool_use(
            tool_name, params, tool_output, session_id=session_id, run_id=run_id)
        return warn

    # legacy 三态 auto_mode 的显示映射（五态 → 最接近的旧值）
    _MODE_TO_AUTO: dict[str, str] = {
        "default": "off", "auto": "read_only", "acceptEdits": "on",
        "plan": "off", "bypassPermissions": "on",
    }

    # 设置当前自动模式（legacy 兼容入口：映射进五态后改写全局默认，无 per-session 覆盖时生效）
    def set_auto_mode(self, mode: str) -> None:
        """
        设置全局默认权限模式（经 AUTO_TO_MODE 兼容映射 off/read_only/on）

        【参数说明】
        - mode: str - 旧三态值 "off" / "read_only" / "on"

        【设计】新代码请直接用 set_permission_mode；本方法保留一个版本是为了
        不碎旧 RPC/脚本——off→default、read_only→auto、on→acceptEdits。
        """
        if mode not in AUTO_MODES:
            raise ValueError(f"auto_mode must be one of {AUTO_MODES}, got {mode!r}")
        self._default_mode = AUTO_TO_MODE[mode]
        logger.info("permission: auto_mode %s -> default mode %s", mode, self._default_mode)

    # 获取当前自动模式（由全局默认 mode 反推旧三态显示值）
    def get_auto_mode(self) -> str:
        """
        返回与当前默认权限模式最接近的 legacy 三态值（供旧客户端展示）
        """
        return self._MODE_TO_AUTO.get(self._default_mode, "off")

    # 切换指定会话（session_id=None 时改全局默认）的权限模式
    def set_permission_mode(self, mode: str, session_id: str | None = None) -> str:
        """
        设置五态权限模式；返回切换前的值（供事件广播 previous_mode）

        【参数说明】
        - mode: PERMISSION_MODES 之一
        - session_id: 目标会话；None 表示改"未显式切换过"的会话共用的默认值

        【设计】per-session 存储：一个会话进 plan 不会把其他会话的
        acceptEdits 一起降级——全局开关是当年 auto_mode 最大的误伤面。
        """
        if mode not in PERMISSION_MODES:
            raise ValueError(f"mode must be one of {PERMISSION_MODES}, got {mode!r}")
        if session_id is None:
            previous, self._default_mode = self._default_mode, mode
        else:
            previous = self.get_permission_mode(session_id)
            self._modes[session_id] = mode
        logger.info(
            "permission: mode %s -> %s (session=%s)", previous, mode, session_id or "<default>")
        return previous

    # 读取指定会话的生效权限模式
    def get_permission_mode(self, session_id: str) -> str:
        """
        返回该会话的生效模式：显式设置过用设置值，否则用全局默认
        """
        return self._modes.get(session_id, self._default_mode)

    # Layer 0：为会话登记项目信任决定（"allow"/"deny"/"ask"；来自 TrustStore 或弹窗）
    def set_trust(self, session_id: str, decision: str) -> None:
        """
        设定该会话的信任档；与权限模式正交——信任是"这个目录可否被 iwan 触碰"，
        模式是"每次触碰要不要问"，两者取更严的一侧生效
        """
        if decision not in ("allow", "deny", "ask"):
            raise ValueError(f"trust decision must be allow/deny/ask, got {decision!r}")
        self._trust[session_id] = decision
        logger.info("permission: trust=%s for session=%s", decision, session_id)

    # 读取会话的信任档（未登记返回 "allow"：ask 语义由默认策略兜底，见 check_and_wait）
    def get_trust(self, session_id: str) -> str:
        return self._trust.get(session_id, "allow")

    # 会话是否已有登记过信任档（send 路径按此决定是否从 TrustStore 重查）
    def has_trust(self, session_id: str) -> bool:
        return session_id in self._trust

    # 设置当前努力等级
    def set_effort_level(self, level: str) -> None:
        """
        设置当前努力等级

        【参数说明】
        - level: str - 努力等级，必须是 "minimal" / "low" / "medium" / "high" / "max" 之一

        【设计目的】
        允许运行时动态切换努力等级，控制 Agent 执行深度。
        """
        if level not in EFFORT_LEVELS:
            raise ValueError(f"effort_level must be one of {EFFORT_LEVELS}, got {level!r}")
        self._effort_level = level
        logger.info("permission: effort_level set to %s", level)

    # 获取当前努力等级
    def get_effort_level(self) -> str:
        """
        获取当前努力等级

        【返回值】
        - str: 当前努力等级（"minimal" / "low" / "medium" / "high" / "max"）
        """
        return self._effort_level

    # 设置当前模型预设
    def set_model_preset(self, preset: str) -> None:
        """
        设置当前模型预设

        【参数说明】
        - preset: str - 模型预设，必须是 "fast" / "balanced" / "powerful" 之一

        【设计目的】
        允许运行时动态切换模型预设，控制 Agent 使用哪个 LLM 模型。
        切换后，下一次 Agent run 会使用新预设对应的模型。
        """
        if preset not in MODEL_PRESETS:
            raise ValueError(f"model_preset must be one of {MODEL_PRESETS}, got {preset!r}")
        self._model_preset = preset
        logger.info("permission: model_preset set to %s", preset)

    # 获取当前模型预设
    def get_model_preset(self) -> str:
        """
        获取当前模型预设

        【返回值】
        - str: 当前模型预设（"fast" / "balanced" / "powerful"）
        """
        return self._model_preset

    # 判断指定工具在该权限模式下是否可被自动批准（Tier 6 的 auto 豁免档）
    def _mode_auto_allows(self, tool_name: str, mode: str) -> bool:
        """
        模式 → 白名单自动批准映射（bash 与未登记工具永不在任何模式的白名单里）

        - acceptEdits：只读 + 写文件白名单（≈旧 "on"，但不含 bash——Edits 不解锁 shell）
        - auto：仅只读（≈旧 "read_only"；官方 auto 走分类器，本期为白名单近似）
        - 其余（default/plan/bypassPermissions）：False——plan 另有整级 DENY，
          bypass 在更早的位置整级 ALLOW，都不经过本函数
        """
        if tool_name == "bash":
            return False
        if mode == "acceptEdits":
            return tool_name in (AUTO_MODE_READ_ONLY_TOOLS | AUTO_MODE_WRITE_ALLOW_TOOLS)
        if mode == "auto":
            return tool_name in AUTO_MODE_READ_ONLY_TOOLS
        return False

    def evaluate(self, tool_name: str, params: dict[str, Any]) -> PermissionDecision:
        """
        对工具名 + 参数执行 4 层静态评估，不挂起

        【参数说明】
        - tool_name: str - 工具名称
        - params: dict[str, Any] - 工具参数

        【返回值】
        - PermissionDecision: 权限决策（ALLOW/DENY/ASK）

        【设计目的】
        提供快速的静态策略评估，不涉及用户审批。

        【评估流程】
        调用 policy.evaluate() 函数，执行 4 层静态评估。

        【示例】
        ```python
        decision = manager.evaluate("bash", {"command": "ls"})
        # 返回: PermissionDecision.ASK
        ```
        """
        from iwan_claude.core.permissions.policy import evaluate
        # 获取工具策略
        policy = self._policies.get(tool_name)
        # 调用策略评估函数
        return evaluate(tool_name, params, policy, self._rules)

    async def check_and_wait(
        self,
        tool_use_id: str,
        tool_name: str,
        params: dict[str, Any],
        session_id: str,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> tuple[bool, str]:
        """
        检查权限；如需 ask 则向客户端发事件并等待响应；返回 (allowed, decision_str)

        【参数说明】
        - tool_use_id: str - 工具调用 ID（唯一标识）
        - tool_name: str - 工具名称
        - params: dict[str, Any] - 工具参数
        - session_id: str - session ID
        - event_emitter: Callable[[dict[str, Any]], Awaitable[None]] -
          事件发射器（向客户端发送权限请求）

        【返回值】
        - tuple[bool, str]: (是否允许, 决策字符串)
          - bool: True 表示允许，False 表示拒绝
          - str: 决策类型（auto_allow, auto_deny, timeout, allow_once,
            always_allow, deny_once, always_deny）

        【评估流程】
        Tier 1: deny_patterns（bash only，不可被缓存绕过）→ DENY
        Tier 2: OUTSIDE_CWD_HEURISTICS（bash only，强制 ASK，不可被任何缓存绕过）
        Tier 2.5: 沙箱检查（路径越界 / run_python 动态写路径，强制 ASK，不可被任何缓存绕过）
        Tier 3: session always 缓存（session 内存，按 工具+参数指纹 键控，重启丢失）
        Tier 4: persistent always（跨 session，键为 "tool|参数指纹"，从 policy_file 加载）
        Tier 5: allow_patterns（bash only）→ ALLOW
        Tier 6: tool default → 默认决策
        ASK 路径: 向客户端发送事件，等待响应

        【用户审批流程】
        1. 创建异步 Future
        2. 存储待审批请求
        3. 向客户端发送 permission.requested 事件
        4. 等待 Future 完成（带超时）
        5. 应用审批决策
        6. 返回结果

        【超时处理】
        - 如果 timeout_s > 0，使用 asyncio.wait_for 等待
        - 超时后取消待审批请求，返回 (False, "timeout")

        【示例】
        ```python
        allowed, decision = await manager.check_and_wait(
            tool_use_id="call_01",
            tool_name="bash",
            params={"command": "ls"},
            session_id="session_01",
            event_emitter=emit_event
        )
        ```
        """
        # 获取工具策略
        policy = self._policies.get(tool_name)

        # Tier 0: PreToolUse hook——先于一切权限评估的外部裁判（六种语境都运行，
        # 对齐官方"hook 是规则之前的一道闸"）。裁定值用字符串比较：HookDecision
        # 是 StrEnum，且此处延迟 import 会打断可读性——协议值即稳定契约。
        hook_pre: Any = None
        if self._hooks is not None and not self._hooks.is_empty():
            hook_pre = await self._hooks.run_pre_tool_use(
                tool_name, params, session_id)
            if hook_pre.decision == "deny":
                logger.info(
                    "permission: hook DENY tool=%s  %s", tool_name, hook_pre.reason)
                return False, "hook_deny"

        # 本会话生效的权限模式（五态；per-session 覆盖 > 全局默认）
        mode = self.get_permission_mode(session_id)

        # Tier 1+2: deny 地板与强制 ASK —— 与 policy.evaluate 共享同一实现，
        # 静态评估和审批链永不漂移（此前这里是复制的第二份 tier 逻辑，已删）
        pre = evaluate_pre_cache(tool_name, params, policy, self._rules)
        if pre is not None and pre.decision == PermissionDecision.DENY:
            logger.debug("permission: deny floor tool=%s  %s", tool_name, pre.detail)
            return False, "auto_deny"
        # Layer 0 信任地板：trust=deny 的会话里，文件变更/执行类工具强制 DENY。
        # 放在 plan/bypass/缓存之前——它是"这个目录根本不该被写"的裁决，
        # bypassPermissions 与 always_allow 都豁免不了（对齐 managed deny 语义）；
        # 未登记会话按 allow 处理：ask 的"每次写走审批"由默认策略兜底 ASK 承担，
        # 这一层只对显式 deny 加码，不改变任何既有会话的行为
        if self._trust.get(session_id, "allow") == "deny" and tool_name in TRUST_DENY_FORBIDDEN_TOOLS:
            logger.info("permission: trust-deny floor tool=%s session=%s", tool_name, session_id)
            return False, "trust_deny"
        # plan 模式的整级 DENY：只读白名单之外的工具（含 bash/未知工具）一律拒绝。
        # 放在 deny 地板之后：地板 DENY 的理由更精确；放在缓存之前：模式是策略
        # 不是用户意愿，always_allow 缓存不该在 plan 里漏执行（官方 ask→deny 语义）
        if mode == "plan" and tool_name not in AUTO_MODE_READ_ONLY_TOOLS:
            logger.debug("permission: plan mode denied tool=%s", tool_name)
            return False, "plan_mode"
        # 强制 ASK 的来源：策略链 Tier2 / hook 显式 ASK —— bypass 也保留（bypass
        # 只豁免"默认会弹问的"，地板与强制档不动，见 docs/design/permission-modes.md §2）
        forced_ask = pre is not None or (
            hook_pre is not None and hook_pre.decision == "ask")
        # bypassPermissions：越过缓存与 Tier5/6 直接放行（地板与强制 ASK 已拦过）
        if mode == "bypassPermissions" and not forced_ask:
            return True, "bypass_allow"

        # 参数指纹：always 缓存按 tool + 参数（命令/路径）键控，不再按工具名一刀切
        fingerprint = param_fingerprint(tool_name, params)

        if not forced_ask:
            # Tier 3: session always 缓存（session 内存，重启丢失）
            session_key = (session_id, tool_name, fingerprint)
            if session_key in self._session_always:
                cached = self._session_always[session_key]
                logger.debug("permission: session cache hit tool=%s decision=%s", tool_name, cached)
                return cached == "allow", f"auto_{cached}"

            # Tier 4: persistent always（跨 session，从 policy_file 加载）
            persistent_key = f"{tool_name}|{fingerprint}"
            if persistent_key in self._persistent_always:
                cached = self._persistent_always[persistent_key]
                logger.debug(
                    "permission: persistent cache hit tool=%s decision=%s", tool_name, cached
                )
                return cached == "allow", f"auto_{cached}"

            # Hook ALLOW 落点（方向不对称的另一半）：它能豁免的只有 Tier 5/6
            # （规则未覆盖时的默认 ASK / auto 模式），而 deny 地板、强制 ASK、
            # 缓存里的 always_deny 这些"更强的声音"都已在前面拦截过——
            # 被攻陷的 hook 脚本因此换不来全系统特赦
            if hook_pre is not None and hook_pre.decision == "allow":
                logger.debug(
                    "permission: hook ALLOW tool=%s  %s", tool_name, hook_pre.reason)
                return True, "hook_allow"

            # Tier 5+6: allow 类（规则/legacy/默认）
            post = evaluate_post_cache(tool_name, params, policy, self._rules)
            if post.decision == PermissionDecision.ALLOW:
                return True, "auto_allow"
            if post.decision == PermissionDecision.DENY:
                return False, "auto_deny"
            # default == ASK（bash、unknown tool）→ 检查权限模式的白名单豁免档；
            # 来自规则引擎的 ASK（未覆盖段/动态语法）不受模式豁免——
            # 模式是"少弹窗"，不是"少审查"
            if self._mode_auto_allows(tool_name, mode) and not post.from_rules:
                logger.debug(
                    "permission: mode %s auto-allowed tool=%s", mode, tool_name
                )
                return True, "auto_allow"
            # 仍需要用户确认 → fall through to Future

        # ASK 路径（来自 OUTSIDE_CWD 强制 ASK、沙箱强制 ASK，或 default=ASK）
        # 获取事件循环
        loop = asyncio.get_event_loop()
        # 创建异步 Future
        future: asyncio.Future[str] = loop.create_future()
        # 存储待审批请求
        self._pending[tool_use_id] = _PendingRequest(
            future=future,
            session_id=session_id,
            tool_name=tool_name,
        )

        # 向客户端发送权限请求事件
        await event_emitter(
            {
                "type": "permission.requested",
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "params": params,
                "param_preview": param_preview(tool_name, params),
                "session_id": session_id,
                "ts": _now(),
            }
        )

        try:
            # 等待 Future 完成（带超时）
            if self._timeout_s > 0:
                raw = await asyncio.wait_for(future, timeout=self._timeout_s)
            else:
                raw = await future
        except TimeoutError:
            # 超时处理：取消待审批请求
            self._pending.pop(tool_use_id, None)
            logger.info("permission: timeout tool_use_id=%s tool=%s", tool_use_id, tool_name)
            return False, "timeout"

        # 应用审批决策
        allowed = self._apply_response(raw, session_id, tool_name, fingerprint)
        return allowed, raw

    def respond(self, tool_use_id: str, decision: str) -> None:
        """
        处理客户端返回的审批决策，resolve 对应 Future

        【参数说明】
        - tool_use_id: str - 工具调用 ID（唯一标识）
        - decision: str - 审批决策（allow_once, always_allow, deny_once, always_deny）

        【执行流程】
        1. 从待审批请求映射中获取请求
        2. 如果请求不存在，记录警告日志
        3. 如果 Future 未完成，设置结果

        【审批决策类型】
        - allow_once: 允许一次
        - always_allow: 始终允许（更新 session 和 persistent 缓存）
        - deny_once: 拒绝一次
        - always_deny: 始终拒绝（更新 session 和 persistent 缓存）

        【示例】
        ```python
        manager.respond("call_01", "always_allow")
        ```
        """
        # 从待审批请求映射中获取请求
        req = self._pending.pop(tool_use_id, None)
        if req is None:
            # 请求不存在 —— 可能是：
            #   a) 审批超时已被 timeout 分支 pop 走了
            #   b) tool_use_id 错了（客户端回了旧的/拼错的）
            #   c) 同一请求被重复 respond 了
            logger.warning(
                "permission.respond: unknown tool_use_id=%s  "
                "(可能是审批超时已自动清理，或 id 不匹配，或重复点击)",
                tool_use_id,
            )
            return
        # Future 未完成 → 设置结果
        if not req.future.done():
            req.future.set_result(decision)
            logger.info(
                "permission.respond: resolved tool_use_id=%s session=%s tool=%s decision=%s",
                tool_use_id, req.session_id, req.tool_name, decision,
            )
        else:
            # Future 已经完成（极端情况：timeout 刚 set_exception，但 respond 也到了）
            logger.warning(
                "permission.respond: future already done tool_use_id=%s decision=%s",
                tool_use_id, decision,
            )

    def _apply_response(
        self, decision: str, session_id: str, tool_name: str, fingerprint: str
    ) -> bool:
        """
        应用审批决策，更新 session + persistent 缓存，返回是否放行

        【参数说明】
        - decision: str - 审批决策
        - session_id: str - session ID
        - tool_name: str - 工具名称
        - fingerprint: str - 参数指纹（param_fingerprint 生成），缓存键的组成部分

        【返回值】
        - bool: True 表示允许，False 表示拒绝

        【审批决策处理】
        - allow_once: 允许一次（不更新缓存）
        - always_allow: 始终允许（更新 session 和 persistent 缓存，保存到 policy_file）
        - deny_once: 拒绝一次（不更新缓存）
        - always_deny: 始终拒绝（更新 session 和 persistent 缓存，保存到 policy_file）

        【缓存更新】
        - session_always: 更新 (session_id, tool_name, fingerprint) → "allow" | "deny"
        - persistent_always: 更新 "tool_name|fingerprint" → "allow" | "deny"
        - policy_file: 如果存在，保存 persistent_always

        【示例】
        ```python
        allowed = manager._apply_response("always_allow", "session_01", "bash", "ab12cd34ef567890")
        # 返回: True
        # 更新: session_always[(session_01, bash, ab12cd34ef567890)] = "allow"
        # 更新: persistent_always["bash|ab12cd34ef567890"] = "allow"
        # 保存: policy_file
        ```
        """
        # 判断是否允许（allow_once 和 always_allow 表示允许）
        allow = decision in ("allow_once", "always_allow")
        persistent_key = f"{tool_name}|{fingerprint}"
        if decision == "always_allow":
            # 更新 session 级缓存（键含参数指纹，只放行同参数调用）
            self._session_always[(session_id, tool_name, fingerprint)] = "allow"
            # 更新持久化缓存
            self._persistent_always[persistent_key] = "allow"
            logger.info(
                "permission: always allow tool=%s fingerprint=%s policy_file=%s",
                tool_name, fingerprint, self._policy_file,
            )
            # 如果策略文件存在，保存持久化缓存
            if self._policy_file is not None:
                try:
                    save_policy_file(self._persistent_always, self._policy_file)
                    logger.info("permission: policy.toml written path=%s", self._policy_file)
                except Exception:
                    logger.exception(
                        "permission: failed to write policy.toml path=%s", self._policy_file
                    )
            else:
                logger.warning("permission: policy_file is None, skipping persistence")
        elif decision == "always_deny":
            # 更新 session 级缓存（键含参数指纹）
            self._session_always[(session_id, tool_name, fingerprint)] = "deny"
            # 更新持久化缓存
            self._persistent_always[persistent_key] = "deny"
            logger.info(
                "permission: always deny tool=%s fingerprint=%s policy_file=%s",
                tool_name, fingerprint, self._policy_file,
            )
            # 如果策略文件存在，保存持久化缓存
            if self._policy_file is not None:
                try:
                    save_policy_file(self._persistent_always, self._policy_file)
                    logger.info("permission: policy.toml written path=%s", self._policy_file)
                except Exception:
                    logger.exception(
                        "permission: failed to write policy.toml path=%s", self._policy_file
                    )
            else:
                logger.warning("permission: policy_file is None, skipping persistence")
        return allow

    def cancel_session(self, session_id: str, reason: str = "client_disconnected") -> None:
        """
        客户端断连时拒绝该 session 所有待审批请求，防止 Future 永久挂起

        【参数说明】
        - session_id: str - session ID
        - reason: str - 取消原因（默认 "client_disconnected"）

        【执行流程】
        1. 查找该 session 的所有待审批请求
        2. 遍历待审批请求
        3. 如果 Future 未完成，设置结果为 "deny_once"
        4. 从待审批请求映射中移除

        【设计目的】
        防止客户端断连导致 Future 永久挂起，
        确保资源正确释放。

        【示例】
        ```python
        manager.cancel_session("session_01", "client_disconnected")
        ```
        """
        # 查找该 session 的所有待审批请求
        to_cancel = [
            uid for uid, req in self._pending.items()
            if req.session_id == session_id
        ]
        # 遍历待审批请求
        for uid in to_cancel:
            req = self._pending.pop(uid)
            # 如果 Future 未完成，设置结果为 "deny_once"
            if not req.future.done():
                logger.debug(
                    "permission: cancel pending tool_use_id=%s reason=%s", uid, reason
                )
                req.future.set_result("deny_once")
        # 会话结束：per-session 模式覆盖一并清除——_modes 是无界字典，
        # 长期运行的 daemon 里不清就是慢性内存泄漏（design §4）
        self._modes.pop(session_id, None)
        # 信任档同理：会话死了它对应的信任记录留在 TrustStore 里，内存态无需保留
        self._trust.pop(session_id, None)
