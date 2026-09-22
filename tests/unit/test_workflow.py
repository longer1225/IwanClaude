"""
DAG 工作流测试模块

测试内容：
1. Task 数据类
2. Workflow DAG 图管理（添加/验证/拓扑排序/分层）
3. WorkflowExecutor 执行器（并行执行/结果传递/错误处理）
"""

from __future__ import annotations

import asyncio

import pytest

from iwan_claude.core.workflow import Task, Workflow, WorkflowExecutor


# ======================================================================
# Task 测试
# ======================================================================


class TestTask:
    """测试 Task 数据类"""

    def test_default_values(self) -> None:
        """测试默认值"""
        task = Task(name="test")
        assert task.name == "test"
        assert task.description == ""
        assert task.depends_on == []
        assert task.inputs == {}

    def test_with_dependencies(self) -> None:
        """测试带依赖的任务"""
        task = Task(
            name="analyze",
            description="分析代码",
            depends_on=["read", "parse"],
            inputs={"language": "python"},
        )
        assert task.name == "analyze"
        assert task.depends_on == ["read", "parse"]
        assert task.inputs["language"] == "python"


# ======================================================================
# Workflow 测试
# ======================================================================


class TestWorkflow:
    """测试 DAG 工作流图管理"""

    def test_add_and_get(self) -> None:
        """测试添加和获取任务"""
        wf = Workflow()
        task = Task(name="a", description="Task A")
        wf.add_task(task)

        assert wf.count() == 1
        assert wf.get_task("a") is task
        assert wf.get_task("nonexistent") is None

    def test_duplicate_task_raises(self) -> None:
        """测试重复添加任务报错"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        with pytest.raises(ValueError, match="already exists"):
            wf.add_task(Task(name="a"))

    def test_validate_simple_dag(self) -> None:
        """测试简单 DAG 验证"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        wf.add_task(Task(name="b", depends_on=["a"]))
        wf.add_task(Task(name="c", depends_on=["a"]))
        wf.add_task(Task(name="d", depends_on=["b", "c"]))

        assert wf.validate() is True

    def test_validate_cycle_detected(self) -> None:
        """测试环检测"""
        wf = Workflow()
        wf.add_task(Task(name="a", depends_on=["c"]))
        wf.add_task(Task(name="b", depends_on=["a"]))
        wf.add_task(Task(name="c", depends_on=["b"]))

        assert wf.validate() is False

    def test_validate_missing_dependency(self) -> None:
        """测试依赖缺失"""
        wf = Workflow()
        wf.add_task(Task(name="a", depends_on=["nonexistent"]))
        assert wf.validate() is False

    def test_topological_sort(self) -> None:
        """测试拓扑排序"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        wf.add_task(Task(name="b", depends_on=["a"]))
        wf.add_task(Task(name="c", depends_on=["a"]))
        wf.add_task(Task(name="d", depends_on=["b", "c"]))

        result = wf.topological_sort()
        assert len(result) == 4
        # a 必须在 b 和 c 之前
        assert result.index("a") < result.index("b")
        assert result.index("a") < result.index("c")
        # b 和 c 必须在 d 之前
        assert result.index("b") < result.index("d")
        assert result.index("c") < result.index("d")

    def test_topological_sort_with_cycle(self) -> None:
        """测试有环时拓扑排序结果不完整"""
        wf = Workflow()
        wf.add_task(Task(name="a", depends_on=["b"]))
        wf.add_task(Task(name="b", depends_on=["a"]))

        result = wf.topological_sort()
        # 有环时结果不完整
        assert len(result) < 2

    def test_get_ready_tasks(self) -> None:
        """测试获取可执行任务"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        wf.add_task(Task(name="b", depends_on=["a"]))
        wf.add_task(Task(name="c", depends_on=["a"]))
        wf.add_task(Task(name="d", depends_on=["b", "c"]))

        # 初始：只有 a 可执行
        ready = wf.get_ready_tasks(set())
        assert ready == ["a"]

        # a 完成后：b 和 c 可执行
        ready = wf.get_ready_tasks({"a"})
        assert set(ready) == {"b", "c"}

        # a, b, c 完成后：d 可执行
        ready = wf.get_ready_tasks({"a", "b", "c"})
        assert ready == ["d"]

        # 全部完成后：无可执行
        ready = wf.get_ready_tasks({"a", "b", "c", "d"})
        assert ready == []

    def test_get_execution_layers(self) -> None:
        """测试分层执行计划"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        wf.add_task(Task(name="b", depends_on=["a"]))
        wf.add_task(Task(name="c", depends_on=["a"]))
        wf.add_task(Task(name="d", depends_on=["b", "c"]))

        layers = wf.get_execution_layers()
        assert len(layers) == 3
        assert layers[0] == ["a"]
        assert set(layers[1]) == {"b", "c"}
        assert layers[2] == ["d"]

    def test_get_dependencies(self) -> None:
        """测试获取依赖"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        wf.add_task(Task(name="b", depends_on=["a"]))

        assert wf.get_dependencies("b") == ["a"]
        assert wf.get_dependencies("a") == []
        assert wf.get_dependencies("nonexistent") == []

    def test_independent_tasks(self) -> None:
        """测试独立任务（无依赖）"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        wf.add_task(Task(name="b"))
        wf.add_task(Task(name="c"))

        # 所有任务都可立即执行
        ready = wf.get_ready_tasks(set())
        assert set(ready) == {"a", "b", "c"}

        layers = wf.get_execution_layers()
        assert len(layers) == 1  # 只有一层


# ======================================================================
# WorkflowExecutor 测试
# ======================================================================


class TestWorkflowExecutor:
    """测试 DAG 工作流执行器"""

    def test_sequential_execution(self) -> None:
        """测试串行执行（A → B → C）"""
        wf = Workflow()
        wf.add_task(Task(name="a", description="Task A"))
        wf.add_task(Task(name="b", description="Task B", depends_on=["a"]))
        wf.add_task(Task(name="c", description="Task C", depends_on=["b"]))

        execution_order: list[str] = []

        async def handler(task: Task, inputs: dict[str, str]) -> str:
            execution_order.append(task.name)
            return f"{task.name}_result"

        executor = WorkflowExecutor(max_concurrency=3)
        results = asyncio.run(executor.execute(wf, handler))

        # 验证执行顺序
        assert execution_order == ["a", "b", "c"]
        # 验证结果
        assert results["a"] == "a_result"
        assert results["b"] == "b_result"
        assert results["c"] == "c_result"

    def test_parallel_execution(self) -> None:
        """测试并行执行（A → [B, C] → D）"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        wf.add_task(Task(name="b", depends_on=["a"]))
        wf.add_task(Task(name="c", depends_on=["a"]))
        wf.add_task(Task(name="d", depends_on=["b", "c"]))

        async def handler(task: Task, inputs: dict[str, str]) -> str:
            await asyncio.sleep(0.01)  # 模拟耗时
            return f"{task.name}_done"

        executor = WorkflowExecutor(max_concurrency=3)
        results = asyncio.run(executor.execute(wf, handler))

        assert len(results) == 4
        assert all(v.endswith("_done") for v in results.values())

    def test_result_passing(self) -> None:
        """测试结果传递"""
        wf = Workflow()
        wf.add_task(Task(name="research", description="调研"))
        wf.add_task(Task(name="design", description="设计", depends_on=["research"]))
        wf.add_task(Task(name="implement", description="实现", depends_on=["design"]))

        async def handler(task: Task, inputs: dict[str, str]) -> str:
            if not inputs:
                return f"output_of_{task.name}"
            # 验证收到了前置任务的输出
            input_str = "|".join(f"{k}={v}" for k, v in inputs.items())
            return f"{task.name}({input_str})"

        executor = WorkflowExecutor()
        results = asyncio.run(executor.execute(wf, handler))

        # research 无输入
        assert results["research"] == "output_of_research"
        # design 收到 research 的输出
        assert "research=output_of_research" in results["design"]
        # implement 收到 design 的输出
        assert "design=" in results["implement"]

    def test_invalid_dag_raises(self) -> None:
        """测试非法 DAG 报错"""
        wf = Workflow()
        wf.add_task(Task(name="a", depends_on=["b"]))
        wf.add_task(Task(name="b", depends_on=["a"]))

        async def handler(task: Task, inputs: dict[str, str]) -> str:
            return "done"

        executor = WorkflowExecutor()
        with pytest.raises(ValueError, match="Invalid DAG"):
            asyncio.run(executor.execute(wf, handler))

    def test_task_failure_raises(self) -> None:
        """测试任务失败报错"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        wf.add_task(Task(name="b", depends_on=["a"]))

        async def handler(task: Task, inputs: dict[str, str]) -> str:
            if task.name == "a":
                raise RuntimeError("Task A failed!")
            return "done"

        executor = WorkflowExecutor()
        with pytest.raises(RuntimeError, match="Task 'a' failed"):
            asyncio.run(executor.execute(wf, handler))

    def test_concurrency_control(self) -> None:
        """测试并发控制"""
        wf = Workflow()
        # 添加 5 个独立任务
        for i in range(5):
            wf.add_task(Task(name=f"task_{i}"))

        concurrent_count = 0
        max_concurrent = 0

        async def handler(task: Task, inputs: dict[str, str]) -> str:
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.05)  # 模拟耗时
            concurrent_count -= 1
            return "done"

        # 限制并发为 2
        executor = WorkflowExecutor(max_concurrency=2)
        asyncio.run(executor.execute(wf, handler))

        # 最大并发不应超过 2
        assert max_concurrent <= 2

    def test_get_execution_plan(self) -> None:
        """测试获取执行计划"""
        wf = Workflow()
        wf.add_task(Task(name="a"))
        wf.add_task(Task(name="b", depends_on=["a"]))
        wf.add_task(Task(name="c", depends_on=["a"]))
        wf.add_task(Task(name="d", depends_on=["b", "c"]))

        executor = WorkflowExecutor()
        plan = executor.get_execution_plan(wf)

        assert len(plan) == 3
        assert plan[0] == ["a"]
        assert set(plan[1]) == {"b", "c"}
        assert plan[2] == ["d"]

    def test_diamond_dependency(self) -> None:
        """测试菱形依赖（经典 DAG 结构）"""
        wf = Workflow()
        wf.add_task(Task(name="start"))
        wf.add_task(Task(name="left", depends_on=["start"]))
        wf.add_task(Task(name="right", depends_on=["start"]))
        wf.add_task(Task(name="merge", depends_on=["left", "right"]))

        async def handler(task: Task, inputs: dict[str, str]) -> str:
            return f"{task.name}_done"

        executor = WorkflowExecutor()
        results = asyncio.run(executor.execute(wf, handler))

        assert len(results) == 4
        # merge 应该收到 left 和 right 的输出
        # （虽然 handler 不检查 inputs，但执行顺序正确）

    def test_empty_workflow(self) -> None:
        """测试空工作流"""
        wf = Workflow()

        async def handler(task: Task, inputs: dict[str, str]) -> str:
            return "done"

        executor = WorkflowExecutor()
        results = asyncio.run(executor.execute(wf, handler))
        assert results == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
