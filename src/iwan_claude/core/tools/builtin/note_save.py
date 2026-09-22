"""
笔记保存工具模块 - 将内容保存到会话笔记

【学习要点】
1. 会话笔记：通过 SessionStore 保存会话级别的笔记
2. 参数验证：使用 Pydantic 模型验证输入参数
3. 内容检查：确保内容不为空
4. 持久化存储：将笔记追加到 session notes.md 文件

【参数说明】
- content: str - 要保存的笔记内容（必填）

【设计特点】
- 构造函数注入：通过 __init__ 注入 SessionStore、session_id 和 run_id
- Pydantic 参数验证：使用 NoteSaveParams 模型验证参数
- 内容校验：确保内容不为空
- 持久化：将笔记追加到会话的 notes.md 文件

【依赖关系】
- 依赖 iwan_claude.core.session.store.SessionStore
- 依赖 iwan_claude.core.tools.base.BaseTool, ToolResult
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from iwan_claude.core.session.store import SessionStore
from iwan_claude.core.tools.base import BaseTool, ToolResult


class NoteSaveParams(BaseModel):
    """
    笔记保存参数模型

    【字段说明】
    - content: str - 要保存的笔记内容（必填）

    【参数校验】
    - content 不能为空字符串
    - 会自动去除首尾空白字符
    """
    model_config = ConfigDict(extra="ignore")
    content: str


class NoteSaveTool(BaseTool):
    """
    笔记保存工具 - 将内容保存到会话笔记

    【学习要点】
    1. 会话笔记：调用 SessionStore.append_note() 保存笔记
    2. 参数验证：使用 Pydantic 模型验证输入参数
    3. 内容检查：确保内容不为空
    4. 持久化存储：将笔记追加到 session notes.md 文件

    【使用示例】
    ```python
    from iwan_claude.core.session.store import SessionStore
    
    session_store = SessionStore()
    tool = NoteSaveTool(session_store, "session_123", "run_456")
    
    # 保存笔记
    result = await tool.invoke({"content": "用户需求：需要实现登录功能"})
    
    # 返回 "saved"
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 去除内容首尾空白字符
    3. 检查内容是否为空
    4. 如果为空，返回错误
    5. 如果不为空，调用 SessionStore.append_note() 保存笔记
    6. 返回成功消息

    【注意事项】
    - 笔记会保存到当前会话的 notes.md 文件
    - 笔记在同一会话的后续轮次中可见
    - 内容会自动去除首尾空白字符
    - 空内容会返回错误
    """
    params_model = NoteSaveParams
    name = "note_save"
    description = (
        "Save a concise fact or decision to this session's notes. "
        "These notes are visible in future turns of the same session."
    )
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

    def __init__(self, store: SessionStore, session_id: str, run_id: str) -> None:
        """
        初始化笔记保存工具

        【参数说明】
        - store: SessionStore - 会话存储实例
        - session_id: str - 当前会话 ID
        - run_id: str - 当前运行 ID

        【设计说明】
        使用构造函数注入 SessionStore、session_id 和 run_id，实现依赖注入
        这样可以在测试时替换为 Mock 对象
        同时绑定当前会话和运行，使工具调用能写入对应 notes.md
        """
        # 绑定当前 session 与 run，使工具调用能写入对应 notes.md
        self._store = store
        self._session_id = session_id
        self._run_id = run_id

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行笔记保存操作

        【参数说明】
        - params: dict - 工具调用参数，包含：
          - content: str - 要保存的笔记内容（必填）

        【返回值】
        - ToolResult: 成功返回 "saved"，失败返回错误信息

        【参数解析逻辑】
        - 使用 Pydantic 模型验证参数
        - 自动去除内容首尾空白字符
        - 检查内容是否为空
        """
        # 1. 验证输入参数（Pydantic）并去除首尾空白字符
        content = NoteSaveParams.model_validate(params).content.strip()

        # 2. 检查内容是否为空
        if not content:
            return ToolResult(
                content="empty content",
                is_error=True,
                error_type="runtime_error",
            )

        # 3. 将非空 content 追加到 session notes.md
        self._store.append_note(self._session_id, content, self._run_id)

        # 4. 返回成功消息
        return ToolResult(content="saved")
