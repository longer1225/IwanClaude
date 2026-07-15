from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from iwan_claude.core.config import AgentConfig, IwanConfig
from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats
from iwan_claude.core.langgraph_loop import LangGraphAgentLoop
from iwan_claude.core.tools.registry import ToolRegistry


def _usage() -> UsageStats:
    return UsageStats(input_tokens=10, output_tokens=20)


@pytest.mark.asyncio
async def test_langgraph_checkpoint_none_is_default(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LlmResponse(
        stop_reason="end_turn",
        tool_calls=[],
        text="hello",
        usage=_usage(),
    ))
    registry = ToolRegistry()
    bus = EventBus()
    loop = LangGraphAgentLoop(provider, registry, bus, checkpointer=None)

    ctx = ExecutionContext(run_id="test123", goal="test", max_steps=5)
    ctx.messages = [{"role": "user", "content": "hi"}]

    await loop.run(ctx)

    assert ctx.status == "success"
    assert ctx.result == "hello"


@pytest.mark.asyncio
async def test_langgraph_checkpoint_sqlite_persists_and_restores(tmp_path: Path) -> None:
    db_path = tmp_path / "checkpoints" / "checkpoints.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    provider = MagicMock()
    call_count = 0

    async def _chat(*args: object, **kwargs: object) -> LlmResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LlmResponse(
                stop_reason="end_turn",
                tool_calls=[],
                text="first response",
                usage=_usage(),
            )
        elif call_count == 2:
            return LlmResponse(
                stop_reason="end_turn",
                tool_calls=[],
                text="second response",
                usage=_usage(),
            )
        return LlmResponse(
            stop_reason="end_turn",
            tool_calls=[],
            text="unexpected",
            usage=_usage(),
        )

    provider.chat = _chat
    registry = ToolRegistry()
    bus = EventBus()

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(str(db_path.resolve())) as saver:
        loop1 = LangGraphAgentLoop(provider, registry, bus, checkpointer=saver)
        ctx1 = ExecutionContext(run_id="checkpoint_test", goal="test", max_steps=5)
        ctx1.messages = [{"role": "user", "content": "hello"}]

        await loop1.run(ctx1)

        assert ctx1.status == "success"
        assert ctx1.result == "first response"
        assert call_count == 1

        loop2 = LangGraphAgentLoop(provider, registry, bus, checkpointer=saver)
        ctx2 = ExecutionContext(run_id="checkpoint_test", goal="test", max_steps=5)
        ctx2.messages = [{"role": "user", "content": "hello"}]

        await loop2.run(ctx2)

        assert ctx2.status == "success"
        assert ctx2.result == "second response"
        assert call_count == 2


@pytest.mark.asyncio
async def test_langgraph_checkpoint_memory_persists_and_restores(tmp_path: Path) -> None:
    provider = MagicMock()
    call_count = 0

    async def _chat(*args: object, **kwargs: object) -> LlmResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LlmResponse(
                stop_reason="end_turn",
                tool_calls=[],
                text="mem first",
                usage=_usage(),
            )
        elif call_count == 2:
            return LlmResponse(
                stop_reason="end_turn",
                tool_calls=[],
                text="mem second",
                usage=_usage(),
            )
        return LlmResponse(
            stop_reason="end_turn",
            tool_calls=[],
            text="unexpected",
            usage=_usage(),
        )

    provider.chat = _chat
    registry = ToolRegistry()
    bus = EventBus()

    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()

    loop1 = LangGraphAgentLoop(provider, registry, bus, checkpointer=saver)
    ctx1 = ExecutionContext(run_id="mem_test", goal="test", max_steps=5)
    ctx1.messages = [{"role": "user", "content": "hi"}]

    await loop1.run(ctx1)

    assert ctx1.status == "success"
    assert ctx1.result == "mem first"
    assert call_count == 1

    loop2 = LangGraphAgentLoop(provider, registry, bus, checkpointer=saver)
    ctx2 = ExecutionContext(run_id="mem_test", goal="test", max_steps=5)
    ctx2.messages = [{"role": "user", "content": "hi"}]

    await loop2.run(ctx2)

    assert ctx2.status == "success"
    assert ctx2.result == "mem second"
    assert call_count == 2


@pytest.mark.asyncio
async def test_langgraph_checkpointer_close_cleans_up(tmp_path: Path) -> None:
    db_path = tmp_path / "checkpoints" / "close_test.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(str(db_path.resolve())) as saver:
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=LlmResponse(
            stop_reason="end_turn",
            tool_calls=[],
            text="ok",
            usage=_usage(),
        ))
        registry = ToolRegistry()
        bus = EventBus()

        loop = LangGraphAgentLoop(provider, registry, bus, checkpointer=saver)
        ctx = ExecutionContext(run_id="close_test", goal="test", max_steps=5)
        ctx.messages = [{"role": "user", "content": "hi"}]

        await loop.run(ctx)

    assert db_path.exists()


@pytest.mark.asyncio
async def test_runner_list_checkpoints_memory(tmp_path: Path) -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LlmResponse(
        stop_reason="end_turn",
        tool_calls=[],
        text="hello",
        usage=_usage(),
    ))
    registry = ToolRegistry()
    bus = EventBus()

    loop = LangGraphAgentLoop(provider, registry, bus, checkpointer=saver)
    ctx = ExecutionContext(run_id="list_test", goal="test", max_steps=5)
    ctx.messages = [{"role": "user", "content": "hi"}]

    await loop.run(ctx)

    checkpoints = list(saver.list({"configurable": {"thread_id": "list_test"}}))
    assert len(checkpoints) >= 1


@pytest.mark.asyncio
async def test_runner_list_checkpoints_none_backend(tmp_path: Path) -> None:
    from iwan_claude.core.runner import AgentRunner
    from iwan_claude.core.config import IwanConfig

    config = IwanConfig(agent=AgentConfig(checkpoint_backend="none"))
    runner = AgentRunner(config)

    checkpoints = await runner.list_checkpoints("test_thread")
    assert checkpoints == []


@pytest.mark.asyncio
async def test_runner_restore_checkpoint_memory(tmp_path: Path) -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LlmResponse(
        stop_reason="end_turn",
        tool_calls=[],
        text="hello",
        usage=_usage(),
    ))
    registry = ToolRegistry()
    bus = EventBus()

    loop = LangGraphAgentLoop(provider, registry, bus, checkpointer=saver)
    ctx = ExecutionContext(run_id="restore_test", goal="test", max_steps=5)
    ctx.messages = [{"role": "user", "content": "hi"}]

    await loop.run(ctx)

    checkpoints = list(saver.list({"configurable": {"thread_id": "restore_test"}}))
    assert len(checkpoints) >= 1

    cp_tuple = checkpoints[0]
    checkpoint_id = cp_tuple.config.get("configurable", {}).get("checkpoint_id")

    restored = saver.get_tuple({"configurable": {"thread_id": "restore_test", "checkpoint_id": checkpoint_id}})
    assert restored is not None
    assert "channel_values" in restored.checkpoint