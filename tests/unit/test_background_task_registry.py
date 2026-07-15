from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.subagent.registry import BackgroundTaskRegistry, BatchStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _make_ctx(goal: str = "test") -> ExecutionContext:
    return ExecutionContext(run_id="unused", goal=goal, max_steps=1)


def _make_registry(**kw: Any) -> BackgroundTaskRegistry:
    # Only set defaults; let **kw override them
    defaults: dict[str, Any] = {"default_timeout_sec": 60}
    if "ttl_after_done_sec" not in kw:
        defaults["ttl_after_done_sec"] = 3600
    defaults.update(kw)
    return BackgroundTaskRegistry(**defaults)


# ── cancel: unknown run_id returns False ─────────────────────────────────────
def test_cancel_unknown_returns_false() -> None:
    reg = _make_registry()
    assert reg.cancel("does-not-exist") is False


# ── cancel_batch: unknown batch_id returns 0 cancelled ───────────────────────
def test_cancel_batch_unknown_returns_zero() -> None:
    reg = _make_registry()
    assert reg.cancel_batch("b-no-such") == 0


# ── cancel: cancels a running task and records cancelled_at ──────────────────
@pytest.mark.asyncio
async def test_cancel_running_task_sets_meta() -> None:
    reg = _make_registry()
    ev = asyncio.Event()

    async def _runner() -> None:
        await ev.wait()

    task: asyncio.Task[None] = asyncio.create_task(_runner())
    ctx = _make_ctx()
    reg.register("r1", task, ctx, description="t1")

    assert reg.get("r1") is not None
    assert task.done() is False

    ok = reg.cancel("r1", reason="by user")
    assert ok is True

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)
    assert task.cancelled() is True
    assert "cancelled_at" in reg._task_meta["r1"]
    assert ctx.status == "cancelled"
    assert ctx.reason == "by user"

    assert reg.cancel("r1") is False
    ev.set()


# ── cancel_all: cancels every running task ───────────────────────────────────
@pytest.mark.asyncio
async def test_cancel_all_many() -> None:
    reg = _make_registry()

    events: list[asyncio.Event] = []
    tasks: list[asyncio.Task[None]] = []
    for i in range(5):
        ev = asyncio.Event()
        events.append(ev)

        async def _r(_e=ev) -> None:
            await _e.wait()

        t: asyncio.Task[None] = asyncio.create_task(_r())
        tasks.append(t)
        reg.register(f"r{i}", t, _make_ctx(), description=f"task-{i}")

    n = reg.cancel_all(reason="shutdown")
    assert n == 5
    await asyncio.sleep(0.02)
    for t in tasks:
        assert t.cancelled() is True
    for ev in events:
        ev.set()


# ── batch_status: unknown batch_id returns None ──────────────────────────────
def test_batch_status_unknown_is_none() -> None:
    reg = _make_registry()
    assert reg.batch_status("b-xyz") is None


# ── batch_status: all success snapshot ───────────────────────────────────────
@pytest.mark.asyncio
async def test_batch_status_all_success() -> None:
    reg = _make_registry()

    run_ids: list[str] = []
    for i in range(3):
        async def _ok(_i=i) -> None:
            await asyncio.sleep(0.01)

        t = asyncio.create_task(_ok())
        rid = f"r{i}"
        ctx = _make_ctx(f"task {i}")
        ctx.status = "success"
        ctx.result = f"done {i}"
        reg.register(rid, t, ctx, description=f"desc-{i}")
        run_ids.append(rid)
        await t

    reg.register_batch("b1", run_ids, description="batch of 3")
    st = reg.batch_status("b1")
    assert st is not None
    assert isinstance(st, BatchStatus)
    assert st.batch_id == "b1"
    assert st.total == 3
    assert st.success == 3
    assert st.running == 0
    assert st.failed == 0
    assert st.cancelled == 0
    assert len(st.results) == 3
    for r in st.results:
        assert r["status"] == "success"
        assert "elapsed_sec" in r


# ── batch_status: mixed running / success / failed / cancelled ───────────────
@pytest.mark.asyncio
async def test_batch_status_mixed() -> None:
    reg = _make_registry()
    run_ids: list[str] = []

    # 1 running
    ev = asyncio.Event()
    t_run: asyncio.Task[None] = asyncio.create_task(ev.wait())
    reg.register("r-run", t_run, _make_ctx(), description="running task")
    run_ids.append("r-run")

    # 2 success
    async def _s() -> None:
        return None

    t_suc = asyncio.create_task(_s())
    ctx_s = _make_ctx()
    ctx_s.status = "success"
    ctx_s.result = "ok"
    reg.register("r-suc", t_suc, ctx_s, description="suc task")
    run_ids.append("r-suc")
    await t_suc

    # 3 failed (exception)
    async def _f() -> None:
        raise RuntimeError("boom")

    t_fail: asyncio.Task[None] = asyncio.create_task(_f())
    reg.register("r-fail", t_fail, _make_ctx(), description="fail task")
    run_ids.append("r-fail")
    with pytest.raises(RuntimeError):
        await t_fail

    # 4 cancelled
    t_can: asyncio.Task[None] = asyncio.create_task(ev.wait())
    reg.register("r-can", t_can, _make_ctx(), description="can task")
    run_ids.append("r-can")
    reg.cancel("r-can", reason="user")
    with pytest.raises(asyncio.CancelledError):
        await t_can

    reg.register_batch("b-mix", run_ids)
    st = reg.batch_status("b-mix")
    assert st is not None
    assert st.running == 1
    assert st.success == 1
    assert st.failed == 1
    assert st.cancelled == 1
    statuses = {r["run_id"]: r["status"] for r in st.results}
    assert statuses == {
        "r-run": "running",
        "r-suc": "success",
        "r-fail": "failed",
        "r-can": "cancelled",
    }

    ev.set()


# ── prune: removes done tasks older than TTL ─────────────────────────────────
@pytest.mark.asyncio
async def test_prune_removes_old_done_entries() -> None:
    reg = _make_registry(ttl_after_done_sec=100)

    t_old: asyncio.Task[None] = asyncio.Future()
    t_old.set_result(None)
    reg.register("r-old", t_old, _make_ctx(), description="old done")
    reg._task_meta["r-old"]["finished_at"] = _utcnow() - timedelta(seconds=500)

    t_recent: asyncio.Task[None] = asyncio.Future()
    t_recent.set_result(None)
    reg.register("r-recent", t_recent, _make_ctx(), description="recent done")
    reg._task_meta["r-recent"]["finished_at"] = _utcnow() - timedelta(seconds=10)

    t_pending: asyncio.Task[None] = asyncio.Future()
    reg.register("r-pending", t_pending, _make_ctx(), description="pending")

    removed = reg.prune()
    assert removed == 1
    assert "r-old" not in reg._tasks
    assert "r-recent" in reg._tasks
    assert "r-pending" in reg._tasks


# ── prune with override TTL ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_prune_with_override_ttl() -> None:
    reg = _make_registry(ttl_after_done_sec=10_000)
    t: asyncio.Task[None] = asyncio.Future()
    t.set_result(None)
    reg.register("r1", t, _make_ctx())
    reg._task_meta["r1"]["finished_at"] = _utcnow() - timedelta(seconds=50)

    assert reg.prune() == 0
    assert reg.prune(ttl_override_sec=10) == 1


# ── prune TTL<=0 disables cleanup ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_prune_zero_ttl_noop() -> None:
    reg = _make_registry(ttl_after_done_sec=0)
    t: asyncio.Task[None] = asyncio.Future()
    t.set_result(None)
    reg.register("r1", t, _make_ctx())
    reg._task_meta["r1"]["finished_at"] = _utcnow() - timedelta(days=365)
    assert reg.prune() == 0


# ── mark_finished sets finished_at meta ──────────────────────────────────────
@pytest.mark.asyncio
async def test_mark_finished_sets_meta() -> None:
    reg = _make_registry()
    t: asyncio.Task[None] = asyncio.Future()
    t.set_result(None)
    reg.register("r1", t, _make_ctx())
    assert "finished_at" not in reg._task_meta["r1"]
    reg.mark_finished("r1")
    assert "finished_at" in reg._task_meta["r1"]
    reg.mark_finished("no-such")


# ── all_batch_ids lists registered batches ───────────────────────────────────
def test_all_batch_ids() -> None:
    reg = _make_registry()
    assert reg.all_batch_ids() == []
    reg.register_batch("b1", ["r1", "r2"])
    reg.register_batch("b2", ["r3"])
    assert set(reg.all_batch_ids()) == {"b1", "b2"}


# ── register: run_id lookup roundtrip ────────────────────────────────────────
@pytest.mark.asyncio
async def test_register_and_get_roundtrip() -> None:
    reg = _make_registry()
    t: asyncio.Task[None] = asyncio.Future()
    ctx = _make_ctx("g1")
    reg.register("r1", t, ctx, description="d1", batch_id="bA")
    got = reg.get("r1")
    assert got is not None
    task2, ctx2 = got
    assert task2 is t
    assert ctx2 is ctx
    assert reg._task_meta["r1"]["description"] == "d1"
    assert reg._task_meta["r1"]["batch_id"] == "bA"
    assert "created_at" in reg._task_meta["r1"]


# ── all() returns registered tasks list ──────────────────────────────────────
@pytest.mark.asyncio
async def test_all_method() -> None:
    reg = _make_registry()
    for i in range(3):
        t: asyncio.Task[None] = asyncio.Future()
        reg.register(f"r{i}", t, _make_ctx())
    entries = reg.all()
    assert len(entries) == 3
