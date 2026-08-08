"""
Debate 引擎测试模块

测试内容：
1. _parse_verdict：critic 反馈解析
2. _worker_router / _critic_router：路由逻辑
3. 完整流程：worker → critic → end（含重辩、max_rounds、失败等场景）
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.langgraph_debate import DebateState, LangGraphDebateLoop
from iwan_claude.core.llm.base import LLMProvider
from iwan_claude.core.llm.types import LlmResponse
from iwan_claude.core.tools.registry import ToolRegistry


# ======================================================================
# 辅助函数
# ======================================================================


def _make_mock_provider(response_text: str = "test") -> MagicMock:
    """创建 mock LLM provider"""
    provider = MagicMock(spec=LLMProvider)
    response = LlmResponse(
        text=response_text,
        stop_reason="end_turn",
        usage=None,
        tool_calls=None,
    )
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
    round: int = 0,
    max_rounds: int = 3,
    worker_answer: str | None = None,
    critic_feedback: str | None = None,
    critic_verdict: str | None = None,
    status: str = "debating",
    fail_reason: str | None = None,
) -> DebateState:
    """创建初始 debate 状态"""
    return {
        "messages": [{"role": "user", "content": user_msg}],
        "worker_system": "You are a worker.",
        "critic_system": "You are a critic.",
        "user_request": user_msg,
        "worker_answer": worker_answer,
        "critic_feedback": critic_feedback,
        "critic_verdict": critic_verdict,
        "round": round,
        "max_rounds": max_rounds,
        "status": status,
        "result": None,
        "fail_reason": fail_reason,
        "step": 0,
    }


def _make_loop(
    provider: MagicMock | None = None,
) -> LangGraphDebateLoop:
    """创建 debate loop 实例"""
    return LangGraphDebateLoop(
        provider=provider or _make_mock_provider(),
        registry=_make_mock_registry(),
        bus=_make_mock_bus(),
        session_id="test_session",
    )


# ======================================================================
# _parse_verdict 测试
# ======================================================================


class TestParseVerdict:
    """测试 critic 反馈解析"""

    def test_parse_satisfied(self) -> None:
        """测试解析 SATISFIED"""
        loop = _make_loop()
        assert loop._parse_verdict("SATISFIED: The answer is correct") == "satisfied"

    def test_parse_needs_improvement(self) -> None:
        """测试解析 NEEDS_IMPROVEMENT"""
        loop = _make_loop()
        assert loop._parse_verdict("NEEDS_IMPROVEMENT: Missing key detail") == "needs_improvement"

    def test_parse_case_insensitive(self) -> None:
        """测试大小写不敏感"""
        loop = _make_loop()
        assert loop._parse_verdict("satisfied: good") == "satisfied"
        assert loop._parse_verdict("Needs_Improvement: bad") == "needs_improvement"

    def test_parse_empty(self) -> None:
        """测试空文本（默认 satisfied）"""
        loop = _make_loop()
        assert loop._parse_verdict("") == "satisfied"

    def test_parse_unknown(self) -> None:
        """测试未知输出（默认 satisfied，安全结束）"""
        loop = _make_loop()
        assert loop._parse_verdict("The answer looks fine to me.") == "satisfied"

    def test_parse_none(self) -> None:
        """测试 None 输入（默认 satisfied）"""
        loop = _make_loop()
        assert loop._parse_verdict(None) == "satisfied"  # type: ignore[arg-type]


# ======================================================================
# 路由测试
# ======================================================================


class TestRouters:
    """测试路由逻辑"""

    def test_worker_router_to_critic(self) -> None:
        """测试 worker 路由：成功 → critic"""
        loop = _make_loop()
        state = _make_initial_state(status="debating")
        assert loop._worker_router(state) == "critic"

    def test_worker_router_error(self) -> None:
        """测试 worker 路由：失败 → end"""
        loop = _make_loop()
        state = _make_initial_state(status="failed", fail_reason="worker error")
        assert loop._worker_router(state) == "error"

    def test_critic_router_satisfied(self) -> None:
        """测试 critic 路由：满意 → done"""
        loop = _make_loop()
        state = _make_initial_state(critic_verdict="satisfied", round=1)
        assert loop._critic_router(state) == "done"

    def test_critic_router_redebate(self) -> None:
        """测试 critic 路由：需改进且未达上限 → worker"""
        loop = _make_loop()
        state = _make_initial_state(critic_verdict="needs_improvement", round=1, max_rounds=3)
        assert loop._critic_router(state) == "worker"

    def test_critic_router_max_rounds(self) -> None:
        """测试 critic 路由：需改进但达上限 → done（强制结束）"""
        loop = _make_loop()
        state = _make_initial_state(critic_verdict="needs_improvement", round=3, max_rounds=3)
        assert loop._critic_router(state) == "done"

    def test_critic_router_error(self) -> None:
        """测试 critic 路由：失败 → end"""
        loop = _make_loop()
        state = _make_initial_state(status="failed", fail_reason="critic error", round=1)
        assert loop._critic_router(state) == "error"

    def test_critic_router_unknown_verdict(self) -> None:
        """测试 critic 路由：未知判定（None）→ done（安全结束）"""
        loop = _make_loop()
        state = _make_initial_state(critic_verdict=None, round=1)
        assert loop._critic_router(state) == "done"


# ======================================================================
# 完整流程测试
# ======================================================================


class TestDebateFlow:
    """测试完整 Debate 流程"""

    def test_simple_flow(self) -> None:
        """测试简单流程：worker → critic(satisfied) → done"""
        # mock provider：worker 回答 → critic 满意
        worker_response = LlmResponse(
            text="The answer is 42.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        critic_response = LlmResponse(
            text="SATISFIED: The answer is correct and complete.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )

        provider = MagicMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=[worker_response, critic_response])

        loop = _make_loop(provider=provider)

        context = ExecutionContext(
            run_id="test_run",
            goal="What is the answer?",
            max_steps=10,
        )

        asyncio.run(loop.run(context))

        # 验证结果
        assert context.status == "success"
        assert "42" in context.result
        # 应该调用了 2 次 LLM（1次 worker + 1次 critic）
        assert provider.chat.call_count == 2

    def test_redebate_flow(self) -> None:
        """测试重辩流程：worker → critic(needs_improvement) → worker → critic(satisfied)"""
        worker_response1 = LlmResponse(
            text="The answer is 41.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        critic_response1 = LlmResponse(
            text="NEEDS_IMPROVEMENT: The answer is wrong, should be 42.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        worker_response2 = LlmResponse(
            text="The answer is 42.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        critic_response2 = LlmResponse(
            text="SATISFIED: Now correct.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )

        provider = MagicMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=[
            worker_response1,
            critic_response1,
            worker_response2,
            critic_response2,
        ])

        loop = _make_loop(provider=provider)

        context = ExecutionContext(
            run_id="test_run",
            goal="What is the answer?",
            max_steps=10,
        )

        asyncio.run(loop.run(context))

        assert context.status == "success"
        assert "42" in context.result
        # 4 次 LLM（2次 worker + 2次 critic）
        assert provider.chat.call_count == 4

    def test_max_rounds_forced_completion(self) -> None:
        """测试达到最大轮数强制结束（3 轮都 needs_improvement）"""
        worker_response = LlmResponse(
            text="Still trying...",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        critic_response = LlmResponse(
            text="NEEDS_IMPROVEMENT: Still not good enough.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )

        provider = MagicMock(spec=LLMProvider)
        # 3 轮：worker, critic × 3
        provider.chat = AsyncMock(side_effect=[
            worker_response, critic_response,
            worker_response, critic_response,
            worker_response, critic_response,
        ])

        loop = _make_loop(provider=provider)

        context = ExecutionContext(
            run_id="test_run",
            goal="Impossible task",
            max_steps=20,
        )

        asyncio.run(loop.run(context))

        # 达到 max_rounds 后强制结束，但仍视为成功（取最后一次 worker_answer）
        assert context.status == "success"
        assert "Still trying" in context.result
        # 6 次 LLM（3次 worker + 3次 critic）
        assert provider.chat.call_count == 6

    def test_worker_failure(self) -> None:
        """测试 worker 失败"""
        provider = MagicMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=RuntimeError("API error"))

        loop = _make_loop(provider=provider)

        context = ExecutionContext(
            run_id="test_run",
            goal="test task",
            max_steps=10,
        )

        asyncio.run(loop.run(context))

        assert context.status == "failed"
        assert "API error" in (context.reason or "")

    def test_critic_failure(self) -> None:
        """测试 critic 失败"""
        worker_response = LlmResponse(
            text="Worker's answer.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )

        provider = MagicMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=[
            worker_response,
            RuntimeError("Critic API error"),
        ])

        loop = _make_loop(provider=provider)

        context = ExecutionContext(
            run_id="test_run",
            goal="test task",
            max_steps=10,
        )

        asyncio.run(loop.run(context))

        assert context.status == "failed"
        assert "Critic" in (context.reason or "")

    def test_end_node_extracts_result(self) -> None:
        """测试 end 节点提取 worker_answer 作为结果"""
        loop = _make_loop()

        state = _make_initial_state(
            worker_answer="The final answer is 42.",
            status="debating",
        )

        result = asyncio.run(loop._end_node(state))
        assert result["status"] == "done"
        assert "42" in result["result"]

    def test_end_node_appends_assistant_message(self) -> None:
        """测试 end 节点追加 assistant 消息到会话历史"""
        loop = _make_loop()

        state = _make_initial_state(
            worker_answer="The answer.",
            status="debating",
        )

        result = asyncio.run(loop._end_node(state))

        # 验证追加了 assistant 消息
        messages = result["messages"]
        assert len(messages) == 2  # 原 user + 新 assistant
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == "The answer."

    def test_end_node_failure_path(self) -> None:
        """测试 end 节点失败路径（有 fail_reason）"""
        loop = _make_loop()

        state = _make_initial_state(
            status="failed",
            fail_reason="Worker failed: timeout",
        )

        result = asyncio.run(loop._end_node(state))
        assert result["status"] == "failed"
        assert "timeout" in result["result"]

    def test_end_node_empty_answer(self) -> None:
        """测试 end 节点 worker_answer 为空时的兜底"""
        loop = _make_loop()

        state = _make_initial_state(
            worker_answer=None,
            status="debating",
        )

        result = asyncio.run(loop._end_node(state))
        assert result["status"] == "done"
        assert result["result"] == "No result."

    def test_run_syncs_messages_to_context(self) -> None:
        """测试 run() 将最终 messages 同步回 context（客户端能拿到回复）"""
        worker_response = LlmResponse(
            text="Final answer.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        critic_response = LlmResponse(
            text="SATISFIED: Good.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )

        provider = MagicMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=[worker_response, critic_response])

        loop = _make_loop(provider=provider)

        context = ExecutionContext(
            run_id="test_run",
            goal="test",
            max_steps=10,
        )

        asyncio.run(loop.run(context))

        # context.messages 应包含 assistant 回复
        assert len(context.messages) >= 2
        assert context.messages[-1]["role"] == "assistant"
        assert "Final answer" in context.messages[-1]["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
