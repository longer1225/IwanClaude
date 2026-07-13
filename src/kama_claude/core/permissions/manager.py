# 导入 Python 3.7+ 的类型注解特性
from __future__ import annotations

# 导入 asyncio（异步 I/O 框架，用于处理 Future 和超时）
import asyncio

# 导入 datetime（用于生成时间戳）
import datetime

# 导入 logging（用于记录日志）
import logging

# 导入 re（正则表达式，用于匹配命令模式）
import re

# 导入 Awaitable 和 Callable（类型注解，用于异步回调函数）
from collections.abc import Awaitable, Callable

# 导入 dataclass（用于定义数据类）
from dataclasses import dataclass

# 导入 UTC 时区（用于生成标准时间戳）
from datetime import UTC

# 导入 Path（用于处理文件路径）
from pathlib import Path

# 导入 Any（表示任意类型）
from typing import Any

# 导入权限策略相关的类和函数
from kama_claude.core.permissions.policy import (
    DEFAULT_POLICIES,      # 内置工具的默认权限策略
    PermissionDecision,    # 权限决策枚举（ALLOW/DENY/ASK）
    ToolPolicy,            # 工具级别的权限策略数据类
    matches_outside_cwd,   # 判断 bash 命令是否操作 cwd 之外路径
    param_preview,         # 生成人类可读的参数摘要
)

# 导入策略文件的读写函数
from kama_claude.core.permissions.storage import load_policy_file, save_policy_file

# 创建日志记录器（属于当前模块）
logger = logging.getLogger(__name__)


# 返回当前 UTC 时间的 ISO 8601 格式字符串（用于事件时间戳）
# ISO 8601 格式：2026-07-12T10:30:00.123456Z
def _now() -> str:
    return datetime.datetime.now(UTC).isoformat()


# 待审批请求的数据类（内部使用，不对外暴露）
# 用于保存等待用户审批的请求信息
@dataclass
class _PendingRequest:
    # Future 对象：用于异步等待用户响应
    # 客户端回复审批决策后，会通过 future.set_result() 唤醒等待的协程
    future: asyncio.Future[str]
    
    # session_id：请求所属的会话 ID
    # 用于客户端断连时取消该会话的所有待审批请求
    session_id: str
    
    # tool_name：工具名
    # 用于缓存策略（如：用户选择 "always_allow" bash，下次自动允许）
    tool_name: str


# 权限管理器核心类
# 职责：
# 1. 对工具调用执行多层策略评估（静态评估 + 缓存 + 用户审批）
# 2. 管理待审批请求（使用 Future 实现异步等待）
# 3. 维护两级缓存：session 级缓存（内存，重启丢失）和持久化缓存（文件，跨 session）
# 4. 处理超时和客户端断连
class PermissionManager:
    def __init__(
        self,
        policies: dict[str, ToolPolicy] | None = None,
        *,
        policy_file: Path | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        # 工具策略字典：key 是工具名，value 是 ToolPolicy 对象
        # 如果没有提供 policies，使用 DEFAULT_POLICIES 的副本（避免修改全局默认值）
        self._policies: dict[str, ToolPolicy] = policies or dict(DEFAULT_POLICIES)
        
        # 待审批请求字典：key 是 tool_use_id，value 是 _PendingRequest 对象
        # 用于存储正在等待用户审批的请求，客户端回复后通过 tool_use_id 查找并 resolve Future
        self._pending: dict[str, _PendingRequest] = {}
        
        # session 级 always 缓存：key 是 (session_id, tool_name) 元组，value 是 "allow" 或 "deny"
        # 仅在当前 session 内存中有效，daemon 重启后丢失
        # 场景：用户在一次聊天中选择 "always_allow" bash，本次会话后续自动允许
        self._session_always: dict[tuple[str, str], str] = {}
        
        # 持久化策略文件路径（通常是 ~/.kama/policy.toml）
        self._policy_file = policy_file
        
        # 持久化 always 缓存：key 是工具名，value 是 "allow" 或 "deny"
        # 从 policy_file 加载，跨 session 持久存在
        # 场景：用户选择 "always_allow" bash，重启 daemon 后仍然自动允许
        self._persistent_always: dict[str, str] = (
            load_policy_file(policy_file) if policy_file is not None else {}
        )
        
        # 审批超时时间（秒），0 表示不超时
        # 如果用户在 timeout_s 秒内没有回复，自动拒绝请求
        self._timeout_s = timeout_s

    # 对工具名 + 参数执行 4 层静态策略评估（不挂起，不等待用户）
    # 参数 tool_name: 工具名
    # 参数 params: 工具调用参数字典
    # 返回值: PermissionDecision（ALLOW/DENY/ASK）
    # 注意：这个方法只做静态评估，不会触发用户审批（不会发送事件或等待响应）
    def evaluate(self, tool_name: str, params: dict[str, Any]) -> PermissionDecision:
        # 延迟导入 evaluate 函数（避免循环导入）
        from kama_claude.core.permissions.policy import evaluate
        
        # 获取该工具的策略（如果没有则使用默认策略）
        policy = self._policies.get(tool_name)
        
        # 调用 policy.py 中的 evaluate 函数进行评估
        return evaluate(tool_name, params, policy)

    # 检查权限的完整流程（核心方法）
    # 如果评估结果是 ASK，则向客户端发送审批请求事件，并等待用户响应
    # 参数 tool_use_id: 工具调用的唯一 ID（由 AgentLoop 生成）
    # 参数 tool_name: 工具名
    # 参数 params: 工具调用参数字典
    # 参数 session_id: 当前会话 ID
    # 参数 event_emitter: 事件发射器（用于向客户端发送审批请求事件）
    # 返回值: tuple[bool, str]（是否允许，决策字符串）
    #   bool: True 表示允许执行，False 表示拒绝
    #   str: 决策类型（auto_allow/auto_deny/allow_once/always_allow/deny_once/always_deny/timeout）
    async def check_and_wait(
        self,
        tool_use_id: str,
        tool_name: str,
        params: dict[str, Any],
        session_id: str,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> tuple[bool, str]:
        # 提取 bash 命令（仅 bash 工具需要，其他工具为空字符串）
        command = str(params.get("command", "")) if tool_name == "bash" else ""
        
        # 获取该工具的策略（如果没有则为 None）
        policy = self._policies.get(tool_name)

        # ========== Tier 1: deny_patterns（bash only，优先级最高，不可被缓存绕过）==========
        # 如果命令匹配任何拒绝模式，直接拒绝（不询问用户）
        if command and policy:
            for pat in policy.deny_patterns:
                if re.search(pat, command):
                    logger.debug("permission: deny_pattern hit tool=%s", tool_name)
                    return False, "auto_deny"

        # ========== Tier 2: OUTSIDE_CWD_HEURISTICS（bash only，强制 ASK，不可被任何缓存绕过）==========
        # 判断命令是否操作 cwd 之外的路径
        outside_cwd = bool(command and matches_outside_cwd(command))

        # 如果命令不涉及 cwd 之外的路径，继续后续评估
        if not outside_cwd:
            # ========== Tier 3: session always 缓存（仅当前 session 有效）==========
            # 检查当前 session 是否对该工具有 "always" 决策
            session_key = (session_id, tool_name)
            if session_key in self._session_always:
                cached = self._session_always[session_key]
                logger.debug("permission: session cache hit tool=%s decision=%s", tool_name, cached)
                return cached == "allow", f"auto_{cached}"

            # ========== Tier 4: persistent always（跨 session，从文件加载）==========
            # 检查持久化缓存中是否有该工具的 "always" 决策
            if tool_name in self._persistent_always:
                cached = self._persistent_always[tool_name]
                logger.debug("permission: persistent cache hit tool=%s decision=%s", tool_name, cached)
                return cached == "allow", f"auto_{cached}"

            # ========== Tier 5: allow_patterns（bash only）==========
            # 如果命令匹配任何允许模式，直接允许
            if command and policy:
                for pat in policy.allow_patterns:
                    if re.search(pat, command):
                        return True, "auto_allow"

            # ========== Tier 6: tool default（工具默认策略）==========
            if policy is not None:
                if policy.default == PermissionDecision.ALLOW:
                    return True, "auto_allow"
                if policy.default == PermissionDecision.DENY:
                    return False, "auto_deny"
            # 如果 default == ASK（bash、unknown tool），继续执行到 ASK 路径

        # ========== ASK 路径（需要用户审批）==========
        # 场景1：命令操作 cwd 之外的路径（强制 ASK）
        # 场景2：工具默认策略是 ASK（如 bash、write_file）
        
        # 1. 获取当前事件循环
        loop = asyncio.get_event_loop()
        
        # 2. 创建 Future 对象（用于异步等待用户响应）
        # Future 是 asyncio 中用于异步结果的占位符
        future: asyncio.Future[str] = loop.create_future()
        
        # 3. 将待审批请求加入 _pending 字典
        self._pending[tool_use_id] = _PendingRequest(
            future=future,
            session_id=session_id,
            tool_name=tool_name,
        )

        # 4. 向客户端发送权限审批请求事件
        # 客户端（TUI/CLI）收到事件后，会显示审批提示并等待用户操作
        await event_emitter(
            {
                "type": "permission.requested",       # 事件类型
                "tool_use_id": tool_use_id,           # 工具调用 ID（用于回复时匹配）
                "tool_name": tool_name,               # 工具名
                "params": params,                     # 完整参数（供客户端显示）
                "param_preview": param_preview(tool_name, params),  # 参数摘要（简洁显示）
                "session_id": session_id,             # 会话 ID
                "ts": _now(),                         # 时间戳
            }
        )

        # 5. 等待用户响应（带超时）
        try:
            if self._timeout_s > 0:
                # 使用 asyncio.wait_for() 设置超时
                # 如果在 timeout_s 秒内没有收到响应，抛出 TimeoutError
                raw = await asyncio.wait_for(future, timeout=self._timeout_s)
            else:
                # 不设置超时，无限等待（不推荐，可能导致永久挂起）
                raw = await future
        except asyncio.TimeoutError:
            # 超时处理：从 _pending 中移除请求，记录日志，返回拒绝
            self._pending.pop(tool_use_id, None)
            logger.info("permission: timeout tool_use_id=%s tool=%s", tool_use_id, tool_name)
            return False, "timeout"

        # 6. 应用用户的审批决策（更新缓存）
        allowed = self._apply_response(raw, session_id, tool_name)
        
        # 7. 返回结果
        return allowed, raw

    # 处理客户端返回的审批决策（由 CoreApp 的 RPC handler 调用）
    # 参数 tool_use_id: 工具调用 ID（用于查找待审批请求）
    # 参数 decision: 用户的决策（allow_once/always_allow/deny_once/always_deny）
    # 返回值: 无（通过 resolve Future 来唤醒等待的 check_and_wait 协程）
    def respond(self, tool_use_id: str, decision: str) -> None:
        # 从 _pending 字典中查找待审批请求
        req = self._pending.pop(tool_use_id, None)
        
        # 如果找不到请求（可能已超时或已处理），记录警告并返回
        if req is None:
            logger.warning("permission.respond: unknown tool_use_id=%s", tool_use_id)
            return
        
        # 如果 Future 还未完成，设置结果（唤醒等待的协程）
        if not req.future.done():
            req.future.set_result(decision)

    # 应用用户的审批决策，更新 session 和持久化缓存
    # 参数 decision: 用户的决策（allow_once/always_allow/deny_once/always_deny）
    # 参数 session_id: 当前会话 ID
    # 参数 tool_name: 工具名
    # 返回值: bool（是否允许执行）
    def _apply_response(self, decision: str, session_id: str, tool_name: str) -> bool:
        # 判断是否允许执行（allow_once 和 always_allow 都允许）
        allow = decision in ("allow_once", "always_allow")
        
        # 如果是 "always_allow"，更新两级缓存
        if decision == "always_allow":
            # 更新 session 级缓存（内存）
            self._session_always[(session_id, tool_name)] = "allow"
            
            # 更新持久化缓存（内存中的字典）
            self._persistent_always[tool_name] = "allow"
            
            logger.info(
                "permission: always allow tool=%s policy_file=%s persistent=%s",
                tool_name, self._policy_file, self._persistent_always,
            )
            
            # 将持久化缓存写入文件（如果配置了 policy_file）
            if self._policy_file is not None:
                try:
                    save_policy_file(self._persistent_always, self._policy_file)
                    logger.info("permission: policy.toml written path=%s", self._policy_file)
                except Exception:
                    # 写入失败时记录异常（不影响正常运行）
                    logger.exception("permission: failed to write policy.toml path=%s", self._policy_file)
            else:
                logger.warning("permission: policy_file is None, skipping persistence")
        
        # 如果是 "always_deny"，同样更新两级缓存
        elif decision == "always_deny":
            # 更新 session 级缓存（内存）
            self._session_always[(session_id, tool_name)] = "deny"
            
            # 更新持久化缓存（内存中的字典）
            self._persistent_always[tool_name] = "deny"
            
            logger.info(
                "permission: always deny tool=%s policy_file=%s persistent=%s",
                tool_name, self._policy_file, self._persistent_always,
            )
            
            # 将持久化缓存写入文件（如果配置了 policy_file）
            if self._policy_file is not None:
                try:
                    save_policy_file(self._persistent_always, self._policy_file)
                    logger.info("permission: policy.toml written path=%s", self._policy_file)
                except Exception:
                    # 写入失败时记录异常（不影响正常运行）
                    logger.exception("permission: failed to write policy.toml path=%s", self._policy_file)
            else:
                logger.warning("permission: policy_file is None, skipping persistence")
        
        # allow_once 和 deny_once 不更新缓存（只影响本次请求）
        
        # 返回是否允许执行
        return allow

    # 客户端断连时调用，取消该 session 所有待审批请求
    # 防止客户端断开后，Future 永久挂起（导致 daemon 内存泄漏）
    # 参数 session_id: 断开连接的会话 ID
    # 参数 reason: 断连原因（默认为 "client_disconnected"）
    # 返回值: 无
    def cancel_session(self, session_id: str, reason: str = "client_disconnected") -> None:
        # 查找该 session 下所有待审批请求的 tool_use_id
        to_cancel = [
            uid for uid, req in self._pending.items()
            if req.session_id == session_id
        ]
        
        # 遍历并取消每个请求
        for uid in to_cancel:
            # 从 _pending 中移除请求
            req = self._pending.pop(uid)
            
            # 如果 Future 还未完成，设置结果为 "deny_once"（拒绝本次请求）
            if not req.future.done():
                logger.debug(
                    "permission: cancel pending tool_use_id=%s reason=%s", uid, reason
                )
                req.future.set_result("deny_once")
