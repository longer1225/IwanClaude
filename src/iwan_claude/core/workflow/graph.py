"""
Workflow DAG 图管理

【设计理念】
Workflow 管理任务之间的依赖关系，形成有向无环图（DAG）。
核心职责：
1. 添加/查询任务
2. 验证 DAG 合法性（无环检测）
3. 拓扑排序（确定执行顺序）
4. 查询可执行任务（依赖已完成的任务）

【DAG 基础知识】
- 有向无环图（DAG）：边有方向，不存在环的图
- 拓扑排序：将 DAG 的节点排成线性序列，使得每条边的起点在终点前面
- 环检测：如果图中有环，拓扑排序无法完成

【拓扑排序算法】
使用 Kahn 算法（BFS）：
1. 计算每个节点的入度（有多少个依赖指向它）
2. 将入度为 0 的节点加入队列
3. 取出队列中的一个节点，加入结果列表
4. 将该节点的所有后继节点的入度减 1
5. 如果后继节点的入度变为 0，加入队列
6. 重复 3-5，直到队列为空
7. 如果结果列表长度 != 节点数，说明有环
"""
from __future__ import annotations

from collections import deque

from iwan_claude.core.workflow.task import Task


class Workflow:
    """
    DAG 工作流图

    【职责】
    - 管理任务集合
    - 维护依赖关系
    - 拓扑排序
    - 环检测
    - 查询可执行任务

    【使用方式】
    ```python
    wf = Workflow()
    wf.add_task(Task(name="a", description="Task A"))
    wf.add_task(Task(name="b", description="Task B", depends_on=["a"]))
    wf.add_task(Task(name="c", description="Task C", depends_on=["a"]))

    assert wf.validate()  # 验证无环

    # 获取可执行任务（a 没有依赖，可以立即执行）
    ready = wf.get_ready_tasks(completed=set())  # ["a"]

    # a 完成后，b 和 c 都可以执行
    ready = wf.get_ready_tasks(completed={"a"})  # ["b", "c"]
    ```
    """

    def __init__(self) -> None:
        """初始化空的工作流图"""
        # 任务字典：name -> Task
        self._tasks: dict[str, Task] = {}
        # 反向依赖图：name -> 依赖它的任务名称列表
        # 用于快速查找"哪些任务依赖我"
        self._dependents: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # 任务管理
    # ------------------------------------------------------------------

    def add_task(self, task: Task) -> None:
        """
        添加任务到工作流

        【参数说明】
        - task: Task - 要添加的任务

        【异常】
        - ValueError: 任务名称已存在
        """
        if task.name in self._tasks:
            raise ValueError(f"Task '{task.name}' already exists")
        self._tasks[task.name] = task
        # 初始化反向依赖
        if task.name not in self._dependents:
            self._dependents[task.name] = []

    def get_task(self, name: str) -> Task | None:
        """按名称获取任务"""
        return self._tasks.get(name)

    def list_tasks(self) -> list[Task]:
        """列出所有任务"""
        return list(self._tasks.values())

    def count(self) -> int:
        """返回任务总数"""
        return len(self._tasks)

    # ------------------------------------------------------------------
    # 依赖查询
    # ------------------------------------------------------------------

    def get_dependencies(self, name: str) -> list[str]:
        """
        获取任务依赖的前置任务列表

        【参数说明】
        - name: str - 任务名称

        【返回值】
        - list[str]: 前置任务名称列表，任务不存在时返回空列表
        """
        task = self._tasks.get(name)
        if task is None:
            return []
        return list(task.depends_on)

    def get_dependents(self, name: str) -> list[str]:
        """
        获取依赖该任务的后继任务列表

        【参数说明】
        - name: str - 任务名称

        【返回值】
        - list[str]: 后继任务名称列表
        """
        return list(self._dependents.get(name, []))

    def get_ready_tasks(self, completed: set[str]) -> list[str]:
        """
        获取所有可以立即执行的任务（依赖已全部完成）

        【参数说明】
        - completed: set[str] - 已完成的任务名称集合

        【返回值】
        - list[str]: 可执行的任务名称列表

        【判断逻辑】
        任务 T 可执行的条件：
        1. T 未完成（T 不在 completed 中）
        2. T 的所有依赖都在 completed 中
        """
        ready: list[str] = []
        for name, task in self._tasks.items():
            if name in completed:
                continue
            # 检查所有依赖是否已完成
            if all(dep in completed for dep in task.depends_on):
                ready.append(name)
        return ready

    # ------------------------------------------------------------------
    # DAG 验证
    # ------------------------------------------------------------------

    def validate(self) -> bool:
        """
        验证 DAG 合法性

        【检查项】
        1. 所有依赖的任务都存在
        2. 无环（不是循环依赖）

        【返回值】
        - bool: True=合法 DAG，False=非法

        【环检测】
        通过拓扑排序检测：如果排序结果长度 < 任务数，说明有环
        """
        # 1. 检查所有依赖的任务是否存在
        for task in self._tasks.values():
            for dep in task.depends_on:
                if dep not in self._tasks:
                    return False  # 依赖的任务不存在

        # 2. 检查是否有环（通过拓扑排序）
        sorted_names = self.topological_sort()
        return len(sorted_names) == len(self._tasks)

    # ------------------------------------------------------------------
    # 拓扑排序
    # ------------------------------------------------------------------

    def topological_sort(self) -> list[str]:
        """
        拓扑排序（Kahn 算法 / BFS）

        【算法步骤】
        1. 计算每个任务的入度（有多少个前置依赖）
        2. 将入度为 0 的任务加入队列
        3. 取出队列中的任务，加入结果
        4. 将该任务的所有后继的入度减 1
        5. 如果后继的入度变为 0，加入队列
        6. 重复 3-5

        【返回值】
        - list[str]: 拓扑排序后的任务名称列表
            如果有环，返回的列表长度 < 任务数

        【时间复杂度】
        O(V + E)，V=任务数，E=依赖边数
        """
        # 计算入度
        in_degree: dict[str, int] = {name: 0 for name in self._tasks}
        for task in self._tasks.values():
            for dep in task.depends_on:
                if dep in in_degree:
                    # dep 被 task 依赖，task 的入度 +1
                    in_degree[task.name] = in_degree.get(task.name, 0) + 1

        # 构建后继列表（反向依赖）
        successors: dict[str, list[str]] = {name: [] for name in self._tasks}
        for task in self._tasks.values():
            for dep in task.depends_on:
                if dep in successors:
                    successors[dep].append(task.name)

        # Kahn 算法
        queue = deque([name for name, deg in in_degree.items() if deg == 0])
        result: list[str] = []

        while queue:
            name = queue.popleft()
            result.append(name)
            for succ in successors.get(name, []):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        return result

    # ------------------------------------------------------------------
    # 执行层级
    # ------------------------------------------------------------------

    def get_execution_layers(self) -> list[list[str]]:
        """
        获取分层执行计划

        【设计理念】
        将任务按依赖关系分层：
        - 第 0 层：无依赖的任务（可立即执行）
        - 第 1 层：依赖第 0 层的任务
        - 第 2 层：依赖第 1 层的任务
        - ...

        同一层的任务可以并行执行，不同层必须串行。

        【返回值】
        - list[list[str]]: 分层任务列表
            [["a"], ["b", "c"], ["d"]] 表示：
            先执行 a，然后并行执行 b 和 c，最后执行 d

        【应用场景】
        可视化工作流结构、调试依赖关系
        """
        completed: set[str] = set()
        layers: list[list[str]] = []

        while len(completed) < len(self._tasks):
            ready = self.get_ready_tasks(completed)
            if not ready:
                break  # 无可执行任务（可能有环）
            layers.append(ready)
            completed.update(ready)

        return layers
