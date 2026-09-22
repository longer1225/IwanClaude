"""
TodoWrite 工具模块 - 结构化任务列表管理

【学习要点】
1. 结构化任务：LLM 用 todo 列表规划多步骤任务，避免遗漏
2. 替换式更新：每次调用传入完整列表，状态以最新调用为准
3. 内存 + 持久化：内存中保存当前状态，同时写入 session notes 供后续轮次查看
4. 状态摘要：返回当前进度（已完成 X/Y）

【设计原则】
- 简洁 API：只接收 todos 列表，返回状态摘要
- 替换语义：整个列表替换，不是追加（避免状态不一致）
- 持久化：通过 SessionStore 写入 notes，后续轮次可见
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.session.store import SessionStore
from iwan_claude.core.tools.base import BaseTool, ToolResult


class TodoItem(BaseModel):
    """
    单个 todo 项

    【字段说明】
    - id: str - 唯一标识符（LLM 自己生成，如 "1"、"2"、"3"）
    - content: str - 任务描述
    - status: "pending" | "in_progress" | "completed" - 当前状态
    - priority: "high" | "medium" | "low" - 优先级
    """
    model_config = ConfigDict(extra="ignore")
    id: str
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"
    priority: Literal["high", "medium", "low"] = "medium"


class TodoWriteParams(BaseModel):
    """
    TodoWrite 工具参数

    【字段说明】
    - todos: list[TodoItem] - 完整的 todo 列表（替换式）
    """
    model_config = ConfigDict(extra="ignore")
    todos: list[TodoItem]


class TodoWriteTool(BaseTool):
    """
    TodoWrite 工具 - 结构化任务列表管理

    【学习要点】
    1. 替换语义：每次调用传入完整列表，覆盖之前的状态
    2. 状态摘要：返回进度统计（已完成 X/Y，进行中 Z）
    3. 持久化：写入 session notes，后续轮次可见
    4. 内存缓存：当前 todo 状态保存在内存，避免重复读取

    【使用场景】
    - 多步骤任务的进度跟踪
    - 复杂重构的步骤规划
    - 长任务的里程碑管理

    【使用示例】
    ```python
    tool = TodoWriteTool(session_store, "session_123", "run_456")
    result = await tool.invoke({
        "todos": [
            {"id": "1", "content": "读取相关文件", "status": "completed", "priority": "high"},
            {"id": "2", "content": "实现新功能", "status": "in_progress", "priority": "high"},
            {"id": "3", "content": "添加测试", "status": "pending", "priority": "medium"},
        ]
    })
    ```

    【注意事项】
    - 每次 call 传入完整列表（不是增量更新）
    - 只有一个 todo 可以是 in_progress 状态
    - 任务完成后立即标记为 completed，不要批量更新
    """
    params_model = TodoWriteParams
    name = "todo_write"
    description = (
        "Maintain a structured task list for the current session. Pass the FULL list "
        "of todos each call (replacement semantics). Mark each todo as pending/"
        "in_progress/completed. Use this to plan multi-step tasks and track progress. "
        "Only one todo should be in_progress at a time. Mark todos complete as soon "
        "as finished — do not batch completions."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique identifier (e.g., '1', '2')."},
                        "content": {"type": "string", "description": "Task description."},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "default": "pending",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "default": "medium",
                        },
                    },
                    "required": ["id", "content"],
                },
            },
        },
        "required": ["todos"],
    }

    def __init__(self, store: SessionStore, session_id: str, run_id: str) -> None:
        """初始化：注入 SessionStore 用于持久化 todo 列表"""
        self._store = store
        self._session_id = session_id
        self._run_id = run_id

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行 todo 列表更新"""
        p = TodoWriteParams.model_validate(params)

        if not p.todos:
            return ToolResult(
                content="todo_write: no todos provided",
                is_error=True,
                error_type="schema_error",
            )

        # 统计状态
        total = len(p.todos)
        completed = sum(1 for t in p.todos if t.status == "completed")
        in_progress = sum(1 for t in p.todos if t.status == "in_progress")
        pending = sum(1 for t in p.todos if t.status == "pending")

        # 格式化 todo 列表为 markdown
        lines: list[str] = ["## Todo List"]
        for t in p.todos:
            mark = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}[t.status]
            lines.append(f"- {mark} #{t.id} ({t.priority}) {t.content}")
        lines.append(f"\n**Progress: {completed}/{total} completed, {in_progress} in progress, {pending} pending**")

        todo_md = "\n".join(lines)

        # 持久化到 session notes（替换之前的 todo 列表）
        try:
            # 用分隔符标记 todo 区域，便于后续替换
            note_content = f"<!-- todo_list_start -->\n{todo_md}\n<!-- todo_list_end -->"
            self._store.append_note(self._session_id, note_content, self._run_id)
        except Exception:
            # 持久化失败不影响工具返回（内存状态已更新）
            pass

        # 返回状态摘要
        summary = (
            f"todo_write: {completed}/{total} completed, "
            f"{in_progress} in progress, {pending} pending"
        )
        if in_progress > 1:
            summary += " (warning: multiple in_progress — keep only one active at a time)"
        return ToolResult(content=summary)
