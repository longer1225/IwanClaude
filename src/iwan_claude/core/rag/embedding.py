"""
嵌入服务模块 - 调用外部 Embedding API 将文本转换为向量

【学习要点】
1. API 抽象：封装 Embedding API 调用，提供统一接口
2. 批量处理：支持批量嵌入，提高效率
3. 错误处理：使用 raise_for_status 确保请求成功
4. 配置灵活：支持自定义模型、API 地址和 API 密钥环境变量

【核心类】
- EmbeddingProvider: 嵌入服务提供者

【API 协议】
遵循 OpenAI 兼容的 Embedding API 协议：
- 端点：{base_url}/embeddings
- 请求方法：POST
- 请求体：{"model": "xxx", "input": ["text1", "text2"]}
- 响应体：{"data": [{"embedding": [0.1, 0.2, ...]}]}

【支持的 API 提供商】
- DeepSeek（默认）
- OpenAI
- 其他兼容 OpenAI 协议的 Embedding API

【环境变量】
- DEEPSEEK_API_KEY: DeepSeek API 密钥（默认）
- OPENAI_API_KEY: OpenAI API 密钥（备选）

【超时设置】
- 总超时：120 秒
- 连接超时：20 秒
"""
from __future__ import annotations

import json
import os
from hashlib import md5

import httpx

from iwan_claude.core.config import RagConfig


class EmbeddingProvider:
    """
    嵌入服务提供者 - 调用外部 Embedding API

    【学习要点】
    1. API 封装：将文本转换为向量的统一接口
    2. 批量处理：支持批量嵌入，提高效率
    3. 错误处理：使用 raise_for_status 确保请求成功
    4. 配置灵活：支持自定义模型、API 地址和 API 密钥

    【核心方法】
    - embed(): 批量嵌入文本
    - _embed_batch(): 单次批量请求

    【设计目的】
    封装 Embedding API 调用，提供统一的向量生成接口，
    便于后续的向量存储和检索。

    【注意事项】
    - 需要设置 API 密钥环境变量
    - base_url 必须以 /v1 结尾或正确配置
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
        初始化嵌入服务提供者

        【参数说明】
        - model: str - Embedding 模型名称（如 "text-embedding-3-small"）
        - base_url: str - API 基础地址（如 "https://api.deepseek.com/v1"）
        - api_key_env: str - API 密钥环境变量名称（默认 "DEEPSEEK_API_KEY"）
        - http_client: httpx.AsyncClient | None - 自定义 HTTP 客户端（可选）

        【环境变量优先级】
        1. api_key_env 指定的环境变量（如 DEEPSEEK_API_KEY）
        2. OPENAI_API_KEY（备选）

        【初始化流程】
        1. 验证 base_url 是否为空
        2. 从环境变量获取 API 密钥
        3. 验证 API 密钥是否存在
        4. 初始化 HTTP 客户端（或使用传入的客户端）

        【异常处理】
        - ValueError: base_url 为空或 API 密钥未设置

        【示例】
        ```python
        provider = EmbeddingProvider(
            model="text-embedding-3-small",
            base_url="https://api.deepseek.com/v1"
        )
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

        # 存储 API 密钥（注意：不要记录到日志中）
        self._api_key = api_key
        # 存储 API 基础地址（去除末尾斜杠）
        self._base_url = base_url.rstrip("/")
        # 存储模型名称
        self._model = model
        # 初始化 HTTP 客户端（或使用传入的客户端）
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0))

    async def embed(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """
        批量嵌入文本

        【参数说明】
        - texts: list[str] - 要嵌入的文本列表
        - batch_size: int - 每批处理的文本数量（默认 32）

        【返回值】
        - list[list[float]]: 向量列表，每个文本对应一个向量

        【执行流程】
        1. 将文本列表按 batch_size 分割
        2. 依次处理每个批次
        3. 合并所有结果

        【设计目的】
        支持大量文本的嵌入，通过分批处理避免单次请求过大，
        提高请求成功率和效率。

        【注意事项】
        - batch_size 不宜过大（API 有限制）
        - 文本为空列表时返回空列表

        【示例】
        ```python
        texts = ["Hello, world!", "Goodbye, world!"]
        vectors = await provider.embed(texts)
        # 返回: [[0.1, 0.2, ...], [0.3, 0.4, ...]]
        ```
        """
        results: list[list[float]] = []
        # 按批次处理
        for i in range(0, len(texts), batch_size):
            # 获取当前批次的文本
            batch = texts[i:i + batch_size]
            # 调用批量嵌入方法
            embeddings = await self._embed_batch(batch)
            # 合并结果
            results.extend(embeddings)
        return results

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        单次批量嵌入请求

        【参数说明】
        - texts: list[str] - 要嵌入的文本列表

        【返回值】
        - list[list[float]]: 向量列表

        【API 请求格式】
        ```json
        {
            "model": "text-embedding-3-small",
            "input": ["text1", "text2"]
        }
        ```

        【API 响应格式】
        ```json
        {
            "data": [
                {"embedding": [0.1, 0.2, ...]},
                {"embedding": [0.3, 0.4, ...]}
            ]
        }
        ```

        【执行流程】
        1. 构建请求头（Authorization 和 Content-Type）
        2. 构建请求体（model 和 input）
        3. 发送 POST 请求
        4. 检查响应状态码
        5. 解析响应，提取嵌入向量
        6. 返回向量列表

        【异常处理】
        - HTTPError: 请求失败（非 200 状态码）
        - JSONDecodeError: 响应不是有效 JSON

        【注意事项】
        - API 密钥通过 Authorization 头传递
        - 使用 Bearer 认证方式
        """
        # 构建请求头
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # 构建请求体
        payload = {
            "model": self._model,
            "input": texts,
        }
        # 构建完整的 API 地址
        url = f"{self._base_url}/embeddings"

        # 发送 POST 请求
        resp = await self._http.post(url, headers=headers, json=payload)
        # 检查响应状态码（非 200 会抛出 HTTPError）
        resp.raise_for_status()

        # 解析响应 JSON
        data = resp.json()
        embeddings = []
        # 提取嵌入向量
        for item in data.get("data", []):
            embeddings.append(item.get("embedding", []))

        return embeddings


def get_embedding_provider(config: RagConfig, llm_base_url: str) -> EmbeddingProvider:
    """
    创建嵌入服务提供者

    【参数说明】
    - config: RagConfig - RAG 配置（包含 embedding_model 和 embedding_base_url）
    - llm_base_url: str - LLM API 基础地址（用于 fallback）

    【返回值】
    - EmbeddingProvider: 嵌入服务提供者

    【base_url 优先级】
    1. config.embedding_base_url（配置文件中指定）
    2. llm_base_url（LLM API 地址作为 fallback）

    【base_url 处理逻辑】
    - 如果 embedding_base_url 为空，使用 llm_base_url
    - 如果 llm_base_url 以 /anthropic 结尾，替换为 /v1
    - 如果 llm_base_url 不以 /v1 结尾，添加 /v1

    【设计目的】
    提供灵活的配置方式，允许用户在配置文件中指定单独的 Embedding API 地址，
    如果未指定，则使用 LLM API 地址作为 fallback。

    【示例】
    ```python
    config = RagConfig(
        embedding_model="text-embedding-3-small",
        embedding_base_url="https://api.deepseek.com/v1"
    )
    provider = get_embedding_provider(config, "https://api.deepseek.com/anthropic")
    # 如果 embedding_base_url 为空，会将 llm_base_url 转换为 https://api.deepseek.com/v1
    ```
    """
    # 获取配置中的 embedding_base_url
    base_url = config.embedding_base_url

    # 如果未显式配置 embedding_base_url，使用通义 dashscope 兼容端点作为默认。
    # 【原因】DeepSeek 不提供 /v1/embeddings 端点（返回 404），
    # 通义 dashscope 的 compatible-mode 兼容 OpenAI 协议，支持 text-embedding-v3。
    # llm_base_url 参数保留用于显式 fallback 场景，但默认不再回退到 DeepSeek。
    if not base_url:
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 创建并返回 EmbeddingProvider
    # 【API Key】从 DASHSCOPE_API_KEY 环境变量读取；未配置时 EmbeddingProvider 构造会 raise ValueError，
    # 由 app.py 的 try/except 捕获后降级为 embedder=None（向量记忆降级，不影响长期记忆和主流程）
    return EmbeddingProvider(
        model=config.embedding_model,
        base_url=base_url,
        api_key_env="DASHSCOPE_API_KEY",
    )
