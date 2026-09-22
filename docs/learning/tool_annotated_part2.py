# =============================================================================
# 二、AgentResultTool —— 查询后台子 Agent 结果
# =============================================================================


class AgentResultParams(BaseModel):
    """AgentResultTool 的参数校验模型。

    极简参数：只需要 run_id
    """
    run_id: str


class AgentResultTool(BaseTool):
    """查询后台子 Agent 的执行结果。

    使用场景：
    - spawn_agent(run_in_background=true) 返回 run_id 后
    - 主 Agent 可以稍后调用 agent_result 查询执行结果

    返回值语义：
    - "still running" —— 子 Agent 还在执行
    - "Subagent was cancelled." —— 被取消
    - "Subagent raised an exception: ..." —— 执行异常
    - 实际结果文本 —— 执行成功
    """

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
        """初始化。

        参数:
            task_registry: 所有后台任务的注册表（共享同一个实例）
        """
        self._task_registry = task_registry

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """查询子 Agent 结果。

        查询逻辑：
        1. 从注册表获取 (Task, ExecutionContext) 元组
        2. 检查 Task 状态：done → cancelled → exception → success
        3. 从 context.result 获取最终结果
        """
        p = AgentResultParams.model_validate(params)
        entry = self._task_registry.get(p.run_id)
        if entry is None:
            return ToolResult(
                content=f"Unknown run_id: {p.run_id}. Only background subagents can be queried.",
                is_error=True,
                error_type="runtime_error",
            )
        task, context = entry

        # 检查 Task 是否已完成
        # asyncio.Task 的状态检查方法：
        # - done(): Task 是否已结束（完成/失败/取消）
        # - cancelled(): Task 是否被取消
        # - exception(): Task 的异常（如果已完成且有异常）
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
        # 成功：返回 context.result
        return ToolResult(content=context.result or "Subagent completed with no text result.")


# =============================================================================
# 三、SpawnAgentsTool —— 批量并行生成子 Agent
# =============================================================================


class SpawnAgentTask(BaseModel):
    """单个子任务的参数模型（用于 SpawnAgentsTool 的 tasks 列表）。

    与 SpawnAgentParams 的区别：
    - 没有 run_in_background 参数（批量模式下默认都是后台运行）
    - 没有 batch_id（由 SpawnAgentsTool 统一分配）
    - 字段精简，只保留批量场景需要的
    """
    model_config = ConfigDict(extra="ignore")
    description: str
    prompt: str
    subagent_type: str = ""
    timeout_sec: float = Field(default=0.0, ge=0.0)


class SpawnAgentsParams(BaseModel):
    """SpawnAgentsTool 的参数校验模型。

    参数说明：
    - tasks: 子任务列表（至少 1 个）
    - max_concurrency: 最大并发数（1-50，默认 3）
      → 使用 asyncio.Semaphore 控制
      → 为什么限制在 1-50？防止一次性开太多 LLM 调用导致 429 限流
    - wait: 是否阻塞等待所有任务完成
      → True: 返回完整执行结果
      → False: 立即返回 batch_id，后续用 batch_result 查询
    - wait_timeout_sec: wait=true 时的总超时（0 表示不限）
    - batch_description: 批次描述，用于 UI 展示
    """
    model_config = ConfigDict(extra="ignore")
    tasks: list[SpawnAgentTask] = Field(min_length=1)
    max_concurrency: int = Field(default=3, ge=1, le=50)
    wait: bool = True
    wait_timeout_sec: float = Field(default=0.0, ge=0.0)
    batch_description: str = ""


class SpawnAgentsTool(BaseTool):
    """批量并行生成多个子 Agent。

    核心特性：
    1. 并发控制：使用 asyncio.Semaphore 限制同时运行的子 Agent 数量
    2. 两种等待模式：wait=true 阻塞等待 / wait=false 立即返回
    3. 优雅取消：超时或某个任务失败时，取消同批次的其他任务
    4. 结果聚合：按原始顺序返回每个子任务的结果

    并发控制的实现原理：
    - Semaphore 控制的不是"同时 spawn 的数量"，而是"同时运行中的数量"
    - 每个 _run_one 协程获取信号量后，会等待子 Agent 完全执行完才释放
    - 这样 max_concurrency=3 就真的保证同时最多 3 个子 Agent 在运行
    """

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
        """初始化。

        参数与 SpawnAgentTool 类似，但 task_registry 和 runs_dir 是必填的
        （批量模式下必须有注册表来管理多个任务）
        """
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
        """批量生成子 Agent 的核心方法。

        完整流程：
        1. 参数校验 + 深度检查
        2. 创建 batch_id 和 SpawnAgentTool 实例（复用单个生成逻辑）
        3. 根据 wait 参数选择后台模式或等待模式
        4. 后台模式：立即生成所有子 Agent，返回 batch_id
        5. 等待模式：用 Semaphore 控制并发，收集所有结果
        """
        p = SpawnAgentsParams.model_validate(params)

        # 深度检查（与 SpawnAgentTool 相同逻辑）
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

        # 创建 batch_id 和复用的 SpawnAgentTool
        # 每个子任务都会通过 per_task_tool.invoke() 来生成
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
            batch_id=batch_id,  # 标记所有子 Agent 属于同一批次
        )

        run_ids: list[str] = []
        start_failed = False
        failure_msg = ""

        # ── 分支 1：wait=false 后台批量模式 ──
        # 每个子任务都立即 spawn（不等待完成）
        if not p.wait:
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
                # 从返回文本中解析 run_id
                # 返回格式: "Subagent started in background. run_id=abc123. Use agent_result..."
                marker = "run_id="
                start = res.content.find(marker)
                if start == -1:
                    start_failed = True
                    failure_msg = f"unexpected background response: {res.content}"
                    break
                tail = res.content[start + len(marker) :]
                rid = tail.split()[0].rstrip(".")
                run_ids.append(rid)

            # 如果有任务启动失败，取消已启动的任务
            if start_failed:
                for started in run_ids:
                    self._task_registry.cancel(started, reason="sibling failed to start")
                return ToolResult(
                    content=f"spawn_agents: one task failed to start: {failure_msg}",
                    is_error=True,
                    error_type="runtime_error",
                )

            # 注册批次信息，后续可通过 batch_id 查询
            self._task_registry.register_batch(
                batch_id, run_ids, description=p.batch_description or f"batch of {len(run_ids)}"
            )
            return ToolResult(
                content=(
                    f"Spawned {len(run_ids)} sub-agents in background batch. batch_id={batch_id}. "
                    f"Use batch_result(batch_id='{batch_id}') to poll / wait for completion."
                )
            )

        # ── 分支 2：wait=true 等待模式 ──
        # 使用 Semaphore 控制并发，用 gather 收集结果
        sem = asyncio.Semaphore(p.max_concurrency)
        cancelled_any: dict[str, bool] = {"v": False}  # 用 dict 以便内部函数修改

        async def _run_one(i: int, task: SpawnAgentTask) -> tuple[int, str | None]:
            """单个子任务的执行协程。

            关键设计：
            1. 获取信号量 → spawn 子 Agent → 等待完成 → 释放信号量
               → 这保证了 max_concurrency 限制的是"运行中"的数量
            2. 返回 (索引, run_id) 元组，用于后续按原始顺序排序

            参数:
                i: 任务在列表中的索引（用于结果排序）
                task: 子任务参数

            返回:
                (index, run_id) 或 (index, None) 表示失败
            """
            async with sem:
                # 检查是否有其他任务失败，提前退出
                if cancelled_any["v"]:
                    return (i, None)

                # 构造参数并调用 SpawnAgentTool（后台模式）
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

                # 解析 run_id
                marker = "run_id="
                pos = res.content.find(marker)
                if pos == -1:
                    return (i, None)
                tail = res.content[pos + len(marker) :]
                rid = tail.split()[0].rstrip(".")

                # 关键：等待子 Agent 实际完成（此时仍持有信号量）
                # 这保证了并发控制是针对"运行中"而非"启动中"
                entry = self._task_registry.get(rid)
                if entry is not None:
                    t, _ctx = entry
                    try:
                        await t  # 等待 Task 完成
                    except BaseException:
                        pass
                return (i, rid)

        # 构造所有 worker 协程
        worker_coros = [_run_one(i, t) for i, t in enumerate(p.tasks)]

        try:
            # asyncio.gather 并发执行所有 worker
            # return_exceptions=True: 不抛出异常，而是把异常作为结果返回
            if p.wait_timeout_sec > 0:
                async with asyncio.timeout(p.wait_timeout_sec):
                    outcomes = await asyncio.gather(*worker_coros, return_exceptions=True)
            else:
                outcomes = await asyncio.gather(*worker_coros, return_exceptions=True)
        except TimeoutError:
            # 批次超时：取消所有已注册的子 Agent
            cancelled_any["v"] = True
            for rid in run_ids:
                self._task_registry.cancel(rid, reason="batch wait timeout")
            # 额外扫描注册表中属于本批次的任务
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

        # 处理结果：按原始顺序排序
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

        # 按索引排序，恢复原始顺序
        ordered.sort(key=lambda x: x[0])
        run_ids = [rid for _idx, rid in ordered]

        # 如果有任务启动失败，取消所有已启动的任务
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

        # 全部成功：注册批次并返回状态
        self._task_registry.register_batch(
            batch_id, run_ids, description=p.batch_description or f"batch of {len(run_ids)}"
        )
        status = self._task_registry.batch_status(batch_id) or _empty_status(batch_id, run_ids)
        return ToolResult(content=format_batch_status(status, include_results=True))


# ── 辅助函数 ──

def _empty_status(batch_id: str, run_ids: list[str]) -> BatchStatus:
    """创建空的批次状态对象。

    设计原因：当注册表中找不到批次信息时，用此函数构造一个默认状态
    避免在主逻辑中到处做 None 检查
    """
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
    """格式化批次状态为可读字符串。

    输出格式：
    spawn_agents batch_id=b_xxx total=3 running=0 success=2 failed=1 cancelled=0 duration_sec=12.5
    - [success] run_id=abc elapsed=4.2s desc='分析日志' result='日志分析完成...'
    - [failed] run_id=def elapsed=8.3s desc='生成报告' result='超时...'

    参数:
        status: 批次状态对象
        include_results: 是否包含每个任务的详细结果
    """
    # 构建头部摘要
    head = (
        f"spawn_agents batch_id={status.batch_id} "
        f"total={status.total} running={status.running} "
        f"success={status.success} failed={status.failed} "
        f"cancelled={status.cancelled} duration_sec={status.duration_sec}"
    )
    if not include_results:
        return head

    # 构建每个任务的详细结果
    lines = [head, ""]
    for r in status.results:
        snippet = ""
        if r.get("result"):
            text = str(r["result"])
            snippet = text[:200].replace("\n", "\\n")  # 截断到 200 字符
            if len(text) > 200:
                snippet += "…"
        lines.append(
            f"- [{r.get('status','?')}] run_id={r.get('run_id')} "
            f"elapsed={r.get('elapsed_sec', 0)}s "
            f"desc={r.get('description','')!r} result={snippet!r}"
        )
    return "\n".join(lines)


# =============================================================================
# 四、BatchResultTool —— 查询批量任务状态
# =============================================================================


class BatchResultParams(BaseModel):
    """BatchResultTool 的参数校验模型。

    参数说明：
    - batch_id: 批次 ID（必填）
    - wait: 是否阻塞等待批次完成
    - timeout_sec: 等待超时（0 不限）
    - poll_interval_sec: 轮询间隔（0.05-10 秒，默认 0.2）
      → 为什么需要轮询？因为 asyncio 没有原生的"事件通知"机制
      → 替代方案：定期检查状态是否变化
    """
    model_config = ConfigDict(extra="ignore")
    batch_id: str
    wait: bool = False
    timeout_sec: float = Field(default=0.0, ge=0.0)
    poll_interval_sec: float = Field(default=0.2, ge=0.05, le=10.0)


class BatchResultTool(BaseTool):
    """查询批量任务的状态。

    两种使用模式：
    1. 快照模式（wait=false）：立即返回当前状态
    2. 等待模式（wait=true）：轮询直到所有任务完成或超时

    轮询实现：
    while not all_done:
        await asyncio.sleep(interval)
        check_status()
    """

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
        """查询批次状态。

        逻辑分支：
        1. wait=false: 立即返回当前快照
        2. wait=true: 轮询等待所有任务完成
           → 判定标准：running == 0（没有正在运行的任务）
        """
        p = BatchResultParams.model_validate(params)

        status = self._task_registry.batch_status(p.batch_id)
        if status is None:
            return ToolResult(
                content=f"Unknown batch_id: {p.batch_id}",
                is_error=True,
                error_type="runtime_error",
            )

        # 快照模式
        if not p.wait:
            return ToolResult(content=format_batch_status(status, include_results=True))

        # 等待模式：轮询直到所有任务完成
        def _is_terminal(st: BatchStatus) -> bool:
            """判断批次是否终止（没有正在运行的任务）。"""
            return st.running == 0

        try:
            if p.timeout_sec > 0:
                async with asyncio.timeout(p.timeout_sec):
                    while not _is_terminal(status):
                        await asyncio.sleep(p.poll_interval_sec)
                        self._task_registry.prune()  # 清理已完成的任务
                        next_status = self._task_registry.batch_status(p.batch_id)
                        status = next_status if next_status is not None else status
            else:
                # 无超时版本：逻辑相同但没有外层的 timeout
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


# =============================================================================
# 五、CancelAgentTool —— 取消子 Agent
# =============================================================================


class CancelAgentParams(BaseModel):
    """CancelAgentTool 的参数校验模型。

    设计约束：run_id 和 batch_id 必须提供且只能提供一个
    （通过 invoke 中的 bool 异或检查实现）
    """
    model_config = ConfigDict(extra="ignore")
    run_id: str | None = None
    batch_id: str | None = None
    reason: str = "user cancelled"


class CancelAgentTool(BaseTool):
    """取消正在运行的子 Agent 或整个批次。

    使用场景：
    - 主 Agent 决定放弃某个子任务
    - 用户手动取消批量任务

    参数约束：
    - run_id 和 batch_id 二选一（不能同时提供也不能都不提供）
    """

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
        """取消子 Agent。

        逻辑：
        1. 校验：run_id 和 batch_id 必须且只能提供一个
           → 使用 bool(x) 异或：bool(a) ^ bool(b) 为 True 说明只有一个为真
        2. 如果提供了 batch_id，调用 cancel_batch 批量取消
        3. 如果提供了 run_id，调用 cancel 单个取消
        """
        p = CancelAgentParams.model_validate(params)

        # 异或检查：恰好一个为 True
        if bool(p.run_id) == bool(p.batch_id):
            return ToolResult(
                content="cancel_agent: provide exactly one of run_id or batch_id",
                is_error=True,
                error_type="schema_error",
            )

        if p.batch_id:
            # 批量取消
            n = self._task_registry.cancel_batch(p.batch_id, reason=p.reason)
            return ToolResult(
                content=f"cancel_agent: cancelled {n} task(s) in batch_id={p.batch_id}"
            )
        else:
            # 单个取消
            assert p.run_id is not None  # 类型断言（经过异或检查后 p.run_id 必然非 None）
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
