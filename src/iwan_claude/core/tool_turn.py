"""
tool_turn 模块 - 引擎共享的"模型→工具→结果→模型"内循环执行器

【学习要点】
1. 什么是"工具回合"（tool turn）：LLM 一次响应里可能要求调用工具，工具结果
   必须回喂给模型再问"接下来怎么办"。一个回合 = 从发给模型一条指令开始，
   到模型给出不含工具调用的最终回答为止，中间可以循环多轮。
   ReAct 引擎把这个循环做成了图上的 chat↔tools 边；plan_execute/debate/
   pipeline 的每个节点内部也需要同样的循环——差别只在"入口消息怎么拼"。
2. 为什么必须回喂而不是把结果拼到答案文本尾部：Anthropic API 里
   assistant 的 tool_use block 必须有配对的 user tool_result block，
   缺配对直接 400；且模型只有"看见"结果才能基于它继续推理。
3. 权限与执行统一入口：invoke_tool 内部已包含参数校验、权限检查
   （check_and_wait + 事件发射）、超时与重试。引擎层再手写一遍
   permission_manager.check_and_wait 既冗余又容易漏参数（历史上的
   debate TypeError 就是这么来的），所以这里只调 invoke_tool。
4. 轨迹（trajectory）：回合内新增的 assistant/tool_result 消息列表。
   引擎把它并入 state["messages"]，会话历史和后续步骤就能看到
   真实执行过程，而不是只剩一段转述文本。
5. 并行安全：同一响应里的多个 tool_use 默认互相独立（模型想串行会
   分两轮提问），所以用 asyncio.gather 并行执行、按原顺序回喂。

【核心函数】
- run_tool_turn: 驱动一个完整工具回合，返回 ToolTurnResult
- maybe_compact: 上下文占用超阈值时用压缩器重写消息历史
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from iwan_claude.core.compact.compactor import Compactor
from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.base import LLMProvider
from iwan_claude.core.message_blocks import (
    _add_tool_result_to_messages,
    _assistant_msg_from_response,
)
from iwan_claude.core.permissions.manager import PermissionManager
from iwan_claude.core.run_registry import pop_steers, steer_as_message
from iwan_claude.core.tools.invocation import invoke_tool
from iwan_claude.core.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

# 单个节点回合内允许的最大工具循环轮数，防止模型无限调用工具
# 【设计】轮数上限是"保险丝"而非常规出口：正常任务一个节点很少超过 3-4 轮
_MAX_TOOL_ROUNDS = 8


@dataclass
class ToolTurnResult:
    """
    工具回合的执行结果

    字段：
        text: 最终无工具调用的回答文本（模型最后一段话）
        trajectory: 回合内新增的 assistant/tool_result 消息（供引擎并入会话历史）
        ctx_pct: 最后一次 LLM 调用报告的上下文占用率（0-1，驱动下一回合压缩）
        error: LLM 调用异常信息（None 表示正常结束；异常前已产生的轨迹仍保留）
        rounds: 实际执行的 LLM 调用轮数
    """
    text: str = ""
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    ctx_pct: float = 0.0
    error: str | None = None
    rounds: int = 0


# 执行一个完整的"模型→工具→结果→模型"内循环，返回最终回答与轨迹消息
async def run_tool_turn(
    provider: LLMProvider,
    registry: ToolRegistry,
    bus: EventBus,
    *,
    system: str,
    messages: list[dict[str, Any]],
    run_id: str,
    step: int = 0,
    permission_manager: PermissionManager | None = None,
    session_id: str = "",
    max_rounds: int = _MAX_TOOL_ROUNDS,
) -> ToolTurnResult:
    initial_len = len(messages)
    working = list(messages)
    final_text = ""
    ctx_pct = 0.0
    error: str | None = None
    rounds = 0

    for _ in range(max_rounds):
        # 消费运行中修正（run.steer）：只在下一次调用模型前注入——回合边界
        # 【设计】不打断进行中的工具执行；修正随轨迹一并写入会话历史
        for steer in pop_steers(run_id):
            working.append(steer_as_message(steer))
        try:
            response = await provider.chat(
                messages=working,
                tool_schemas=registry.tool_schemas(),
                bus=bus,
                run_id=run_id,
                step=step + rounds,
                system=system,
            )
        except Exception as exc:
            log.error("run_tool_turn LLM call failed: %s", exc)
            error = str(exc)
            break

        if response.usage is not None:
            ctx_pct = response.usage.context_pct

        working = working + [_assistant_msg_from_response(response)]
        rounds += 1

        if response.stop_reason == "error":
            error = response.text or "llm error"
            break
        # 没有工具调用（或工具调用被 max_tokens 截断丢失）→ 回合结束
        if response.stop_reason != "tool_use" and not response.tool_calls:
            final_text = response.text or ""
            break

        results = await asyncio.gather(*[
            invoke_tool(
                registry,
                tc,
                bus,
                run_id,
                permission_manager=permission_manager,
                session_id=session_id,
            )
            for tc in response.tool_calls
        ])
        for tc, result in zip(response.tool_calls, results, strict=True):
            working = _add_tool_result_to_messages(working, tc.id, result)

        # 工具调用后继续循环让模型消费结果；若已到轮数上限则保留最后一次文本
        final_text = response.text or ""
    else:
        log.warning("run_tool_turn hit max_rounds=%d, stopping tool loop", max_rounds)

    trajectory = working[initial_len:]
    return ToolTurnResult(
        text=final_text,
        trajectory=trajectory,
        ctx_pct=ctx_pct,
        error=error,
        rounds=rounds,
    )


# 上下文占用超过阈值时用压缩器重写消息历史，返回（新历史, 是否已压缩）
async def maybe_compact(
    compactor: Compactor | None,
    provider: LLMProvider,
    messages: list[dict[str, Any]],
    ctx_pct: float,
    threshold: float,
    run_id: str,
) -> tuple[list[dict[str, Any]], bool]:
    if compactor is None or threshold <= 0 or ctx_pct < threshold:
        return messages, False
    temp_ctx = ExecutionContext(run_id=run_id, goal="", max_steps=0)
    temp_ctx.messages = list(messages)
    try:
        await compactor.compact(temp_ctx, provider)
    except Exception as exc:
        log.warning("engine compaction failed, continuing: %s", exc)
        return messages, False
    return temp_ctx.messages, True
