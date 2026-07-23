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

【权限检查流程】
Tier 1: deny_patterns（bash only，不可被缓存绕过）→ DENY
Tier 2: OUTSIDE_CWD_HEURISTICS（bash only，强制 ASK，不可被任何缓存绕过）
Tier 3: session always 缓存（session 内存，重启丢失）
Tier 4: persistent always（跨 session，从 policy_file 加载）
Tier 5: allow_patterns（bash only）→ ALLOW
Tier 6: tool default → 默认决策
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
import re
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
    ToolPolicy,
    matches_outside_cwd,
    param_preview,
)
from iwan_claude.core.permissions.storage import load_policy_file, save_policy_file

# 合法的自动模式值
AUTO_MODES = ("off", "read_only", "on")

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
    - _session_always: dict[tuple[str, str], str] - session 级缓存
    - _persistent_always: dict[str, str] - 持久化缓存
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
    ) -> None:
        """
        初始化权限管理器

        【参数说明】
        - policies: dict[str, ToolPolicy] | None - 工具策略映射（默认使用 DEFAULT_POLICIES）
        - policy_file: Path | None - 策略文件路径（用于持久化缓存）
        - timeout_s: float - 审批超时时间（秒，0 表示不超时，默认 60.0）

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
        # session 级缓存（(session_id, tool_name) → "allow" | "deny"，重启丢失）
        self._session_always: dict[tuple[str, str], str] = {}
        # 策略文件路径（用于持久化缓存）
        self._policy_file = policy_file
        # 持久化缓存（tool_name → "allow" | "deny"，从 policy_file 加载，跨 session）
        self._persistent_always: dict[str, str] = (
            load_policy_file(policy_file) if policy_file is not None else {}
        )
        # 审批超时时间（秒，0 表示不超时）
        self._timeout_s = timeout_s
        # 自动模式：off / read_only / on
        self._auto_mode: str = "off"
        # 努力等级：minimal / low / medium / high / max
        self._effort_level: str = "medium"
        # 模型预设：fast / balanced / powerful
        self._model_preset: str = "balanced"

    # 设置当前自动模式
    def set_auto_mode(self, mode: str) -> None:
        """
        设置当前自动模式

        【参数说明】
        - mode: str - 自动模式，必须是 "off" / "read_only" / "on" 之一

        【设计目的】
        允许运行时动态切换自动模式，无需重启守护进程。
        """
        if mode not in AUTO_MODES:
            raise ValueError(f"auto_mode must be one of {AUTO_MODES}, got {mode!r}")
        self._auto_mode = mode
        logger.info("permission: auto_mode set to %s", mode)

    # 获取当前自动模式
    def get_auto_mode(self) -> str:
        """
        获取当前自动模式

        【返回值】
        - str: 当前自动模式（"off" / "read_only" / "on"）
        """
        return self._auto_mode

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

    # 判断指定工具在当前自动模式下是否可以自动批准
    def _auto_mode_allows(self, tool_name: str) -> bool:
        """
        判断当前自动模式是否允许自动批准该工具

        【参数说明】
        - tool_name: str - 工具名称

        【返回值】
        - bool: True 表示可以自动批准，False 表示仍需走正常审批流程

        【逻辑说明】
        - off: 不允许任何自动批准
        - read_only: 只自动批准只读工具
        - on: 自动批准只读工具 + 白名单内的写工具
        """
        if self._auto_mode == "off":
            return False
        if tool_name in AUTO_MODE_READ_ONLY_TOOLS:
            return True
        if self._auto_mode == "on" and tool_name in AUTO_MODE_WRITE_ALLOW_TOOLS:
            return True
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
        return evaluate(tool_name, params, policy)

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
        - event_emitter: Callable[[dict[str, Any]], Awaitable[None]] - 事件发射器（向客户端发送权限请求）

        【返回值】
        - tuple[bool, str]: (是否允许, 决策字符串)
          - bool: True 表示允许，False 表示拒绝
          - str: 决策类型（auto_allow, auto_deny, timeout, allow_once, always_allow, deny_once, always_deny）

        【评估流程】
        Tier 1: deny_patterns（bash only，不可被缓存绕过）→ DENY
        Tier 2: OUTSIDE_CWD_HEURISTICS（bash only，强制 ASK，不可被任何缓存绕过）
        Tier 3: session always 缓存（session 内存，重启丢失）
        Tier 4: persistent always（跨 session，从 policy_file 加载）
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
        # 获取 bash 命令（非 bash 工具为空字符串）
        command = str(params.get("command", "")) if tool_name == "bash" else ""
        # 获取工具策略
        policy = self._policies.get(tool_name)

        # Tier 0: Auto Mode 自动批准（仅适用于非 bash 工具，且不能绕过安全规则）
        auto_allowed = False
        if tool_name != "bash" and self._auto_mode_allows(tool_name):
            auto_allowed = True

        # Tier 1: deny_patterns（bash only，不可被缓存绕过）
        if command and policy:
            for pat in policy.deny_patterns:
                if re.search(pat, command):
                    logger.debug("permission: deny_pattern hit tool=%s", tool_name)
                    return False, "auto_deny"

        # Tier 2: OUTSIDE_CWD_HEURISTICS（bash only，强制 ASK，不可被任何缓存绕过）
        outside_cwd = bool(command and matches_outside_cwd(command))

        if not outside_cwd:
            # Tier 3: session always 缓存（session 内存，重启丢失）
            session_key = (session_id, tool_name)
            if session_key in self._session_always:
                cached = self._session_always[session_key]
                logger.debug("permission: session cache hit tool=%s decision=%s", tool_name, cached)
                return cached == "allow", f"auto_{cached}"

            # Tier 4: persistent always（跨 session，从 policy_file 加载）
            if tool_name in self._persistent_always:
                cached = self._persistent_always[tool_name]
                logger.debug("permission: persistent cache hit tool=%s decision=%s", tool_name, cached)
                return cached == "allow", f"auto_{cached}"

            # Tier 5: allow_patterns（bash only）
            if command and policy:
                for pat in policy.allow_patterns:
                    if re.search(pat, command):
                        return True, "auto_allow"

            # Tier 6: tool default
            if policy is not None:
                if policy.default == PermissionDecision.ALLOW:
                    return True, "auto_allow"
                if policy.default == PermissionDecision.DENY:
                    return False, "auto_deny"
            # default == ASK（bash、unknown tool）→ 检查 Auto Mode 是否允许自动批准
            if auto_allowed:
                logger.debug("permission: auto_mode allowed tool=%s mode=%s", tool_name, self._auto_mode)
                return True, "auto_allow"
            # 仍需要用户确认 → fall through to Future

        # ASK 路径（来自 OUTSIDE_CWD 强制 ASK，或 default=ASK）
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
        except asyncio.TimeoutError:
            # 超时处理：取消待审批请求
            self._pending.pop(tool_use_id, None)
            logger.info("permission: timeout tool_use_id=%s tool=%s", tool_use_id, tool_name)
            return False, "timeout"

        # 应用审批决策
        allowed = self._apply_response(raw, session_id, tool_name)
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
            # 如果请求不存在，记录警告日志
            logger.warning("permission.respond: unknown tool_use_id=%s", tool_use_id)
            return
        # 如果 Future 未完成，设置结果
        if not req.future.done():
            req.future.set_result(decision)

    def _apply_response(self, decision: str, session_id: str, tool_name: str) -> bool:
        """
        应用审批决策，更新 session + persistent 缓存，返回是否放行

        【参数说明】
        - decision: str - 审批决策
        - session_id: str - session ID
        - tool_name: str - 工具名称

        【返回值】
        - bool: True 表示允许，False 表示拒绝

        【审批决策处理】
        - allow_once: 允许一次（不更新缓存）
        - always_allow: 始终允许（更新 session 和 persistent 缓存，保存到 policy_file）
        - deny_once: 拒绝一次（不更新缓存）
        - always_deny: 始终拒绝（更新 session 和 persistent 缓存，保存到 policy_file）

        【缓存更新】
        - session_always: 更新 (session_id, tool_name) → "allow" | "deny"
        - persistent_always: 更新 tool_name → "allow" | "deny"
        - policy_file: 如果存在，保存 persistent_always

        【示例】
        ```python
        allowed = manager._apply_response("always_allow", "session_01", "bash")
        # 返回: True
        # 更新: session_always[(session_01, bash)] = "allow"
        # 更新: persistent_always[bash] = "allow"
        # 保存: policy_file
        ```
        """
        # 判断是否允许（allow_once 和 always_allow 表示允许）
        allow = decision in ("allow_once", "always_allow")
        if decision == "always_allow":
            # 更新 session 级缓存
            self._session_always[(session_id, tool_name)] = "allow"
            # 更新持久化缓存
            self._persistent_always[tool_name] = "allow"
            logger.info(
                "permission: always allow tool=%s policy_file=%s persistent=%s",
                tool_name, self._policy_file, self._persistent_always,
            )
            # 如果策略文件存在，保存持久化缓存
            if self._policy_file is not None:
                try:
                    save_policy_file(self._persistent_always, self._policy_file)
                    logger.info("permission: policy.toml written path=%s", self._policy_file)
                except Exception:
                    logger.exception("permission: failed to write policy.toml path=%s", self._policy_file)
            else:
                logger.warning("permission: policy_file is None, skipping persistence")
        elif decision == "always_deny":
            # 更新 session 级缓存
            self._session_always[(session_id, tool_name)] = "deny"
            # 更新持久化缓存
            self._persistent_always[tool_name] = "deny"
            logger.info(
                "permission: always deny tool=%s policy_file=%s persistent=%s",
                tool_name, self._policy_file, self._persistent_always,
            )
            # 如果策略文件存在，保存持久化缓存
            if self._policy_file is not None:
                try:
                    save_policy_file(self._persistent_always, self._policy_file)
                    logger.info("permission: policy.toml written path=%s", self._policy_file)
                except Exception:
                    logger.exception("permission: failed to write policy.toml path=%s", self._policy_file)
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
