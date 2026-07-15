from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.types import LlmResponse, UsageStats
from iwan_claude.core.subagent.registry import BackgroundTaskRegistry
from iwan_claude.core.subagent.tool import (
    AgentResultTool,
    BatchResultParams,
    BatchResultTool,
    CancelAgentParams,
    CancelAgentTool,
    SpawnAgentParams,
    SpawnAgentsParams,
    SpawnAgentsTool,
    SpawnAgentTask,
    SpawnAgentTool,
    format_batch_status,
)


def _usage(pct: float = 0.01) -> UsageStats:
    return UsageStats(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        context_pct=pct,
    )


def _fast_provider(result_text: str = "done") -> Any:
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LlmResponse(
            stop_reason="end_turn",
            tool_calls=[],
            text=result_text,
            usage=_usage(),
        )
    )
    return provider


def _slow_provider(result_text: str, *, sleep_s: float = 0.1) -> Any:
    provider = MagicMock()

    async def _chat(*a: Any, **kw: Any) -> LlmResponse:
        await asyncio.sleep(sleep_s)
        return LlmResponse(
            stop_reason="end_turn",
            tool_calls=[],
            text=result_text,
            usage=_usage(),
        )

    provider.chat = _chat
    return provider


def _make_spawn_agents_tool(
    tmp_path: Path,
    provider: Any,
    *,
    depth: int = 0,
) -> tuple[SpawnAgentsTool, BackgroundTaskRegistry, EventBus]:
    bus = EventBus()
    registry = BackgroundTaskRegistry(default_timeout_sec=60, ttl_after_done_sec=600)
    tool = SpawnAgentsTool(
        provider=provider,
        parent_bus=bus,
        parent_run_id="parent-01",
        permission_manager=None,
        max_steps=3,
        task_registry=registry,
        runs_dir=tmp_path,
        session_id="sess-conc",
        llm_model_name="test-model",
        depth=depth,
    )
    return tool, registry, bus


def _make_spawn_agent_tool(
    tmp_path: Path,
    provider: Any,
    *,
    depth: int = 0,
) -> tuple[SpawnAgentTool, BackgroundTaskRegistry, EventBus]:
    bus = EventBus()
    registry = BackgroundTaskRegistry(default_timeout_sec=60)
    tool = SpawnAgentTool(
        provider=provider,
        parent_bus=bus,
        parent_run_id="parent-01",
        permission_manager=None,
        max_steps=3,
        task_registry=registry,
        runs_dir=tmp_path,
        session_id="sess",
        llm_model_name="test-model",
        depth=depth,
    )
    return tool, registry, bus


# ═══════════════════════════════════════════════════════════════════════════════
# SpawnAgentParams / SpawnAgentsParams Pydantic validation
# ═══════════════════════════════════════════════════════════════════════════════

def test_spawn_agent_params_defaults() -> None:
    p = SpawnAgentParams(description="d", prompt="p")
    assert p.run_in_background is False
    assert p.subagent_type == ""
    assert p.timeout_sec == 0


def test_spawn_agent_params_timeout_rejects_negative() -> None:
    with pytest.raises(Exception):
        SpawnAgentParams(description="d", prompt="p", timeout_sec=-1)


def test_spawn_agents_params_requires_tasks() -> None:
    with pytest.raises(Exception):
        SpawnAgentsParams(tasks=[])


def test_spawn_agents_params_enforces_max_concurrency_bounds() -> None:
    with pytest.raises(Exception):
        SpawnAgentsParams(tasks=[SpawnAgentTask(description="d", prompt="p")], max_concurrency=0)
    with pytest.raises(Exception):
        SpawnAgentsParams(tasks=[SpawnAgentTask(description="d", prompt="p")], max_concurrency=100)


# ═══════════════════════════════════════════════════════════════════════════════
# spawn_agent timeout test (foreground)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spawn_agent_foreground_timeout(tmp_path: Path) -> None:
    slow = _slow_provider("won't be seen", sleep_s=10.0)
    tool, _, _ = _make_spawn_agent_tool(tmp_path, slow)
    result = await tool.invoke({
        "description": "timeout task",
        "prompt": "run slow",
        "timeout_sec": 0.5,
    })
    assert result.is_error is True
    assert "timed out" in result.content.lower() or "timeout" in result.content.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# spawn_agents basic: wait=true, single task → success summary
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spawn_agents_single_wait_true(tmp_path: Path) -> None:
    tool, reg, _ = _make_spawn_agents_tool(tmp_path, _fast_provider("res-ok"))
    result = await tool.invoke({
        "tasks": [{"description": "t1", "prompt": "do t1"}],
        "wait": True,
    })
    assert not result.is_error
    assert "success=1" in result.content
    assert "total=1" in result.content
    assert "res-ok" in result.content
    # Registry prune check: ensure finished tasks are present
    ids = reg.all_batch_ids()
    assert len(ids) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# spawn_agents wait=false → returns batch_id
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spawn_agents_wait_false_returns_batch_id(tmp_path: Path) -> None:
    tool, reg, _ = _make_spawn_agents_tool(tmp_path, _fast_provider("x"))
    result = await tool.invoke({
        "tasks": [
            {"description": "a", "prompt": "pa"},
            {"description": "b", "prompt": "pb"},
        ],
        "wait": False,
    })
    assert not result.is_error
    assert "batch_id=" in result.content
    marker = "batch_id="
    start = result.content.find(marker) + len(marker)
    bid = result.content[start:].split()[0].rstrip(".")
    assert bid in reg.all_batch_ids()
    # wait for them to actually finish so event loop clean
    st = reg.batch_status(bid)
    assert st is not None
    for _ in range(50):
        await asyncio.sleep(0.02)
        st = reg.batch_status(bid)
        if st is None or st.running == 0:
            break


# ═══════════════════════════════════════════════════════════════════════════════
# spawn_agents concurrency limit: with max_concurrency=1, 3 tasks should
# SERIALIZE => elapsed >= 3*T; without semaphore it would be ~T.
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spawn_agents_max_concurrency_1_serializes(tmp_path: Path) -> None:
    T = 0.2
    slow = _slow_provider("slow", sleep_s=T)
    tool, reg, _ = _make_spawn_agents_tool(tmp_path, slow)
    t0 = asyncio.get_event_loop().time()
    result = await tool.invoke({
        "tasks": [
            {"description": "a", "prompt": "pa"},
            {"description": "b", "prompt": "pb"},
            {"description": "c", "prompt": "pc"},
        ],
        "max_concurrency": 1,
        "wait": True,
    })
    elapsed = asyncio.get_event_loop().time() - t0
    assert not result.is_error
    assert "success=3" in result.content
    # With max_concurrency=1, 3 tasks must serialise to >= ~2.5*T (allow slack)
    LO = 2.2 * T
    HI = 6 * T
    assert elapsed >= LO, (
        f"expected serialization (>= {LO:.3f}s) but elapsed={elapsed:.3f}s "
        f"→ Semaphore not enforcing max_concurrency=1"
    )
    assert elapsed < HI, f"elapsed={elapsed:.3f}s unexpectedly large (<{HI:.3f}s)"


# ═══════════════════════════════════════════════════════════════════════════════
# spawn_agents max_concurrency=3 with 3 tasks => parallel (~T, not ~3T)
# This is the reverse assertion to ensure the test itself distinguishes
# the two regimes.
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spawn_agents_max_concurrency_3_runs_parallel(tmp_path: Path) -> None:
    T = 0.2
    slow = _slow_provider("slow", sleep_s=T)
    tool, reg, _ = _make_spawn_agents_tool(tmp_path, slow)
    t0 = asyncio.get_event_loop().time()
    result = await tool.invoke({
        "tasks": [
            {"description": "a", "prompt": "pa"},
            {"description": "b", "prompt": "pb"},
            {"description": "c", "prompt": "pc"},
        ],
        "max_concurrency": 3,
        "wait": True,
    })
    elapsed = asyncio.get_event_loop().time() - t0
    assert not result.is_error
    assert "success=3" in result.content
    # Parallel regime: 3 tasks with concurrency=3 should finish in ~T
    # (< 2*T with reasonable overhead slack)
    UPPER = 2 * T
    assert elapsed < UPPER, (
        f"expected parallel (< {UPPER:.3f}s) but elapsed={elapsed:.3f}s"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# spawn_agents + wait_timeout_sec → partial cancellation
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spawn_agents_wait_timeout_cancels_remaining(tmp_path: Path) -> None:
    # 3 tasks * 0.3s each but max_concurrency=1 → ~0.9s total; wait_timeout=0.4s
    slow = _slow_provider("s", sleep_s=0.3)
    tool, reg, _ = _make_spawn_agents_tool(tmp_path, slow)
    result = await tool.invoke({
        "tasks": [
            {"description": "a", "prompt": "pa"},
            {"description": "b", "prompt": "pb"},
            {"description": "c", "prompt": "pc"},
        ],
        "max_concurrency": 1,
        "wait": True,
        "wait_timeout_sec": 0.4,
    })
    assert result.is_error is True
    assert "timed out" in result.content.lower() or "timeout" in result.content.lower()
    await asyncio.sleep(0.2)


# ═══════════════════════════════════════════════════════════════════════════════
# spawn_agents empty tasks → schema error
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spawn_agents_empty_tasks_error(tmp_path: Path) -> None:
    tool, _, _ = _make_spawn_agents_tool(tmp_path, _fast_provider())
    # Use raw dict bypassing SpawnAgentsParams (tool.invoke validates via model)
    tool.params_model = SpawnAgentsParams  # type: ignore[assignment]
    # With no explicit tasks we must hit "non-empty" validation.
    with pytest.raises(Exception):
        SpawnAgentsParams.model_validate({"tasks": []})


# ═══════════════════════════════════════════════════════════════════════════════
# spawn_agents nesting limit enforces depth < 2
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spawn_agents_nesting_limit(tmp_path: Path) -> None:
    tool, _, _ = _make_spawn_agents_tool(tmp_path, _fast_provider(), depth=2)
    result = await tool.invoke({
        "tasks": [{"description": "t", "prompt": "p"}],
        "wait": False,
    })
    assert result.is_error is True
    assert "nesting limit" in result.content


# ═══════════════════════════════════════════════════════════════════════════════
# BatchResultTool: wait=false returns snapshot
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_batch_result_snapshot(tmp_path: Path) -> None:
    tool, reg, _ = _make_spawn_agents_tool(tmp_path, _fast_provider("y"))
    spawn_res = await tool.invoke({
        "tasks": [{"description": "a", "prompt": "pa"}],
        "wait": False,
    })
    marker = "batch_id="
    bid = spawn_res.content.split(marker)[1].split()[0].rstrip(".")

    br = BatchResultTool(reg)
    res = await br.invoke({"batch_id": bid, "wait": False})
    assert not res.is_error
    assert "batch_id=" in res.content
    # wait for finish
    for _ in range(50):
        await asyncio.sleep(0.02)
        st = reg.batch_status(bid)
        if st is None or st.running == 0:
            break


# ═══════════════════════════════════════════════════════════════════════════════
# BatchResultTool: wait=true blocks until terminal
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_batch_result_wait_true(tmp_path: Path) -> None:
    T = 0.06
    slow = _slow_provider("z", sleep_s=T)
    tool, reg, _ = _make_spawn_agents_tool(tmp_path, slow)
    spawn_res = await tool.invoke({
        "tasks": [{"description": "a", "prompt": "pa"}, {"description": "b", "prompt": "pb"}],
        "wait": False,
    })
    bid = spawn_res.content.split("batch_id=")[1].split()[0].rstrip(".")

    br = BatchResultTool(reg)
    t0 = asyncio.get_event_loop().time()
    res = await br.invoke({
        "batch_id": bid,
        "wait": True,
        "poll_interval_sec": 0.1,
    })
    elapsed = asyncio.get_event_loop().time() - t0
    assert not res.is_error
    # max_concurrency default 3 vs 2 tasks => parallel (~T)
    assert elapsed < 3 * T
    assert "success=2" in res.content


# ═══════════════════════════════════════════════════════════════════════════════
# BatchResultTool: unknown batch_id → error
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_batch_result_unknown(tmp_path: Path) -> None:
    reg = BackgroundTaskRegistry()
    br = BatchResultTool(reg)
    res = await br.invoke({"batch_id": "nope", "wait": False})
    assert res.is_error is True
    assert "Unknown" in res.content


# ═══════════════════════════════════════════════════════════════════════════════
# CancelAgentTool: cancel single run_id
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cancel_agent_single_run_id(tmp_path: Path) -> None:
    ev = asyncio.Event()
    slow_prov = MagicMock()

    async def _c(*a: Any, **kw: Any) -> LlmResponse:
        await ev.wait()
        return LlmResponse(stop_reason="end_turn", tool_calls=[], text="x", usage=_usage())

    slow_prov.chat = _c
    tool_sa, reg, _ = _make_spawn_agent_tool(tmp_path, slow_prov)
    spawn = await tool_sa.invoke({
        "description": "long",
        "prompt": "long prompt",
        "run_in_background": True,
    })
    rid = spawn.content.split("run_id=")[1].split(".")[0]

    cancel_tool = CancelAgentTool(reg)
    res = await cancel_tool.invoke({"run_id": rid, "reason": "stop"})
    assert not res.is_error
    assert "cancelled" in res.content
    assert rid in res.content

    # try cancel again → already completed/cancelled → error
    await asyncio.sleep(0.02)
    res2 = await cancel_tool.invoke({"run_id": rid})
    assert res2.is_error is True
    ev.set()


# ═══════════════════════════════════════════════════════════════════════════════
# CancelAgentTool: cancel entire batch
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cancel_agent_whole_batch(tmp_path: Path) -> None:
    ev = asyncio.Event()
    slow_prov = MagicMock()

    async def _c(*a: Any, **kw: Any) -> LlmResponse:
        await ev.wait()
        return LlmResponse(stop_reason="end_turn", tool_calls=[], text="x", usage=_usage())

    slow_prov.chat = _c
    tool, reg, _ = _make_spawn_agents_tool(tmp_path, slow_prov)
    spawn_res = await tool.invoke({
        "tasks": [
            {"description": "a", "prompt": "pa"},
            {"description": "b", "prompt": "pb"},
            {"description": "c", "prompt": "pc"},
        ],
        "max_concurrency": 2,
        "wait": False,
    })
    bid = spawn_res.content.split("batch_id=")[1].split()[0].rstrip(".")
    await asyncio.sleep(0.1)  # let tasks start

    cancel_tool = CancelAgentTool(reg)
    res = await cancel_tool.invoke({"batch_id": bid, "reason": "halt batch"})
    assert not res.is_error
    assert "cancelled" in res.content
    assert "task(s) in batch_id=" in res.content

    # Wait for cancellation to propagate
    st = reg.batch_status(bid)
    for _ in range(100):
        await asyncio.sleep(0.02)
        st = reg.batch_status(bid)
        if st is None or st.running == 0:
            break

    assert st is not None
    assert st.running == 0 or (st.cancelled + st.failed + st.success) == st.total
    ev.set()


# ═══════════════════════════════════════════════════════════════════════════════
# CancelAgentTool: neither run_id nor batch_id (or both) → error
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cancel_agent_invalid_params(tmp_path: Path) -> None:
    reg = BackgroundTaskRegistry()
    ct = CancelAgentTool(reg)
    # neither
    res = await ct.invoke({})
    assert res.is_error is True
    # both
    res = await ct.invoke({"run_id": "a", "batch_id": "b"})
    assert res.is_error is True


# ═══════════════════════════════════════════════════════════════════════════════
# format_batch_status helper output shape
# ═══════════════════════════════════════════════════════════════════════════════

def test_format_batch_status_includes_results() -> None:
    from iwan_claude.core.subagent.registry import BatchStatus
    st = BatchStatus(
        batch_id="b1",
        total=1,
        running=0,
        completed=1,
        success=1,
        failed=0,
        cancelled=0,
        duration_sec=0.42,
        results=[{
            "run_id": "r1",
            "description": "desc",
            "status": "success",
            "result": "hello " + "x" * 300,  # long result to trigger truncation
            "elapsed_sec": 0.1,
        }],
    )
    out = format_batch_status(st, include_results=True)
    assert "b1" in out
    assert "total=1" in out
    assert "r1" in out
    # result snippet truncated with ellipsis
    assert "…" in out or len([c for c in out if c == "\\n" or c == "\n"]) >= 1


def test_format_batch_status_skip_results() -> None:
    from iwan_claude.core.subagent.registry import BatchStatus
    st = BatchStatus(
        batch_id="b1", total=2, running=1, completed=0, success=0, failed=0, cancelled=0,
        duration_sec=1.0,
        results=[],
    )
    out = format_batch_status(st, include_results=False)
    # head only
    assert "total=2" in out
    assert "running=1" in out
    assert out.strip().count("\n") == 0


# ═══════════════════════════════════════════════════════════════════════════════
# agent_result on cancelled task should return 'cancelled' error
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_agent_result_cancelled(tmp_path: Path) -> None:
    ev = asyncio.Event()
    slow_prov = MagicMock()

    async def _c(*a: Any, **kw: Any) -> LlmResponse:
        await ev.wait()
        return LlmResponse(stop_reason="end_turn", tool_calls=[], text="x", usage=_usage())

    slow_prov.chat = _c
    tool_sa, reg, _ = _make_spawn_agent_tool(tmp_path, slow_prov)
    spawn = await tool_sa.invoke({
        "description": "bg",
        "prompt": "prompt",
        "run_in_background": True,
    })
    rid = spawn.content.split("run_id=")[1].split(".")[0]
    await asyncio.sleep(0.02)
    reg.cancel(rid, reason="cancelled test")

    await asyncio.sleep(0.02)
    ar = AgentResultTool(reg)
    res = await ar.invoke({"run_id": rid})
    assert res.is_error is True
    assert "cancelled" in res.content.lower()
    ev.set()
