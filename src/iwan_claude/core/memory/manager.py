"""
记忆管理器 - 统一管理三层记忆

【设计理念】
MemoryManager 是记忆系统的统一入口，整合三层记忆：
1. 项目级记忆（CLAUDE.md）：项目描述、技术栈、规则
2. 长期记忆（LongTermMemory）：用户偏好、历史决策（关键词搜索）
3. 向量记忆（VectorMemory）：历史对话片段（语义搜索）

短期记忆（当前会话上下文）不在本模块管理，由 SessionManager 负责。

【工作流程】
1. 用户提问时，MemoryManager.recall(query) 检索相关记忆
2. 检索结果注入到系统提示词或用户消息中
3. Agent 回答后，MemoryManager.remember_conversation() 存储对话
4. Agent 发现重要信息时，MemoryManager.remember() 存储为长期记忆

【与 Claude Code 的对比】
Claude Code 的记忆系统：
- CLAUDE.md：项目级记忆（我们也有）
- /memory 命令：手动添加记忆（我们通过 remember() 方法实现）
- 自动记忆：对话中自动提取重要信息（未来可以加 LLM 自动提取）

【示例】
```python
manager = MemoryManager(
    long_term=LongTermMemory(Path("~/.iwan_claude/memory/long_term.jsonl")),
    vector_memory=VectorMemory(store, embedder, "vector_memory.json"),
)
manager.load()

# 检索相关记忆
context = await manager.recall("怎么配置 RAG？")
# → "用户偏好：用 pytest\n相关对话：上次讨论过 RAG 配置..."

# 存储对话
await manager.remember_conversation("怎么配置 RAG？", "你可以在...", session_id="abc")

# 存储长期记忆
manager.remember("用户偏好用 pytest", type="preference", tags=["pytest"])
```
"""

from __future__ import annotations

from typing import Any

from iwan_claude.core.memory.long_term import LongTermMemory, MemoryEntry
from iwan_claude.core.memory.vector_memory import VectorMemory

# ======================================================================
# 记忆管理器
# ======================================================================


class MemoryManager:
    """
    统一记忆管理器

    【职责】
    - 整合三层记忆的检索和存储
    - 提供统一的 recall() 和 remember() 接口
    - 格式化记忆文本供系统提示词使用

    【字段说明】
    - _long_term: LongTermMemory - 长期记忆（关键词搜索）
    - _vector_memory: VectorMemory - 向量记忆（语义搜索）
    - _project_context: str - 项目级记忆（CLAUDE.md 内容，由外部注入）
    """

    def __init__(
        self,
        long_term: LongTermMemory,
        vector_memory: VectorMemory,
        *,
        project_context: str = "",
    ) -> None:
        """
        初始化记忆管理器

        【参数说明】
        - long_term: LongTermMemory - 长期记忆实例
        - vector_memory: VectorMemory - 向量记忆实例
        - project_context: str - 项目级记忆（CLAUDE.md 渲染后的文本）
            由外部加载后传入，本模块不负责加载 CLAUDE.md
        """
        self._long_term = long_term
        self._vector_memory = vector_memory
        self._project_context = project_context

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def load(self) -> None:
        """从磁盘加载所有记忆"""
        self._long_term.load()
        self._vector_memory.load()

    def save(self) -> None:
        """保存所有记忆到磁盘"""
        self._long_term.save()
        self._vector_memory.save()

    # ------------------------------------------------------------------
    # 检索记忆
    # ------------------------------------------------------------------

    async def recall(self, query: str, *, top_k: int = 3) -> str:
        """
        检索与查询相关的记忆

        【执行流程】
        1. 从长期记忆中关键词搜索
        2. 从向量记忆中语义搜索
        3. 合并结果，格式化为文本

        【返回格式】
        ```
        ## Long-term Memory
        - [preference] 用户偏好用 pytest (tags: pytest, testing)
        - [decision] 选择 FAISS 而非 Milvus

        ## Relevant Conversations
        [score: 0.85] User: 怎么配置测试？
        Assistant: 你可以用 pytest...
        ```

        【参数说明】
        - query: str - 搜索查询（当前用户的问题）
        - top_k: int - 每层记忆返回的最大数量（默认 3）

        【返回值】
        - str: 格式化的记忆文本，无记忆时返回空字符串
        """
        parts: list[str] = []

        # 1. 长期记忆（关键词搜索）
        lt_results = self._long_term.search(query, top_k=top_k)
        if lt_results:
            lines = ["## Long-term Memory"]
            for entry, score in lt_results:
                tags_str = f" (tags: {', '.join(entry.tags)})" if entry.tags else ""
                lines.append(f"- [{entry.type}] {entry.content}{tags_str}")
            parts.append("\n".join(lines))

        # 2. 向量记忆（语义搜索）
        vm_results = await self._vector_memory.search(query, top_k=top_k)
        if vm_results:
            lines = ["## Relevant Conversations"]
            for chunk, score in vm_results:
                # 截断过长的对话文本
                text = chunk.text.strip()
                if len(text) > 300:
                    text = text[:300] + "..."
                lines.append(f"[score: {score:.2f}]\n{text}")
            parts.append("\n".join(lines))

        return "\n\n".join(parts) if parts else ""

    def get_project_context(self) -> str:
        """
        获取项目级记忆（CLAUDE.md 内容）

        【返回值】
        - str: 项目上下文文本，无则返回空字符串
        """
        return self._project_context

    # ------------------------------------------------------------------
    # 存储记忆
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        *,
        type: str = "fact",
        tags: list[str] | None = None,
        session_id: str | None = None,
    ) -> MemoryEntry:
        """
        存储一条长期记忆

        【使用场景】
        - Agent 发现用户偏好时调用
        - Agent 做出重要决策时调用
        - 用户明确说"记住这个"时调用

        【参数说明】
        - content: str - 记忆内容
        - type: str - 记忆类型（preference/decision/fact/feedback）
        - tags: list[str] | None - 标签
        - session_id: str | None - 来源会话

        【返回值】
        - MemoryEntry: 创建的记忆条目
        """
        return self._long_term.add(
            content,
            type=type,
            tags=tags,
            session_id=session_id,
        )

    async def remember_conversation(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """
        将一段对话存入向量记忆

        【使用场景】
        - 每次 Agent 回答完后调用
        - 存储完整的问答对，供未来语义检索

        【参数说明】
        - user_msg: str - 用户消息
        - assistant_msg: str - 助手回复
        - session_id: str | None - 会话 ID
        - tags: list[str] | None - 标签
        """
        await self._vector_memory.add_conversation(
            user_msg,
            assistant_msg,
            session_id=session_id,
            tags=tags,
        )

    # ------------------------------------------------------------------
    # 遗忘
    # ------------------------------------------------------------------

    def forget(self, memory_id: str) -> bool:
        """
        遗忘一条长期记忆

        【参数说明】
        - memory_id: str - 记忆 ID

        【返回值】
        - bool: 是否成功删除
        """
        return self._long_term.forget(memory_id)

    async def forget_session(self, session_id: str) -> int:
        """
        遗忘指定会话的所有向量记忆

        【参数说明】
        - session_id: str - 会话 ID

        【返回值】
        - int: 删除的对话数量
        """
        return await self._vector_memory.delete_by_session(session_id)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """
        返回记忆统计信息

        【返回值】
        - dict[str, int]: 各层记忆的数量
        ```python
        {
            "long_term": 15,      # 长期记忆数量
            "vector_memory": 42,  # 向量记忆数量
            "project_context": 1,  # 项目级记忆（有/无）
        }
        ```
        """
        return {
            "long_term": self._long_term.count(),
            "vector_memory": self._vector_memory.count(),
            "project_context": 1 if self._project_context else 0,
        }

    # ------------------------------------------------------------------
    # 列出所有长期记忆（供 /memory 命令使用）
    # ------------------------------------------------------------------

    def list_long_term(self) -> list[MemoryEntry]:
        """
        列出所有长期记忆（按时间倒序）

        【使用场景】
        - 用户执行 /memory 命令查看所有记忆
        - 调试时检查记忆内容

        【返回值】
        - list[MemoryEntry]: 所有长期记忆
        """
        return self._long_term.list_all()
