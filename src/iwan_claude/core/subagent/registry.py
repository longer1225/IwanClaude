from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from iwan_claude.core.context import ExecutionContext


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class BatchStatus:
    batch_id: str
    total: int
    running: int
    completed: int
    success: int
    failed: int
    cancelled: int
    duration_sec: float
    results: list[dict[str, Any]]


class BackgroundTaskRegistry:
    def __init__(
        self,
        default_timeout_sec: int = 600,
        ttl_after_done_sec: int = 3600,
    ) -> None:
        self._tasks: dict[str, tuple[asyncio.Task[None], ExecutionContext]] = {}
        self._task_meta: dict[str, dict[str, Any]] = {}
        self._batches: dict[str, list[str]] = {}
        self._batch_meta: dict[str, dict[str, Any]] = {}
        self.default_timeout_sec = default_timeout_sec
        self.ttl_after_done_sec = ttl_after_done_sec

    def register(
        self,
        run_id: str,
        task: asyncio.Task[None],
        context: ExecutionContext,
        *,
        description: str = "",
        batch_id: str | None = None,
    ) -> None:
        self._tasks[run_id] = (task, context)
        self._task_meta[run_id] = {
            "created_at": _utcnow(),
            "description": description,
            "batch_id": batch_id,
            "started_at": _utcnow(),
        }

    def get(
        self, run_id: str
    ) -> tuple[asyncio.Task[None], ExecutionContext] | None:
        return self._tasks.get(run_id)

    def all(self) -> list[tuple[asyncio.Task[None], ExecutionContext]]:
        return list(self._tasks.values())

    # ── cancellation ──────────────────────────────────────────────────────

    def cancel(self, run_id: str, *, reason: str = "cancelled") -> bool:
        entry = self._tasks.get(run_id)
        if entry is None:
            return False
        task, ctx = entry
        if task.done():
            return False
        task.cancel(msg=reason)
        self._task_meta[run_id]["cancelled_at"] = _utcnow()
        try:
            ctx.status = "cancelled"
            ctx.reason = reason
        except Exception:
            pass
        return True

    def cancel_batch(self, batch_id: str, *, reason: str = "batch cancelled") -> int:
        run_ids = self._batches.get(batch_id, [])
        cancelled = 0
        for rid in run_ids:
            if self.cancel(rid, reason=reason):
                cancelled += 1
        if batch_id in self._batch_meta:
            self._batch_meta[batch_id]["cancelled_at"] = _utcnow()
        return cancelled

    def cancel_all(self, *, reason: str = "registry shutdown") -> int:
        cancelled = 0
        for rid in list(self._tasks.keys()):
            if self.cancel(rid, reason=reason):
                cancelled += 1
        return cancelled

    # ── batch management ──────────────────────────────────────────────────

    def register_batch(
        self,
        batch_id: str,
        run_ids: list[str],
        *,
        description: str = "",
    ) -> None:
        self._batches[batch_id] = list(run_ids)
        self._batch_meta[batch_id] = {
            "created_at": _utcnow(),
            "description": description,
            "total": len(run_ids),
        }

    def batch_status(self, batch_id: str) -> BatchStatus | None:
        run_ids = self._batches.get(batch_id)
        if run_ids is None:
            return None
        meta = self._batch_meta.get(batch_id, {})
        created_at = meta.get("created_at", _utcnow())
        duration = (_utcnow() - created_at).total_seconds()

        results: list[dict[str, Any]] = []
        running = completed = success = failed = cancelled = 0
        for rid in run_ids:
            entry = self._tasks.get(rid)
            task_meta = self._task_meta.get(rid, {})
            description = task_meta.get("description", "")
            if entry is None:
                status = "unknown"
                result_text = None
                elapsed = 0.0
            else:
                task, ctx = entry
                started_at = task_meta.get("started_at", created_at)
                elapsed = (_utcnow() - started_at).total_seconds()
                if task.cancelled():
                    status = "cancelled"
                    cancelled += 1
                elif task.done():
                    completed += 1
                    exc = task.exception()
                    if exc is not None:
                        status = "failed"
                        failed += 1
                    else:
                        st = getattr(ctx, "status", "unknown")
                        if st == "success":
                            status = "success"
                            success += 1
                        else:
                            status = "failed"
                            failed += 1
                else:
                    status = "running"
                    running += 1
                result_text = getattr(ctx, "result", None)
            results.append(
                {
                    "run_id": rid,
                    "description": description,
                    "status": status,
                    "result": result_text,
                    "elapsed_sec": round(elapsed, 3),
                }
            )
        return BatchStatus(
            batch_id=batch_id,
            total=len(run_ids),
            running=running,
            completed=completed,
            success=success,
            failed=failed,
            cancelled=cancelled,
            duration_sec=round(duration, 3),
            results=results,
        )

    def all_batch_ids(self) -> list[str]:
        return list(self._batches.keys())

    # ── TTL cleanup (memory leak prevention) ──────────────────────────────

    def prune(self, *, ttl_override_sec: int | None = None) -> int:
        ttl = self.ttl_after_done_sec if ttl_override_sec is None else ttl_override_sec
        if ttl <= 0:
            return 0
        cutoff = _utcnow() - timedelta(seconds=ttl)
        removed = 0
        for rid in list(self._tasks.keys()):
            task, _ctx = self._tasks[rid]
            if not task.done() and not task.cancelled():
                continue
            meta = self._task_meta.get(rid, {})
            done_at = meta.get("cancelled_at") or meta.get("finished_at") or meta.get("created_at")
            if done_at is not None and done_at <= cutoff:
                del self._tasks[rid]
                self._task_meta.pop(rid, None)
                removed += 1
        return removed

    # ── per-task finish hooks (called by SpawnAgentTool) ──────────────────

    def mark_finished(self, run_id: str) -> None:
        self._task_meta.setdefault(run_id, {})["finished_at"] = _utcnow()
