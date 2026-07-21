"""
任务管理器

该模块实现了任务的 CRUD 操作，使用 JSON 文件存储任务数据。

核心功能：
- 创建任务（create）
- 读取任务（get）
- 更新任务（update）
- 列出所有任务（list_all）
- 格式化任务列表（format_list）
- 管理任务依赖关系

设计要点：
- 每个任务存储为独立的 JSON 文件（task_{id}.json）
- 任务 ID 自动递增
- 支持任务依赖关系（blocked_by）
- 任务完成时自动清理其他任务的依赖
- 使用 glob 模式匹配任务文件
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from iwan_claude.core.task.model import Task, TaskStatus


def _now() -> str:
    """
    获取当前 UTC 时间的 ISO 8601 格式字符串

    返回：
        str: 当前 UTC 时间的 ISO 8601 格式字符串

    使用示例：
        >>> print(_now())
        "2026-07-21T10:30:00Z"
    """
    return datetime.now(UTC).isoformat()


class TaskManager:
    """
    任务管理器

    负责任务的创建、读取、更新和删除操作，使用 JSON 文件持久化存储。

    工作原理：
    1. 初始化时扫描任务目录，确定下一个可用的任务 ID
    2. 创建任务时生成新 ID，写入 JSON 文件
    3. 更新任务时读取 JSON 文件，修改后写回
    4. 任务完成时自动清理其他任务的依赖

    文件结构：
    - tasks_dir/
      - task_1.json
      - task_2.json
      - task_3.json

    使用示例：
        >>> tm = TaskManager(Path(".tasks"))
        >>> task = tm.create("完成项目文档")
        >>> tm.update(task.id, status="in_progress")
        >>> tasks = tm.list_all()
    """

    def __init__(self, tasks_dir: Path) -> None:
        """
        初始化任务管理器

        参数：
            tasks_dir: 任务文件存储目录

        属性：
            _dir: 任务目录路径
            _next_id: 下一个可用的任务 ID

        实现步骤：
        1. 创建任务目录（如果不存在）
        2. 扫描现有任务文件，确定最大 ID
        3. 设置 _next_id 为最大 ID + 1

        使用示例：
            >>> tm = TaskManager(Path(".tasks"))
        """
        self._dir = tasks_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._next_id = self._max_id() + 1

    def _max_id(self) -> int:
        """
        获取当前最大任务 ID

        扫描任务目录中所有 task_*.json 文件，提取最大的数字 ID。

        返回：
            int: 当前最大任务 ID，如果没有任务文件则返回 0

        实现步骤：
        1. 使用 glob 模式匹配 task_*.json 文件
        2. 提取文件名中的数字部分（如 task_123.json → 123）
        3. 返回最大的数字，无文件时返回 0

        使用示例：
            >>> max_id = tm._max_id()
            >>> print(f"Next ID: {max_id + 1}")
        """
        ids = [
            int(f.stem.split("_")[1])
            for f in self._dir.glob("task_*.json")
            if f.stem.split("_")[1].isdigit()
        ]
        return max(ids) if ids else 0

    def _load(self, task_id: int) -> Task:
        """
        加载任务

        根据任务 ID 读取对应的 JSON 文件并解析为 Task 对象。

        参数：
            task_id: 任务 ID

        返回：
            Task: 解析后的任务对象

        异常：
            ValueError: 任务文件不存在时抛出

        实现步骤：
        1. 构建任务文件路径（task_{id}.json）
        2. 检查文件是否存在
        3. 读取文件内容
        4. 使用 Task.from_dict() 解析为 Task 对象

        使用示例：
            >>> task = tm._load(1)
        """
        path = self._dir / f"task_{task_id}.json"
        if not path.exists():
            raise ValueError(f"task {task_id} not found")
        return Task.from_dict(json.loads(path.read_text()))

    def _save(self, task: Task) -> None:
        """
        保存任务

        将任务对象序列化为 JSON 并写入文件。

        参数：
            task: 要保存的任务对象

        实现步骤：
        1. 构建任务文件路径（task_{id}.json）
        2. 将任务转换为字典
        3. 写入 JSON 文件，使用缩进和 UTF-8 编码

        使用示例：
            >>> tm._save(task)
        """
        path = self._dir / f"task_{task.id}.json"
        path.write_text(json.dumps(task.to_dict(), indent=2, ensure_ascii=False))

    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
    ) -> Task:
        """
        创建新任务

        创建一个新任务并写入 JSON 文件。

        参数：
            subject: 任务主题，简短描述
            description: 任务详细描述，默认为空字符串
            blocked_by: 依赖的任务 ID 列表，这些任务完成后才能执行当前任务

        返回：
            Task: 创建的任务对象

        异常：
            ValueError: 依赖的任务不存在时抛出

        实现步骤：
        1. 验证所有依赖任务是否存在
        2. 获取当前时间戳
        3. 创建 Task 对象，分配新 ID
        4. 保存任务到 JSON 文件
        5. 递增 _next_id

        使用示例：
            >>> task = tm.create("完成项目文档", "编写 README 和 API 文档")
            >>> task2 = tm.create("部署项目", blocked_by=[task.id])
        """
        for dep_id in (blocked_by or []):
            if not (self._dir / f"task_{dep_id}.json").exists():
                raise ValueError(f"blocked_by task {dep_id} not found")
        now = _now()
        task = Task(
            id=self._next_id,
            subject=subject,
            description=description,
            status="pending",
            blocked_by=list(blocked_by or []),
            created_at=now,
            updated_at=now,
        )
        self._save(task)
        self._next_id += 1
        return task

    def get(self, task_id: int) -> Task:
        """
        获取任务

        根据任务 ID 读取任务信息。

        参数：
            task_id: 任务 ID

        返回：
            Task: 任务对象

        异常：
            ValueError: 任务不存在时抛出

        使用示例：
            >>> task = tm.get(1)
            >>> print(task.subject)
        """
        return self._load(task_id)

    def update(
        self,
        task_id: int,
        *,
        status: TaskStatus | None = None,
        add_blocked_by: list[int] | None = None,
        remove_blocked_by: list[int] | None = None,
    ) -> Task:
        """
        更新任务

        更新任务的状态或依赖关系。

        参数：
            task_id: 任务 ID
            status: 新的任务状态，可选
            add_blocked_by: 要添加的依赖任务 ID 列表，可选
            remove_blocked_by: 要移除的依赖任务 ID 列表，可选

        返回：
            Task: 更新后的任务对象

        异常：
            ValueError: 任务不存在或状态无效时抛出

        特殊处理：
        - 当 status 设置为 "completed" 时，自动调用 _clear_dependency()
          将该任务从其他任务的 blocked_by 列表中移除

        实现步骤：
        1. 加载任务
        2. 更新状态（如果提供）
        3. 添加依赖（如果提供）
        4. 移除依赖（如果提供）
        5. 更新时间戳
        6. 保存任务

        使用示例：
            >>> task = tm.update(1, status="in_progress")
            >>> task = tm.update(1, add_blocked_by=[2, 3])
            >>> task = tm.update(1, status="completed")
        """
        task = self._load(task_id)
        if status is not None:
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"invalid status: {status!r}")
            task.status = status
            if status == "completed":
                self._clear_dependency(task_id)
        if add_blocked_by:
            task.blocked_by = list(set(task.blocked_by + add_blocked_by))
        if remove_blocked_by:
            task.blocked_by = [x for x in task.blocked_by if x not in remove_blocked_by]
        task.updated_at = _now()
        self._save(task)
        return task

    def list_all(self) -> list[Task]:
        """
        获取所有任务

        返回任务目录中的所有任务，按 ID 升序排列。

        返回：
            list[Task]: 任务列表，按 ID 升序排列

        容错设计：
        - 跳过格式错误的任务文件
        - 跳过无法解析的任务文件

        使用示例：
            >>> tasks = tm.list_all()
            >>> for task in tasks:
            ...     print(task.id, task.subject)
        """
        tasks = []
        for f in sorted(self._dir.glob("task_*.json"), key=lambda p: int(p.stem.split("_")[1])):
            try:
                tasks.append(Task.from_dict(json.loads(f.read_text())))
            except (ValueError, KeyError):
                pass
        return tasks

    def _clear_dependency(self, completed_id: int) -> None:
        """
        清理依赖

        将已完成的任务从其他任务的 blocked_by 列表中移除。

        参数：
            completed_id: 已完成任务的 ID

        实现步骤：
        1. 遍历所有任务文件
        2. 读取任务数据
        3. 检查 blocked_by 列表是否包含 completed_id
        4. 如果包含，移除该 ID
        5. 更新 updated_at 时间戳
        6. 写回文件

        使用场景：
        - 当某个任务完成时自动调用
        - 确保依赖该任务的其他任务可以继续执行

        使用示例：
            >>> tm._clear_dependency(1)
        """
        for f in self._dir.glob("task_*.json"):
            try:
                data = json.loads(f.read_text())
            except (ValueError, json.JSONDecodeError):
                continue
            blocked = [int(x) for x in data.get("blocked_by", [])]
            if completed_id in blocked:
                data["blocked_by"] = [x for x in blocked if x != completed_id]
                data["updated_at"] = _now()
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def format_list(self) -> str:
        """
        格式化任务列表

        将任务列表格式化为可读的文本，供 task_list 工具返回给 Agent。

        返回：
            str: 格式化的任务列表文本

        格式说明：
        - 待处理任务：[ ] #id: subject
        - 进行中任务：[>] #id: subject
        - 已完成任务：[x] #id: subject
        - 有依赖的任务：显示 "(blocked by: [dep1, dep2])"

        使用示例：
            >>> print(tm.format_list())
            [>] #1: 完成项目文档
            [ ] #2: 部署项目 (blocked by: [1])
            [x] #3: 编写测试
        """
        tasks = self.list_all()
        if not tasks:
            return "No tasks."
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        lines = []
        for t in tasks:
            blocked = f" (blocked by: {t.blocked_by})" if t.blocked_by else ""
            lines.append(f"{marker.get(t.status, '[?]')} #{t.id}: {t.subject}{blocked}")
        return "\n".join(lines)
