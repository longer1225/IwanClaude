# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 json：用于序列化/反序列化 JSON 文件
import json
# 导入 datetime：用于生成时间戳
from datetime import UTC, datetime
# 导入 Path：用于文件路径操作
from pathlib import Path

# 导入 Task 和 TaskStatus：任务模型
from kama_claude.core.task.model import Task, TaskStatus


# 返回当前时间的 ISO 格式字符串（UTC 时区）
def _now() -> str:
    return datetime.now(UTC).isoformat()


# TaskManager 类：管理任务的 CRUD（创建、读取、更新、删除）操作
# 什么是 CRUD？就是数据的四种基本操作：Create（创建）、Read（读取）、Update（更新）、Delete（删除）
class TaskManager:
    # 初始化：确保目录存在，扫描现有文件确定下一个 ID
    def __init__(self, tasks_dir: Path) -> None:
        # 任务文件存储目录
        self._dir = tasks_dir
        # 创建目录（如果不存在），parents=True 表示创建所有父目录
        self._dir.mkdir(parents=True, exist_ok=True)
        # 计算下一个可用的任务 ID（最大现有 ID + 1）
        self._next_id = self._max_id() + 1

    # 扫描目录中 task_*.json 文件，返回最大 ID（无文件则返回 0）
    # 什么是 glob？就是文件通配符匹配，task_*.json 匹配所有以 task_ 开头的 JSON 文件
    def _max_id(self) -> int:
        ids = [
            # 从文件名中提取 ID：task_1.json → 1
            int(f.stem.split("_")[1])
            # 遍历目录中所有 task_*.json 文件
            for f in self._dir.glob("task_*.json")
            # 确保提取的 ID 是数字
            if f.stem.split("_")[1].isdigit()
        ]
        # 返回最大 ID，如果没有文件则返回 0
        return max(ids) if ids else 0

    # 读取指定 ID 的任务文件（内部方法）
    def _load(self, task_id: int) -> Task:
        # 构建文件路径：tasks_dir/task_{id}.json
        path = self._dir / f"task_{task_id}.json"
        # 如果文件不存在，抛出异常
        if not path.exists():
            raise ValueError(f"task {task_id} not found")
        # 读取文件内容，解析为字典，然后转换为 Task 对象
        return Task.from_dict(json.loads(path.read_text()))

    # 将任务写入对应 JSON 文件（内部方法）
    def _save(self, task: Task) -> None:
        # 构建文件路径：tasks_dir/task_{id}.json
        path = self._dir / f"task_{task.id}.json"
        # 将任务转换为字典，然后写入文件
        # indent=2：格式化输出，便于阅读
        # ensure_ascii=False：保留中文等非 ASCII 字符
        path.write_text(json.dumps(task.to_dict(), indent=2, ensure_ascii=False))

    # 创建新任务，写入 JSON 文件，返回 Task
    # subject：任务主题（必填）
    # description：任务描述（可选）
    # blocked_by：依赖的任务 ID 列表（可选）
    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
    ) -> Task:
        # 验证依赖的任务是否存在
        for dep_id in (blocked_by or []):
            # 检查依赖的任务文件是否存在
            if not (self._dir / f"task_{dep_id}.json").exists():
                raise ValueError(f"blocked_by task {dep_id} not found")
        
        # 获取当前时间
        now = _now()
        
        # 创建 Task 对象
        task = Task(
            id=self._next_id,              # 使用下一个可用 ID
            subject=subject,               # 任务主题
            description=description,       # 任务描述
            status="pending",              # 默认状态为 pending
            blocked_by=list(blocked_by or []),  # 依赖列表（转换为列表，避免 None）
            created_at=now,                # 创建时间
            updated_at=now,                # 更新时间（和创建时间相同）
        )
        
        # 保存到文件
        self._save(task)
        
        # 下一个 ID 加 1（保证 ID 唯一）
        self._next_id += 1
        
        # 返回创建的任务
        return task

    # 读取指定 ID 的任务（公开方法）
    def get(self, task_id: int) -> Task:
        return self._load(task_id)

    # 更新任务状态或依赖列表；status="completed" 时自动清理其他任务的 blocked_by
    # status：新状态（可选）
    # add_blocked_by：要添加的依赖（可选）
    # remove_blocked_by：要移除的依赖（可选）
    def update(
        self,
        task_id: int,
        *,
        status: TaskStatus | None = None,
        add_blocked_by: list[int] | None = None,
        remove_blocked_by: list[int] | None = None,
    ) -> Task:
        # 先加载现有任务
        task = self._load(task_id)
        
        # 如果指定了新状态
        if status is not None:
            # 验证状态是否合法
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"invalid status: {status!r}")
            # 更新状态
            task.status = status
            # 如果状态变为 completed，清理其他任务的依赖
            if status == "completed":
                self._clear_dependency(task_id)
        
        # 如果指定了要添加的依赖
        if add_blocked_by:
            # 使用集合去重，然后转换为列表
            task.blocked_by = list(set(task.blocked_by + add_blocked_by))
        
        # 如果指定了要移除的依赖
        if remove_blocked_by:
            # 过滤掉要移除的依赖
            task.blocked_by = [x for x in task.blocked_by if x not in remove_blocked_by]
        
        # 更新更新时间
        task.updated_at = _now()
        
        # 保存到文件
        self._save(task)
        
        # 返回更新后的任务
        return task

    # 返回所有任务，按 ID 升序排列
    def list_all(self) -> list[Task]:
        tasks = []
        # 遍历目录中所有 task_*.json 文件，按 ID 排序
        for f in sorted(self._dir.glob("task_*.json"), key=lambda p: int(p.stem.split("_")[1])):
            try:
                # 读取文件内容，解析为 Task 对象
                tasks.append(Task.from_dict(json.loads(f.read_text())))
            except (ValueError, KeyError):
                # 如果文件格式有问题，跳过
                pass
        return tasks

    # 将 completed_id 从所有其他任务的 blocked_by 列表中移除
    # 什么是依赖清理？当一个任务完成后，其他依赖它的任务应该解除依赖
    def _clear_dependency(self, completed_id: int) -> None:
        # 遍历所有任务文件
        for f in self._dir.glob("task_*.json"):
            try:
                # 读取文件内容
                data = json.loads(f.read_text())
            except (ValueError, json.JSONDecodeError):
                # 如果文件格式有问题，跳过
                continue
            
            # 获取 blocked_by 列表（转换为整数）
            blocked = [int(x) for x in data.get("blocked_by", [])]
            
            # 如果 completed_id 在依赖列表中
            if completed_id in blocked:
                # 移除 completed_id
                data["blocked_by"] = [x for x in blocked if x != completed_id]
                # 更新更新时间
                data["updated_at"] = _now()
                # 写回文件
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # 格式化任务列表摘要，供 task_list 工具返回给 Agent
    # 为什么要格式化？因为 LLM 需要简洁、易读的格式来理解任务状态
    def format_list(self) -> str:
        # 获取所有任务
        tasks = self.list_all()
        
        # 如果没有任务，返回提示
        if not tasks:
            return "No tasks."
        
        # 状态标记映射：方便 LLM 快速识别
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        
        lines = []
        for t in tasks:
            # 如果有依赖，添加依赖信息
            blocked = f" (blocked by: {t.blocked_by})" if t.blocked_by else ""
            # 格式化每一行：[状态] #ID: 主题 (依赖)
            lines.append(f"{marker.get(t.status, '[?]')} #{t.id}: {t.subject}{blocked}")
        
        # 用换行符连接所有行
        return "\n".join(lines)
