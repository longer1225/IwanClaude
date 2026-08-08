"""
知识索引管理模块 - 管理 RAG 索引的创建、更新、检索和维护

【学习要点】
1. 增量索引：仅索引变更文件，提高效率
2. 混合检索：结合语义检索和关键词检索
3. 查询重写：自动生成同义词查询，提高召回率
4. 索引维护：支持重建、清理、备份

【核心类】
- KnowledgeIndexManager: 知识索引管理器
- IndexResult: 索引结果数据类
- IndexStatus: 索引状态数据类

【索引流程】
1. 分块：将文件分割为 Chunk
2. 嵌入：将 Chunk 转换为向量
3. 存储：将向量存储到向量数据库
4. 检索：根据查询向量检索相似 Chunk

【混合检索】
- 语义检索：基于 Embedding 的相似度匹配
- 关键词检索：基于关键词的精确匹配
- 组合评分：semantic_weight * semantic_score + keyword_weight * keyword_score

【索引维护】
- 增量索引：根据文件修改时间判断是否需要重新索引
- 重建索引：清空所有索引，重新构建
- 清理索引：删除索引目录
- 备份索引：复制索引到备份目录
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iwan_claude.core.rag.chunker import Chunk, DocumentChunker
from iwan_claude.core.rag.embedding import EmbeddingProvider
from iwan_claude.core.rag.llm_client import LLMClient
from iwan_claude.core.rag.vectorstore import VectorStore


@dataclass
class IndexResult:
    """
    索引结果数据类 - 表示索引操作的结果

    【字段说明】
    - added_chunks: int - 新增的 Chunk 数量
    - updated_chunks: int - 更新的 Chunk 数量
    - deleted_chunks: int - 删除的 Chunk 数量
    - total_tokens: int - 总 token 数量（当前未使用）

    【使用场景】
    返回索引操作的统计信息，便于用户了解索引状态。
    """
    added_chunks: int = 0
    updated_chunks: int = 0
    deleted_chunks: int = 0
    total_tokens: int = 0


@dataclass
class IndexStatus:
    """
    索引状态数据类 - 表示索引的当前状态

    【字段说明】
    - total_chunks: int - 总 Chunk 数量
    - total_sources: int - 总来源文件数量
    - last_indexed_at: str - 最后索引时间
    - index_size_bytes: int - 索引大小（字节）

    【使用场景】
    返回索引的统计信息，便于用户了解索引规模。
    """
    total_chunks: int = 0
    total_sources: int = 0
    last_indexed_at: str = ""
    index_size_bytes: int = 0


class KnowledgeIndexManager:
    """
    知识索引管理器 - 管理 RAG 索引的创建、更新、检索和维护

    【学习要点】
    1. 增量索引：仅索引变更文件，提高效率
    2. 混合检索：结合语义检索和关键词检索
    3. 查询重写：自动生成同义词查询，提高召回率
    4. 索引维护：支持重建、清理、备份

    【核心组件】
    - vector_store: VectorStore - 向量存储
    - embedding_provider: EmbeddingProvider - 嵌入服务提供者
    - chunker: DocumentChunker - 文档分块器

    【元数据管理】
    - index_meta.json: 存储索引元数据（来源文件、修改时间等）
    - sources: dict - 文件路径 -> {mtime, chunk_count}

    【核心方法】
    - index_directory(): 索引目录
    - index_file(): 索引单个文件
    - remove_file(): 移除文件索引
    - search(): 语义检索
    - hybrid_search(): 混合检索（语义 + 关键词）
    - rebuild_index(): 重建索引
    - cleanup_index(): 清理索引
    - backup_index(): 备份索引
    - save(): 保存索引
    - load(): 加载索引

    【设计目的】
    提供完整的 RAG 索引管理功能，
    包括索引创建、更新、检索和维护。
    """
    def __init__(
        self,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        chunker: DocumentChunker,
        index_path: str = ".iwan/rag_index",
        *,
        llm_client: LLMClient | None = None,
    ) -> None:
        """
        初始化知识索引管理器

        【参数说明】
        - vector_store: VectorStore - 向量存储
        - embedding_provider: EmbeddingProvider - 嵌入服务提供者
        - chunker: DocumentChunker - 文档分块器
        - index_path: str - 索引存储路径（默认 ".iwan/rag_index"）
        - llm_client: LLMClient | None - 轻量 LLM 客户端（可选）
            用于 Contextual Retrieval（生成上下文摘要）和查询重写。
            传入 None 时降级为无上下文 + 硬编码同义词重写。

        【字段说明】
        - _vector_store: VectorStore - 向量存储
        - _embedding_provider: EmbeddingProvider - 嵌入服务提供者
        - _chunker: DocumentChunker - 文档分块器
        - _index_path: Path - 索引存储路径
        - _meta_path: Path - 元数据文件路径
        - _meta: dict - 索引元数据
        - _llm_client: LLMClient | None - LLM 客户端（可选）

        【初始化流程】
        1. 保存核心组件引用
        2. 设置索引路径和元数据路径
        3. 加载元数据

        【示例】
        ```python
        manager = KnowledgeIndexManager(
            vector_store=MemoryVectorStore(),
            embedding_provider=EmbeddingProvider(...),
            chunker=DocumentChunker(),
            llm_client=LLMClient(...)  # 可选，启用 Contextual Retrieval
        )
        ```
        """
        # 向量存储
        self._vector_store = vector_store
        # 嵌入服务提供者
        self._embedding_provider = embedding_provider
        # 文档分块器
        self._chunker = chunker
        # 索引存储路径
        self._index_path = Path(index_path)
        # 元数据文件路径
        self._meta_path = self._index_path / "index_meta.json"
        # LLM 客户端（可选，用于 Contextual Retrieval 和查询重写）
        self._llm_client = llm_client
        # 加载元数据
        self._load_meta()

    def _load_meta(self) -> None:
        """
        加载索引元数据

        【执行流程】
        1. 检查元数据文件是否存在
        2. 如果存在，加载元数据
        3. 如果不存在，初始化空元数据

        【元数据格式】
        ```json
        {
            "sources": {
                "src/main.py": {"mtime": 1234567890, "chunk_count": 5},
                "docs/README.md": {"mtime": 1234567891, "chunk_count": 3}
            }
        }
        ```

        【设计目的】
        保存索引的元数据，用于增量索引判断。
        """
        if self._meta_path.exists():
            with open(self._meta_path, "r", encoding="utf-8") as f:
                self._meta = json.load(f)
        else:
            # 初始化空元数据
            self._meta = {"sources": {}}

    def _save_meta(self) -> None:
        """
        保存索引元数据

        【执行流程】
        1. 创建索引目录（如果不存在）
        2. 将元数据写入文件

        【设计目的】
        持久化索引元数据，确保增量索引功能正常工作。
        """
        # 创建索引目录（如果不存在）
        self._index_path.mkdir(parents=True, exist_ok=True)
        # 将元数据写入文件
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(self._meta, f, ensure_ascii=False, indent=2)

    async def index_directory(
        self,
        root: str = ".",
        include: list[str] = ["**/*.py", "**/*.md"],
        exclude: list[str] = [".git/**", "node_modules/**", ".venv/**"],
        incremental: bool = True,
    ) -> IndexResult:
        """
        索引目录

        【参数说明】
        - root: str - 根目录（默认 "."，当前目录）
        - include: list[str] - 包含的文件模式列表（默认 ["**/*.py", "**/*.md"]）
        - exclude: list[str] - 排除的文件模式列表（默认 [".git/**", "node_modules/**", ".venv/**"]）
        - incremental: bool - 是否增量索引（默认 True）

        【返回值】
        - IndexResult: 索引结果

        【执行流程】
        1. 解析根目录路径
        2. 根据 include 模式查找所有文件
        3. 根据 exclude 模式过滤文件
        4. 遍历文件，判断是否需要索引（增量模式下检查修改时间）
        5. 索引文件并更新元数据
        6. 保存元数据并返回结果

        【增量索引逻辑】
        - 如果文件已在索引中，检查修改时间
        - 如果文件未修改（mtime <= last_mtime），跳过
        - 如果文件已修改或未索引，重新索引

        【文件过滤规则】
        - include: 使用 glob 模式匹配
        - exclude: 使用 match 方法匹配相对路径

        【设计目的】
        批量索引目录中的文件，支持增量索引提高效率。

        【注意事项】
        - root 应为绝对路径或相对于当前工作目录的路径
        - include 和 exclude 使用 glob 模式
        """
        # 初始化索引结果
        result = IndexResult()
        # 解析根目录路径（转为绝对路径）
        root_path = Path(root).resolve()

        # 根据 include 模式查找所有文件
        all_files: list[Path] = []
        for pattern in include:
            all_files.extend(root_path.glob(pattern))

        # 转换排除模式为 Path 对象
        excluded_patterns = [Path(p) for p in exclude]

        # 判断文件是否应被排除
        def is_excluded(file_path: Path) -> bool:
            rel_path = file_path.relative_to(root_path)
            for pattern in excluded_patterns:
                if rel_path.match(str(pattern)):
                    return True
            return False

        # 过滤文件（排除不需要索引的文件）
        files_to_index = [f for f in all_files if not is_excluded(f)]

        # 遍历文件并索引
        for file_path in files_to_index:
            # 获取相对路径
            rel_path = str(file_path.relative_to(root_path))

            # 增量索引：检查文件是否已修改
            if incremental:
                # 获取文件修改时间
                mtime = os.path.getmtime(file_path)
                # 检查文件是否已在索引中
                if rel_path in self._meta["sources"]:
                    # 获取上次索引时间
                    last_mtime = self._meta["sources"][rel_path].get("mtime", 0)
                    # 如果文件未修改，跳过
                    if mtime <= last_mtime:
                        continue

            # 索引文件
            await self.index_file(file_path)
            # 更新元数据
            self._meta["sources"][rel_path] = {
                "mtime": mtime,
                "chunk_count": 0,
            }
            # 增加新增 Chunk 计数
            result.added_chunks += 1

        # 保存元数据
        self._save_meta()
        return result

    async def index_file(self, path: Path) -> None:
        """
        索引单个文件

        【参数说明】
        - path: Path - 文件路径

        【执行流程】
        1. 使用分块器将文件分割为 Chunk
        2. 如果没有 Chunk，直接返回
        3. Contextual Retrieval：如果有 LLM 客户端，给每个 Chunk 生成上下文摘要
        4. 提取文本用于 embedding（有 context 时拼接到 text 前面）
        5. 调用嵌入服务将文本转换为向量
        6. 删除该文件之前的所有索引（避免重复）
        7. 添加新的 Chunk 和向量到向量存储

        【Contextual Retrieval 策略】
        Anthropic 提出的上下文增强检索策略：
        - 在 embedding 前，用 LLM 给每个 chunk 生成 50-100 token 的上下文摘要
        - 摘要说明该 chunk 在项目中的位置和作用
        - embedding 时将摘要拼接到 chunk text 前面
        - chunk.text 保持原始内容不变（检索返回时不含摘要）
        - 检索失败率可降低 49%

        【降级处理】
        - 没有 LLM 客户端时，跳过上下文生成，直接用原始 text embedding
        - LLM 调用失败时（返回空字符串），该 chunk 无上下文

        【设计目的】
        将单个文件转换为向量索引，便于后续检索。
        """
        # 使用分块器将文件分割为 Chunk
        chunks = self._chunker.chunk_file(path)
        # 如果没有 Chunk，直接返回
        if not chunks:
            return

        # Contextual Retrieval：如果有 LLM 客户端，给每个 Chunk 生成上下文摘要
        if self._llm_client:
            for chunk in chunks:
                context = await self._llm_client.generate_context(chunk.text, chunk.source_path)
                if context:
                    chunk.context = context

        # 提取文本用于 embedding
        # 如果有 context，拼接到 text 前面（Contextual Retrieval 策略）
        # 注意：chunk.text 保持原始内容不变，拼接只用于 embedding
        texts = []
        for c in chunks:
            if c.context:
                texts.append(f"{c.context}\n\n{c.text}")
            else:
                texts.append(c.text)

        # 调用嵌入服务将文本转换为向量
        vectors = await self._embedding_provider.embed(texts)

        # 删除该文件之前的所有索引（避免重复）
        await self._vector_store.delete_by_source(str(path))
        # 添加新的 Chunk 和向量到向量存储
        await self._vector_store.add(chunks, vectors)

    async def remove_file(self, path: Path) -> None:
        """
        移除文件索引

        【参数说明】
        - path: Path - 文件路径

        【执行流程】
        1. 从向量存储中移除该文件的所有 Chunk
        2. 从元数据中移除该文件的记录
        3. 保存元数据

        【设计目的】
        当文件被删除或不再需要索引时，清理相关索引。

        【注意事项】
        - 如果文件不在索引中，操作无效果
        """
        # 从向量存储中移除该文件的所有 Chunk
        await self._vector_store.delete_by_source(str(path))
        # 获取文件路径字符串
        rel_path = str(path)
        # 从元数据中移除该文件的记录
        if rel_path in self._meta["sources"]:
            del self._meta["sources"][rel_path]
            # 保存元数据
            self._save_meta()

    def status(self) -> IndexStatus:
        """
        获取索引状态

        【返回值】
        - IndexStatus: 索引状态

        【执行流程】
        1. 计算总来源文件数量
        2. 获取最后索引时间
        3. 计算索引目录大小
        4. 返回索引状态

        【设计目的】
        提供索引的统计信息，便于用户了解索引规模。

        【注意事项】
        - total_chunks 当前未正确计算（始终为 0）
        - index_size_bytes 计算索引目录下所有文件的大小
        """
        import time

        # 总 Chunk 数量（当前未正确计算）
        total_chunks = 0
        # 总来源文件数量
        total_sources = len(self._meta["sources"])
        # 最后索引时间
        last_indexed_at = self._meta.get("last_indexed_at", "")
        # 索引大小（字节）
        index_size_bytes = 0

        # 计算索引目录大小
        if self._index_path.exists():
            for file in self._index_path.rglob("*"):
                if file.is_file():
                    index_size_bytes += file.stat().st_size

        # 返回索引状态
        return IndexStatus(
            total_chunks=total_chunks,
            total_sources=total_sources,
            last_indexed_at=last_indexed_at,
            index_size_bytes=index_size_bytes,
        )

    async def search(
        self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None
    ) -> list[tuple[Chunk, float]]:
        """
        语义检索 - 基于 Embedding 的相似度匹配

        【参数说明】
        - query: str - 查询文本
        - top_k: int - 返回前 K 个结果（默认 5）
        - filters: dict[str, Any] | None - 过滤器

        【返回值】
        - list[tuple[Chunk, float]]: (Chunk, 相似度分数) 列表

        【执行流程】
        1. 将查询文本转换为向量
        2. 如果向量为空或第一个向量为空，返回空列表
        3. 使用向量存储进行相似度检索

        【设计目的】
        根据查询文本的语义查找相似的 Chunk。

        【注意事项】
        - 依赖 EmbeddingProvider 将文本转换为向量
        - 相似度分数范围：0（完全不相似）到 1（完全相同）
        """
        # 将查询文本转换为向量
        query_vector = await self._embedding_provider.embed([query])
        # 如果向量为空或第一个向量为空，返回空列表
        if not query_vector or not query_vector[0]:
            return []
        # 使用向量存储进行相似度检索
        return await self._vector_store.search(query_vector[0], top_k, filters)

    async def hybrid_search(
        self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None,
        keyword_weight: float = 0.3, semantic_weight: float = 0.7,
    ) -> list[tuple[Chunk, float]]:
        """
        混合检索 - 结合语义检索和关键词检索，支持查询重写

        【参数说明】
        - query: str - 查询文本
        - top_k: int - 返回前 K 个结果（默认 5）
        - filters: dict[str, Any] | None - 过滤器
        - keyword_weight: float - 关键词检索权重（默认 0.3）
        - semantic_weight: float - 语义检索权重（默认 0.7）

        【返回值】
        - list[tuple[Chunk, float]]: (Chunk, 综合分数) 列表

        【执行流程】
        1. 查询重写：生成同义词查询列表
        2. 语义检索：对每个重写后的查询执行语义检索
        3. 合并结果：取每个 Chunk 的最高语义分数
        4. 关键词检索：对候选 Chunk 执行关键词检索
        5. 综合评分：semantic_score * semantic_weight + keyword_score * keyword_weight
        6. 排序返回：按综合分数降序排序，返回前 top_k 个结果

        【查询重写机制】
        - 使用同义词表替换查询中的关键词
        - 生成多个变体查询
        - 提高召回率（找到更多相关结果）

        【综合评分公式】
        combined_score = semantic_score * semantic_weight + keyword_score * keyword_weight

        【设计目的】
        结合语义检索和关键词检索的优点：
        - 语义检索：理解查询意图，处理同义词
        - 关键词检索：精确匹配，提高召回率
        - 查询重写：扩展查询范围，覆盖更多可能

        【权重调整】
        - semantic_weight + keyword_weight 应为 1.0
        - 语义权重越高，越重视语义理解
        - 关键词权重越高，越重视精确匹配
        """
        # 查询重写：如果有 LLM 客户端，用 LLM 生成查询变体；否则降级为硬编码同义词
        # LLM 重写能理解查询语义，生成更准确的变体，比硬编码同义词表覆盖面更广
        if self._llm_client:
            rewritten_queries = await self._llm_client.rewrite_query(query)
        else:
            rewritten_queries = self._rewrite_query(query)
        # 存储所有语义检索结果（chunk_id -> (Chunk, score)）
        all_results: dict[str, tuple[Chunk, float]] = {}

        # 对每个重写后的查询执行语义检索
        for q in rewritten_queries:
            # 将查询转换为向量
            query_vector = await self._embedding_provider.embed([q])
            # 如果向量为空，跳过
            if not query_vector or not query_vector[0]:
                continue
            # 执行语义检索（返回 top_k * 2 个结果）
            results = await self._vector_store.search(query_vector[0], top_k * 2, filters)
            # 合并结果，取最高分数
            for chunk, score in results:
                if chunk.chunk_id in all_results:
                    # 如果当前分数更高，更新
                    if score > all_results[chunk.chunk_id][1]:
                        all_results[chunk.chunk_id] = (chunk, score)
                else:
                    all_results[chunk.chunk_id] = (chunk, score)

        # 对候选 Chunk 执行关键词检索
        keyword_results = self._keyword_search(query, list(all_results.values()), top_k)

        # 计算综合分数
        scored_results: list[tuple[Chunk, float]] = []
        for chunk_id, (chunk, semantic_score) in all_results.items():
            # 获取关键词分数（默认 0.0）
            keyword_score = keyword_results.get(chunk_id, 0.0)
            # 计算综合分数
            combined_score = (semantic_score * semantic_weight) + (keyword_score * keyword_weight)
            scored_results.append((chunk, combined_score))

        # 按综合分数降序排序
        scored_results.sort(key=lambda x: x[1], reverse=True)
        # 取前 top_k 个结果
        top_results = scored_results[:top_k]
        # Parent-Child：为有 parent_id 的 chunk 附带父级上下文
        # 检索到子 chunk（如方法）后，返回其父级 chunk（如类）的文本，提供更完整的上下文
        await self._enrich_with_parent_context(top_results)
        return top_results

    def _rewrite_query(self, query: str) -> list[str]:
        """
        查询重写 - 生成同义词查询列表

        【参数说明】
        - query: str - 原始查询

        【返回值】
        - list[str]: 查询列表（包含原始查询和同义词变体）

        【同义词表】
        - config: configuration, setting, setup
        - function: method, def, func
        - class: type, model
        - file: document, module
        - search: find, lookup
        - error: exception, bug, issue
        - test: verify, check

        【执行流程】
        1. 将原始查询添加到结果列表
        2. 遍历同义词表
        3. 如果同义词出现在查询中，生成变体查询
        4. 避免重复查询
        5. 返回查询列表

        【设计目的】
        通过同义词替换扩展查询范围，提高召回率。

        【示例】
        ```python
        query = "how to configure function"
        # 返回: ["how to configure function", "how to setup function", 
        #        "how to configuration function", "how to setting function",
        #        "how to configure method", "how to configure def", "how to configure func"]
        ```
        """
        # 将原始查询添加到结果列表
        queries = [query]

        # 同义词表
        synonyms = {
            "config": ["configuration", "setting", "setup"],
            "function": ["method", "def", "func"],
            "class": ["type", "model"],
            "file": ["document", "module"],
            "search": ["find", "lookup"],
            "error": ["exception", "bug", "issue"],
            "test": ["verify", "check"],
        }

        # 遍历同义词表，生成变体查询
        for word, syns in synonyms.items():
            # 检查同义词是否出现在查询中（不区分大小写）
            if word.lower() in query.lower():
                # 对每个同义词生成变体查询
                for syn in syns:
                    # 替换第一个出现的同义词
                    new_query = query.replace(word, syn, 1)
                    # 避免重复查询
                    if new_query not in queries:
                        queries.append(new_query)

        return queries

    def _keyword_search(
        self, query: str, candidates: list[tuple[Chunk, float]], top_k: int
    ) -> dict[str, float]:
        """
        关键词检索 - 对候选 Chunk 执行关键词匹配

        【参数说明】
        - query: str - 查询文本
        - candidates: list[tuple[Chunk, float]] - 候选 Chunk 列表
        - top_k: int - 返回前 K 个结果

        【返回值】
        - dict[str, float]: chunk_id -> 关键词分数

        【关键词提取】
        - 使用正则表达式 \w+ 提取单词
        - 转换为小写
        - 过滤空关键词

        【关键词评分算法】
        - 每个匹配的关键词 +1.0 分
        - 每个额外出现的关键词 +0.1 分（鼓励关键词密集的 Chunk）
        - 最终分数 = min(总分数 / 关键词数量, 1.0)

        【执行流程】
        1. 提取查询中的关键词
        2. 遍历候选 Chunk
        3. 对每个 Chunk 计算关键词匹配分数
        4. 归一化分数到 [0, 1] 范围
        5. 返回结果

        【设计目的】
        通过关键词精确匹配提高检索的召回率，
        补充语义检索可能遗漏的结果。

        【注意事项】
        - 不区分大小写
        - 关键词匹配是子字符串匹配
        - 分数上限为 1.0
        """
        import re

        # 提取查询中的关键词（使用正则表达式）
        keywords = re.findall(r"\w+", query.lower())
        # 存储关键词检索结果（chunk_id -> score）
        results: dict[str, float] = {}

        # 遍历候选 Chunk
        for chunk, _ in candidates:
            # 将 Chunk 文本转换为小写
            chunk_text = chunk.text.lower()
            # 初始化分数
            score = 0.0
            # 对每个关键词进行匹配
            for keyword in keywords:
                if keyword in chunk_text:
                    # 匹配到关键词，+1.0 分
                    score += 1.0
                    # 额外出现的关键词，+0.1 分（鼓励关键词密集的 Chunk）
                    score += chunk_text.count(keyword) * 0.1

            # 如果分数大于 0，添加到结果
            if score > 0:
                # 归一化分数到 [0, 1] 范围
                results[chunk.chunk_id] = min(score / len(keywords), 1.0)

        return results

    async def _enrich_with_parent_context(
        self, results: list[tuple[Chunk, float]]
    ) -> None:
        """
        为检索结果附带父级上下文（Parent-Child 策略）

        【参数说明】
        - results: list[tuple[Chunk, float]] - 检索结果列表（原地修改 chunk.metadata）

        【执行流程】
        1. 收集所有有 parent_id 的 chunk 的 parent_id（去重）
        2. 批量查找父级 chunk（一次 API 调用）
        3. 将父级 chunk 的 text 存入子 chunk 的 metadata["parent_context"]

        【Parent-Child 策略说明】
        检索到子 chunk（如类的方法）后，其上下文可能不够完整。
        通过 parent_id 找到父级 chunk（如整个类），将父级文本存入 metadata，
        供后续生成阶段使用。

        【示例】
        检索到 `def search(self, query)` → parent_id 指向 `class KnowledgeIndexManager`
        → metadata["parent_context"] = "class KnowledgeIndexManager:\n    ..."

        【性能优化】
        批量查找父级 chunk，避免多次调用 get_by_ids。
        """
        # 收集所有需要查找的 parent_id（去重）
        parent_ids = set()
        for chunk, _ in results:
            if chunk.parent_id:
                parent_ids.add(chunk.parent_id)

        # 如果没有需要查找的父级，直接返回
        if not parent_ids:
            return

        # 批量查找父级 chunk（一次调用）
        parent_chunks = await self._vector_store.get_by_ids(list(parent_ids))
        # 构建 parent_id -> Chunk 的映射
        parent_map = {c.chunk_id: c for c in parent_chunks}

        # 将父级上下文存入子 chunk 的 metadata
        for chunk, _ in results:
            if chunk.parent_id and chunk.parent_id in parent_map:
                parent = parent_map[chunk.parent_id]
                chunk.metadata["parent_context"] = parent.text

    def rebuild_index(self) -> None:
        """
        重建索引 - 清空所有索引，重新构建

        【执行流程】
        1. 重置元数据为空
        2. 创建新的向量存储实例（使用相同类型）
        3. 保存元数据

        【设计目的】
        当索引出现问题或需要完全重新索引时使用。

        【注意事项】
        - 此操作会删除所有现有索引
        - 需要重新运行 index_directory 或 index_file 来重建索引
        """
        # 重置元数据为空
        self._meta = {"sources": {}}
        # 创建新的向量存储实例（使用相同类型）
        self._vector_store = type(self._vector_store)()
        # 保存元数据
        self._save_meta()

    def cleanup_index(self) -> None:
        """
        清理索引 - 删除索引目录

        【执行流程】
        1. 如果索引目录存在，删除目录
        2. 重置元数据为空

        【设计目的】
        删除所有索引数据，释放存储空间。

        【注意事项】
        - 此操作不可逆
        - 需要重新运行 index_directory 或 index_file 来重建索引
        """
        # 如果索引目录存在，删除目录
        if self._index_path.exists():
            import shutil

            shutil.rmtree(self._index_path)
        # 重置元数据为空
        self._meta = {"sources": {}}

    def backup_index(self, backup_path: str) -> None:
        """
        备份索引 - 复制索引到备份目录

        【参数说明】
        - backup_path: str - 备份目录路径

        【执行流程】
        1. 转换备份路径为 Path 对象
        2. 如果索引目录存在，复制到备份目录（允许目录已存在）
        3. 确保元数据目录存在
        4. 复制元数据文件到备份目录

        【设计目的】
        在进行危险操作前备份索引，便于恢复。

        【注意事项】
        - 使用 shutil.copytree 复制目录，dirs_exist_ok=True 允许覆盖
        - 使用 shutil.copy2 保留文件元数据
        """
        import shutil

        # 转换备份路径为 Path 对象
        backup = Path(backup_path)
        # 如果索引目录存在，复制到备份目录
        if self._index_path.exists():
            shutil.copytree(self._index_path, backup, dirs_exist_ok=True)
        # 确保元数据目录存在
        self._meta_path.parent.mkdir(parents=True, exist_ok=True)
        # 复制元数据文件到备份目录（保留文件元数据）
        shutil.copy2(self._meta_path, backup / "index_meta.json")

    def save(self) -> None:
        """
        保存索引 - 持久化到磁盘

        【执行流程】
        1. 保存向量存储
        2. 保存元数据

        【设计目的】
        将索引数据持久化到磁盘，便于下次启动时恢复。
        """
        # 保存向量存储
        self._vector_store.save(self._index_path)
        # 保存元数据
        self._save_meta()

    def load(self) -> None:
        """
        加载索引 - 从磁盘恢复

        【执行流程】
        1. 加载向量存储
        2. 加载元数据

        【设计目的】
        从磁盘加载之前保存的索引数据。
        """
        # 加载向量存储
        self._vector_store.load(self._index_path)
        # 加载元数据
        self._load_meta()
