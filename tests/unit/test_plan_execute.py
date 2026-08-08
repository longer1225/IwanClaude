"""
Plan & Execute 引擎测试模块

测试内容：
1. _parse_plan：计划解析
2. _execute_router：执行路由
3. _reflect_router：反思路由
4. 完整流程：plan → execute → reflect → end
5. 重新规划流程
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.langgraph_plan_execute import (
    LangGraphPlanExecuteLoop,
    PlanExecuteState,
)
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


def _make_initial_state(user_msg: str = "test task") -> PlanExecuteState:
    """创建初始状态"""
    return {
        "messages": [{"role": "user", "content": user_msg}],
        "system_prompt": "You are a helpful assistant.",
        "plan": [],
        "current_step": 0,
        "step_results": [],
        "reflection": None,
        "status": "planning",
        "result": None,
        "fail_reason": None,
        "step": 0,
        "replan_count": 0,
    }


# ======================================================================
# _parse_plan 测试
# ======================================================================


class TestParsePlan:
    """测试计划解析"""

    def test_parse_numbered_steps(self) -> None:
        """测试解析数字编号步骤"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        plan_text = "1. Read the file\n2. Analyze content\n3. Write report"
        steps = loop._parse_plan(plan_text)
        assert len(steps) == 3
        assert "Read the file" in steps[0]
        assert "Analyze content" in steps[1]
        assert "Write report" in steps[2]

    def test_parse_parenthesis_steps(self) -> None:
        """测试解析括号编号步骤"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        plan_text = "1) First step\n2) Second step"
        steps = loop._parse_plan(plan_text)
        assert len(steps) == 2
        assert "First step" in steps[0]

    def test_parse_dash_steps(self) -> None:
        """测试解析破折号步骤"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        plan_text = "- Step one\n- Step two\n- Step three"
        steps = loop._parse_plan(plan_text)
        assert len(steps) == 3

    def test_parse_empty_text(self) -> None:
        """测试解析空文本"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        steps = loop._parse_plan("")
        assert steps == []

    def test_parse_with_empty_lines(self) -> None:
        """测试解析带空行的文本"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        plan_text = "1. Step one\n\n\n2. Step two\n"
        steps = loop._parse_plan(plan_text)
        assert len(steps) == 2


# ======================================================================
# 路由测试
# ======================================================================


class TestRouters:
    """测试路由逻辑"""

    def test_execute_router_next_step(self) -> None:
        """测试执行路由：还有步骤"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        state: PlanExecuteState = {
            "messages": [],
            "system_prompt": "",
            "plan": ["step1", "step2", "step3"],
            "current_step": 1,  # 还没到最后一步
            "step_results": ["result1"],
            "reflection": None,
            "status": "executing",
            "result": None,
            "fail_reason": None,
            "step": 1,
            "replan_count": 0,
        }
        assert loop._execute_router(state) == "next_step"

    def test_execute_router_reflect(self) -> None:
        """测试执行路由：所有步骤完成"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        state: PlanExecuteState = {
            "messages": [],
            "system_prompt": "",
            "plan": ["step1", "step2"],
            "current_step": 2,  # 所有步骤完成
            "step_results": ["result1", "result2"],
            "reflection": None,
            "status": "executing",
            "result": None,
            "fail_reason": None,
            "step": 2,
            "replan_count": 0,
        }
        assert loop._execute_router(state) == "reflect"

    def test_execute_router_error(self) -> None:
        """测试执行路由：出错"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        state: PlanExecuteState = {
            "messages": [],
            "system_prompt": "",
            "plan": ["step1"],
            "current_step": 0,
            "step_results": [],
            "reflection": None,
            "status": "failed",
            "result": None,
            "fail_reason": "error",
            "step": 0,
            "replan_count": 0,
        }
        assert loop._execute_router(state) == "error"

    def test_reflect_router_done(self) -> None:
        """测试反思路由：满意"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        state: PlanExecuteState = {
            "messages": [],
            "system_prompt": "",
            "plan": ["step1"],
            "current_step": 1,
            "step_results": ["result1"],
            "reflection": "SATISFIED: Task completed successfully.",
            "status": "reflecting",
            "result": None,
            "fail_reason": None,
            "step": 1,
            "replan_count": 0,
        }
        assert loop._reflect_router(state) == "done"

    def test_reflect_router_replan(self) -> None:
        """测试反思路由：需要重新规划"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        state: PlanExecuteState = {
            "messages": [],
            "system_prompt": "",
            "plan": ["step1"],
            "current_step": 1,
            "step_results": ["result1"],
            "reflection": "NEEDS_REPLAN: The approach was wrong.",
            "status": "reflecting",
            "result": None,
            "fail_reason": None,
            "step": 1,
            "replan_count": 0,
        }
        assert loop._reflect_router(state) == "replan"

    def test_reflect_router_max_replan(self) -> None:
        """测试反思路由：超过最大重规划次数"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )
        state: PlanExecuteState = {
            "messages": [],
            "system_prompt": "",
            "plan": ["step1"],
            "current_step": 1,
            "step_results": ["result1"],
            "reflection": "NEEDS_REPLAN: Still not right.",
            "status": "reflecting",
            "result": None,
            "fail_reason": None,
            "step": 1,
            "replan_count": 5,  # 超过最大次数
        }
        # 超过最大次数，强制结束
        assert loop._reflect_router(state) == "done"


# ======================================================================
# 完整流程测试
# ======================================================================


class TestPlanExecuteFlow:
    """测试完整 Plan & Execute 流程"""

    def test_simple_flow(self) -> None:
        """测试简单流程：plan → execute → reflect → done"""
        # mock provider：依次返回计划、执行结果、反思
        plan_response = LlmResponse(
            text="1. Read file\n2. Analyze content",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        execute_response = LlmResponse(
            text="File read successfully. Content analyzed.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        reflect_response = LlmResponse(
            text="SATISFIED: Task completed. The file was read and analyzed.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )

        provider = MagicMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=[
            plan_response,
            execute_response,
            execute_response,  # 两个步骤
            reflect_response,
        ])

        loop = LangGraphPlanExecuteLoop(
            provider=provider,
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
            session_id="test_session",
        )

        context = ExecutionContext(
            run_id="test_run",
            goal="Read and analyze the file",
            max_steps=10,
        )

        asyncio.run(loop.run(context))

        # 验证结果
        assert context.status == "success"
        assert context.result is not None
        assert "SATISFIED" in context.result or "completed" in context.result.lower()

    def test_replan_flow(self) -> None:
        """测试重新规划流程"""
        # 第一次计划 + 执行 + 反思（需要重新规划）
        # 第二次计划 + 执行 + 反思（满意）
        plan_response1 = LlmResponse(
            text="1. Wrong approach",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        execute_response1 = LlmResponse(
            text="Failed to execute",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        reflect_response1 = LlmResponse(
            text="NEEDS_REPLAN: Wrong approach, need to try differently.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        plan_response2 = LlmResponse(
            text="1. Correct approach",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        execute_response2 = LlmResponse(
            text="Successfully executed",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )
        reflect_response2 = LlmResponse(
            text="SATISFIED: Task completed with correct approach.",
            stop_reason="end_turn",
            usage=None,
            tool_calls=None,
        )

        provider = MagicMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=[
            plan_response1,
            execute_response1,
            reflect_response1,
            plan_response2,
            execute_response2,
            reflect_response2,
        ])

        loop = LangGraphPlanExecuteLoop(
            provider=provider,
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
            session_id="test_session",
        )

        context = ExecutionContext(
            run_id="test_run",
            goal="Do the task",
            max_steps=10,
        )

        asyncio.run(loop.run(context))

        assert context.status == "success"
        # 应该调用了 6 次 LLM（2次计划 + 2次执行 + 2次反思）
        assert provider.chat.call_count == 6

    def test_plan_failure(self) -> None:
        """测试规划失败"""
        provider = MagicMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=RuntimeError("API error"))

        loop = LangGraphPlanExecuteLoop(
            provider=provider,
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
            session_id="test_session",
        )

        context = ExecutionContext(
            run_id="test_run",
            goal="test task",
            max_steps=10,
        )

        asyncio.run(loop.run(context))

        assert context.status == "failed"
        assert "API error" in (context.reason or "")

    def test_end_node_extracts_result(self) -> None:
        """测试 end 节点从反思中提取结果"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )

        state: PlanExecuteState = {
            "messages": [],
            "system_prompt": "",
            "plan": ["step1"],
            "current_step": 1,
            "step_results": ["result1"],
            "reflection": "SATISFIED: The answer is 42.",
            "status": "reflecting",
            "result": None,
            "fail_reason": None,
            "step": 1,
            "replan_count": 0,
        }

        result = asyncio.run(loop._end_node(state))
        assert result["status"] == "done"
        assert "42" in result["result"]

    def test_end_node_fallback_to_step_results(self) -> None:
        """测试 end 节点回退到步骤结果"""
        loop = LangGraphPlanExecuteLoop(
            provider=_make_mock_provider(),
            registry=_make_mock_registry(),
            bus=_make_mock_bus(),
        )

        state: PlanExecuteState = {
            "messages": [],
            "system_prompt": "",
            "plan": ["step1", "step2"],
            "current_step": 2,
            "step_results": ["result1", "result2"],
            "reflection": "Some reflection without clear marker",
            "status": "reflecting",
            "result": None,
            "fail_reason": None,
            "step": 2,
            "replan_count": 0,
        }

        result = asyncio.run(loop._end_node(state))
        assert result["status"] == "done"
        # 没有SATISFIED标记时，回退到步骤结果
        assert "result1" in result["result"]
        assert "result2" in result["result"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
