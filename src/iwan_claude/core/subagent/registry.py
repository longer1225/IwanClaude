"""
后台任务注册表

该模块实现了后台任务的管理和跟踪功能，用于管理并发子 Agent 的生命周期。

核心功能：
- 注册和管理后台任务
- 支持任务取消（单个、批量、全部）
- 支持任务分组（batch）管理
- 提供任务状态查询和统计
- 自动清理过期任务（TTL 机制）

设计要点：
- 使用字典存储任务和元数据，支持 O(1) 查找
- 支持任务分组，便于批量管理
- 使用 TTL 机制防止内存泄漏
- 线程安全：使用 list() 复制键列表进行遍历
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from iwan_claude.core.context import ExecutionContext


def _utcnow() -> datetime:
    """
    获取当前 UTC 时间

    返回：
        datetime: 当前 UTC 时间对象
    """
    return datetime.now(UTC)


@dataclass
class BatchStatus:
    """
    批量任务状态数据类

    存储批量任务的执行状态和统计信息。

    属性：
        batch_id: 批次 ID
        total: 总任务数
        running: 运行中任务数
        completed: 已完成任务数
        success: 成功任务数
        failed: 失败任务数
        cancelled: 已取消任务数
        duration_sec: 批次执行时长（秒）
        results: 每个任务的详细结果列表

    使用示例：
        >>> status = BatchStatus(
        ...     batch_id="b_abc123",
        ...     total=3,
        ...     running=1,
        ...     completed=2,
        ...     success=1,
        ...     failed=1,
        ...     cancelled=0,
        ...     duration_sec=45.5,
        ...     results=[...]
        ... )
    """
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
    """
    后台任务注册表

    管理后台运行的子 Agent 任务，提供任务注册、取消、状态查询和清理功能。

    工作原理：
    1. 通过 register() 注册后台任务，存储 asyncio.Task 和 ExecutionContext
    2. 通过 get() 查询任务状态
    3. 通过 cancel()/cancel_batch()/cancel_all() 取消任务
    4. 通过 batch_status() 查询批量任务状态
    5. 通过 prune() 清理过期任务，防止内存泄漏

    数据结构：
    - _tasks: dict[run_id, (Task, ExecutionContext)] - 任务存储
    - _task_meta: dict[run_id, dict] - 任务元数据（创建时间、描述、批次 ID 等）
    - _batches: dict[batch_id, list[run_id]] - 批次到任务列表的映射
    - _batch_meta: dict[batch_id, dict] - 批次元数据（创建时间、描述、总数等）

    使用示例：
        >>> registry = BackgroundTaskRegistry()
        >>> task = asyncio.create_task(run_subagent())
        >>> registry.register("run-abc123", task, context, description="test task")
        >>> status = registry.get("run-abc123")
        >>> registry.cancel("run-abc123")
    """

    def __init__(
        self,
        default_timeout_sec: int = 600,
        ttl_after_done_sec: int = 3600,
    ) -> None:
        """
        初始化后台任务注册表

        参数：
            default_timeout_sec: 默认超时时间（秒），默认 600 秒（10 分钟）
            ttl_after_done_sec: 任务完成后的 TTL（秒），默认 3600 秒（1 小时）

        属性：
            _tasks: 任务字典，key 为 run_id，value 为 (Task, ExecutionContext) 元组
            _task_meta: 任务元数据字典
            _batches: 批次字典，key 为 batch_id，value 为 run_id 列表
            _batch_meta: 批次元数据字典
            default_timeout_sec: 默认超时时间
            ttl_after_done_sec: 任务完成后的 TTL
        """
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
        """
        注册后台任务

        将任务添加到注册表，存储任务对象、上下文和元数据。

        参数：
            run_id: 任务的唯一标识符
            task: asyncio.Task 对象
            context: ExecutionContext 对象，包含任务执行上下文
            description: 任务描述，用于展示和调试
            batch_id: 批次 ID，用于分组管理

        实现步骤：
        1. 将任务和上下文存储到 _tasks 字典
        2. 创建任务元数据，记录创建时间、描述和批次 ID

        使用示例：
            >>> task = asyncio.create_task(run_subagent())
            >>> registry.register("run-abc123", task, context, description="test")
        """
        self._tasks[run_id] = (task, context)
        self._task_meta[run_id] = {
            "created_at": _utcnow(),
            "description": description,
            "batch_id": batch_id,
            "started_at": _utcnow(),
        }

    def get(
        self,
        run_id: str
    ) -> tuple[asyncio.Task[None], ExecutionContext] | None:
        """
        获取任务信息

        根据 run_id 查询任务对象和上下文。

        参数：
            run_id: 任务的唯一标识符

        返回：
            tuple[asyncio.Task[None], ExecutionContext] | None: 任务和上下文元组，
            如果未找到则返回 None

        使用示例：
            >>> entry = registry.get("run-abc123")
            >>> if entry:
            ...     task, context = entry
            ...     print(task.done())
        """
        return self._tasks.get(run_id)

    def all(self) -> list[tuple[asyncio.Task[None], ExecutionContext]]:
        """
        获取所有任务

        返回注册表中所有任务的列表。

        返回：
            list[tuple[asyncio.Task[None], ExecutionContext]]: 所有任务的列表

        使用示例：
            >>> for task, context in registry.all():
            ...     print(context.run_id)
        """
        return list(self._tasks.values())

    # ── cancellation ──────────────────────────────────────────────────────

    def cancel(self, run_id: str, *, reason: str = "cancelled") -> bool:
        """
        取消单个任务

        取消指定 run_id 的任务，并更新任务状态。

        参数：
            run_id: 任务的唯一标识符
            reason: 取消原因，默认 "cancelled"

        返回：
            bool: 如果任务被成功取消返回 True，否则返回 False

        返回 False 的情况：
        - run_id 不存在
        - 任务已经完成

        实现步骤：
        1. 查找任务
        2. 如果任务不存在或已完成，返回 False
        3. 调用 task.cancel() 取消任务
        4. 记录取消时间
        5. 更新上下文状态为 "cancelled"

        使用示例：
            >>> success = registry.cancel("run-abc123", reason="user request")
        """
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
        """
        取消批量任务

        取消指定批次中的所有任务。

        参数：
            batch_id: 批次 ID
            reason: 取消原因，默认 "batch cancelled"

        返回：
            int: 成功取消的任务数量

        实现步骤：
        1. 获取批次中的所有 run_id
        2. 遍历 run_id，调用 cancel() 取消每个任务
        3. 记录批次取消时间

        使用示例：
            >>> cancelled = registry.cancel_batch("b_abc123")
            >>> print(f"Cancelled {cancelled} tasks")
        """
        run_ids = self._batches.get(batch_id, [])
        cancelled = 0
        for rid in run_ids:
            if self.cancel(rid, reason=reason):
                cancelled += 1
        if batch_id in self._batch_meta:
            self._batch_meta[batch_id]["cancelled_at"] = _utcnow()
        return cancelled

    def cancel_all(self, *, reason: str = "registry shutdown") -> int:
        """
        取消所有任务

        取消注册表中的所有任务，通常在关闭时调用。

        参数：
            reason: 取消原因，默认 "registry shutdown"

        返回：
            int: 成功取消的任务数量

        实现步骤：
        1. 使用 list() 复制键列表，避免遍历过程中字典被修改
        2. 遍历所有 run_id，调用 cancel() 取消每个任务

        使用示例：
            >>> cancelled = registry.cancel_all()
        """
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
        """
        注册批次

        将一组 run_id 关联到一个批次，便于批量管理和状态查询。

        参数：
            batch_id: 批次 ID
            run_ids: 属于该批次的 run_id 列表
            description: 批次描述

        实现步骤：
        1. 将 run_ids 列表存储到 _batches 字典
        2. 创建批次元数据，记录创建时间、描述和总任务数

        使用示例：
            >>> registry.register_batch("b_abc123", ["run-1", "run-2"], description="batch test")
        """
        self._batches[batch_id] = list(run_ids)
        self._batch_meta[batch_id] = {
            "created_at": _utcnow(),
            "description": description,
            "total": len(run_ids),
        }

    def batch_status(self, batch_id: str) -> BatchStatus | None:
        """
        获取批次状态

        查询指定批次的执行状态和统计信息。

        参数：
            batch_id: 批次 ID

        返回：
            BatchStatus | None: 批次状态对象，如果批次不存在则返回 None

        实现步骤：
        1. 获取批次的 run_id 列表
        2. 遍历每个 run_id，查询任务状态
        3. 统计运行中、已完成、成功、失败、已取消的任务数
        4. 收集每个任务的详细结果
        5. 创建并返回 BatchStatus 对象

        状态判断逻辑：
        - task.cancelled() → cancelled
        - task.done() + exception → failed
        - task.done() + context.status == "success" → success
        - task.done() + other → failed
        - not done() → running

        使用示例：
            >>> status = registry.batch_status("b_abc123")
            >>> print(f"Success: {status.success}/{status.total}")
        """
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
        """
        获取所有批次 ID

        返回注册表中所有批次的 ID 列表。

        返回：
            list[str]: 所有批次 ID 的列表

        使用示例：
            >>> for batch_id in registry.all_batch_ids():
            ...     status = registry.batch_status(batch_id)
        """
        return list(self._batches.keys())

    # ── TTL cleanup (memory leak prevention) ──────────────────────────────

    def prune(self, *, ttl_override_sec: int | None = None) -> int:
        """
        清理过期任务

        根据 TTL 清理已完成或已取消的任务，防止内存泄漏。

        参数：
            ttl_override_sec: 自定义 TTL（秒），如果为 None 则使用默认值

        返回：
            int: 清理的任务数量

        实现步骤：
        1. 计算过期时间阈值（当前时间 - TTL）
        2. 遍历所有任务
        3. 跳过仍在运行的任务
        4. 检查任务完成时间是否超过阈值
        5. 如果超过阈值，删除任务和元数据

        TTL 优先级：
        - cancelled_at（取消时间）优先
        - finished_at（完成时间）次之
        - created_at（创建时间）最后

        使用示例：
            >>> removed = registry.prune()
            >>> print(f"Removed {removed} expired tasks")
        """
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
        """
        标记任务完成

        记录任务的完成时间，用于 TTL 清理。

        参数：
            run_id: 任务的唯一标识符

        实现步骤：
        1. 使用 setdefault() 获取或创建任务元数据
        2. 设置 finished_at 字段为当前时间

        使用示例：
            >>> registry.mark_finished("run-abc123")
        """
        self._task_meta.setdefault(run_id, {})["finished_at"] = _utcnow()
