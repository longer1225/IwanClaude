"""
工作流执行装配层（W1）— 把存量 DAG 引擎桥接到 subagent 与总线

【学习要点】
1. 存量 executor.py 一行不改的桥接法：它对世界的全部假设就是
   `TaskHandler = async (task, inputs) -> str`。本模块用一个闭包 handler
   把"记节点账 → 拼上游产出进 prompt → launch 子 Agent → await 收尾"
   塞进这个签名，DAG 语义（分层、并行、结果传递、fail-fast）原样继承。
2. fail-fast 的账不能信 execute() 的返回值：executor 在首个异常处 raise
   RuntimeError，同层兄弟结果虽已进它的局部 results 却随异常蒸发。
   所以每个节点的终态都在 handler 内当场写穿 store（record_node）——
   异常炸掉的是调度循环，不是记账。
3. 四个协作者全是注入的回调（launch/await_child/on_node/on_run_finished）：
   引擎本体不知道 subagent、总线、会话的存在，单测给四个 fake 就能把
   全部时序断言跑完，零 LLM 零网络。app.py 负责把真实现拧上。
4. CancelledError 单独收口：daemon 停机 cancel 活跃 run 时先 finish_run
   ("interrupted") 再 re-raise——记账必须完成，广播则不保证（事件循环正在
   解散，订阅端靠 workflow.get 对账补画）。re-raise 是给 gather 的礼貌。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from iwan_claude.core.workflow.executor import WorkflowExecutor
from iwan_claude.core.workflow.graph import Workflow
from iwan_claude.core.workflow.store import WorkflowStore
from iwan_claude.core.workflow.task import Task

log = logging.getLogger(__name__)

# 节点 prompt 里每路上游产出的截断宽度（子 Agent 上下文预算的粗粒度保护）
UPSTREAM_KEEP = 2000


# datetime → 秒级 ISO 串（与 store._iso 同口径；不跨模块导私有名，复制一行最干净）
def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@dataclass(frozen=True)
class NodeLaunchCtx:
    """
    launch 回调的入参：一个节点开跑所需的全部上下文

    - run_id / workflow_id: str - 本次运行与归属工作流
    - node: str - 节点名
    - prompt: str - 已拼好上游产出的完整指令（子 Agent 的 goal）
    """
    run_id: str
    workflow_id: str
    node: str
    prompt: str


@dataclass(frozen=True)
class NodeTransition:
    """
    on_node 回调的载荷：一个节点进入 running/ok/fail 的事实快照

    - status: str - "running" | "ok" | "fail"
    - detail: str - 失败摘要（仅 fail 非空）
    """
    run_id: str
    workflow_id: str
    node: str
    status: str
    child_run_id: str = ""
    detail: str = ""


@dataclass(frozen=True)
class RunTransition:
    """on_run_finished 回调的载荷：一次 run 的终局快照（与 workflow.finished 事件同字段）"""
    run_id: str
    workflow_id: str
    workflow_name: str
    session_id: str
    status: str
    error: str = ""
    finished_at: str = ""


# 启动一个节点子 Agent：async (ctx) -> (child_run_id, error)，error 非空即启动失败
LaunchFn = Callable[[NodeLaunchCtx], Awaitable[tuple[str, str]]]
# 等一个子 Agent 收尾：async (child_run_id) -> (是否成功, 产出全文或失败原因)
AwaitFn = Callable[[str], Awaitable[tuple[bool, str]]]
# 节点状态广播 / 运行收尾广播（app.py 拧上总线发布 + 定义行 touch）
OnNodeCb = Callable[[NodeTransition], Awaitable[None]]
OnRunFinishedCb = Callable[[RunTransition], Awaitable[None]]


class WorkflowEngine:
    """
    工作流运行调度器：即发即返 start_run + 事件回调 + 停机收口

    活跃 run 全部是本进程内存里的 asyncio.Task；进程重启后残留的 running
    行由 store.mark_orphans_interrupted 在装配时清算，引擎本身不管账。
    """

    def __init__(
        self,
        store: WorkflowStore,
        launch: LaunchFn,
        await_child: AwaitFn,
        on_node: OnNodeCb,
        on_run_finished: OnRunFinishedCb,
        max_concurrency: int = 3,
        max_parallel_runs: int = 2,
    ) -> None:
        self._store = store
        self._launch = launch
        self._await_child = await_child
        self._on_node = on_node
        self._on_run_finished = on_run_finished
        self._max_concurrency = max_concurrency
        self._max_parallel_runs = max_parallel_runs
        # run_id -> (workflow_id, 执行协程)；协程未收尾前条目一直在=活跃判据
        self._active: dict[str, tuple[str, asyncio.Task[None]]] = {}

    # 某工作流是否已有进行中的 run；有则返回其 run_id，否则空串
    def active_run_for(self, wf_id: str) -> str:
        for rid, (wid, _) in self._active.items():
            if wid == wf_id:
                return rid
        return ""

    # 启动一次运行：三重门（定义在/同流无活跃/全局并发未满）后 create_task 即发即返
    def start_run(self, wf_id: str) -> tuple[str, str]:
        self._store.load()
        wf_row = self._store.get(wf_id)
        if wf_row is None:
            return "", f"工作流不存在：{wf_id}"
        if self.active_run_for(wf_id):
            return "", "该工作流已有进行中的运行，请等待结束"
        if len(self._active) >= self._max_parallel_runs:
            return "", f"并行运行数已达上限（{self._max_parallel_runs}）"
        row = self._store.create_run(wf_row)
        run_id = str(row["id"])
        self._active[run_id] = (
            wf_id, asyncio.create_task(self._execute(run_id, wf_id), name=f"wf-run-{run_id}"),
        )
        return run_id, ""

    # 停机：cancel 全部活跃 run 协程并等收口；协程侧漏标的行在这里补刀，幂等
    async def shutdown(self) -> None:
        # _active 的值是 (wf_id, task) 二元组，items() 解出三层嵌套，逐层拆
        entries = [(rid, task) for rid, (_, task) in self._active.items()]
        self._active.clear()
        for _, t in entries:
            t.cancel()
        if entries:
            await asyncio.gather(*(t for _, t in entries), return_exceptions=True)
        # 【学习要点】"cancel 前协程从未被调度"的窗口：start_run 建的 Task 若
        # 一帧都没跑过就被 cancel，协程体连 except CancelledError 都不会执行，
        # interrupted 落不了账。gather 之后统一按内存登记补标一次——对已自行
        # 收尾的行是幂等覆盖（值相同），把这个窗口彻底焊死
        for rid, _ in entries:
            row = self._store.get_run(rid)
            if row is not None and row.get("status") == "running":
                self._store.finish_run(rid, "interrupted", "运行被取消")

    # 单次运行主体：构图 → 存量 executor 分层跑 → 三种终态各收尾一次
    async def _execute(self, run_id: str, wf_id: str) -> None:
        try:
            wf_row = self._store.get(wf_id)
            if wf_row is None:
                # run 已建、定义却被删——照常跑完 run 行，图从 run 行骨架重建不了，直接失败
                await self._finish(run_id, "failed", "工作流定义已被删除")
                return
            wf = Workflow()
            for t in wf_row.get("tasks", []):
                wf.add_task(Task(
                    name=str(t.get("name") or ""),
                    description=str(t.get("prompt") or ""),
                    depends_on=[str(d) for d in (t.get("depends_on") or [])],
                ))
            executor = WorkflowExecutor(max_concurrency=self._max_concurrency)
            await executor.execute(wf, self._make_handler(run_id, wf_id))
            await self._finish(run_id, "success", "")
        except asyncio.CancelledError:
            # 停机/取消：账必须落地（同步写穿），广播不补——事件循环正在解散
            self._store.finish_run(run_id, "interrupted", "运行被取消")
            self._active.pop(run_id, None)
            raise
        except Exception as e:
            # executor 的 fail-fast（RuntimeError）与一切意外都从这收口
            await self._finish(run_id, "failed", f"{type(e).__name__}: {e}")

    # 终局收尾：finish_run 落账 → on_run_finished 广播 → 摘活跃登记（幂等保护）
    async def _finish(self, run_id: str, status: str, error: str) -> None:
        row = self._store.finish_run(run_id, status, error)
        self._active.pop(run_id, None)
        if row is None:
            return
        # session_id 兜底重读：launch 懒建的会话 attach 在 run 行上，
        # finish_run 返回的旧快照可能没带（零节点失败路径则根本没有会话）
        fresh = self._store.get_run(run_id)
        if fresh is not None:
            row["session_id"] = str(fresh.get("session_id") or "")
        await self._safe_emit(self._on_run_finished, RunTransition(
            run_id=run_id,
            workflow_id=str(row.get("workflow_id") or ""),
            workflow_name=str(row.get("workflow_name") or ""),
            session_id=str(row.get("session_id") or ""),
            status=status,
            error=error,
            finished_at=str(row.get("finished_at") or ""),
        ))

    # 广播回调的统一兜底：订阅端异常绝不许掀翻运行主体（schedule 广播同款纪律）
    async def _safe_emit(self, cb: Any, payload: Any) -> None:
        try:
            await cb(payload)
        except Exception:
            log.exception("workflow: 事件广播回调异常（已忽略）")

    # 生成存量 TaskHandler 签名的闭包：记账+桥接全在这十几行里
    def _make_handler(
        self, run_id: str, wf_id: str,
    ) -> Callable[[Task, dict[str, str]], Awaitable[str]]:
        # 单节点完整生命周期：running 记账广播 → launch → child_run_id 补记 → await → 终态
        async def handler(task: Task, inputs: dict[str, str]) -> str:
            now = _iso(datetime.now())
            self._store.record_node(run_id, task.name, {"status": "running", "started_at": now})
            await self._safe_emit_node(run_id, wf_id, task.name, "running", "", "")
            prompt = _compose_prompt(task.description, inputs)
            child, err = await self._launch(NodeLaunchCtx(
                run_id=run_id, workflow_id=wf_id, node=task.name, prompt=prompt,
            ))
            if err:
                await self._fail_node(run_id, wf_id, task.name, child, f"启动失败：{err}")
                raise RuntimeError(f"节点「{task.name}」启动失败：{err}")
            # child_run_id 当场补记：哪怕子 Agent 挂到超时，历史行里也有深链可查
            self._store.record_node(run_id, task.name, {"child_run_id": child})
            ok, text = await self._await_child(child)
            if not ok:
                await self._fail_node(run_id, wf_id, task.name, child, text)
                raise RuntimeError(f"节点「{task.name}」执行失败：{text[:200]}")
            self._store.record_node(run_id, task.name, {
                "status": "ok", "output": text, "finished_at": _iso(datetime.now()),
            })
            await self._safe_emit_node(run_id, wf_id, task.name, "ok", child, "")
            return text
        return handler

    # 节点失败收口：fail 记账（detail 截断交 store）+ 广播
    async def _fail_node(
        self, run_id: str, wf_id: str, node: str, child: str, detail: str,
    ) -> None:
        self._store.record_node(run_id, node, {
            "status": "fail", "child_run_id": child,
            "detail": detail, "finished_at": _iso(datetime.now()),
        })
        await self._safe_emit_node(run_id, wf_id, node, "fail", child, detail)

    # 组装 NodeTransition 并广播（异常吞掉，纪律同 _safe_emit）
    async def _safe_emit_node(
        self, run_id: str, wf_id: str, node: str, status: str, child: str, detail: str,
    ) -> None:
        await self._safe_emit(self._on_node, NodeTransition(
            run_id=run_id, workflow_id=wf_id, node=node,
            status=status, child_run_id=child, detail=detail[:200],
        ))


# 节点指令 + 上游产出 → 子 Agent 的完整 prompt（每路上游截 UPSTREAM_KEEP 字）
def _compose_prompt(prompt: str, inputs: dict[str, str]) -> str:
    if not inputs:
        return prompt
    parts = [prompt, "", "【上游节点产出】"]
    for name, out in inputs.items():
        parts.append(f"◆ {name}：\n{out[:UPSTREAM_KEEP]}")
    return "\n\n".join(parts)
