# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 SessionStore：会话存储类（用于读写 notes.md）
from kama_claude.core.session.store import SessionStore
# 导入 BaseTool 和 ToolResult：工具基类和结果类型
from kama_claude.core.tools.base import BaseTool, ToolResult


# NoteSaveTool：保存笔记工具
# 解决的问题：LLM 的"短期记忆"很差，每次调用时上下文有限
# 笔记系统让 LLM 可以将重要信息持久化到文件，在后续对话中引用
class NoteSaveTool(BaseTool):
    # 工具名称（LLM 调用时使用）
    name = "note_save"
    # 工具描述（告诉 LLM 这个工具的作用）
    description = (
        "Save a concise fact or decision to this session's notes. "
        "These notes are visible in future turns of the same session."
    )
    # 输入参数 schema（定义 LLM 需要传入什么参数）
    input_schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The durable fact or decision to remember.",
            },
        },
        "required": ["content"],
    }

    # 初始化：注入 SessionStore、session_id 和 run_id
    # 为什么需要这些？因为笔记是按 session 存储的，需要知道写入哪个 session
    def __init__(self, store: SessionStore, session_id: str, run_id: str) -> None:
        self._store = store      # 会话存储（负责读写文件）
        self._session_id = session_id  # 当前会话 ID
        self._run_id = run_id    # 当前 run ID（用于笔记的时间戳）

    # 执行工具：将笔记内容追加到 notes.md 文件
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 获取笔记内容，去除首尾空白
        content = str(params.get("content", "")).strip()
        # 如果内容为空，返回错误
        if not content:
            return ToolResult(
                content="empty content",
                is_error=True,
                error_type="runtime_error",
            )
        # 调用 SessionStore 将笔记追加到 notes.md
        self._store.append_note(self._session_id, content, self._run_id)
        # 返回成功结果
        return ToolResult(content="saved")
