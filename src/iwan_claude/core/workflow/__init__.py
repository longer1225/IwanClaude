"""
DAG 工作流编排模块

【设计理念】
本模块实现了基于有向无环图（DAG）的多任务编排系统。
与现有的 SpawnAgent（工具调用级别的并行）不同，
本模块在图级别定义任务依赖关系，自动调度并行和串行执行。

【与现有 subagent 的区别】
- subagent/tool.py：Agent 通过工具调用生成子 Agent，子 Agent 之间独立并行
- workflow/（本模块）：用户预定义任务依赖图，执行器自动调度

【模块结构】
- task.py：Task 数据类，定义单个任务
- graph.py：Workflow DAG 图管理，支持拓扑排序和环检测
- executor.py：WorkflowExecutor 执行器，并行调度 + 结果传递

【使用示例】
```python
from iwan_claude.core.workflow import Workflow, WorkflowExecutor, Task

# 1. 定义工作流
workflow = Workflow()
workflow.add_task(Task(name="research", description="调研需求", depends_on=[]))
workflow.add_task(Task(name="design", description="设计方案", depends_on=["research"]))
workflow.add_task(Task(name="frontend", description="前端开发", depends_on=["design"]))
workflow.add_task(Task(name="backend", description="后端开发", depends_on=["design"]))
workflow.add_task(Task(name="integrate", description="集成测试", depends_on=["frontend", "backend"]))

# 2. 验证 DAG
assert workflow.validate()  # 确保无环

# 3. 执行
executor = WorkflowExecutor(max_concurrency=3)

async def handler(task: Task, inputs: dict[str, str]) -> str:
    # inputs 是前置任务的输出
    return f"完成 {task.name}"

results = await executor.execute(workflow, handler)
# results = {"research": "...", "design": "...", "frontend": "...", ...}
```

【面试亮点】
"实现了 DAG 工作流引擎，支持任务依赖图定义、拓扑排序、
并行调度（asyncio.Semaphore 控制并发）、结果自动传递。
前端和后端开发任务在设计方案完成后自动并行执行。"
"""

from iwan_claude.core.workflow.executor import WorkflowExecutor
from iwan_claude.core.workflow.graph import Workflow
from iwan_claude.core.workflow.task import Task

__all__ = ["Task", "Workflow", "WorkflowExecutor"]
