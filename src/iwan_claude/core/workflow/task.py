"""
Task 数据类 - DAG 工作流中的单个任务

【设计理念】
Task 是工作流中最小的执行单元。每个任务有：
- 唯一名称（用于依赖引用）
- 描述（告诉执行器要做什么）
- 依赖列表（depends_on：必须完成后才能执行本任务）
- 输入参数（额外的静态参数）

【数据流】
任务的输出会自动传递给依赖它的任务：
  Task A → output → Task B（B 的 inputs 中会收到 A 的 output）
  Task A → output → Task C（C 也会收到 A 的 output）

如果多个任务都依赖 A，A 的 output 会传递给所有依赖它的任务。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    """
    工作流任务定义

    【字段说明】
    - name: str - 任务名称（唯一标识，用于依赖引用）
    - description: str - 任务描述（告诉执行器要做什么）
    - depends_on: list[str] - 依赖的任务名称列表
        这些任务必须完成后，本任务才能执行。
        空列表表示无依赖（可以立即执行）。
    - inputs: dict[str, Any] - 静态输入参数
        这些参数在定义时设置，不会从前置任务获取。
        前置任务的输出会通过 executor 动态注入。

    【示例】
    ```python
    Task(
        name="analyze",
        description="分析代码质量",
        depends_on=["read_code", "read_tests"],
        inputs={"language": "python", "strict": True},
    )
    ```
    在这个例子中，"analyze" 任务会在 "read_code" 和 "read_tests" 都完成后执行。
    executor 会把 "read_code" 和 "read_tests" 的输出传递给 "analyze" 的 handler。
    """
    name: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
