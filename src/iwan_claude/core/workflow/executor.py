"""
DAG 工作流执行器

【设计理念】
WorkflowExecutor 负责按拓扑顺序执行 DAG 工作流：
- 同层任务并行执行（asyncio.Semaphore 控制并发度）
- 跨层任务串行执行（等前置层全部完成）
- 前置任务的输出自动传递给后续任务

【执行流程】
```
Layer 0: [A]          → 并行执行 A
Layer 1: [B, C]       → 并行执行 B, C（A 的输出传给 B, C）
Layer 2: [D]          → 执行 D（B, C 的输出传给 D）
```

【并发控制】
使用 asyncio.Semaphore 限制同时执行的任务数。
例如 max_concurrency=3 时，即使同层有 10 个任务，也最多同时执行 3 个。

【错误处理】
- 单个任务失败时，整个工作流停止
- 失败任务的后继任务不会执行
- 已完成任务的结果会保留

【与 subagent 的区别】
- subagent：Agent 通过工具调用生成子 Agent，子 Agent 之间独立
- workflow：用户预定义依赖图，执行器自动调度
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from iwan_claude.core.workflow.graph import Workflow
from iwan_claude.core.workflow.task import Task

log = logging.getLogger(__name__)

# 任务处理函数类型：接收 Task + 前置任务输出，返回当前任务输出
TaskHandler = Callable[[Task, dict[str, str]], Awaitable[str]]


class WorkflowExecutor:
    """
    DAG 工作流执行器

    【职责】
    - 按拓扑顺序执行工作流
    - 同层任务并行执行
    - 控制并发度
    - 传递前置任务输出

    【使用方式】
    ```python
    executor = WorkflowExecutor(max_concurrency=3)

    async def handler(task: Task, inputs: dict[str, str]) -> str:
        # inputs 是前置任务的输出
        if inputs:
            context = "\n".join(f"{k}: {v}" for k, v in inputs.items())
            return f"{task.name} done (context: {context})"
        return f"{task.name} done"

    results = await executor.execute(workflow, handler)
    # results = {"a": "a done", "b": "b done (context: a: a done)", ...}
    ```
    """

    def __init__(self, max_concurrency: int = 3) -> None:
        """
        初始化执行器

        【参数说明】
        - max_concurrency: int - 最大并发任务数（默认 3）
            设置为 0 或负数时不限制并发
        """
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))

    # ------------------------------------------------------------------
    # 执行入口
    # ------------------------------------------------------------------

    async def execute(
        self,
        workflow: Workflow,
        task_handler: TaskHandler,
    ) -> dict[str, str]:
        """
        执行 DAG 工作流

        【执行流程】
        1. 验证 DAG 合法性（无环 + 依赖存在）
        2. 循环执行：
           a. 获取当前可执行的任务（依赖已全部完成）
           b. 并行执行这些任务（Semaphore 控制并发）
           c. 收集结果
           d. 标记为已完成
        3. 所有任务完成后返回结果

        【参数说明】
        - workflow: Workflow - 工作流图
        - task_handler: TaskHandler - 任务处理函数
            接收 (task, inputs)，返回执行结果字符串
            inputs 是前置任务的输出 {task_name: output}

        【返回值】
        - dict[str, str]: 任务名 → 执行结果

        【异常】
        - ValueError: DAG 不合法（有环或依赖缺失）
        - RuntimeError: 执行过程中任务失败
        """
        # 1. 验证 DAG
        if not workflow.validate():
            raise ValueError("Invalid DAG: cycle detected or missing dependency")

        # 2. 执行
        completed: set[str] = set()
        results: dict[str, str] = {}
        total = workflow.count()

        while len(completed) < total:
            # 获取可执行任务
            ready = workflow.get_ready_tasks(completed)
            if not ready:
                log.warning("No ready tasks but %d remain (possible cycle)",
                            total - len(completed))
                break

            log.info("Executing layer: %s", ready)

            # 并行执行同层任务
            coroutines = []
            for name in ready:
                task = workflow.get_task(name)
                assert task is not None  # validate() 已确保存在
                # 收集前置任务的输出
                inputs = {
                    dep: results[dep]
                    for dep in task.depends_on
                    if dep in results
                }
                coroutines.append(self._run_task(task, inputs, task_handler))

            # 等待所有任务完成
            task_results = await asyncio.gather(*coroutines, return_exceptions=True)

            # 处理结果
            for name, result in zip(ready, task_results):
                if isinstance(result, Exception):
                    log.error("Task '%s' failed: %s", name, result)
                    # 保留已完成的结果，但标记失败
                    results[name] = f"ERROR: {result}"
                    # 单个任务失败，停止整个工作流
                    raise RuntimeError(f"Task '{name}' failed: {result}") from result
                results[name] = result
                completed.add(name)
                log.info("Task '%s' completed", name)

        return results

    # ------------------------------------------------------------------
    # 单任务执行
    # ------------------------------------------------------------------

    async def _run_task(
        self,
        task: Task,
        inputs: dict[str, str],
        handler: TaskHandler,
    ) -> str:
        """
        执行单个任务（带并发控制）

        【执行流程】
        1. 获取 Semaphore（控制并发度）
        2. 调用 task_handler 执行任务
        3. 返回结果

        【参数说明】
        - task: Task - 要执行的任务
        - inputs: dict[str, str] - 前置任务的输出
        - handler: TaskHandler - 任务处理函数

        【返回值】
        - str: 任务执行结果
        """
        async with self._semaphore:
            log.debug("Starting task '%s' with inputs: %s", task.name, list(inputs.keys()))
            return await handler(task, inputs)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def get_execution_plan(self, workflow: Workflow) -> list[list[str]]:
        """
        获取执行计划（分层展示）

        【用途】
        在执行前预览执行顺序，用于调试和可视化

        【返回值】
        - list[list[str]]: 分层任务列表
            [["a"], ["b", "c"], ["d"]]

        【示例】
        ```python
        plan = executor.get_execution_plan(workflow)
        for i, layer in enumerate(plan):
            print(f"Layer {i}: {layer}")
        ```
        """
        return workflow.get_execution_layers()
