"""
轻量 LLM 客户端 - 用于 RAG 模块的 LLM 调用

【设计目的】
RAG 模块需要调用 LLM 完成轻量任务（生成上下文摘要、查询重写），
但主 LLMProvider.chat() 接口太重（需要 EventBus、run_id、tool_schemas、流式输出），
不适合这些简单的一次性调用。

本模块提供一个轻量的 LLM 客户端：
- 直接调用 OpenAI 兼容的 /chat/completions API
- 不依赖 EventBus / run_id
- 支持自定义 temperature 和 max_tokens
- 和 EmbeddingProvider 的设计风格一致（httpx + api_key_env）

【核心类】
- LLMClient: 轻量 LLM 客户端

【使用场景】
1. Contextual Retrieval：给每个 chunk 生成 50-100 token 的上下文摘要
2. 查询重写：将用户查询重写为多个语义变体，提高召回率
3. 查询分类（Adaptive RAG）：判断用户问题类型（简单/代码导航/语义检索）

【API 协议】
遵循 OpenAI 兼容的 Chat Completions API：
- 端点：{base_url}/chat/completions
- 请求方法：POST
- 请求体：{"model": "xxx", "messages": [...], "temperature": 0.0, "max_tokens": 200}
- 响应体：{"choices": [{"message": {"content": "..."}}]}
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class LLMClient:
    """
    轻量 LLM 客户端 - 用于 RAG 模块的简单 LLM 调用

    【学习要点】
    1. 轻量封装：只做 chat completions 调用，不涉及流式/工具/事件总线
    2. 可测试性：支持注入 mock http_client，便于单元测试
    3. 错误处理：API 调用失败时返回空字符串而非抛异常（RAG 不应因 LLM 失败而崩溃）
    4. 配置灵活：支持自定义模型、API 地址、温度、最大 token 数

    【核心方法】
    - complete(): 调用 LLM 生成文本（传入 messages，返回文本）

    【与 LLMProvider 的区别】
    | 特性       | LLMProvider       | LLMClient         |
    |-----------|-------------------|-------------------|
    | 流式输出    | ✅ 逐 token 发布事件 | ❌ 一次性返回       |
    | 工具调用    | ✅ 支持             | ❌ 不支持           |
    | EventBus  | ✅ 必须             | ❌ 不需要           |
    | 用途       | Agent 主对话       | RAG 辅助调用       |
    | 复杂度     | 高                 | 低                 |

    【设计目的】
    为 RAG 模块提供轻量的 LLM 调用能力，
    不引入对 EventBus / run_id 的依赖，
    便于在索引/检索阶段独立使用。
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str = "DEEPSEEK_API_KEY",
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        初始化轻量 LLM 客户端

        【参数说明】
        - model: str - LLM 模型名称（如 "deepseek-chat"）
        - base_url: str - API 基础地址（如 "https://api.deepseek.com/v1"）
        - api_key_env: str - API 密钥环境变量名（默认 "DEEPSEEK_API_KEY"）
        - http_client: httpx.AsyncClient | None - 自定义 HTTP 客户端（可选，用于测试）

        【环境变量优先级】
        1. api_key_env 指定的环境变量（如 DEEPSEEK_API_KEY）
        2. OPENAI_API_KEY（备选）

        【异常处理】
        - ValueError: base_url 为空或 API 密钥未设置

        【示例】
        ```python
        client = LLMClient(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1"
        )
        text = await client.complete([{"role": "user", "content": "Hello"}])
        ```
        """
        # 验证 base_url 是否为空
        if not base_url:
            raise ValueError("base_url is required")

        # 从环境变量获取 API 密钥
        api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")
        # 验证 API 密钥是否存在
        if not api_key:
            raise ValueError(f"{api_key_env} or OPENAI_API_KEY environment variable is required")

        # 存储 API 密钥（不要记录到日志）
        self._api_key = api_key
        # 存储 API 基础地址（去除末尾斜杠）
        self._base_url = base_url.rstrip("/")
        # 存储模型名称
        self._model = model
        # 初始化 HTTP 客户端（或使用传入的客户端）
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 200,
    ) -> str:
        """
        调用 LLM 生成文本

        【参数说明】
        - messages: list[dict[str, str]] - 消息列表
            格式：[{"role": "system"|"user"|"assistant", "content": "..."}]
        - temperature: float - 温度参数（默认 0.0，确定性输出）
        - max_tokens: int - 最大生成 token 数（默认 200）

        【返回值】
        - str: LLM 生成的文本（API 调用失败时返回空字符串）

        【执行流程】
        1. 构建请求头（Authorization + Content-Type）
        2. 构建请求体（model、messages、temperature、max_tokens）
        3. 发送 POST 请求到 /chat/completions
        4. 检查响应状态码
        5. 解析响应，提取生成的文本
        6. 返回文本（失败时返回空字符串）

        【错误处理策略】
        RAG 模块不应因 LLM 调用失败而崩溃：
        - API 错误 → 返回空字符串，RAG 降级为无上下文/无重写
        - 网络超时 → 返回空字符串
        - JSON 解析失败 → 返回空字符串

        【示例】
        ```python
        # 生成上下文摘要
        text = await client.complete([
            {"role": "system", "content": "用一句话概括以下代码的作用"},
            {"role": "user", "content": chunk.text}
        ])

        # 查询重写
        text = await client.complete([
            {"role": "system", "content": "将用户查询重写为3个语义变体"},
            {"role": "user", "content": query}
        ])
        ```
        """
        # 构建请求头
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # 构建请求体
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # 构建完整的 API 地址
        url = f"{self._base_url}/chat/completions"

        try:
            # 发送 POST 请求
            resp = await self._http.post(url, headers=headers, json=payload)
            # 检查响应状态码
            resp.raise_for_status()
            # 解析响应 JSON
            data = resp.json()
            # 提取生成的文本（第一个 choice 的 message content）
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
            return ""
        except Exception:
            # RAG 不应因 LLM 失败而崩溃，返回空字符串降级处理
            return ""

    async def generate_context(self, chunk_text: str, source_path: str) -> str:
        """
        为 Chunk 生成上下文摘要（Contextual Retrieval）

        【参数说明】
        - chunk_text: str - Chunk 的文本内容
        - source_path: str - Chunk 的来源文件路径

        【返回值】
        - str: 50-100 token 的上下文摘要

        【设计目的】
        Anthropic 的 Contextual Retrieval 策略：
        在 embedding 前给每个 chunk 添加 LLM 生成的上下文摘要，
        说明该 chunk 在项目中的位置和作用，
        检索失败率可降低 49%。

        【Prompt 设计】
        让 LLM 用简洁的语言说明：
        - 这个 chunk 来自哪个文件
        - 它的作用是什么
        - 它在项目中的位置

        【示例输出】
        "该代码片段来自 index.py，定义了 KnowledgeIndexManager 的
         hybrid_search 方法，负责混合检索（语义+关键词）。"

        【错误处理】
        LLM 调用失败时返回空字符串，RAG 降级为无上下文。
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert code analyst. Generate a brief 1-2 sentence context "
                    "for the following code chunk, explaining where it sits in the project "
                    "and what it does. Be concise (under 50 words). "
                    "Reply in the same language as the code comments."
                ),
            },
            {
                "role": "user",
                "content": f"File: {source_path}\n\n```\n{chunk_text[:1500]}\n```",
            },
        ]
        return await self.complete(messages, temperature=0.0, max_tokens=100)

    async def rewrite_query(self, query: str) -> list[str]:
        """
        用 LLM 重写查询，生成多个语义变体（替代硬编码同义词表）

        【参数说明】
        - query: str - 原始查询

        【返回值】
        - list[str]: 查询变体列表（包含原始查询 + LLM 生成的变体）

        【设计目的】
        原始的 _rewrite_query 方法使用硬编码同义词表，覆盖面有限且不灵活。
        本方法用 LLM 生成查询变体：
        - 理解查询的语义意图
        - 生成不同表述方式的变体
        - 提高检索召回率

        【Prompt 设计】
        让 LLM 生成 3 个查询变体，用换行分隔：
        - 同义词替换（如 "配置" → "设置"）
        - 不同的表述方式（如 "如何配置X" → "X的配置方法"）
        - 更具体/更抽象的表述

        【错误处理】
        LLM 调用失败时返回仅包含原始查询的列表（降级为无重写）。

        【示例】
        ```python
        queries = await client.rewrite_query("如何配置权限管理")
        # 返回: ["如何配置权限管理", "权限管理的配置方法", "设置权限控制", ...]
        ```
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a search query optimizer. Given a user query, "
                    "generate 3 alternative queries with different phrasings "
                    "but the same intent. Output one query per line, "
                    "no numbering, no explanations. "
                    "Reply in the same language as the user query."
                ),
            },
            {
                "role": "user",
                "content": query,
            },
        ]
        result = await self.complete(messages, temperature=0.3, max_tokens=150)

        # 解析 LLM 输出：每行一个查询变体
        variants = [line.strip() for line in result.splitlines() if line.strip()]
        # 去重，并确保原始查询在列表中
        unique_variants = []
        seen = {query.lower().strip()}
        for v in variants:
            vl = v.lower().strip()
            if vl not in seen:
                seen.add(vl)
                unique_variants.append(v)

        # 返回原始查询 + 变体（最多 4 个变体，避免过多 API 调用）
        return [query] + unique_variants[:3]

    async def rerank(
        self,
        query: str,
        chunks: list[tuple[Any, float]],
        *,
        top_k: int = 5,
    ) -> list[tuple[Any, float]]:
        """
        用 LLM 对检索结果重排序（Reranking）

        【参数说明】
        - query: str - 用户查询
        - chunks: list[tuple[Any, float]] - 检索结果列表（Chunk, 原始分数）
        - top_k: int - 返回前 K 个结果（默认 5）

        【返回值】
        - list[tuple[Any, float]]: 重排序后的结果列表（Chunk, LLM 评分 0-1）

        【Reranking 原理】
        传统的向量检索（双塔模型）分别编码 query 和 document，
        通过余弦相似度匹配。这种方式快但不够精确。

        Reranking 用 LLM（类似 cross-encoder）同时看 query 和 document，
        判断相关性更准确：
        - 双塔模型：query → 向量，doc → 向量，算余弦相似度
        - Reranking：query + doc → LLM → 相关性分数

        【执行流程】
        1. 把 query 和所有 chunk 文本放入一个 prompt
        2. LLM 给每个 chunk 打分（0-10）
        3. 解析分数，按分数重新排序
        4. 返回前 top_k 个结果

        【Prompt 设计】
        一次 LLM 调用处理所有 chunk（而非逐个调用），
        减少 API 调用次数，提高效率。

        【错误处理】
        - LLM 调用失败 → 返回原始顺序的前 top_k 个
        - 分数解析失败 → 返回原始顺序的前 top_k 个
        - 分数数量不匹配 → 返回原始顺序的前 top_k 个

        【示例】
        ```python
        # 检索 top-10，然后 rerank 取 top-5
        chunks = await manager.hybrid_search(query, top_k=10)
        reranked = await client.rerank(query, chunks, top_k=5)
        ```
        """
        if not chunks:
            return []

        # 构建 chunk 文本列表（每个截断到 500 字符，避免 prompt 过长）
        chunk_texts = []
        for i, (chunk, _) in enumerate(chunks):
            text = chunk.text[:500] if hasattr(chunk, "text") else str(chunk)[:500]
            chunk_texts.append(f"[{i}] {text}")

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a relevance judge. Given a query and multiple text chunks, "
                    "rate the relevance of each chunk to the query on a scale of 0-10. "
                    "10 means highly relevant, 0 means not relevant at all. "
                    "Output ONLY the scores, one per line, in the same order as the chunks. "
                    "No explanations, no numbering."
                ),
            },
            {
                "role": "user",
                "content": f"Query: {query}\n\nChunks:\n" + "\n\n".join(chunk_texts),
            },
        ]

        result = await self.complete(messages, temperature=0.0, max_tokens=100)

        # 解析 LLM 输出的分数
        scores: list[float] = []
        for line in result.splitlines():
            line = line.strip()
            if line:
                try:
                    score = float(line)
                    # 归一化到 [0, 1]
                    scores.append(min(max(score, 0.0), 10.0) / 10.0)
                except ValueError:
                    scores.append(0.0)

        # 如果解析失败或数量不匹配，返回原始顺序
        if len(scores) != len(chunks):
            return chunks[:top_k]

        # 用 LLM 分数重新排序
        reranked = list(zip([c for c, _ in chunks], scores))
        reranked.sort(key=lambda x: x[1], reverse=True)
        return reranked[:top_k]
