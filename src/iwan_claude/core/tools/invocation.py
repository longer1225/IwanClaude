"""
工具调用模块 - 工具执行的统一入口，包含完整的调用流程

【学习要点】
1. 中间件模式：invoke_tool 作为工具调用的中间件，统一处理参数校验、权限检查、超时控制、重试逻辑
2. 事件驱动：通过 EventBus 发布工具调用的各个阶段事件，便于监控和调试
3. 指数退避：失败重试时使用指数退避策略，避免频繁请求
4. 错误分类：将错误分为 schema_error、permission_denied、timeout、runtime_error、rate_limited 五类
5. 异步超时：使用 asyncio.wait_for 实现异步操作的超时控制

【调用流程】
1. 发布 ToolCallStartedEvent
2. 根据名称查找工具
3. 参数校验（Pydantic 模型）
4. 权限检查（PermissionManager）
5. 执行工具（带超时控制）
6. 发布 ToolCallFinishedEvent 或 ToolCallFailedEvent
7. 返回 ToolResult
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from iwan_claude.core.bus.events import (
    PermissionDeniedEvent,
    PermissionGrantedEvent,
    PermissionRequestedEvent,
    ToolCallFailedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.types import ToolCallBlock
from iwan_claude.core.tools.base import ToolResult
from iwan_claude.core.tools.errors import RateLimitedError
from iwan_claude.core.tools.registry import ToolRegistry

# TYPE_CHECKING 是一个特殊常量，仅在类型检查时为 True，运行时为 False
# 用于避免循环导入问题
if TYPE_CHECKING:
    from iwan_claude.core.permissions.manager import PermissionManager

# 工具调用的默认超时时间（秒）
_DEFAULT_TIMEOUT: float = 120.0
# 最大重试次数（不包括首次尝试）
_MAX_RETRIES: int = 2
# 重试退避的基础时间（秒），指数退避公式：base * 2^(attempt-1)
_RETRY_BASE_S: float = 2.0
# 可重试的错误类型集合
_RETRYABLE: frozenset[str] = frozenset({"runtime_error", "rate_limited"})


def _now() -> str:
    """
    获取当前 UTC 时间的 ISO 格式字符串

    【用途】
    用于事件的时间戳标记，确保所有事件使用统一的时间格式

    【示例输出】
    "2024-01-15T10:30:45.123456+00:00"
    """
    return datetime.now(UTC).isoformat()


async def _fail(
    bus: EventBus,
    run_id: str,
    tool_call: ToolCallBlock,
    error_class: str,
    error_message: str,
    elapsed_ms: int,
    *,
    attempt: int = 1,
) -> ToolResult:
    """
    工具调用失败处理函数

    【学习要点】
    1. 事件发布：失败时发布 ToolCallFailedEvent，便于日志记录和监控
    2. 统一返回：无论什么错误，都返回 ToolResult 而非抛出异常
    3. 错误分类：通过 error_class 区分不同类型的错误

    【参数说明】
    - bus: EventBus - 事件总线，用于发布失败事件
    - run_id: str - 当前运行的唯一标识
    - tool_call: ToolCallBlock - 工具调用信息
    - error_class: str - 错误类型（runtime_error/schema_error/timeout/permission_denied/rate_limited）
    - error_message: str - 错误详细信息
    - elapsed_ms: int - 从调用开始到失败的耗时（毫秒）
    - attempt: int - 当前尝试次数（默认 1）

    【返回值】
    - ToolResult: 标记为错误的结果对象
    """
    # 发布工具调用失败事件
    await bus.publish(
        ToolCallFailedEvent(
            run_id=run_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            error_class=error_class,
            error_message=error_message,
            elapsed_ms=elapsed_ms,
            attempt=attempt,
            ts=_now(),
        )
    )
    # 返回错误结果
    return ToolResult(content=error_message, is_error=True, error_type=error_class)


async def invoke_tool(
    registry: ToolRegistry,
    tool_call: ToolCallBlock,
    bus: EventBus,
    run_id: str,
    timeout: float = _DEFAULT_TIMEOUT,
    *,
    permission_manager: PermissionManager | None = None,
    session_id: str = "",
) -> ToolResult:
    """
    工具调用的统一入口 - 完整的工具执行流程

    【学习要点】
    1. 中间件模式：在实际工具调用前后添加多个处理环节
    2. 参数校验：使用 Pydantic 模型验证参数合法性
    3. 权限检查：通过 PermissionManager 实现用户授权机制
    4. 超时控制：使用 asyncio.wait_for 防止工具执行过长时间
    5. 指数退避：失败时按指数退避策略重试，避免资源浪费
    6. 事件追踪：每个阶段都发布事件，便于调试和监控

    【调用流程详解】
    ┌─────────────────────────────────────────────────────────────┐
    │ 1. 发布 ToolCallStartedEvent（调用开始）                     │
    ├─────────────────────────────────────────────────────────────┤
    │ 2. 根据名称查找工具                                          │
    │    └─ 找不到 → 返回 runtime_error                            │
    ├─────────────────────────────────────────────────────────────┤
    │ 3. 参数校验（如果工具定义了 params_model）                     │
    │    └─ 校验失败 → 返回 schema_error                           │
    ├─────────────────────────────────────────────────────────────┤
    │ 4. 权限检查（如果提供了 permission_manager）                  │
    │    └─ 拒绝 → 返回 permission_denied                         │
    ├─────────────────────────────────────────────────────────────┤
    │ 5. 执行工具（带超时控制）                                      │
    │    ├─ 成功 → 发布 ToolCallFinishedEvent → 返回结果            │
    │    ├─ 超时 → 返回 timeout                                    │
    │    ├─ 限流 → 重试或返回 rate_limited                         │
    │    └─ 其他错误 → 重试或返回 runtime_error                     │
    └─────────────────────────────────────────────────────────────┘

    【参数说明】
    - registry: ToolRegistry - 工具注册表，用于查找工具
    - tool_call: ToolCallBlock - 工具调用信息（名称、参数、调用 ID）
    - bus: EventBus - 事件总线，用于发布调用事件
    - run_id: str - 当前运行的唯一标识
    - timeout: float - 工具执行超时时间（默认 120 秒）
    - permission_manager: PermissionManager | None - 权限管理器（可选）
    - session_id: str - 会话 ID，用于权限持久化

    【返回值】
    - ToolResult: 工具执行结果，可能是成功内容或错误信息

    【重试策略】
    对于可重试的错误（runtime_error、rate_limited），采用指数退避：
    - 第 1 次重试：等待 2s (2 * 2^0)
    - 第 2 次重试：等待 4s (2 * 2^1)
    总共最多尝试 3 次（首次 + 2 次重试）
    """
    # 记录调用开始时间，用于计算耗时
    t0 = time.monotonic()

    # 1. 发布工具调用开始事件
    await bus.publish(
        ToolCallStartedEvent(
            run_id=run_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            params=dict(tool_call.input),
            ts=_now(),
        )
    )

    # 计算从调用开始到现在的耗时（毫秒）
    def elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    # 2. 根据名称查找工具
    tool = registry.get(tool_call.name)
    if tool is None:
        return await _fail(
            bus, run_id, tool_call,
            "runtime_error", f"unknown tool: {tool_call.name}", elapsed(),
        )

    # 3. 参数校验（如果工具定义了 Pydantic 模型）
    if tool.params_model is not None:
        try:
            # 使用 Pydantic 模型验证参数
            tool.params_model.model_validate(dict(tool_call.input))
        except ValidationError as exc:
            # 参数校验失败，返回 schema_error
            return await _fail(
                bus, run_id, tool_call,
                "schema_error", str(exc), elapsed(),
            )

    # 4. 权限检查（如果提供了权限管理器）
    if permission_manager is not None:
        # 定义权限事件的发布函数
        async def _emit_permission(raw: dict[str, Any]) -> None:
            await bus.publish(PermissionRequestedEvent(**raw, run_id=run_id))

        # 检查权限并等待用户确认（如果需要）
        allowed, decision = await permission_manager.check_and_wait(
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            params=dict(tool_call.input),
            session_id=session_id,
            event_emitter=_emit_permission,
        )

        if allowed:
            # 权限通过，发布授权事件（非自动授权时）
            if decision not in ("auto_allow",):
                await bus.publish(
                    PermissionGrantedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        decision=decision,
                        ts=_now(),
                    )
                )
            logging.getLogger(__name__).debug(
                "invoke_tool: permission allowed, executing tool=%s id=%s",
                tool_call.name, tool_call.id[:16],
            )
        else:
            # 权限拒绝，发布拒绝事件（非自动拒绝时）
            if decision != "auto_deny":
                await bus.publish(
                    PermissionDeniedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        decision=decision,
                        ts=_now(),
                    )
                )
            # 返回权限拒绝错误
            return await _fail(
                bus, run_id, tool_call,
                "permission_denied",
                "Permission denied by user. You may not execute this command. "
                "Try an alternative approach or ask the user what to do.",
                elapsed(),
            )

    # 5. 执行工具（带重试逻辑）
    # 循环次数：_MAX_RETRIES + 2 = 4 次（range(1, 4) → 1, 2, 3）
    # 实际上首次尝试 + 最多 2 次重试 = 最多 3 次执行
    for attempt in range(1, _MAX_RETRIES + 2):
        error_class: str | None = None
        error_message: str | None = None

        try:
            # 使用 asyncio.wait_for 实现超时控制
            logging.getLogger(__name__).debug(
                "invoke_tool: calling tool.invoke %s (timeout=%ss)", tool_call.name, timeout,
            )
            result = await asyncio.wait_for(
                tool.invoke(dict(tool_call.input)), timeout=timeout
            )
            ms = elapsed()
            logging.getLogger(__name__).debug(
                "invoke_tool: tool.invoke returned %s in %dms", tool_call.name, ms,
            )

            if result.is_error:
                # 工具内部返回错误
                error_class = result.error_type or "runtime_error"
                error_message = result.content
            else:
                # 工具执行成功，发布完成事件
                await bus.publish(
                    ToolCallFinishedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        tool_name=tool_call.name,
                        elapsed_ms=ms,
                        output=result.content,
                        ts=_now(),
                    )
                )
                return result

        except RateLimitedError as exc:
            # 限流错误，标记为 rate_limited
            error_class = "rate_limited"
            error_message = str(exc)
        except TimeoutError:
            # 超时错误，直接返回，不重试
            return await _fail(
                bus, run_id, tool_call,
                "timeout", f"tool timed out after {timeout}s", elapsed(),
                attempt=attempt,
            )
        except Exception as exc:
            # 其他未知异常，标记为 runtime_error
            # 【配额超限识别】sandbox 会抛 "sandbox quota exceeded: ..."（ValueError），
            # 这种情况重试多少次都没用，直接返回，不消耗 _MAX_RETRIES。
            exc_text = str(exc)
            if "sandbox quota exceeded" in exc_text or "sandbox file size limit exceeded" in exc_text:
                # 【Textual 转义】错误消息会在 TUI 渲染为 Static widget（markup=True），
                # 其中的 [sandbox] 会被 Textual 误解析为 markup 标签（=value 属性语法），
                # 触发 MarkupError。必须把 [ ] 用反斜杠转义成 \[ \]。
                safe_exc = exc_text.replace("[", "\\[").replace("]", "\\]")
                safe_fix = (
                    "Fix: increase \\[sandbox\\].max_total_size in ~/.iwan/config.toml "
                    "or set env IWAN_SANDBOX_MAX_TOTAL_SIZE=1000000000 then restart core."
                )
                return await _fail(
                    bus, run_id, tool_call,
                    "sandbox_quota",
                    f"Sandbox quota exceeded. {safe_exc}. {safe_fix}",
                    elapsed(),
                    attempt=attempt,
                )
            error_class = "runtime_error"
            error_message = exc_text

        # 确保错误信息已设置
        assert error_class is not None and error_message is not None
        ms = elapsed()

        # 检查是否可以重试
        if error_class in _RETRYABLE and attempt <= _MAX_RETRIES:
            # 发布失败事件
            await bus.publish(
                ToolCallFailedEvent(
                    run_id=run_id,
                    tool_use_id=tool_call.id,
                    tool_name=tool_call.name,
                    error_class=error_class,
                    error_message=error_message,
                    elapsed_ms=ms,
                    attempt=attempt,
                    ts=_now(),
                )
            )
            # 指数退避等待：base * 2^(attempt-1)
            await asyncio.sleep(_RETRY_BASE_S * (2 ** (attempt - 1)))
            # 继续下一次尝试
            continue

        # 不可重试或已达到最大重试次数，返回失败结果
        return await _fail(
            bus, run_id, tool_call,
            error_class, error_message, ms,
            attempt=attempt,
        )

    # 理论上不会到达这里，但为了通过类型检查，添加兜底返回
    return ToolResult(content="internal error", is_error=True, error_type="runtime_error")
