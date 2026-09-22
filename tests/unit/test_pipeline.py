"""
Pipeline 引擎测试模块

测试内容：
1. _parse_verdict：reviewer 反馈解析（APPROVED / NEEDS_REWORK）
2. _planner_router / _executor_router / _reviewer_router：路由逻辑
3. 完整流程：planner → executor → reviewer → end（含返工、max_rounds、失败等场景）
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.langgraph_pipeline import PipelineState, LangGraphPipelineLoop
from iwan_claude.core.llm.base import LLMProvider
from iwan_claude.core.llm.types import LlmResponse
from iwan_claude.core.tools.registry import ToolRegistry


# ======================================================================
# 辅助函数
# ======================================================================


def _make_mock_provider(side_effect=None, response_text: str = "test") -> MagicMock:
    """创建 mock LLM provider，支持 side_effect（多次调用返回不同结果）"""
    provider = MagicMock(spec=LLMProvider)
    response = LlmResponse(
        text=response_text,
        stop_reason="end_turn",
        usage=None,
        tool_calls=None,
    )
    if side_effect is not None:
        provider.chat = AsyncMock(side_effect=side_effect)
    else:
        provider.chat = AsyncMock(return_value=response)
    return provider


def _make_mock_bus() -> MagicMock:
    """创建 mock EventBus"""
    bus = MagicMock(spec=EventBus)
    bus.publish = AsyncMock()
    return bus


def _make_mock_registry() -> MagicMock:
    """创建 mock ToolRegistry"""
    registry = MagicMock(spec=ToolRegistry)
    registry.list_tools = MagicMock(return_value=[])
    registry.tool_schemas = MagicMock(return_value=[])
    return registry


def _make_initial_state(
    user_msg: str = "test task",
    *,
    plan: str | None = None,
    executor_result: str | None = None,
    reviewer_feedback: str | None = None,
    reviewer_verdict: str | None = None,
    round: int = 0,
    max_rounds: int = 2,
    replanned: bool = False,
    status: str = "planning",
    fail_reason: str | None = None,
) -> PipelineState:
    """创建初始 pipeline 状态"""
    return {
        "messages": [{"role": "user", "content": user_msg}],
        "planner_system": "You are a planner.",
        "executor_system": "You are an executor.",
        "reviewer_system": "You are a reviewer.",
        "user_request": user_msg,
        "plan": plan,
        "executor_result": executor_result,
        "reviewer_feedback": reviewer_feedback,
        "reviewer_verdict": reviewer_verdict,
        "round": round,
        "max_rounds": max_rounds,
        "replanned": replanned,
        "status": status,
        "result": None,
        "fail_reason": fail_reason,
        "step": 0,
    }


# ======================================================================
# _parse_verdict 测试
# ======================================================================


class TestParseVerdict:
    """测试 reviewer 反馈解析"""

    def test_parse_approved(self) -> None:
        """APPROVED 被正确解析"""
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._parse_verdict("APPROVED: The work is correct") == "approved"

    def test_parse_approved_lowercase(self) -> None:
        """小写 approved 也被正确解析"""
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._parse_verdict("approved: good job") == "approved"

    def test_parse_needs_rework(self) -> None:
        """NEEDS_REWORK 被正确解析"""
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._parse_verdict("NEEDS_REWORK: missing error handling") == "needs_rework"

    def test_parse_needs_rework_lowercase(self) -> None:
        """小写 needs_rework 也被正确解析"""
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._parse_verdict("needs_rework: fix the bug") == "needs_rework"

    def test_parse_unknown_defaults_approved(self) -> None:
        """未知输出默认 approved（安全结束）"""
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._parse_verdict("I think it's fine") == "approved"

    def test_parse_empty_defaults_approved(self) -> None:
        """空字符串默认 approved"""
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._parse_verdict("") == "approved"


# ======================================================================
# 路由测试
# ======================================================================


class TestRouters:
    """测试路由逻辑"""

    def test_planner_router_success(self) -> None:
        """planner 成功 → executor"""
        state = _make_initial_state(status="executing")
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._planner_router(state) == "executor"

    def test_planner_router_failed(self) -> None:
        """planner 失败 → error"""
        state = _make_initial_state(status="failed")
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._planner_router(state) == "error"

    def test_executor_router_success(self) -> None:
        """executor 成功 → reviewer"""
        state = _make_initial_state(status="reviewing")
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._executor_router(state) == "reviewer"

    def test_executor_router_failed(self) -> None:
        """executor 失败 → error"""
        state = _make_initial_state(status="failed")
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._executor_router(state) == "error"

    def test_reviewer_router_approved(self) -> None:
        """reviewer 通过 → done"""
        state = _make_initial_state(reviewer_verdict="approved", status="reviewing")
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._reviewer_router(state) == "done"

    def test_reviewer_router_needs_rework_first_time_to_planner(self) -> None:
        """reviewer 需返工且首次（未重新规划过）→ planner（角色复用：反馈回传给 planner）"""
        state = _make_initial_state(
            reviewer_verdict="needs_rework",
            round=0,
            max_rounds=2,
            replanned=False,  # 首次返工
            status="reviewing",
        )
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._reviewer_router(state) == "planner"

    def test_reviewer_router_needs_rework_after_replan_to_executor(self) -> None:
        """reviewer 需返工但已重新规划过 → executor（避免无限重规划）"""
        state = _make_initial_state(
            reviewer_verdict="needs_rework",
            round=1,
            max_rounds=2,
            replanned=True,  # 已重新规划过
            status="reviewing",
        )
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._reviewer_router(state) == "executor"

    def test_reviewer_router_max_rounds(self) -> None:
        """reviewer 需返工但达最大轮数 → done"""
        state = _make_initial_state(
            reviewer_verdict="needs_rework",
            round=2,
            max_rounds=2,
            status="reviewing",
        )
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._reviewer_router(state) == "done"

    def test_reviewer_router_failed(self) -> None:
        """reviewer 失败 → error"""
        state = _make_initial_state(status="failed")
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._reviewer_router(state) == "error"

    def test_reviewer_router_unknown_verdict(self) -> None:
        """reviewer 未知判定 → done（安全结束）"""
        state = _make_initial_state(reviewer_verdict=None, status="reviewing")
        loop = LangGraphPipelineLoop(_make_mock_provider(), _make_mock_registry(), _make_mock_bus())
        assert loop._reviewer_router(state) == "done"


# ======================================================================
# 完整流程测试
# ======================================================================


class TestPipelineFlow:
    """测试完整的 pipeline 流程"""

    @pytest.mark.asyncio
    async def test_simple_flow_approved_first_round(self) -> None:
        """简单流程：planner → executor → reviewer(APPROVED) → end"""
        provider = _make_mock_provider(side_effect=[
            LlmResponse(text="1. Step one\n2. Step two", stop_reason="end_turn", usage=None, tool_calls=None),  # planner
            LlmResponse(text="Executed both steps", stop_reason="end_turn", usage=None, tool_calls=None),       # executor
            LlmResponse(text="APPROVED: Work is complete", stop_reason="end_turn", usage=None, tool_calls=None), # reviewer
        ])
        loop = LangGraphPipelineLoop(provider, _make_mock_registry(), _make_mock_bus())
        context = ExecutionContext(run_id="test", goal="do something", max_steps=5)
        await loop.run(context)

        assert context.status == "success"
        assert "Executed both steps" in context.result
        assert context.messages[-1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_flow_with_rework(self) -> None:
        """返工流程（角色复用）：planner → executor → reviewer(NEEDS_REWORK)
        → planner(基于反馈重新规划) → executor → reviewer(APPROVED) → end
        """
        provider = _make_mock_provider(side_effect=[
            LlmResponse(text="Plan: do X", stop_reason="end_turn", usage=None, tool_calls=None),                # planner (1st)
            LlmResponse(text="Did X wrong", stop_reason="end_turn", usage=None, tool_calls=None),               # executor (1st)
            LlmResponse(text="NEEDS_REWORK: X is wrong", stop_reason="end_turn", usage=None, tool_calls=None),  # reviewer (1st)
            LlmResponse(text="Revised plan: do X correctly", stop_reason="end_turn", usage=None, tool_calls=None),  # planner (replan)
            LlmResponse(text="Did X correctly now", stop_reason="end_turn", usage=None, tool_calls=None),     # executor (2nd)
            LlmResponse(text="APPROVED: X is correct", stop_reason="end_turn", usage=None, tool_calls=None),   # reviewer (2nd)
        ])
        loop = LangGraphPipelineLoop(provider, _make_mock_registry(), _make_mock_bus())
        context = ExecutionContext(run_id="test", goal="do X", max_steps=10)
        await loop.run(context)

        assert context.status == "success"
        assert "Did X correctly now" in context.result

    @pytest.mark.asyncio
    async def test_flow_max_rounds_forced_done(self) -> None:
        """达到最大轮数强制结束（含 planner 重规划）：
        planner → executor → reviewer(NEEDS_REWORK) → planner(replan) → executor → reviewer(NEEDS_REWORK) → end
        """
        provider = _make_mock_provider(side_effect=[
            LlmResponse(text="Plan: do Y", stop_reason="end_turn", usage=None, tool_calls=None),                # planner (1st)
            LlmResponse(text="Attempt 1", stop_reason="end_turn", usage=None, tool_calls=None),                # executor (1st)
            LlmResponse(text="NEEDS_REWORK: not good", stop_reason="end_turn", usage=None, tool_calls=None),   # reviewer (1st)
            LlmResponse(text="Revised plan", stop_reason="end_turn", usage=None, tool_calls=None),             # planner (replan)
            LlmResponse(text="Attempt 2", stop_reason="end_turn", usage=None, tool_calls=None),                # executor (2nd)
            LlmResponse(text="NEEDS_REWORK: still not good", stop_reason="end_turn", usage=None, tool_calls=None),  # reviewer (2nd)
        ])
        loop = LangGraphPipelineLoop(provider, _make_mock_registry(), _make_mock_bus())
        context = ExecutionContext(run_id="test", goal="do Y", max_steps=10)
        await loop.run(context)

        # 达到 max_rounds=2，强制结束（用最后一次 executor 的结果）
        assert context.status == "success"
        assert "Attempt 2" in context.result

    @pytest.mark.asyncio
    async def test_planner_failure(self) -> None:
        """planner 失败 → end(failed)"""
        provider = MagicMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=RuntimeError("LLM error"))
        loop = LangGraphPipelineLoop(provider, _make_mock_registry(), _make_mock_bus())
        context = ExecutionContext(run_id="test", goal="test", max_steps=5)
        await loop.run(context)

        assert context.status == "failed"
        assert "Planner failed" in (context.reason or "")

    @pytest.mark.asyncio
    async def test_executor_failure(self) -> None:
        """executor 失败 → end(failed)"""
        provider = _make_mock_provider(side_effect=[
            LlmResponse(text="Plan: do Z", stop_reason="end_turn", usage=None, tool_calls=None),  # planner OK
            RuntimeError("Executor LLM error"),                                                      # executor fails
        ])
        loop = LangGraphPipelineLoop(provider, _make_mock_registry(), _make_mock_bus())
        context = ExecutionContext(run_id="test", goal="do Z", max_steps=5)
        await loop.run(context)

        assert context.status == "failed"
        assert "Executor failed" in (context.reason or "")

    @pytest.mark.asyncio
    async def test_reviewer_failure(self) -> None:
        """reviewer 失败 → end(failed)"""
        provider = _make_mock_provider(side_effect=[
            LlmResponse(text="Plan: do W", stop_reason="end_turn", usage=None, tool_calls=None),  # planner OK
            LlmResponse(text="Did W", stop_reason="end_turn", usage=None, tool_calls=None),       # executor OK
            RuntimeError("Reviewer LLM error"),                                                      # reviewer fails
        ])
        loop = LangGraphPipelineLoop(provider, _make_mock_registry(), _make_mock_bus())
        context = ExecutionContext(run_id="test", goal="do W", max_steps=5)
        await loop.run(context)

        assert context.status == "failed"
        assert "Reviewer failed" in (context.reason or "")

    @pytest.mark.asyncio
    async def test_end_node_appends_assistant_message(self) -> None:
        """end 节点将结果作为 assistant 消息追加到 messages"""
        provider = _make_mock_provider(side_effect=[
            LlmResponse(text="Plan", stop_reason="end_turn", usage=None, tool_calls=None),
            LlmResponse(text="Executed", stop_reason="end_turn", usage=None, tool_calls=None),
            LlmResponse(text="APPROVED", stop_reason="end_turn", usage=None, tool_calls=None),
        ])
        loop = LangGraphPipelineLoop(provider, _make_mock_registry(), _make_mock_bus())
        context = ExecutionContext(run_id="test", goal="test", max_steps=5)
        await loop.run(context)

        # 最后一条消息应该是 assistant 角色
        last_msg = context.messages[-1]
        assert last_msg["role"] == "assistant"
        assert "Executed" in last_msg["content"]
