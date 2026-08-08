"""
向量存储模块 - 存储和检索文本向量

【学习要点】
1. 抽象基类：定义向量存储的统一接口
2. 内存实现：基于内存的简单向量存储（开发/测试用）
3. 余弦相似度：计算向量之间的相似度
4. 持久化：支持保存和加载到磁盘

【核心接口】
- VectorStore: 向量存储抽象基类
- MemoryVectorStore: 内存向量存储实现

【检索流程】
1. 将查询文本转换为向量（Embedding）
2. 计算与所有存储向量的余弦相似度
3. 按相似度排序，返回 Top-K 结果
4. 可选：应用过滤器（source_path, symbol 等）

【持久化格式】
- chunks.json: 存储 Chunk 对象列表
- vectors.json: 存储向量列表

【设计目的】
提供统一的向量存储接口，便于替换不同的向量数据库实现，
当前实现基于内存（开发/测试用），未来可扩展为 Redis、Milvus、Pinecone 等。
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from iwan_claude.core.rag.chunker import Chunk


class VectorStore(ABC):
    """
    向量存储抽象基类 - 定义向量存储的统一接口

    【学习要点】
    1. 抽象接口：定义向量存储的核心操作
    2. 多态设计：便于替换不同的向量数据库实现
    3. 异步接口：支持异步操作

    【核心方法】
    - add(): 添加 Chunk 和对应的向量
    - delete(): 根据 chunk_id 删除
    - delete_by_source(): 根据来源路径删除
    - search(): 向量相似度检索
    - save(): 持久化到磁盘
    - load(): 从磁盘加载

    【设计目的】
    定义统一的向量存储接口，使上层代码不依赖具体实现，
    便于替换不同的向量数据库（如 Milvus、Pinecone、Redis 等）。

    【注意事项】
    - 所有方法都是抽象方法，子类必须实现
    - search 方法返回 (Chunk, 相似度分数) 列表
    """
    @abstractmethod
    async def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """
        添加 Chunk 和对应的向量

        【参数说明】
        - chunks: list[Chunk] - Chunk 对象列表
        - vectors: list[list[float]] - 向量列表（与 chunks 一一对应）

        【设计目的】
        将文本块和其向量存储到向量数据库中，便于后续检索。
        """
        ...

    @abstractmethod
    async def delete(self, chunk_ids: list[str]) -> None:
        """
        根据 chunk_id 删除

        【参数说明】
        - chunk_ids: list[str] - 要删除的 chunk_id 列表

        【设计目的】
        删除指定的 Chunk 和其向量，用于更新索引。
        """
        ...

    @abstractmethod
    async def delete_by_source(self, source_path: str) -> None:
        """
        根据来源路径删除

        【参数说明】
        - source_path: str - 来源文件路径

        【设计目的】
        删除指定文件对应的所有 Chunk 和向量，
        用于文件更新时的增量索引。
        """
        ...

    @abstractmethod
    async def search(
        self, query_vector: list[float], top_k: int = 5,
        filters: dict[str, Any] | None = None
    ) -> list[tuple[Chunk, float]]:
        """
        向量相似度检索

        【参数说明】
        - query_vector: list[float] - 查询向量
        - top_k: int - 返回前 K 个结果（默认 5）
        - filters: dict[str, Any] | None - 过滤器（如 {"source_path": "src/main.py"}）

        【返回值】
        - list[tuple[Chunk, float]]: (Chunk, 相似度分数) 列表，按相似度降序排列

        【设计目的】
        根据查询向量检索最相似的 Chunk，
        支持过滤器精确定位。
        """
        ...

    @abstractmethod
    async def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        """
        根据 chunk_id 列表获取 Chunk

        【参数说明】
        - chunk_ids: list[str] - chunk_id 列表

        【返回值】
        - list[Chunk]: Chunk 对象列表（不存在的 id 会被跳过）

        【设计目的】
        用于 Parent-Child 检索：根据子 chunk 的 parent_id 查找父级 chunk，
        提供更完整的上下文。
        """
        ...

    @abstractmethod
    async def search_by_text(
        self, query: str, top_k: int = 5
    ) -> list[tuple[Chunk, float]]:
        """
        关键词搜索 - 在所有 Chunk 中做精确文本匹配（不使用向量）

        【参数说明】
        - query: str - 查询文本
        - top_k: int - 返回前 K 个结果（默认 5）

        【返回值】
        - list[tuple[Chunk, float]]: (Chunk, 关键词匹配分数) 列表

        【设计目的】
        用于 Adaptive RAG 的 grep 策略：
        当用户查询是精确的代码标识符（如 "AuthService"）时，
        关键词搜索比语义检索更准确。
        """
        ...

    @abstractmethod
    def save(self, path: Path) -> None:
        """
        持久化到磁盘

        【参数说明】
        - path: Path - 存储目录路径

        【设计目的】
        将向量存储保存到磁盘，便于下次启动时恢复。
        """
        ...

    @abstractmethod
    def load(self, path: Path) -> None:
        """
        从磁盘加载

        【参数说明】
        - path: Path - 存储目录路径

        【设计目的】
        从磁盘加载之前保存的向量存储。
        """
        ...


class MemoryVectorStore(VectorStore):
    """
    内存向量存储实现 - 基于内存的简单向量存储

    【学习要点】
    1. 数据结构：使用列表存储 Chunk 和向量，字典存储索引
    2. 索引优化：使用 chunk_id_map 和 source_map 加速查找
    3. 余弦相似度：计算向量之间的相似度
    4. 持久化：支持保存和加载到磁盘

    【数据结构】
    - _chunks: list[Chunk] - Chunk 对象列表
    - _vectors: list[list[float]] - 向量列表（与 _chunks 一一对应）
    - _chunk_id_map: dict[str, int] - chunk_id 到索引的映射（加速删除）
    - _source_map: dict[str, list[int]] - source_path 到索引列表的映射（加速按来源删除）

    【适用场景】
    - 开发/测试环境
    - 小规模数据集
    - 快速原型验证

    【性能特点】
    - 添加：O(n)（n 为新添加的 Chunk 数量）
    - 删除：O(n)（需要重建索引）
    - 检索：O(n)（需要计算与所有向量的相似度）
    - 不适合大规模数据（建议使用专业向量数据库）

    【设计目的】
    提供一个简单的内存向量存储实现，
    便于开发和测试，以及小规模数据的快速原型验证。
    """
    def __init__(self) -> None:
        """
        初始化内存向量存储

        【字段说明】
        - _chunks: list[Chunk] - Chunk 对象列表
        - _vectors: list[list[float]] - 向量列表（与 _chunks 一一对应）
        - _chunk_id_map: dict[str, int] - chunk_id 到索引的映射（加速删除）
        - _source_map: dict[str, list[int]] - source_path 到索引列表的映射（加速按来源删除）

        【设计要点】
        - 使用列表存储数据，索引作为位置标识
        - 使用字典存储索引映射，加速查找和删除
        - 初始化时为空列表和空字典
        """
        # Chunk 对象列表
        self._chunks: list[Chunk] = []
        # 向量列表（与 _chunks 一一对应）
        self._vectors: list[list[float]] = []
        # chunk_id 到索引的映射（加速删除）
        self._chunk_id_map: dict[str, int] = {}
        # source_path 到索引列表的映射（加速按来源删除）
        self._source_map: dict[str, list[int]] = {}

    async def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """
        添加 Chunk 和对应的向量

        【参数说明】
        - chunks: list[Chunk] - Chunk 对象列表
        - vectors: list[list[float]] - 向量列表（与 chunks 一一对应）

        【执行流程】
        1. 遍历 chunks 和 vectors
        2. 将 Chunk 添加到 _chunks 列表
        3. 将向量添加到 _vectors 列表
        4. 更新 _chunk_id_map（chunk_id -> 索引）
        5. 更新 _source_map（source_path -> 索引列表）

        【设计要点】
        - 索引从 0 开始，每次添加时自增
        - _source_map 存储每个文件对应的所有 Chunk 索引
        - 时间复杂度：O(n)，n 为新添加的 Chunk 数量

        【注意事项】
        - chunks 和 vectors 长度必须一致
        - 重复的 chunk_id 会被覆盖
        """
        # 遍历 chunks 和 vectors
        for i, chunk in enumerate(chunks):
            # 获取新索引（当前列表长度）
            idx = len(self._chunks)
            # 添加 Chunk
            self._chunks.append(chunk)
            # 添加向量
            self._vectors.append(vectors[i])
            # 更新 chunk_id 映射
            self._chunk_id_map[chunk.chunk_id] = idx

            # 更新 source_path 映射
            source = chunk.source_path
            if source not in self._source_map:
                self._source_map[source] = []
            self._source_map[source].append(idx)

    async def delete(self, chunk_ids: list[str]) -> None:
        """
        根据 chunk_id 删除

        【参数说明】
        - chunk_ids: list[str] - 要删除的 chunk_id 列表

        【执行流程】
        1. 查找所有要删除的索引
        2. 如果没有要删除的索引，直接返回
        3. 过滤 _chunks 和 _vectors（保留不在删除列表中的元素）
        4. 重建 _chunk_id_map（重新映射索引）
        5. 重建 _source_map（重新映射来源路径）

        【设计要点】
        - 使用集合存储要删除的索引，加速查找
        - 重建索引映射时，使用字典推导式
        - 时间复杂度：O(n)，n 为总 Chunk 数量

        【注意事项】
        - 不存在的 chunk_id 会被忽略
        - 删除后索引会重新排列
        """
        # 收集要删除的索引
        indices_to_remove = set()
        for chunk_id in chunk_ids:
            if chunk_id in self._chunk_id_map:
                indices_to_remove.add(self._chunk_id_map[chunk_id])

        # 如果没有要删除的索引，直接返回
        if not indices_to_remove:
            return

        # 过滤 _chunks（保留不在删除列表中的元素）
        self._chunks = [
            c for i, c in enumerate(self._chunks) if i not in indices_to_remove
        ]
        # 过滤 _vectors（保留不在删除列表中的元素）
        self._vectors = [
            v for i, v in enumerate(self._vectors) if i not in indices_to_remove
        ]

        # 重建 _chunk_id_map（重新映射索引）
        self._chunk_id_map = {c.chunk_id: i for i, c in enumerate(self._chunks)}

        # 重建 _source_map（重新映射来源路径）
        self._source_map = {}
        for i, chunk in enumerate(self._chunks):
            source = chunk.source_path
            if source not in self._source_map:
                self._source_map[source] = []
            self._source_map[source].append(i)

    async def delete_by_source(self, source_path: str) -> None:
        """
        根据来源路径删除

        【参数说明】
        - source_path: str - 来源文件路径

        【执行流程】
        1. 检查 source_path 是否存在于 _source_map
        2. 如果不存在，直接返回
        3. 获取要删除的索引列表
        4. 过滤 _chunks 和 _vectors（保留不在删除列表中的元素）
        5. 重建 _chunk_id_map（重新映射索引）
        6. 重建 _source_map（重新映射来源路径）

        【设计要点】
        - 使用 _source_map 快速定位要删除的索引
        - 时间复杂度：O(n)，n 为总 Chunk 数量

        【注意事项】
        - 不存在的 source_path 会被忽略
        - 删除后索引会重新排列

        【使用场景】
        - 文件更新时，删除旧的索引，然后重新添加新的索引
        """
        # 检查 source_path 是否存在
        if source_path not in self._source_map:
            return

        # 获取要删除的索引列表
        indices_to_remove = set(self._source_map[source_path])
        
        # 过滤 _chunks（保留不在删除列表中的元素）
        self._chunks = [
            c for i, c in enumerate(self._chunks) if i not in indices_to_remove
        ]
        # 过滤 _vectors（保留不在删除列表中的元素）
        self._vectors = [
            v for i, v in enumerate(self._vectors) if i not in indices_to_remove
        ]

        # 重建 _chunk_id_map（重新映射索引）
        self._chunk_id_map = {c.chunk_id: i for i, c in enumerate(self._chunks)}

        # 重建 _source_map（重新映射来源路径）
        self._source_map = {}
        for i, chunk in enumerate(self._chunks):
            source = chunk.source_path
            if source not in self._source_map:
                self._source_map[source] = []
            self._source_map[source].append(i)

    async def search(
        self, query_vector: list[float], top_k: int = 5,
        filters: dict[str, Any] | None = None
    ) -> list[tuple[Chunk, float]]:
        """
        向量相似度检索

        【参数说明】
        - query_vector: list[float] - 查询向量
        - top_k: int - 返回前 K 个结果（默认 5）
        - filters: dict[str, Any] | None - 过滤器（如 {"source_path": "src/main.py"}）

        【返回值】
        - list[tuple[Chunk, float]]: (Chunk, 相似度分数) 列表，按相似度降序排列

        【执行流程】
        1. 如果没有向量，返回空列表
        2. 计算查询向量与所有存储向量的余弦相似度
        3. 按相似度降序排序
        4. 取前 top_k 个结果
        5. 应用过滤器（可选）
        6. 返回结果

        【过滤器支持】
        - source_path: 过滤指定文件的 Chunk
        - symbol: 过滤指定符号的 Chunk

        【时间复杂度】
        - O(n)，n 为总 Chunk 数量（需要计算与所有向量的相似度）

        【注意事项】
        - 相似度分数范围：0（完全不相似）到 1（完全相同）
        - 查询向量为空时返回空列表

        【设计目的】
        根据查询向量检索最相似的 Chunk，
        支持过滤器精确定位。
        """
        # 如果没有向量，返回空列表
        if not self._vectors:
            return []

        # 计算查询向量与所有存储向量的余弦相似度
        scores: list[tuple[int, float]] = []
        for i, vector in enumerate(self._vectors):
            score = self._cosine_similarity(query_vector, vector)
            scores.append((i, score))

        # 按相似度降序排序
        scores.sort(key=lambda x: x[1], reverse=True)

        # 取前 top_k 个结果
        results: list[tuple[Chunk, float]] = []
        for idx, score in scores[:top_k]:
            chunk = self._chunks[idx]
            # 应用过滤器
            if filters:
                if "source_path" in filters and chunk.source_path != filters["source_path"]:
                    continue
                if "symbol" in filters and chunk.symbol != filters["symbol"]:
                    continue
            results.append((chunk, score))

        return results

    async def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        """
        根据 chunk_id 列表获取 Chunk

        【参数说明】
        - chunk_ids: list[str] - chunk_id 列表

        【返回值】
        - list[Chunk]: Chunk 对象列表（不存在的 id 会被跳过）

        【执行流程】
        1. 遍历 chunk_ids
        2. 通过 _chunk_id_map 查找索引
        3. 从 _chunks 获取 Chunk 对象
        4. 返回结果列表

        【时间复杂度】
        O(k)，k 为 chunk_ids 的数量（每次查找是 O(1) 哈希查找）
        """
        result: list[Chunk] = []
        for chunk_id in chunk_ids:
            if chunk_id in self._chunk_id_map:
                result.append(self._chunks[self._chunk_id_map[chunk_id]])
        return result

    async def search_by_text(
        self, query: str, top_k: int = 5
    ) -> list[tuple[Chunk, float]]:
        """
        关键词搜索 - 在所有 Chunk 中做精确文本匹配

        【参数说明】
        - query: str - 查询文本
        - top_k: int - 返回前 K 个结果（默认 5）

        【返回值】
        - list[tuple[Chunk, float]]: (Chunk, 关键词匹配分数) 列表

        【执行流程】
        1. 提取查询中的关键词（正则 \w+）
        2. 遍历所有 Chunk，计算关键词匹配分数
        3. 按分数降序排序，返回前 top_k 个结果

        【评分算法】
        - 每个匹配的关键词 +1.0 分
        - 额外出现的关键词 +0.1 分（鼓励关键词密集的 Chunk）
        - 最终分数归一化到 [0, 1]

        【设计目的】
        用于 Adaptive RAG 的 grep 策略：
        代码标识符（如 "AuthService"）用关键词搜索比语义检索更准确。
        """
        import re

        # 提取查询中的关键词
        keywords = re.findall(r"\w+", query.lower())
        if not keywords:
            return []

        results: list[tuple[Chunk, float]] = []
        for chunk in self._chunks:
            chunk_text = chunk.text.lower()
            score = 0.0
            for kw in keywords:
                if kw in chunk_text:
                    score += 1.0
                    score += chunk_text.count(kw) * 0.1
            if score > 0:
                results.append((chunk, min(score / len(keywords), 1.0)))

        # 按分数降序排序
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """
        计算两个向量的余弦相似度

        【参数说明】
        - a: list[float] - 向量 a
        - b: list[float] - 向量 b

        【返回值】
        - float: 余弦相似度（0 到 1）

        【数学公式】
        cos(θ) = (a · b) / (||a|| × ||b||)
        - a · b: 向量点积
        - ||a||: 向量 a 的模（L2 范数）
        - ||b||: 向量 b 的模（L2 范数）

        【相似度解读】
        - 1.0: 完全相同（方向一致）
        - 0.0: 完全不相似（垂直）
        - -1.0: 完全相反（方向相反）

        【执行流程】
        1. 计算向量点积
        2. 计算向量 a 的模
        3. 计算向量 b 的模
        4. 如果任一向量模为 0，返回 0.0
        5. 返回点积除以模的乘积

        【注意事项】
        - 向量长度必须一致
        - 空向量返回 0.0
        """
        # 计算向量点积
        dot = sum(x * y for x, y in zip(a, b))
        # 计算向量 a 的模（L2 范数）
        norm_a: float = sum(x * x for x in a) ** 0.5
        # 计算向量 b 的模（L2 范数）
        norm_b: float = sum(x * x for x in b) ** 0.5
        # 如果任一向量模为 0，返回 0.0
        if norm_a == 0 or norm_b == 0:
            return 0.0
        # 返回余弦相似度
        return float(dot / (norm_a * norm_b))

    def save(self, path: Path) -> None:
        """
        持久化到磁盘

        【参数说明】
        - path: Path - 存储目录路径

        【执行流程】
        1. 创建存储目录（如果不存在）
        2. 将 _chunks 转换为字典列表
        3. 保存到 chunks.json
        4. 将 _vectors 保存到 vectors.json

        【持久化格式】
        - chunks.json: Chunk 对象列表（使用 model_dump 序列化）
        - vectors.json: 向量列表（直接序列化）

        【设计目的】
        将向量存储保存到磁盘，便于下次启动时恢复。

        【注意事项】
        - 目录不存在时会自动创建
        - 使用 UTF-8 编码
        - chunks.json 使用 indent=2 格式化输出
        """
        # 创建存储目录（如果不存在）
        path.mkdir(parents=True, exist_ok=True)

        # 将 _chunks 转换为字典列表
        chunks_data = [c.model_dump() for c in self._chunks]
        # 保存到 chunks.json
        with open(path / "chunks.json", "w", encoding="utf-8") as f:
            json.dump(chunks_data, f, ensure_ascii=False, indent=2)

        # 将 _vectors 保存到 vectors.json
        with open(path / "vectors.json", "w", encoding="utf-8") as f:
            json.dump(self._vectors, f, ensure_ascii=False)

    def load(self, path: Path) -> None:
        """
        从磁盘加载

        【参数说明】
        - path: Path - 存储目录路径

        【执行流程】
        1. 检查目录是否存在
        2. 如果 chunks.json 存在，加载并转换为 Chunk 对象列表
        3. 如果 vectors.json 存在，加载向量列表
        4. 重建 _chunk_id_map
        5. 重建 _source_map

        【设计目的】
        从磁盘加载之前保存的向量存储。

        【注意事项】
        - 目录不存在时直接返回
        - 文件不存在时跳过
        - 加载后重建索引映射
        """
        # 检查目录是否存在
        if not path.exists():
            return

        # 定义文件路径
        chunks_path = path / "chunks.json"
        vectors_path = path / "vectors.json"

        # 加载 chunks.json
        if chunks_path.exists():
            with open(chunks_path, "r", encoding="utf-8") as f:
                chunks_data = json.load(f)
            # 将字典列表转换为 Chunk 对象列表
            self._chunks = [Chunk(**c) for c in chunks_data]

        # 加载 vectors.json
        if vectors_path.exists():
            with open(vectors_path, "r", encoding="utf-8") as f:
                self._vectors = json.load(f)

        # 重建 _chunk_id_map
        self._chunk_id_map = {c.chunk_id: i for i, c in enumerate(self._chunks)}

        # 重建 _source_map
        self._source_map = {}
        for i, chunk in enumerate(self._chunks):
            source = chunk.source_path
            if source not in self._source_map:
                self._source_map[source] = []
            self._source_map[source].append(i)


def get_vector_store(config: dict[str, Any] | None = None) -> VectorStore:
    """
    创建向量存储实例

    【参数说明】
    - config: dict[str, Any] | None - 配置（当前未使用）

    【返回值】
    - VectorStore: 向量存储实例

    【设计目的】
    提供统一的向量存储创建接口，
    便于后续扩展为其他向量数据库实现。

    【当前实现】
    返回 MemoryVectorStore（内存向量存储）

    【未来扩展】
    可根据配置返回其他向量数据库实现（如 Milvus、Pinecone、Redis 等）

    【示例】
    ```python
    vector_store = get_vector_store()
    # 返回 MemoryVectorStore 实例
    ```
    """
    # 当前返回 MemoryVectorStore（内存向量存储）
    return MemoryVectorStore()
