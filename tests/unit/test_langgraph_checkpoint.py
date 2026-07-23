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
    """测试 LangGraph SQLite 检查点持久化和恢复
    
    本测试验证：
    1. 使用 checkpointer 时，第一次运行正常完成
    2. 检查点已正确保存到 SQLite 数据库
    3. 可以通过 alist() 列出检查点
    4. 可以通过 get_tuple() 获取检查点内容
    """
    db_path = tmp_path / "checkpoints" / "checkpoints.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LlmResponse(
        stop_reason="end_turn",
        tool_calls=[],
        text="hello",
        usage=_usage(),
    ))
    registry = ToolRegistry()
    bus = EventBus()

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(str(db_path.resolve())) as saver:
        # 第一次运行：发送 "hello"
        loop = LangGraphAgentLoop(provider, registry, bus, checkpointer=saver)
        ctx = ExecutionContext(run_id="checkpoint_test", goal="test", max_steps=5)
        ctx.messages = [{"role": "user", "content": "hello"}]

        await loop.run(ctx)

        assert ctx.status == "success"
        assert ctx.result == "hello"

        # 验证检查点已保存（使用异步接口 alist）
        checkpoints = [cp async for cp in saver.alist({"configurable": {"thread_id": "checkpoint_test"}})]
        assert len(checkpoints) >= 1

        # 验证检查点内容
        cp_tuple = checkpoints[0]
        assert "channel_values" in cp_tuple.checkpoint
        assert cp_tuple.config.get("configurable", {}).get("thread_id") == "checkpoint_test"


@pytest.mark.asyncio
async def test_langgraph_checkpoint_memory_persists_and_restores(tmp_path: Path) -> None:
    """测试 LangGraph 内存检查点持久化和恢复
    
    本测试验证：
    1. 使用 checkpointer 时，第一次运行正常完成
    2. 检查点已正确保存到内存
    3. 可以通过 list() 列出检查点
    4. 可以通过 get_tuple() 获取检查点内容
    """
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LlmResponse(
        stop_reason="end_turn",
        tool_calls=[],
        text="hello",
        usage=_usage(),
    ))
    registry = ToolRegistry()
    bus = EventBus()

    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()

    # 第一次运行：发送 "hi"
    loop = LangGraphAgentLoop(provider, registry, bus, checkpointer=saver)
    ctx = ExecutionContext(run_id="mem_test", goal="test", max_steps=5)
    ctx.messages = [{"role": "user", "content": "hi"}]

    await loop.run(ctx)

    assert ctx.status == "success"
    assert ctx.result == "hello"

    # 验证检查点已保存
    checkpoints = list(saver.list({"configurable": {"thread_id": "mem_test"}}))
    assert len(checkpoints) >= 1

    # 验证检查点内容
    cp_tuple = checkpoints[0]
    assert "channel_values" in cp_tuple.checkpoint
    assert cp_tuple.config.get("configurable", {}).get("thread_id") == "mem_test"


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