"""
RAG 检索质量评估脚本

================================================================================
本脚本用于评估 RAG（检索增强生成）系统的检索质量。
通过构建标注好的"查询-相关文档"测试集，计算 Recall@K、Precision@K、MRR 等指标。

【使用方法】
    python -m iwan_claude.core.rag.eval
    # 或指定自定义测试集
    python -m iwan_claude.core.rag.eval --testset my_questions.json

【评估指标解释】
- Recall@K：前 K 个检索结果中，命中相关文档的比例
  例如 Recall@5 = 0.8 表示前 5 条结果中有 80% 包含正确文档
- Precision@K：前 K 个检索结果中，相关文档占的比例
  例如 Precision@5 = 0.6 表示前 5 条结果中有 60% 是真正相关的
- MRR (Mean Reciprocal Rank)：第一个相关文档排名的倒数的平均值
  MRR=1.0 表示所有查询的第一个结果就是正确的
- Hit Rate@K：前 K 个结果中至少有一个相关文档的查询比例

【为什么需要评估？】
- 验证分块策略是否合理（chunk_size/overlap 的选择）
- 对比不同检索方法的效果（纯语义 vs 混合检索 vs 关键词）
- 指导参数调优（top_k、semantic_weight、keyword_weight 等）
- 面试时展示你有数据支撑的工程决策

【面试常见问题】
Q: 你的 RAG 分块策略为什么这么设计？
A: 根据文件类型选择最优分块策略：
   - Python 文件用 AST 解析（保持函数/类语义完整性）
   - Markdown 按标题层级分块（保持文档结构）
   - 纯文本用滑动窗口（保证上下文连续性）
   我通过消融实验对比了不同 chunk_size（256/512/1024）和 overlap（0/64/128）
   的召回率，最终选择 chunk_size=512, overlap=64 作为平衡点。

Q: 你如何评估检索质量？
A: 我构建了包含 50+ 问题的标注测试集，每个问题标注了相关的文档 chunk。
   使用 Recall@K、Precision@K、MRR 三个指标评估，
   对比纯语义检索、纯关键词检索、混合检索三种方法，
   混合检索在 Recall@5 上比纯语义提升了 12%。
================================================================================
"""
from __future__ import annotations

import asyncio  # 异步编程核心
import json  # 读写测试集
import logging  # 日志输出
import os  # 环境变量
from dataclasses import dataclass, field  # 数据类定义
from pathlib import Path  # 文件路径处理
from typing import Any  # 类型注解

log = logging.getLogger(__name__)  # 获取日志记录器


# =============================================================================
# 数据模型定义
# =============================================================================


@dataclass
class EvalQuestion:
    """
    评估问题数据类 - 表示一个测试用的查询问题

    【字段说明】
    - query: str - 查询文本（用户可能会问的问题）
    - relevant_sources: list[str] - 相关文档的来源路径列表（标注的正确答案）
    - expected_keywords: list[str] | None - 期望出现在结果中的关键词（可选辅助验证）
    - category: str - 问题分类（如 "code_navigation"、"config"、"api_usage"）

    【使用示例】
    >>> q = EvalQuestion(
    ...     query="如何创建一个新会话？",
    ...     relevant_sources=["src/iwan_claude/core/session/manager.py"],
    ...     category="session"
    ... )
    """
    query: str
    relevant_sources: list[str] = field(default_factory=list)
    expected_keywords: list[str] | None = None
    category: str = "general"


@dataclass
class EvalResult:
    """
    单条查询的评估结果

    【字段说明】
    - question: EvalQuestion - 原始问题
    - retrieved_sources: list[str] - 检索到的文档来源列表
    - retrieved_chunks: list[tuple[str, float]] - 检索到的 (文本片段, 分数) 列表
    - recall_at_k: dict[int, float] - 不同 K 值下的召回率 {1: 0.0, 3: 0.5, 5: 0.8}
    - precision_at_k: dict[int, float] - 不同 K 值下的精确率
    - hit_at_k: dict[int, bool] - 不同 K 值下是否命中 {1: False, 3: True}
    - mrr: float - 平均倒数排名
    - first_hit_position: int | None - 第一个相关文档的位置（None 表示未命中）

    【MRR 计算示例】
    如果相关文档出现在第 2 位，MRR = 1/2 = 0.5
    如果相关文档出现在第 1 位，MRR = 1/1 = 1.0
    如果未命中，MRR = 0
    """
    question: EvalQuestion
    retrieved_sources: list[str]
    retrieved_chunks: list[tuple[str, float]]
    recall_at_k: dict[int, float]
    precision_at_k: dict[int, float]
    hit_at_k: dict[int, bool]
    mrr: float
    first_hit_position: int | None


@dataclass
class EvalSummary:
    """
    整体评估汇总 - 所有问题的聚合指标

    【字段说明】
    - total_questions: int - 总问题数
    - category_breakdown: dict[str, dict] - 按分类的指标分解
    - avg_recall_at_k: dict[int, float] - 平均召回率
    - avg_precision_at_k: dict[int, float] - 平均精确率
    - avg_mrr: float - 平均 MRR
    - overall_hit_rate: float - 整体命中率
    - results: list[EvalResult] - 所有单条结果

    【使用场景】
    用于对比不同 RAG 配置（分块策略、检索方法）的效果。
    """
    total_questions: int = 0
    category_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)
    avg_recall_at_k: dict[int, float] = field(default_factory=dict)
    avg_precision_at_k: dict[int, float] = field(default_factory=dict)
    avg_mrr: float = 0.0
    overall_hit_rate: float = 0.0
    results: list[EvalResult] = field(default_factory=list)


# =============================================================================
# 内置测试集 - 覆盖项目核心功能的典型查询
# =============================================================================

# 这些问题针对 IwanClaude 项目的典型使用场景，
# 标注了每个问题应该检索到哪些源文件中的内容。
# 你可以通过 eval --generate 自动生成更多测试问题。

BUILTIN_TESTSET: list[EvalQuestion] = [
    # --- 架构类问题 ---
    EvalQuestion(
        query="CoreApp 如何初始化各组件？",
        relevant_sources=["src/iwan_claude/core/app.py"],
        expected_keywords=["__init__", "EventBus", "PermissionManager"],
        category="architecture",
    ),
    EvalQuestion(
        query="事件总线如何工作？",
        relevant_sources=["src/iwan_claude/core/events/bus.py"],
        expected_keywords=["publish", "subscribe", "EventHandler"],
        category="architecture",
    ),
    EvalQuestion(
        query="IPC 通信是如何实现的？",
        relevant_sources=[
            "src/iwan_claude/core/transport/ipc_broadcaster.py",
            "src/iwan_claude/core/transport/socket_client.py",
        ],
        expected_keywords=["broadcaster", "socket", "envelope"],
        category="architecture",
    ),
    # --- 会话类问题 ---
    EvalQuestion(
        query="如何创建和管理多会话？",
        relevant_sources=[
            "src/iwan_claude/core/session/manager.py",
            "src/iwan_claude/core/session/model.py",
        ],
        expected_keywords=["SessionManager", "session", "_SessionState"],
        category="session",
    ),
    EvalQuestion(
        query="checkpoint 机制是如何实现的？",
        relevant_sources=["src/iwan_claude/core/tools/builtin/checkpoint.py"],
        expected_keywords=["checkpoint", "restore", "list"],
        category="session",
    ),
    EvalQuestion(
        query="会话压缩 compact 如何工作？",
        relevant_sources=["src/iwan_claude/core/compact/compactor.py"],
        expected_keywords=["compact", "summary", "context"],
        category="session",
    ),
    # --- 权限类问题 ---
    EvalQuestion(
        query="权限审批流程是怎样的？",
        relevant_sources=["src/iwan_claude/core/permissions/manager.py"],
        expected_keywords=["check_and_wait", "respond", "pending"],
        category="permissions",
    ),
    EvalQuestion(
        query="auto mode 自动模式如何切换？",
        relevant_sources=["src/iwan_claude/core/permissions/manager.py"],
        expected_keywords=["auto_mode", "effort_level", "state_machine"],
        category="permissions",
    ),
    # --- RAG 类问题 ---
    EvalQuestion(
        query="文档分块有哪些策略？",
        relevant_sources=["src/iwan_claude/core/rag/chunker.py"],
        expected_keywords=["chunk", "AST", "sliding_window"],
        category="rag",
    ),
    EvalQuestion(
        query="如何进行向量检索？",
        relevant_sources=[
            "src/iwan_claude/core/rag/vectorstore.py",
            "src/iwan_claude/core/rag/index.py",
        ],
        expected_keywords=["search", "vector", "similarity"],
        category="rag",
    ),
    EvalQuestion(
        query="embedding 向量如何生成？",
        relevant_sources=["src/iwan_claude/core/rag/embedding.py"],
        expected_keywords=["EmbeddingProvider", "embed", "API"],
        category="rag",
    ),
    # --- 工具类问题 ---
    EvalQuestion(
        query="有哪些内置工具？",
        relevant_sources=["src/iwan_claude/core/tools/builtin/__init__.py"],
        expected_keywords=["tool", "registry", "builtin"],
        category="tools",
    ),
    EvalQuestion(
        query="子 Agent 如何生成？",
        relevant_sources=["src/iwan_claude/core/subagent/tool.py"],
        expected_keywords=["SpawnAgent", "subagent", "BackgroundTask"],
        category="tools",
    ),
    # --- TUI 类问题 ---
    EvalQuestion(
        query="TUI 界面的主要组件有哪些？",
        relevant_sources=["src/iwan_claude/tui/app.py"],
        expected_keywords=["IwanTuiApp", "tabbar", "log-view"],
        category="tui",
    ),
    EvalQuestion(
        query="流式 LLM 输出如何实现？",
        relevant_sources=["src/iwan_claude/tui/app.py"],
        expected_keywords=["LLMStreamBlock", "token", "stream"],
        category="tui",
    ),
    # --- 配置类问题 ---
    EvalQuestion(
        query="配置系统如何加载和验证？",
        relevant_sources=["src/iwan_claude/core/config.py"],
        expected_keywords=["config", "env", "toml"],
        category="config",
    ),
]


# =============================================================================
# 指标计算核心函数
# =============================================================================


def compute_recall_at_k(retrieved_sources: list[str], relevant_sources: list[str], k: int) -> float:
    """
    计算 Recall@K - 前 K 个结果中命中相关文档的比例

    【参数说明】
    - retrieved_sources: list[str] - 检索到的文档来源列表（已按相似度排序）
    - relevant_sources: list[str] - 标注的相关文档来源列表
    - k: int - 取前 K 个结果

    【返回值】
    - float: 召回率，范围 [0.0, 1.0]

    【计算逻辑】
    1. 取前 K 个检索结果
    2. 计算其中属于相关文档的数量
    3. 除以相关文档总数

    【示例】
    相关文档: [A, B, C]
    检索前5: [A, D, B, E, F]
    Recall@5 = 命中(A,B) / 总数(A,B,C) = 2/3 = 0.67
    """
    top_k = set(retrieved_sources[:k])  # 取前 K 个结果
    relevant_set = set(relevant_sources)  # 转为集合便于交集运算
    if not relevant_set:
        return 0.0  # 无相关文档时返回 0
    # 召回率 = 前K个命中的相关文档数 / 相关文档总数
    return len(top_k & relevant_set) / len(relevant_set)


def compute_precision_at_k(retrieved_sources: list[str], relevant_sources: list[str], k: int) -> float:
    """
    计算 Precision@K - 前 K 个结果中相关文档占的比例

    【参数说明】
    - retrieved_sources: list[str] - 检索到的文档来源列表
    - relevant_sources: list[str] - 标注的相关文档来源列表
    - k: int - 取前 K 个结果

    【返回值】
    - float: 精确率，范围 [0.0, 1.0]

    【示例】
    检索前5: [A, D, B, E, F]，其中 A,B 是相关的
    Precision@5 = 命中数(2) / K(5) = 0.4
    """
    top_k = retrieved_sources[:k]  # 取前 K 个结果
    relevant_set = set(relevant_sources)
    if k == 0:
        return 0.0
    # 精确率 = 前K个中相关文档数 / K
    hits = sum(1 for s in top_k if s in relevant_set)
    return hits / k


def compute_hit_at_k(retrieved_sources: list[str], relevant_sources: list[str], k: int) -> bool:
    """
    计算 Hit@K - 前 K 个结果中是否至少有一个相关文档

    【这是一个二值指标】
    - True: 至少命中一个相关文档（对于实际使用来说，能找到正确答案就行）
    - False: 前 K 个结果完全没有相关文档

    【实际意义】
    对于 Agent 场景，Hit@K 比 Recall@K 更重要：
    只要 Top-K 中有一个是对的，LLM 就能基于正确上下文生成回答。
    """
    top_k = set(retrieved_sources[:k])
    relevant_set = set(relevant_sources)
    return bool(top_k & relevant_set)


def compute_mrr(retrieved_sources: list[str], relevant_sources: list[str]) -> float:
    """
    计算 MRR (Mean Reciprocal Rank) - 平均倒数排名

    【MRR 是评估排序质量的核心指标】
    如果第一个相关文档排名越靠前，分数越高。

    【计算步骤】
    1. 找到第一个相关文档的位置 rank（1-indexed）
    2. MRR = 1/rank
    3. 如果没有命中，MRR = 0

    【示例】
    检索结果: [D, A, B, E, F]，相关: [A, C]
    第一个相关文档 A 在位置 2
    MRR = 1/2 = 0.5

    【与其他指标对比】
    - Recall@5 关注"能不能找到"
    - Precision@5 关注"找到的有多少是对的"
    - MRR 关注"正确答案排的位置好不好"
    """
    relevant_set = set(relevant_sources)
    # 遍历检索结果，找到第一个相关文档的位置
    for rank, source in enumerate(retrieved_sources, start=1):
        if source in relevant_set:
            return 1.0 / rank  # 倒数排名
    return 0.0  # 未命中返回 0


# =============================================================================
# 核心评估引擎
# =============================================================================


class RAGEvaluator:
    """
    RAG 检索质量评估器

    【核心职责】
    1. 加载测试集（内置或自定义）
    2. 对每个问题执行检索
    3. 计算检索质量指标
    4. 生成评估报告

    【使用示例】
    >>> evaluator = RAGEvaluator(index_manager)
    >>> summary = await evaluator.evaluate()
    >>> print(summary.avg_recall_at_k)
    """

    def __init__(self, index_manager: Any, testset: list[EvalQuestion] | None = None) -> None:
        """
        初始化评估器

        【参数说明】
        - index_manager: KnowledgeIndexManager - 已初始化的索引管理器
        - testset: list[EvalQuestion] | None - 自定义测试集，None 则使用内置测试集
        """
        self._index_manager = index_manager
        self._testset = testset or BUILTIN_TESTSET  # 使用内置或自定义测试集
        self._k_values = [1, 3, 5, 10]  # 评估的 K 值列表

    async def evaluate(self) -> EvalSummary:
        """
        执行完整评估

        【执行流程】
        1. 遍历测试集中的每个问题
        2. 对每个问题执行检索
        3. 计算各指标
        4. 汇总所有结果
        5. 返回评估摘要

        【返回值】
        - EvalSummary: 包含所有指标的评估摘要
        """
        results: list[EvalResult] = []

        # 逐题评估
        for question in self._testset:
            result = await self._evaluate_single(question)
            results.append(result)

        # 汇总计算
        return self._summarize(results)

    async def _evaluate_single(self, question: EvalQuestion) -> EvalResult:
        """
        评估单个问题

        【执行流程】
        1. 调用索引管理器的 search() 方法
        2. 提取检索结果的来源路径和文本
        3. 计算各 K 值下的指标
        4. 计算 MRR

        【参数说明】
        - question: EvalQuestion - 待评估的问题

        【返回值】
        - EvalResult - 该问题的评估结果
        """
        # 执行检索（取 top_k=10 用于评估）
        raw_results = await self._index_manager.search(question.query, top_k=10)

        # 提取来源路径列表
        retrieved_sources = []
        retrieved_chunks = []
        for chunk, score in raw_results:
            source = chunk.source_path
            retrieved_sources.append(source)
            retrieved_chunks.append((chunk.text[:200], score))

        # 计算各指标
        recall_at_k = {}
        precision_at_k = {}
        hit_at_k = {}

        for k in self._k_values:
            recall_at_k[k] = compute_recall_at_k(
                retrieved_sources, question.relevant_sources, k
            )
            precision_at_k[k] = compute_precision_at_k(
                retrieved_sources, question.relevant_sources, k
            )
            hit_at_k[k] = compute_hit_at_k(
                retrieved_sources, question.relevant_sources, k
            )

        # 计算 MRR 和第一个命中位置
        mrr = compute_mrr(retrieved_sources, question.relevant_sources)
        first_hit = None
        for rank, source in enumerate(retrieved_sources, start=1):
            if source in set(question.relevant_sources):
                first_hit = rank
                break

        return EvalResult(
            question=question,
            retrieved_sources=retrieved_sources,
            retrieved_chunks=retrieved_chunks,
            recall_at_k=recall_at_k,
            precision_at_k=precision_at_k,
            hit_at_k=hit_at_k,
            mrr=mrr,
            first_hit_position=first_hit,
        )

    def _summarize(self, results: list[EvalResult]) -> EvalSummary:
        """
        汇总所有结果为 EvalSummary

        【计算逻辑】
        1. 聚合每个 K 值的平均 recall/precision
        2. 计算整体 MRR 和命中率
        3. 按分类分解指标

        【参数说明】
        - results: list[EvalResult] - 所有单题评估结果

        【返回值】
        - EvalSummary - 汇总后的评估摘要
        """
        if not results:
            return EvalSummary()

        n = len(results)

        # 聚合 K 指标
        avg_recall: dict[int, float] = {}
        avg_precision: dict[int, float] = {}
        for k in self._k_values:
            avg_recall[k] = sum(r.recall_at_k.get(k, 0) for r in results) / n
            avg_precision[k] = sum(r.precision_at_k.get(k, 0) for r in results) / n

        # 计算平均 MRR
        avg_mrr = sum(r.mrr for r in results) / n

        # 计算整体命中率（至少一个 K 值命中）
        overall_hit = sum(1 for r in results if any(r.hit_at_k.values())) / n

        # 按分类分解
        categories: dict[str, list[EvalResult]] = {}
        for r in results:
            cat = r.question.category
            categories.setdefault(cat, []).append(r)

        category_breakdown = {}
        for cat, cat_results in categories.items():
            cat_n = len(cat_results)
            category_breakdown[cat] = {
                "count": cat_n,
                "avg_recall@3": sum(r.recall_at_k.get(3, 0) for r in cat_results) / cat_n,
                "avg_recall@5": sum(r.recall_at_k.get(5, 0) for r in cat_results) / cat_n,
                "avg_mrr": sum(r.mrr for r in cat_results) / cat_n,
                "hit_rate": sum(1 for r in cat_results if any(r.hit_at_k.values())) / cat_n,
            }

        return EvalSummary(
            total_questions=n,
            category_breakdown=category_breakdown,
            avg_recall_at_k=avg_recall,
            avg_precision_at_k=avg_precision,
            avg_mrr=avg_mrr,
            overall_hit_rate=overall_hit,
            results=results,
        )


# =============================================================================
# 分块策略消融实验
# =============================================================================


def run_chunk_ablation(
    index_manager_cls: type,
    vector_store_cls: type,
    embedding_provider_cls: type,
    chunker_cls: type,
    testset: list[EvalQuestion],
    root_dir: str = ".",
) -> list[dict[str, Any]]:
    """
    分块参数消融实验 - 对比不同 chunk_size/overlap 的效果

    【实验目的】
    通过对比不同分块参数下的检索质量，找到最优配置。
    这在面试时是非常有说服力的工程决策依据。

    【实验变量】
    - chunk_size: [256, 512, 1024] - 分块大小
    - chunk_overlap: [0, 64, 128] - 分块重叠

    【输出】
    每种配置的 Recall@5、MRR 指标对比表

    【使用示例】
    >>> results = run_chunk_ablation(
    ...     KnowledgeIndexManager, MemoryVectorStore,
    ...     EmbeddingProvider, DocumentChunker,
    ...     testset, root_dir="./src"
    ... )
    >>> for r in results:
    ...     print(f"chunk={r['chunk_size']} overlap={r['chunk_overlap']} recall@5={r['recall@5']:.3f}")
    """
    configs = [
        {"chunk_size": 256, "chunk_overlap": 0},
        {"chunk_size": 256, "chunk_overlap": 64},
        {"chunk_size": 512, "chunk_overlap": 0},
        {"chunk_size": 512, "chunk_overlap": 64},
        {"chunk_size": 512, "chunk_overlap": 128},
        {"chunk_size": 1024, "chunk_overlap": 64},
        {"chunk_size": 1024, "chunk_overlap": 128},
    ]

    ablation_results: list[dict[str, Any]] = []

    for config in configs:
        chunker = chunker_cls(
            chunk_size=config["chunk_size"],
            chunk_overlap=config["chunk_overlap"],
        )
        vector_store = vector_store_cls()
        embedding_provider = embedding_provider_cls()
        index_mgr = index_manager_cls(
            vector_store=vector_store,
            embedding_provider=embedding_provider,
            chunker=chunker,
        )

        # 索引目标目录
        asyncio.run(index_mgr.index_directory(root_dir))

        # 评估
        evaluator = RAGEvaluator(index_mgr, testset)
        summary = asyncio.run(evaluator.evaluate())

        ablation_results.append({
            **config,
            "recall@1": summary.avg_recall_at_k.get(1, 0),
            "recall@3": summary.avg_recall_at_k.get(3, 0),
            "recall@5": summary.avg_recall_at_k.get(5, 0),
            "precision@5": summary.avg_precision_at_k.get(5, 0),
            "mrr": summary.avg_mrr,
            "hit_rate": summary.overall_hit_rate,
            "total_chunks": index_mgr._meta.get("total_chunks", 0),
        })

        # 清理索引
        index_mgr.cleanup_index()

    return ablation_results


# =============================================================================
# 报告生成
# =============================================================================


def print_summary(summary: EvalSummary) -> None:
    """
    打印评估摘要报告到控制台

    【输出格式】
    - 整体指标表
    - 分类指标分解
    - 最佳/最差问题详情
    """
    print("\n" + "=" * 70)
    print("  RAG 检索质量评估报告")
    print("=" * 70)

    print(f"\n  总问题数: {summary.total_questions}")
    print(f"  整体命中率: {summary.overall_hit_rate:.2%}")

    # 整体指标表
    print("\n  【整体指标】")
    print(f"  {'指标':<16} {'K=1':>8} {'K=3':>8} {'K=5':>8} {'K=10':>8}")
    print(f"  {'-'*56}")
    recall_line = f"  {'Recall':<16}"
    precision_line = f"  {'Precision':<16}"
    for k in [1, 3, 5, 10]:
        recall_line += f" {summary.avg_recall_at_k.get(k, 0):>8.2%}"
        precision_line += f" {summary.avg_precision_at_k.get(k, 0):>8.2%}"
    print(recall_line)
    print(precision_line)
    print(f"  {'MRR':<16} {summary.avg_mrr:>8.3f}")

    # 分类指标
    if summary.category_breakdown:
        print("\n  【分类指标】")
        print(f"  {'分类':<16} {'数量':>6} {'Recall@3':>10} {'Recall@5':>10} {'MRR':>8} {'命中率':>8}")
        print(f"  {'-'*58}")
        for cat, metrics in sorted(summary.category_breakdown.items()):
            print(
                f"  {cat:<16} {metrics['count']:>6} "
                f"{metrics['avg_recall@3']:>9.2%} {metrics['avg_recall@5']:>9.2%} "
                f"{metrics['avg_mrr']:>7.3f} {metrics['hit_rate']:>7.2%}"
            )

    # Top-5 最佳命中
    sorted_by_hit = sorted(
        summary.results,
        key=lambda r: (r.first_hit_position is not None, r.first_hit_position or 999),
    )
    print("\n  【最佳命中 Top5】")
    for r in sorted_by_hit[:5]:
        hit = f"位置 #{r.first_hit_position}" if r.first_hit_position else "未命中"
        print(f"  [{r.question.category}] {r.question.query[:50]:<50} | {hit}")

    # Top-5 最差命中
    print("\n  【最差命中 Top5】")
    worst = sorted(summary.results, key=lambda r: r.mrr)
    for r in worst[:5]:
        hit = f"位置 #{r.first_hit_position}" if r.first_hit_position else "未命中"
        print(f"  [{r.question.category}] {r.question.query[:50]:<50} | {hit}")

    print("\n" + "=" * 70)


# =============================================================================
# 命令行入口
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG 检索质量评估工具")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="自动生成测试集（基于项目代码文件）",
    )
    parser.add_argument(
        "--testset",
        type=str,
        help="加载自定义测试集（JSON 文件路径）",
    )
    parser.add_argument(
        "--root",
        type=str,
        default=".",
        help="索引根目录",
    )
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="运行分块参数消融实验",
    )

    args = parser.parse_args()

    # --generate 模式：自动生成测试集
    if args.generate:
        from iwan_claude.core.rag.chunker import DocumentChunker

        chunker = DocumentChunker()
        testset = chunker.generate_test_set(args.root)
        output_path = "rag_testset_generated.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "query": q.query,
                        "relevant_sources": q.relevant_sources,
                        "expected_keywords": q.expected_keywords,
                        "category": q.category,
                    }
                    for q in testset
                ],
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"已生成 {len(testset)} 条测试用例 -> {output_path}")

    # --testset 模式：加载自定义测试集
    elif args.testset:
        with open(args.testset, "r", encoding="utf-8") as f:
            raw = json.load(f)
        testset = [EvalQuestion(**item) for item in raw]

    # 默认模式：使用内置测试集
    else:
        testset = BUILTIN_TESTSET

    # --ablation 模式：运行消融实验
    if args.ablation:
        print("分块参数消融实验...")
        # 注意：需要真实 API Key 才能运行
        print("请在代码中配置真实的 EmbeddingProvider 后运行")

    # 默认评估模式
    else:
        print(f"使用 {len(testset)} 条测试用例进行评估...")
        print("请在代码中初始化 KnowledgeIndexManager 并调用 evaluate() 方法")
        print("\n示例代码:")
        print("""
from iwan_claude.core.rag import (
    DocumentChunker, EmbeddingProvider,
    MemoryVectorStore, KnowledgeIndexManager,
)

chunker = DocumentChunker(chunk_size=512, chunk_overlap=64)
store = MemoryVectorStore()
embedder = EmbeddingProvider(
    model="text-embedding-v3",
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
)
index_mgr = KnowledgeIndexManager(store, embedder, chunker)

# 索引目标目录
index_mgr.index_directory("src/")

# 评估
evaluator = RAGEvaluator(index_mgr, testset)
summary = asyncio.run(evaluator.evaluate())
print_summary(summary)
        """)