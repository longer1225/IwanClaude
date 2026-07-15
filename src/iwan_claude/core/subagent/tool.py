from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.agents.loader import AgentProfile, AgentProfileLoader
from iwan_claude.core.bus.events import SubagentFinishedEvent, SubagentStartedEvent
from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.events.writer import EventWriter
from iwan_claude.core.loop import AgentLoop
from iwan_claude.core.runs import new_run_id
from iwan_claude.core.subagent.registry import BackgroundTaskRegistry, BatchStatus
from iwan_claude.core.tools.base import BaseTool, ToolResult
from iwan_claude.core.tools.builtin.bash import BashTool
from iwan_claude.core.tools.builtin.list_dir import ListDirTool
from iwan_claude.core.tools.builtin.read_file import ReadFileTool
from iwan_claude.core.tools.builtin.task_create import TaskCreateTool
from iwan_claude.core.tools.builtin.task_get import TaskGetTool
from iwan_claude.core.tools.builtin.task_list import TaskListTool
from iwan_claude.core.tools.builtin.task_update import TaskUpdateTool
from iwan_claude.core.tools.builtin.write_file import WriteFileTool
from iwan_claude.core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from iwan_claude.core.llm.base import LLMProvider
    from iwan_claude.core.permissions.manager import PermissionManager

_profile_loader = AgentProfileLoader()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_batch_id() -> str:
    return f"b_{uuid.uuid4().hex[:12]}"


# ═══════════════════════════════════════════════════════════════════════════════
# SpawnAgentTool (existing, enhanced: timeout + updated registry signature)
# ═══════════════════════════════════════════════════════════════════════════════


class SpawnAgentParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str
    prompt: str
    run_in_background: bool = False
    subagent_type: str = ""
    timeout_sec: float = Field(default=0.0, ge=0.0)


class SpawnAgentTool(BaseTool):
    name = "spawn_agent"
    description = (
        "Spawn an isolated sub-agent to handle a self-contained sub-task. "
        "The sub-agent starts with a clean context containing only the provided prompt — "
        "it does not inherit the current conversation history. "
        "Use run_in_background=true to run in parallel; retrieve result later with agent_result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "3-5 word task description shown in progress display",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Complete task description including all context the sub-agent needs. "
                    "The sub-agent cannot see the parent conversation, so be explicit."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": "When true, returns immediately with a run_id; use agent_result to poll.",
            },
            "subagent_type": {
                "type": "string",
                "description": "Agent role profile (planner/executor/reviewer). Leave empty for default.",
            },
            "timeout_sec": {
                "type": "number",
                "default": 0,
                "description": "Per-subagent timeout in seconds (0 = use registry default, usually 10 min).",
            },
        },
        "required": ["description", "prompt"],
    }
    params_model = SpawnAgentParams

    def __init__(
        self,
        provider: LLMProvider,
        parent_bus: EventBus,
        parent_run_id: str,
        permission_manager: PermissionManager | None,
        max_steps: int,
        task_registry: BackgroundTaskRegistry | None = None,
        runs_dir: Path | None = None,
        session_id: str = "",
        llm_model_name: str = "",
        depth: int = 0,
        *,
        batch_id: str | None = None,
    ) -> None:
        self._provider = provider
        self._parent_bus = parent_bus
        self._parent_run_id = parent_run_id
        self._permission_manager = permission_manager
        self._max_steps = max_steps
        self._task_registry = task_registry if task_registry is not None else BackgroundTaskRegistry()
        self._runs_dir = runs_dir if runs_dir is not None else Path.cwd()
        self._session_id = session_id
        self._llm_model_name = llm_model_name
        self._depth = depth
        self._batch_id = batch_id

    # Spawn one subagent. This method is reused both by foreground/background modes and
    # by SpawnAgentsTool when fanning out a batch.
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SpawnAgentParams.model_validate(params)

        if self._depth >= 2:
            return ToolResult(
                content="Subagent nesting limit (2) reached; cannot spawn further subagents.",
                is_error=True,
                error_type="runtime_error",
            )

        profile: AgentProfile | None = None
        if p.subagent_type:
            try:
                profile = _profile_loader.load(p.subagent_type)
            except Exception as exc:
                return ToolResult(
                    content=f"spawn_agent: unknown subagent_type={p.subagent_type}: {exc}",
                    is_error=True,
                    error_type="runtime_error",
                )

        child_run_id = new_run_id()
        child_context = ExecutionContext(
            run_id=child_run_id,
            goal=p.prompt,
            max_steps=self._max_steps,
            system_prompt_override=profile.system_prompt if profile else None,
        )

        child_bus = EventBus()

        async def _bridge(event: BaseModel) -> None:
            await self._parent_bus.publish(event)

        child_bus.subscribe(_bridge)
        child_registry = self._build_child_registry(child_bus, child_run_id, profile)

        child_loop = AgentLoop(
            self._provider,
            child_registry,
            child_bus,
            llm_model_name=self._llm_model_name,
            permission_manager=self._permission_manager,
            session_id=self._session_id,
        )

        await self._parent_bus.publish(
            SubagentStartedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                description=p.description,
                ts=_now(),
            )
        )

        child_run_path = self._runs_dir / child_run_id
        child_run_path.mkdir(parents=True, exist_ok=True)

        timeout = p.timeout_sec if p.timeout_sec > 0 else self._task_registry.default_timeout_sec
        if timeout <= 0:
            timeout = 600

        # ── background mode: fan out immediately ──────────────────────────
        if p.run_in_background:
            task: asyncio.Task[None] = asyncio.create_task(
                self._run_background_wrapped(
                    child_loop,
                    child_context,
                    child_bus,
                    child_run_path,
                    child_run_id,
                    timeout=timeout,
                )
            )
            self._task_registry.register(
                child_run_id,
                task,
                child_context,
                description=p.description,
                batch_id=self._batch_id,
            )
            return ToolResult(
                content=(
                    f"Subagent started in background. run_id={child_run_id}. "
                    f"Use agent_result(run_id='{child_run_id}') to retrieve result."
                )
            )

        # ── foreground mode: block until done ─────────────────────────────
        try:
            async with asyncio.timeout(timeout):
                async with EventWriter(child_run_path / "events.jsonl") as writer:
                    writer.subscribe(child_bus)
                    await child_loop.run(child_context)
        except TimeoutError:
            child_context.status = "failed"
            child_context.reason = f"timed out after {timeout}s"
            child_context.result = child_context.result or f"Subagent timed out after {timeout}s"

        await self._parent_bus.publish(
            SubagentFinishedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                status=child_context.status,
                ts=_now(),
            )
        )

        if child_context.status == "success":
            return ToolResult(
                content=child_context.result or "Subagent completed with no text output."
            )
        return ToolResult(
            content=(
                child_context.result
                or f"Subagent failed (status={child_context.status}, reason={child_context.reason})"
            ),
            is_error=True,
            error_type="runtime_error",
        )

    # Background wrapper with timeout + mark_finished hook.
    async def _run_background_wrapped(
        self,
        loop: AgentLoop,
        context: ExecutionContext,
        bus: EventBus,
        run_path: Path,
        run_id: str,
        *,
        timeout: int,
    ) -> None:
        try:
            async with asyncio.timeout(timeout):
                await self._run_background(loop, context, bus, run_path, run_id)
        except TimeoutError:
            try:
                context.status = "failed"
                context.reason = f"timed out after {timeout}s"
                context.result = context.result or f"Subagent timed out after {timeout}s"
            except Exception:
                pass
            try:
                await self._parent_bus.publish(
                    SubagentFinishedEvent(
                        run_id=run_id,
                        parent_run_id=self._parent_run_id,
                        status="failed",
                        ts=_now(),
                    )
                )
            except Exception:
                pass
        except asyncio.CancelledError:
            try:
                context.status = "cancelled"
                context.reason = context.reason or "cancelled"
            except Exception:
                pass
            try:
                await self._parent_bus.publish(
                    SubagentFinishedEvent(
                        run_id=run_id,
                        parent_run_id=self._parent_run_id,
                        status="cancelled",
                        ts=_now(),
                    )
                )
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                context.status = "failed"
                context.reason = f"exception: {exc}"
                context.result = context.result or str(exc)
            except Exception:
                pass
            try:
                await self._parent_bus.publish(
                    SubagentFinishedEvent(
                        run_id=run_id,
                        parent_run_id=self._parent_run_id,
                        status="failed",
                        ts=_now(),
                    )
                )
            except Exception:
                pass
        finally:
            try:
                self._task_registry.mark_finished(run_id)
            except Exception:
                pass

    async def _run_background(
        self,
        loop: AgentLoop,
        context: ExecutionContext,
        bus: EventBus,
        run_path: Path,
        run_id: str,
    ) -> None:
        async with EventWriter(run_path / "events.jsonl") as writer:
            writer.subscribe(bus)
            await loop.run(context)
        await self._parent_bus.publish(
            SubagentFinishedEvent(
                run_id=run_id,
                parent_run_id=self._parent_run_id,
                status=context.status,
                ts=_now(),
            )
        )

    def _build_child_registry(
        self,
        child_bus: EventBus,
        child_run_id: str,
        profile: AgentProfile | None,
    ) -> ToolRegistry:
        from iwan_claude.core.task.manager import TaskManager

        allowed: set[str] | None = (
            set(profile.allowed_tools) if profile and profile.allowed_tools else None
        )

        def _allowed(name: str) -> bool:
            return allowed is None or name in allowed

        registry = ToolRegistry()
        _all_tools = [
            ReadFileTool(),
            BashTool(),
            WriteFileTool(),
            ListDirTool(),
        ]
        for t in _all_tools:
            if _allowed(t.name):
                registry.register(t)

        child_task_manager = TaskManager(self._runs_dir / child_run_id / ".tasks")
        for t in [
            TaskCreateTool(child_task_manager),
            TaskUpdateTool(child_task_manager),
            TaskListTool(child_task_manager),
            TaskGetTool(child_task_manager),
        ]:
            if _allowed(t.name):
                registry.register(t)

        if self._depth < 1:
            nested = SpawnAgentTool(
                provider=self._provider,
                parent_bus=child_bus,
                parent_run_id=child_run_id,
                permission_manager=self._permission_manager,
                max_steps=self._max_steps,
                task_registry=self._task_registry,
                runs_dir=self._runs_dir,
                session_id=self._session_id,
                llm_model_name=self._llm_model_name,
                depth=self._depth + 1,
            )
            if _allowed("spawn_agent"):
                registry.register(nested)
            if _allowed("agent_result"):
                registry.register(AgentResultTool(self._task_registry))
            if _allowed("spawn_agents"):
                registry.register(
                    SpawnAgentsTool(
                        provider=self._provider,
                        parent_bus=child_bus,
                        parent_run_id=child_run_id,
                        permission_manager=self._permission_manager,
                        max_steps=self._max_steps,
                        task_registry=self._task_registry,
                        runs_dir=self._runs_dir,
                        session_id=self._session_id,
                        llm_model_name=self._llm_model_name,
                        depth=self._depth + 1,
                    )
                )
            if _allowed("batch_result"):
                registry.register(BatchResultTool(self._task_registry))
            if _allowed("cancel_agent"):
                registry.register(CancelAgentTool(self._task_registry))

        return registry


# ═══════════════════════════════════════════════════════════════════════════════
# AgentResultTool (existing, unchanged semantics)
# ═══════════════════════════════════════════════════════════════════════════════


class AgentResultParams(BaseModel):
    run_id: str


class AgentResultTool(BaseTool):
    name = "agent_result"
    description = (
        "Retrieve the result of a background sub-agent previously started with spawn_agent. "
        "Returns 'still running' if the sub-agent has not yet completed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The run_id returned by spawn_agent(run_in_background=true)",
            },
        },
        "required": ["run_id"],
    }
    params_model = AgentResultParams

    def __init__(self, task_registry: BackgroundTaskRegistry) -> None:
        self._task_registry = task_registry

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = AgentResultParams.model_validate(params)
        entry = self._task_registry.get(p.run_id)
        if entry is None:
            return ToolResult(
                content=f"Unknown run_id: {p.run_id}. Only background subagents can be queried.",
                is_error=True,
                error_type="runtime_error",
            )
        task, context = entry
        if not task.done():
            return ToolResult(content="still running")
        if task.cancelled():
            return ToolResult(
                content="Subagent was cancelled.", is_error=True, error_type="runtime_error"
            )
        exc = task.exception()
        if exc is not None:
            return ToolResult(
                content=f"Subagent raised an exception: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=context.result or "Subagent completed with no text result.")


# ═══════════════════════════════════════════════════════════════════════════════
# SpawnAgentsTool (NEW): batch parallel spawning with concurrency semaphore
# ═══════════════════════════════════════════════════════════════════════════════


class SpawnAgentTask(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str
    prompt: str
    subagent_type: str = ""
    timeout_sec: float = Field(default=0.0, ge=0.0)


class SpawnAgentsParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tasks: list[SpawnAgentTask] = Field(min_length=1)
    max_concurrency: int = Field(default=3, ge=1, le=50)
    wait: bool = True
    wait_timeout_sec: float = Field(default=0.0, ge=0.0)
    batch_description: str = ""


class SpawnAgentsTool(BaseTool):
    name = "spawn_agents"
    description = (
        "Spawn MULTIPLE isolated sub-agents in parallel to handle a batch of independent tasks. "
        "max_concurrency limits how many sub-agents run at once (prevents rate-limit 429). "
        "wait=true blocks until all complete and returns aggregated results; "
        "wait=false returns immediately with a batch_id; use batch_result to poll later."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "prompt": {"type": "string"},
                        "subagent_type": {"type": "string", "default": ""},
                        "timeout_sec": {"type": "number", "default": 0},
                    },
                    "required": ["description", "prompt"],
                },
                "minItems": 1,
                "description": "List of independent sub-agent tasks.",
            },
            "max_concurrency": {
                "type": "integer",
                "default": 3,
                "minimum": 1,
                "maximum": 50,
                "description": "Max sub-agents running simultaneously (semaphore limit).",
            },
            "wait": {
                "type": "boolean",
                "default": True,
                "description": "True=block until all done & return summary; False=return batch_id immediately.",
            },
            "wait_timeout_sec": {
                "type": "number",
                "default": 0,
                "description": "Total wait timeout when wait=true; 0=unlimited or per-task timeout applies.",
            },
            "batch_description": {
                "type": "string",
                "default": "",
                "description": "Short label shown in progress UI for the whole batch.",
            },
        },
        "required": ["tasks"],
    }
    params_model = SpawnAgentsParams

    def __init__(
        self,
        provider: LLMProvider,
        parent_bus: EventBus,
        parent_run_id: str,
        permission_manager: PermissionManager | None,
        max_steps: int,
        task_registry: BackgroundTaskRegistry,
        runs_dir: Path,
        session_id: str,
        llm_model_name: str,
        depth: int = 0,
    ) -> None:
        self._provider = provider
        self._parent_bus = parent_bus
        self._parent_run_id = parent_run_id
        self._permission_manager = permission_manager
        self._max_steps = max_steps
        self._task_registry = task_registry
        self._runs_dir = runs_dir
        self._session_id = session_id
        self._llm_model_name = llm_model_name
        self._depth = depth

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SpawnAgentsParams.model_validate(params)

        if self._depth >= 2:
            return ToolResult(
                content="Subagent nesting limit (2) reached; cannot spawn further subagents.",
                is_error=True,
                error_type="runtime_error",
            )
        if not p.tasks:
            return ToolResult(
                content="spawn_agents: tasks must be non-empty",
                is_error=True,
                error_type="schema_error",
            )

        batch_id = _new_batch_id()
        per_task_tool = SpawnAgentTool(
            provider=self._provider,
            parent_bus=self._parent_bus,
            parent_run_id=self._parent_run_id,
            permission_manager=self._permission_manager,
            max_steps=self._max_steps,
            task_registry=self._task_registry,
            runs_dir=self._runs_dir,
            session_id=self._session_id,
            llm_model_name=self._llm_model_name,
            depth=self._depth,
            batch_id=batch_id,
        )

        run_ids: list[str] = []
        start_failed = False
        failure_msg = ""

        if not p.wait:
            # Background batch: register each task immediately (no concurrency cap on
            # spawning — caller uses batch_result for ongoing polling).
            for task in p.tasks:
                sub_params = {
                    "description": task.description,
                    "prompt": task.prompt,
                    "run_in_background": True,
                    "subagent_type": task.subagent_type,
                    "timeout_sec": task.timeout_sec,
                }
                res = await per_task_tool.invoke(sub_params)
                if res.is_error:
                    start_failed = True
                    failure_msg = res.content
                    break
                marker = "run_id="
                start = res.content.find(marker)
                if start == -1:
                    start_failed = True
                    failure_msg = f"unexpected background response: {res.content}"
                    break
                tail = res.content[start + len(marker) :]
                rid = tail.split()[0].rstrip(".")
                run_ids.append(rid)

            if start_failed:
                for started in run_ids:
                    self._task_registry.cancel(started, reason="sibling failed to start")
                return ToolResult(
                    content=f"spawn_agents: one task failed to start: {failure_msg}",
                    is_error=True,
                    error_type="runtime_error",
                )

            self._task_registry.register_batch(
                batch_id, run_ids, description=p.batch_description or f"batch of {len(run_ids)}"
            )
            return ToolResult(
                content=(
                    f"Spawned {len(run_ids)} sub-agents in background batch. batch_id={batch_id}. "
                    f"Use batch_result(batch_id='{batch_id}') to poll / wait for completion."
                )
            )

        # ── wait=true: use Semaphore to cap how many sub-agents run in parallel ──
        sem = asyncio.Semaphore(p.max_concurrency)
        cancelled_any: dict[str, bool] = {"v": False}

        async def _run_one(i: int, task: SpawnAgentTask) -> tuple[int, str | None]:
            """
            One per-task coroutine that:
              (1) holds semaphore for the ENTIRE sub-agent lifecycle (spawn+run),
                  so max_concurrency truly limits parallel-in-flight sub-agents.
              (2) registers the run_id into the shared list (ordered by input order).
            Returns (index, extracted run_id or None on failure).
            """
            async with sem:
                if cancelled_any["v"]:
                    return (i, None)
                sub_params = {
                    "description": task.description,
                    "prompt": task.prompt,
                    "run_in_background": True,
                    "subagent_type": task.subagent_type,
                    "timeout_sec": task.timeout_sec,
                }
                res = await per_task_tool.invoke(sub_params)
                if res.is_error:
                    return (i, None)
                marker = "run_id="
                pos = res.content.find(marker)
                if pos == -1:
                    return (i, None)
                tail = res.content[pos + len(marker) :]
                rid = tail.split()[0].rstrip(".")

                # Wait for actual sub-agent completion WHILE STILL holding the
                # semaphore — this is what guarantees max_concurrency caps
                # PARALLEL RUNS, not just parallel spawn calls.
                entry = self._task_registry.get(rid)
                if entry is not None:
                    t, _ctx = entry
                    try:
                        await t
                    except BaseException:
                        pass
                return (i, rid)

        worker_coros = [_run_one(i, t) for i, t in enumerate(p.tasks)]
        try:
            if p.wait_timeout_sec > 0:
                async with asyncio.timeout(p.wait_timeout_sec):
                    outcomes = await asyncio.gather(*worker_coros, return_exceptions=True)
            else:
                outcomes = await asyncio.gather(*worker_coros, return_exceptions=True)
        except TimeoutError:
            # Try to cancel any not-yet-started siblings by flipping the flag and
            # cancelling tasks that may be running.
            cancelled_any["v"] = True
            # Even though gather returned due to timeout, some sub-agents might
            # still be in flight inside their spawned Task already registered.
            for rid in run_ids:
                self._task_registry.cancel(rid, reason="batch wait timeout")
            # Also scan registry for any registered run_ids belonging to this
            # batch by checking parent_run_id/description heuristics not possible,
            # so rely on the run_ids list we *did* collect so far plus cancel all
            # tasks whose batch_id matches ours.
            for already_rid in list(self._task_registry._tasks.keys()):
                meta = self._task_registry._task_meta.get(already_rid, {})
                if meta.get("batch_id") == batch_id:
                    self._task_registry.cancel(already_rid, reason="batch wait timeout")
            return ToolResult(
                content=(
                    f"spawn_agents: batch_id={batch_id} timed out after {p.wait_timeout_sec}s; "
                    f"running tasks cancelled. Use batch_result(batch_id='{batch_id}') for partial snapshot."
                ),
                is_error=True,
                error_type="runtime_error",
            )

        # Build run_ids list preserving original task order.
        ordered: list[tuple[int, str]] = []
        any_failed_start = False
        for out in outcomes:
            if isinstance(out, BaseException) or not isinstance(out, tuple):
                any_failed_start = True
                continue
            idx, rid = out
            if rid is None:
                any_failed_start = True
                continue
            ordered.append((idx, rid))
        ordered.sort(key=lambda x: x[0])
        run_ids = [rid for _idx, rid in ordered]

        if any_failed_start or len(run_ids) != len(p.tasks):
            for started in run_ids:
                self._task_registry.cancel(started, reason="sibling failed to start")
            return ToolResult(
                content=(
                    f"spawn_agents: one or more tasks failed to start; "
                    f"succeeded={len(run_ids)}/{len(p.tasks)}."
                ),
                is_error=True,
                error_type="runtime_error",
            )

        self._task_registry.register_batch(
            batch_id, run_ids, description=p.batch_description or f"batch of {len(run_ids)}"
        )
        status = self._task_registry.batch_status(batch_id) or _empty_status(batch_id, run_ids)
        return ToolResult(content=format_batch_status(status, include_results=True))


def _empty_status(batch_id: str, run_ids: list[str]) -> BatchStatus:
    return BatchStatus(
        batch_id=batch_id,
        total=len(run_ids),
        running=0,
        completed=0,
        success=0,
        failed=0,
        cancelled=0,
        duration_sec=0.0,
        results=[
            {
                "run_id": rid,
                "description": "",
                "status": "unknown",
                "result": None,
                "elapsed_sec": 0.0,
            }
            for rid in run_ids
        ],
    )


def format_batch_status(status: BatchStatus, *, include_results: bool = True) -> str:
    head = (
        f"spawn_agents batch_id={status.batch_id} "
        f"total={status.total} running={status.running} "
        f"success={status.success} failed={status.failed} "
        f"cancelled={status.cancelled} duration_sec={status.duration_sec}"
    )
    if not include_results:
        return head
    lines = [head, ""]
    for r in status.results:
        snippet = ""
        if r.get("result"):
            text = str(r["result"])
            snippet = text[:200].replace("\n", "\\n")
            if len(text) > 200:
                snippet += "…"
        lines.append(
            f"- [{r.get('status','?')}] run_id={r.get('run_id')} "
            f"elapsed={r.get('elapsed_sec', 0)}s "
            f"desc={r.get('description','')!r} result={snippet!r}"
        )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# BatchResultTool (NEW): poll / wait for batch completion
# ═══════════════════════════════════════════════════════════════════════════════


class BatchResultParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    batch_id: str
    wait: bool = False
    timeout_sec: float = Field(default=0.0, ge=0.0)
    poll_interval_sec: float = Field(default=0.2, ge=0.05, le=10.0)


class BatchResultTool(BaseTool):
    name = "batch_result"
    description = (
        "Retrieve current status of a batch started by spawn_agents(wait=false). "
        "wait=false returns an immediate snapshot; wait=true blocks until all tasks "
        "complete/fail/cancel or the timeout elapses."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "batch_id": {"type": "string", "description": "Batch id returned by spawn_agents."},
            "wait": {
                "type": "boolean",
                "default": False,
                "description": "True=block until batch terminal; False=snapshot now.",
            },
            "timeout_sec": {
                "type": "number",
                "default": 0,
                "description": "Wait timeout when wait=true. 0=unlimited.",
            },
            "poll_interval_sec": {
                "type": "number",
                "default": 0.2,
                "description": "Polling interval when wait=true (0.05 ~ 10s).",
            },
        },
        "required": ["batch_id"],
    }
    params_model = BatchResultParams

    def __init__(self, task_registry: BackgroundTaskRegistry) -> None:
        self._task_registry = task_registry

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = BatchResultParams.model_validate(params)

        status = self._task_registry.batch_status(p.batch_id)
        if status is None:
            return ToolResult(
                content=f"Unknown batch_id: {p.batch_id}",
                is_error=True,
                error_type="runtime_error",
            )

        if not p.wait:
            return ToolResult(content=format_batch_status(status, include_results=True))

        # wait=true: poll until batch is terminal or timeout
        def _is_terminal(st: BatchStatus) -> bool:
            return st.running == 0

        try:
            if p.timeout_sec > 0:
                async with asyncio.timeout(p.timeout_sec):
                    while not _is_terminal(status):
                        await asyncio.sleep(p.poll_interval_sec)
                        self._task_registry.prune()
                        next_status = self._task_registry.batch_status(p.batch_id)
                        status = next_status if next_status is not None else status
            else:
                while not _is_terminal(status):
                    await asyncio.sleep(p.poll_interval_sec)
                    self._task_registry.prune()
                    next_status = self._task_registry.batch_status(p.batch_id)
                    status = next_status if next_status is not None else status
        except TimeoutError:
            return ToolResult(
                content=(
                    f"batch_result: batch_id={p.batch_id} still running after "
                    f"{p.timeout_sec}s; latest={format_batch_status(status, include_results=False)}"
                ),
                is_error=True,
                error_type="runtime_error",
            )

        return ToolResult(content=format_batch_status(status, include_results=True))


# ═══════════════════════════════════════════════════════════════════════════════
# CancelAgentTool (NEW): cancel single run_id or entire batch_id
# ═══════════════════════════════════════════════════════════════════════════════


class CancelAgentParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    run_id: str | None = None
    batch_id: str | None = None
    reason: str = "user cancelled"


class CancelAgentTool(BaseTool):
    name = "cancel_agent"
    description = (
        "Cancel a running background sub-agent (run_id) or an entire batch (batch_id). "
        "Exactly one of run_id or batch_id must be provided. "
        "Cancelled tasks return 'cancelled' status from agent_result / batch_result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "Cancel a single background sub-agent by run_id.",
            },
            "batch_id": {
                "type": "string",
                "description": "Cancel all tasks belonging to batch_id.",
            },
            "reason": {
                "type": "string",
                "default": "user cancelled",
                "description": "Short human-readable reason stored with cancelled tasks.",
            },
        },
    }
    params_model = CancelAgentParams

    def __init__(self, task_registry: BackgroundTaskRegistry) -> None:
        self._task_registry = task_registry

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = CancelAgentParams.model_validate(params)
        if bool(p.run_id) == bool(p.batch_id):
            return ToolResult(
                content="cancel_agent: provide exactly one of run_id or batch_id",
                is_error=True,
                error_type="schema_error",
            )

        if p.batch_id:
            n = self._task_registry.cancel_batch(p.batch_id, reason=p.reason)
            return ToolResult(
                content=f"cancel_agent: cancelled {n} task(s) in batch_id={p.batch_id}"
            )
        else:
            assert p.run_id is not None
            ok = self._task_registry.cancel(p.run_id, reason=p.reason)
            if not ok:
                return ToolResult(
                    content=(
                        f"cancel_agent: run_id={p.run_id} not found or already completed. "
                        "Use agent_result to check status."
                    ),
                    is_error=True,
                    error_type="runtime_error",
                )
            return ToolResult(content=f"cancel_agent: cancelled run_id={p.run_id}")
