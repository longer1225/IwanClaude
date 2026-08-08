"""
向量记忆模块 - 用 RAG 检索历史对话

【设计理念】
向量记忆是长期记忆的"语义搜索"版本。与 LongTermMemory 的关键词搜索不同，
向量记忆使用 Embedding 向量化历史对话，支持语义级别的模糊匹配。

【与长期记忆的分工】
- LongTermMemory（关键词搜索）：存储明确的偏好/决策，如"用户喜欢 TypeScript"
- VectorMemory（语义搜索）：存储完整对话片段，支持"上次讨论过类似问题"的检索

【复用 RAG 基础设施】
本模块不重新实现向量存储，而是复用现有的：
- MemoryVectorStore：内存向量存储（add/search/save/load）
- EmbeddingProvider：Embedding API 调用
- Chunk：文档分块数据结构（复用为对话片段载体）

【存储格式】
向量数据存储在 MemoryVectorStore 中，持久化到 JSON 文件：
~/.iwan_claude/memory/vector_memory.json

【示例场景】
1. 用户在第 3 次会话中问"怎么配置 RAG？"
2. Agent 在第 1 次会话中讨论过 RAG 配置
3. VectorMemory 语义搜索找到第 1 次会话的相关对话
4. 注入到当前上下文，帮助 Agent 回答
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iwan_claude.core.rag.chunker import Chunk
from iwan_claude.core.rag.embedding import EmbeddingProvider
from iwan_claude.core.rag.vectorstore import MemoryVectorStore

# ======================================================================
# 向量记忆存储
# ======================================================================


def _now() -> str:
    """获取当前 UTC 时间的 ISO 8601 格式字符串"""
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    """生成唯一的对话片段 ID"""
    return uuid.uuid4().hex[:12]


class VectorMemory:
    """
    向量记忆存储管理器

    【职责】
    - 将历史对话片段向量化存储
    - 语义搜索相关历史对话
    - 持久化向量数据到磁盘

    【使用方式】
    ```python
    vector_mem = VectorMemory(
        vector_store=MemoryVectorStore(),
        embedding_provider=embedder,
        index_path=Path("~/.iwan_claude/memory/vector_memory.json"),
    )
    vector_mem.load()

    # 存储对话
    await vector_mem.add_conversation(
        user_msg="怎么配置 RAG？",
        assistant_msg="你可以在 config.py 中设置...",
        session_id="sess_abc",
    )

    # 语义搜索
    results = await vector_mem.search("RAG 配置方法", top_k=3)
    ```

    【依赖】
    - EmbeddingProvider：需要配置 API Key 才能使用
    - 如果没有 API Key，add_conversation 和 search 会返回空结果（降级）
    """

    def __init__(
        self,
        vector_store: MemoryVectorStore,
        embedding_provider: EmbeddingProvider | None,
        index_path: str | Path | None = None,
    ) -> None:
        """
        初始化向量记忆存储

        【参数说明】
        - vector_store: MemoryVectorStore - 向量存储实例
        - embedding_provider: EmbeddingProvider | None - Embedding 提供者
            传入 None 时降级为无向量模式（add/search 返回空结果）
        - index_path: str | Path | None - 持久化文件路径
            传入 None 时不持久化（纯内存模式）
        """
        self._store = vector_store
        self._embedder = embedding_provider
        self._index_path = Path(index_path) if index_path else None

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def load(self) -> None:
        """从磁盘加载向量数据"""
        if self._index_path:
            self._store.load(self._index_path)

    def save(self) -> None:
        """保存向量数据到磁盘"""
        if self._index_path:
            self._store.save(self._index_path)

    # ------------------------------------------------------------------
    # 添加对话
    # ------------------------------------------------------------------

    async def add_conversation(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> Chunk | None:
        """
        将一段对话向量化存储

        【执行流程】
        1. 将用户消息和助手回复拼接为一段文本
        2. 调用 EmbeddingProvider 生成向量
        3. 用 Chunk 封装对话片段
        4. 存入 MemoryVectorStore
        5. 持久化到磁盘

        【对话文本格式】
        ```
        User: {用户消息}

        Assistant: {助手回复}
        ```
        这种格式让 LLM 在检索时能看到完整的问答上下文。

        【参数说明】
        - user_msg: str - 用户消息
        - assistant_msg: str - 助手回复
        - session_id: str | None - 来源会话 ID
        - tags: list[str] | None - 标签（存入 metadata）

        【返回值】
        - Chunk | None: 存储的对话片段，失败返回 None
            （没有 EmbeddingProvider 时返回 None）
        """
        if not self._embedder:
            return None

        # 拼接对话文本
        text = f"User: {user_msg}\n\nAssistant: {assistant_msg}"

        # 生成向量（embedding 服务不可用时静默降级，不影响长期记忆）
        try:
            vectors = await self._embedder.embed([text])
        except Exception:
            return None
        if not vectors:
            return None

        # 封装为 Chunk
        chunk = Chunk(
            chunk_id=_new_id(),
            text=text,
            source_path="conversation",
            start_line=0,
            end_line=0,
            metadata={
                "session_id": session_id,
                "tags": tags or [],
                "timestamp": _now(),
                "type": "conversation",
            },
        )

        # 存入向量存储
        await self._store.add([chunk], vectors)
        self.save()

        return chunk

    # ------------------------------------------------------------------
    # 语义搜索
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        top_k: int = 3,
    ) -> list[tuple[Chunk, float]]:
        """
        语义搜索历史对话

        【执行流程】
        1. 将查询文本向量化
        2. 在 MemoryVectorStore 中搜索相似向量
        3. 返回最相似的 top_k 个对话片段

        【与 LongTermMemory.search 的区别】
        - LongTermMemory：关键词匹配（"pytest" → 匹配含 "pytest" 的记忆）
        - VectorMemory：语义匹配（"测试框架" → 匹配讨论过 pytest 的对话）

        【参数说明】
        - query: str - 搜索查询
        - top_k: int - 返回前 K 个结果（默认 3）

        【返回值】
        - list[tuple[Chunk, float]]: 对话片段 + 相似度分数
            没有结果时返回空列表
        """
        if not self._embedder:
            return []

        # 生成查询向量（embedding 服务不可用时静默降级为空结果）
        try:
            query_vectors = await self._embedder.embed([query])
        except Exception:
            return []
        if not query_vectors:
            return []

        # 语义搜索
        results = await self._store.search(query_vectors[0], top_k=top_k)
        return results

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------

    async def delete_by_session(self, session_id: str) -> int:
        """
        删除指定会话的所有对话记忆

        【参数说明】
        - session_id: str - 会话 ID

        【返回值】
        - int: 删除的对话数量
        """
        # MemoryVectorStore 内部有 _chunks（list[Chunk]）和 _vectors（list[list[float]]）
        # 两个列表一一对应，通过 _chunks 找到匹配的 chunk ID
        to_delete: list[str] = []
        for chunk in self._store._chunks:
            if hasattr(chunk, "metadata") and chunk.metadata.get("session_id") == session_id:
                to_delete.append(chunk.chunk_id)

        if to_delete:
            await self._store.delete(to_delete)
            self.save()

        return len(to_delete)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def count(self) -> int:
        """返回存储的对话片段数量"""
        return len(self._store._chunks)
