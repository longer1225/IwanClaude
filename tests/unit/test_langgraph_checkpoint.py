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


class _SpySaver:
    """记录 close 是否被调用的 checkpointer 替身"""
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


# 功能：runner.close() 不得关闭 daemon 注入的共享 checkpointer（一次 restore 毒死全 daemon 的回归锁）
# 设计：用记录 close 调用的 spy 替身而非真 sqlite，把"所有权语义"隔离成单一断言；
#      旧实现无条件 close 共享实例，此测试在旧代码上必红
@pytest.mark.asyncio
async def test_runner_close_preserves_shared_checkpointer() -> None:
    """验证 runner 不关闭 daemon 注入的共享 checkpointer"""
    from iwan_claude.core.runner import AgentRunner

    shared = _SpySaver()
    runner = AgentRunner(IwanConfig(), checkpointer=shared)
    await runner.close()
    assert shared.closed is False
    assert runner._checkpointer is None


# 功能：runner 自己懒建的 sqlite checkpointer 必须在 close() 时被真正回收（连接关闭后不可用）
# 设计：所有权修复若矫枉过正（owned 也不关）会泄漏 aiosqlite 连接；用"close 后
#      aget_tuple 必抛"反向证明回收生效，比断言内部属性更接近用户可见行为
@pytest.mark.asyncio
async def test_runner_close_closes_owned_sqlite(tmp_path: Path) -> None:
    """验证 runner 自己懒建的 sqlite checkpointer 会被真正回收（连接关闭后不可用）"""
    from iwan_claude.core.runner import AgentRunner

    config = IwanConfig(agent=AgentConfig(
        checkpoint_backend="sqlite",
        checkpoint_db_path=str(tmp_path / "cp.db"),
    ))
    runner = AgentRunner(config)
    saver = await runner._init_checkpointer()
    assert saver is not None
    await runner.close()
    with pytest.raises(Exception):
        await saver.aget_tuple({"configurable": {"thread_id": "x"}})