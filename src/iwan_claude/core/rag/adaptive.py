"""
Adaptive RAG - 自适应检索路由

【设计目的】
传统 RAG 是固定流水线：不管什么问题都强制走向量检索。
Adaptive RAG 把检索变成智能决策：LLM 先判断问题类型，再路由到最合适的检索策略。

【三种检索策略】
1. direct（直接回答）：简单常识问题，不需要检索代码
   - 示例："1+1=?"、"什么是 Python"
   - 策略：跳过检索，让 LLM 直接回答

2. grep（关键词搜索）：精确代码查找，关键词匹配比语义检索更准
   - 示例："找到 AuthService 类"、"grep configure 方法"
   - 策略：在所有 chunk 中做精确文本匹配

3. rag（语义检索）：概念/架构问题，需要语义理解
   - 示例："权限系统是怎么设计的"、"RAG 模块的工作流程"
   - 策略：使用 hybrid_search（语义+关键词混合检索）

【与 Claude Code 的对比】
Claude Code 完全放弃了 RAG，只用 agentic search（grep/glob/read）。
我们保留 RAG 但增加路由能力：简单问题不检索、精确查找用 grep、
语义问题用 RAG，兼顾准确性和效率。

【降级处理】
没有 LLM 客户端时，所有查询都走 RAG（保持向后兼容）。
LLM 分类失败时，降级为 RAG（最通用的策略）。

【核心类】
- AdaptiveRetriever: 自适应检索器
- RetrievalResult: 检索结果数据类
"""
from __future__ import annotations

from dataclasses import dataclass, field

from iwan_claude.core.rag.chunker import Chunk
from iwan_claude.core.rag.index import KnowledgeIndexManager
from iwan_claude.core.rag.llm_client import LLMClient


@dataclass
class RetrievalResult:
    """
    检索结果数据类 - 包含检索策略和结果

    【字段说明】
    - strategy: str - 使用的检索策略
        （"direct" | "grep" | "rag" | "crag_fallback_grep" | "crag_rewrite"）
    - query_type: str - 查询类型分类（与初始 strategy 相同）
    - chunks: list[tuple[Chunk, float]] - 检索结果列表（Chunk, 分数）
    - rewritten: bool - 是否使用了查询重写
    - quality: str - 检索结果质量评估（CRAG）
        （"correct" | "ambiguous" | "incorrect" | "unknown"）

    【使用场景】
    调用方可以根据 strategy 了解使用了哪种检索策略，
    根据 quality 了解检索结果的可信度，
    便于日志记录、性能分析和效果评估。
    """
    strategy: str = "rag"
    query_type: str = "rag"
    chunks: list[tuple[Chunk, float]] = field(default_factory=list)
    rewritten: bool = False
    quality: str = "unknown"
    reranked: bool = False


class AdaptiveRetriever:
    """
    自适应检索器 - 根据查询类型自动路由检索策略

    【学习要点】
    1. 智能路由：LLM 判断问题类型，选择最优检索策略
    2. 降级容错：LLM 不可用时降级为纯 RAG，保证可用性
    3. 策略分离：direct/grep/rag 三种策略各有适用场景
    4. 可观测性：RetrievalResult 记录使用的策略，便于分析

    【核心方法】
    - retrieve(): 自适应检索（分类 → 路由 → 检索）

    【路由逻辑】
    ```
    用户查询
       ↓
    LLM 分类（direct / grep / rag）
       ↓
    ┌─────────┬─────────┬─────────┐
    │ direct  │  grep   │   rag   │
    │ 不检索   │ 关键词   │ 语义+   │
    │         │ 搜索     │ 关键词  │
    └─────────┴─────────┴─────────┘
    ```

    【设计目的】
    减少无意义检索（简单问题不检索），
    提高精确查找的准确性（代码标识符用 grep），
    在需要语义理解时才使用 RAG。
    """

    def __init__(
        self,
        index_manager: KnowledgeIndexManager,
        llm_client: LLMClient | None = None,
    ) -> None:
        """
        初始化自适应检索器

        【参数说明】
        - index_manager: KnowledgeIndexManager - 知识索引管理器
        - llm_client: LLMClient | None - LLM 客户端（可选）
            用于查询分类。传入 None 时所有查询都走 RAG。

        【字段说明】
        - _index_manager: 知识索引管理器
        - _llm_client: LLM 客户端（可选）
        """
        self._index_manager = index_manager
        self._llm_client = llm_client

    async def retrieve(
        self, query: str, top_k: int = 5
    ) -> RetrievalResult:
        """
        自适应检索 - 分类查询并路由到最优策略，含 CRAG 质量修正

        【参数说明】
        - query: str - 用户查询
        - top_k: int - 返回前 K 个结果（默认 5）

        【返回值】
        - RetrievalResult: 检索结果（包含策略信息和 chunk 列表）

        【执行流程】
        1. 查询分类：LLM 判断查询类型（direct/grep/rag）
        2. 策略路由：
           - direct: 返回空结果（不检索）
           - grep: 调用 search_by_text 做关键词搜索
           - rag: 调用 hybrid_search 做语义+关键词混合检索
        3. CRAG 质量修正（仅 rag 策略）：
           - correct（高置信）：直接使用检索结果
           - ambiguous（中等置信）：改写查询重新检索，取最优结果
           - incorrect（低置信）：回退到 grep 关键词搜索

        【CRAG 三档处理】
        ```
        RAG 检索结果
            ↓
        质量评估（top_score 分档）
            ↓
        ┌──────────┬──────────┬──────────┐
        │ correct  │ambiguous │incorrect │
        │ ≥0.6     │ 0.3~0.6  │ <0.3     │
        │ 直接使用  │ 改写查询  │ 回退grep │
        └──────────┴──────────┴──────────┘
        ```

        【降级处理】
        - 没有 LLM 客户端 → 走 RAG，不做 CRAG 修正
        - LLM 分类失败 → 走 RAG
        - LLM 返回未知类型 → 走 RAG
        """
        # 查询分类
        query_type = await self._classify_query(query)

        # 根据分类结果路由
        if query_type == "direct":
            # 简单问题：不检索，直接回答
            return RetrievalResult(
                strategy="direct",
                query_type="direct",
                chunks=[],
                quality="unknown",
            )
        elif query_type == "grep":
            # 精确查找：用关键词搜索
            chunks = await self._index_manager._vector_store.search_by_text(
                query, top_k=top_k
            )
            # 附带父级上下文
            await self._index_manager._enrich_with_parent_context(chunks)
            return RetrievalResult(
                strategy="grep",
                query_type="grep",
                chunks=chunks,
                quality="correct" if chunks else "incorrect",
            )
        else:
            # 语义检索：使用 hybrid_search（含 LLM 查询重写 + Parent-Child）
            chunks = await self._index_manager.hybrid_search(query, top_k=top_k)

            # CRAG 质量评估
            quality = self._evaluate_quality(chunks)
            strategy = "rag"
            rewritten = self._llm_client is not None

            if quality == "correct":
                # 高置信：直接使用检索结果
                strategy = "rag"
            elif quality == "ambiguous" and self._llm_client:
                # 中等置信：改写查询重新检索，取最优结果
                chunks = await self._rewrite_and_research(query, chunks, top_k)
                strategy = "crag_rewrite"
                quality = self._evaluate_quality(chunks)
                rewritten = True
            else:
                # 低置信：回退到 grep 关键词搜索
                grep_chunks = await self._index_manager._vector_store.search_by_text(
                    query, top_k=top_k
                )
                if grep_chunks:
                    await self._index_manager._enrich_with_parent_context(grep_chunks)
                    chunks = grep_chunks
                    strategy = "crag_fallback_grep"
                    quality = "incorrect"
                    rewritten = False
                # else: grep 也没结果，保留原始 RAG 结果

            # Reranking：如果有 LLM 且有结果，用 LLM 重排序
            # Reranking 在 CRAG 修正之后执行，对最终返回的结果做精排
            reranked = False
            if self._llm_client and chunks:
                chunks = await self._llm_client.rerank(query, chunks, top_k=top_k)
                reranked = True

            return RetrievalResult(
                strategy=strategy,
                query_type="rag",
                chunks=chunks,
                rewritten=rewritten,
                quality=quality,
                reranked=reranked,
            )

    def _evaluate_quality(
        self, results: list[tuple[Chunk, float]]
    ) -> str:
        """
        评估检索结果质量（CRAG 三档分类）

        【参数说明】
        - results: list[tuple[Chunk, float]] - 检索结果列表

        【返回值】
        - str: 质量等级
            - "correct": 高置信（top_score >= 0.6），直接使用
            - "ambiguous": 中等置信（0.3 <= top_score < 0.6），改写查询
            - "incorrect": 低置信（top_score < 0.3），回退 grep

        【评分标准】
        基于检索结果的最高分数（hybrid_search 的综合分数）：
        - 分数 >= 0.6：语义和关键词都高度匹配，结果可信
        - 分数 0.3~0.6：部分匹配，可能需要改写查询
        - 分数 < 0.3：匹配度低，RAG 可能找错了方向

        【设计目的】
        避免 low-quality 检索结果误导 LLM 生成。
        低质量时回退到 grep，比返回不相关的 RAG 结果更好。
        """
        if not results:
            return "incorrect"
        top_score = results[0][1]
        if top_score >= 0.6:
            return "correct"
        elif top_score >= 0.3:
            return "ambiguous"
        else:
            return "incorrect"

    async def _rewrite_and_research(
        self,
        query: str,
        original_chunks: list[tuple[Chunk, float]],
        top_k: int,
    ) -> list[tuple[Chunk, float]]:
        """
        改写查询并重新检索（CRAG ambiguous 修正）

        【参数说明】
        - query: str - 原始查询
        - original_chunks: list[tuple[Chunk, float]] - 原始检索结果
        - top_k: int - 返回前 K 个结果

        【返回值】
        - list[tuple[Chunk, float]]: 最优检索结果（原始或改写后取最好的）

        【执行流程】
        1. 用 LLM 生成查询变体
        2. 对每个变体执行 hybrid_search
        3. 比较所有结果，返回分数最高的一组

        【设计目的】
        当检索结果质量中等时，可能是查询表述不够好。
        通过改写查询（换一种问法），可能找到更相关的结果。
        """
        if not self._llm_client:
            return original_chunks

        # 用 LLM 生成查询变体
        rewritten_queries = await self._llm_client.rewrite_query(query)

        # 保留原始结果作为基准
        best_chunks = original_chunks
        best_score = original_chunks[0][1] if original_chunks else 0.0

        # 对每个变体执行检索，取最优结果
        for rq in rewritten_queries[1:]:  # 跳过原始查询
            new_chunks = await self._index_manager.hybrid_search(rq, top_k=top_k)
            if new_chunks and new_chunks[0][1] > best_score:
                best_chunks = new_chunks
                best_score = new_chunks[0][1]

        return best_chunks

    async def _classify_query(self, query: str) -> str:
        """
        查询分类 - 用 LLM 判断查询类型

        【参数说明】
        - query: str - 用户查询

        【返回值】
        - str: 查询类型（"direct" | "grep" | "rag"）

        【分类标准】
        - direct: 简单常识问题，不需要检索代码
          （如 "1+1=?"、"什么是 Python"）
        - grep: 精确代码查找，关键词匹配更有效
          （如 "找到 AuthService"、"grep configure"）
        - rag: 语义/概念问题，需要语义理解
          （如 "权限系统怎么设计的"、"RAG 工作流程"）

        【降级处理】
        - 没有 LLM 客户端 → 返回 "rag"（最通用策略）
        - LLM 调用失败 → 返回 "rag"
        - LLM 返回未知类型 → 返回 "rag"
        """
        # 没有 LLM 客户端，降级为 RAG
        if not self._llm_client:
            return "rag"

        # 用 LLM 分类查询
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a query classifier for a code RAG system. "
                    "Classify the user's query into one of three categories:\n"
                    "- direct: Simple/factual questions that don't need code search "
                    "(e.g., 'what is 1+1', 'what is Python')\n"
                    "- grep: Exact code lookups where keyword matching is better "
                    "(e.g., 'find AuthService class', 'grep configure function')\n"
                    "- rag: Semantic/conceptual questions needing understanding "
                    "(e.g., 'how is the permission system designed', 'RAG workflow')\n\n"
                    "Reply with ONLY the category name (direct, grep, or rag). "
                    "No explanation."
                ),
            },
            {"role": "user", "content": query},
        ]
        result = await self._llm_client.complete(
            messages, temperature=0.0, max_tokens=10
        )

        # 解析 LLM 输出，降级为 rag
        result = result.strip().lower()
        if result in ("direct", "grep", "rag"):
            return result
        return "rag"
