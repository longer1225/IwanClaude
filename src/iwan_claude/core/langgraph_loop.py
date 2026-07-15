from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from iwan_claude.core.bus.events import StepFinishedEvent, StepStartedEvent
from iwan_claude.core.compact.compactor import Compactor
from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.base import LLMProvider
from iwan_claude.core.llm.types import LlmResponse, ToolCallBlock
from iwan_claude.core.permissions.manager import PermissionManager
from iwan_claude.core.system_prompt import build_base_system_prompt
from iwan_claude.core.tools.invocation import invoke_tool
from iwan_claude.core.tools.registry import ToolRegistry
import logging

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentState(TypedDict):
    messages: list[dict[str, Any]]
    system_prompt: str
    step: int
    result: str | None
    status: Literal["running", "success", "failed"]
    fail_reason: str | None
    _stop_reason: str | None
    _tool_calls: list[ToolCallBlock] | None
    _usage: Any | None


class LangGraphAgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        bus: EventBus,
        *,
        llm_model_name: str = "",
        permission_manager: PermissionManager | None = None,
        compactor: Compactor | None = None,
        compact_threshold: float = 0.0,
        session_id: str = "",
        checkpointer: Any = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._bus = bus
        self._llm_model_name = llm_model_name
        self._permission_manager = permission_manager
        self._compactor = compactor
        self._compact_threshold = compact_threshold
        self._session_id = session_id
        self._checkpointer = checkpointer
        self._graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(AgentState)

        workflow.add_node("chat", self._chat_node)
        workflow.add_node("tools", self._tools_node)
        workflow.add_node("compact", self._compact_node)
        workflow.add_node("end", self._end_node)

        workflow.add_edge(START, "chat")

        workflow.add_conditional_edges(
            "chat",
            self._chat_router,
            {
                "tool_use": "tools",
                "max_tokens_tool_use": "tools",
                "compact": "compact",
                "end_turn": "end",
                "error": "end",
            },
        )

        workflow.add_conditional_edges(
            "tools",
            self._tools_router,
            {
                "chat": "chat",
                "compact": "compact",
                "error": "end",
            },
        )

        workflow.add_conditional_edges(
            "compact",
            self._compact_router,
            {
                "chat": "chat",
                "end": "end",
            },
        )

        workflow.add_edge("end", END)

        return workflow.compile(checkpointer=self._checkpointer)

    async def run(self, context: ExecutionContext) -> None:
        system_prompt = build_base_system_prompt(self._llm_model_name)

        initial_state: AgentState = {
            "messages": context.messages,
            "system_prompt": system_prompt,
            "step": context.step,
            "result": context.result,
            "status": "running" if not context.is_done() else context.status,
            "fail_reason": context.reason,
            "_stop_reason": None,
            "_tool_calls": None,
            "_usage": None,
        }

        config = {"configurable": {"thread_id": context.run_id}}

        try:
            final_state = await self._graph.ainvoke(initial_state, config)
        except Exception as exc:
            log.error("LangGraph run failed", exc_info=True)
            context.mark_failed(reason=str(exc))
            return

        context.step = final_state["step"]
        context.messages = final_state["messages"]
        context.status = final_state["status"]
        context.result = final_state["result"]
        context.reason = final_state["fail_reason"]

    async def _chat_node(self, state: AgentState, config: Any | None = None) -> dict[str, Any]:
        run_id = ""
        if config is not None and isinstance(config, dict):
            run_id = config.get("configurable", {}).get("run_id", "")
        state["step"] += 1

        await self._bus.publish(
            StepStartedEvent(run_id=run_id, step=state["step"], ts=_now())
        )

        try:
            response = await self._provider.chat(
                messages=state["messages"],
                tool_schemas=self._registry.tool_schemas(),
                bus=self._bus,
                run_id=run_id,
                step=state["step"],
                system=state["system_prompt"],
            )
        except Exception as exc:
            await self._bus.publish(
                StepFinishedEvent(run_id=run_id, step=state["step"], ts=_now())
            )
            return {
                **state,
                "status": "failed",
                "fail_reason": str(exc),
                "_stop_reason": "error",
            }

        new_messages = state["messages"] + [_assistant_msg_from_response(response)]

        await self._bus.publish(
            StepFinishedEvent(run_id=run_id, step=state["step"], ts=_now())
        )

        return {
            **state,
            "messages": new_messages,
            "_stop_reason": response.stop_reason,
            "_tool_calls": response.tool_calls,
            "_usage": response.usage,
        }

    def _chat_router(self, state: AgentState) -> str:
        sr = state.get("_stop_reason")
        if sr == "tool_use":
            return "tool_use"
        elif sr == "max_tokens" and state.get("_tool_calls"):
            return "max_tokens_tool_use"
        elif sr == "end_turn":
            return "end_turn"
        elif sr == "error":
            return "error"

        if self._compactor and self._compact_threshold > 0:
            total_len = sum(len(str(m.get("content", ""))) for m in state["messages"])
            if total_len > self._compact_threshold:
                return "compact"

        if sr == "max_tokens":
            return "compact"

        return "end_turn"

    async def _tools_node(self, state: AgentState, config: Any | None = None) -> dict[str, Any]:
        run_id = ""
        if config is not None and isinstance(config, dict):
            run_id = config.get("configurable", {}).get("run_id", "")
        tool_calls = state.get("_tool_calls") or []
        new_messages = state["messages"]

        for tc in tool_calls:
            result = await invoke_tool(
                self._registry,
                tc,
                self._bus,
                run_id,
                permission_manager=self._permission_manager,
                session_id=self._session_id,
            )
            new_messages = new_messages + [_tool_result_msg(tc.id, result)]

        return {**state, "messages": new_messages}

    def _tools_router(self, state: AgentState) -> str:
        sr = state.get("_stop_reason")
        if sr == "error":
            return "error"

        if self._compactor and self._compact_threshold > 0:
            total_len = sum(len(str(m.get("content", ""))) for m in state["messages"])
            if total_len > self._compact_threshold:
                return "compact"

        return "chat"

    async def _compact_node(self, state: AgentState, config: Any | None = None) -> dict[str, Any]:
        if self._compactor is None:
            return {**state}

        run_id = ""
        if config is not None and isinstance(config, dict):
            run_id = config.get("configurable", {}).get("run_id", "")
        temp_ctx = ExecutionContext(
            run_id=run_id,
            goal="",
            max_steps=state["step"],
        )
        temp_ctx.messages = state["messages"]

        try:
            await self._compactor.compact(temp_ctx, self._provider)
        except Exception as exc:
            log.warning("Compaction failed, continuing without compact", exc_info=True)
            return {**state}

        return {**state, "messages": temp_ctx.messages}

    def _compact_router(self, state: AgentState) -> str:
        sr = state.get("_stop_reason")
        if sr == "end_turn":
            return "end"
        return "chat"

    async def _end_node(self, state: AgentState, config: Any | None = None) -> dict[str, Any]:
        sr = state.get("_stop_reason")
        if sr == "error" or state["status"] == "failed":
            return {**state, "status": "failed"}
        return {**state, "status": "success", "result": _extract_last_assistant_text(state["messages"])}


def _assistant_msg_from_response(response: LlmResponse) -> dict[str, Any]:
    if response.stop_reason == "tool_use" or (response.stop_reason == "max_tokens" and response.tool_calls):
        blocks = []
        if response.thinking_blocks:
            for block in response.thinking_blocks:
                blocks.append({"type": "text", "text": block})
        if response.text:
            blocks.append({"type": "text", "text": response.text})
        for tc in response.tool_calls:
            blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        return {"role": "assistant", "content": blocks}
    else:
        content = response.text or ""
        if response.thinking_blocks:
            content = "\n".join(response.thinking_blocks) + "\n" + content
        return {"role": "assistant", "content": content}


def _tool_result_msg(tool_use_id: str, result: Any) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": result.content if hasattr(result, "content") else str(result),
            "is_error": result.is_error if hasattr(result, "is_error") else False,
        }],
    }


def _extract_last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            continue
                    else:
                        text_parts.append(str(block))
                return "\n".join(text_parts).strip()
            return str(content).strip()
    return ""