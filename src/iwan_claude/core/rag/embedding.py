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
import logging
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

        【环境变量读取顺序（按优先级从高到低）】
        1. api_key_env 显式指定的环境变量（如 DASHSCOPE_API_KEY）
        2. 通义/千问常用别名（QIANWEN_API_KEY / QWEN_API_KEY / DASHSCOPE_API_KEY）
        3. OpenAI 通用备选（OPENAI_API_KEY）
        4. DeepSeek 备选（DEEPSEEK_API_KEY）

        【初始化流程】
        1. 验证 base_url 是否为空
        2. 按优先级从环境变量获取 API 密钥
        3. 验证 API 密钥是否存在
        4. 初始化 HTTP 客户端（或使用传入的客户端）

        【异常处理】
        - ValueError: base_url 为空或 API 密钥未设置

        【示例】
        ```python
        provider = EmbeddingProvider(
            model="text-embedding-3-small",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        ```
        """
        # 验证 base_url 是否为空
        if not base_url:
            raise ValueError("base_url is required")

        # ===== 按优先级尝试从环境变量获取 API Key =====
        # 【兼容设计】用户可能用不同的命名习惯存放通义 API Key：
        #   - QIANWEN_API_KEY  : 千问中文拼音（本次用户实际使用的命名）
        #   - QWEN_API_KEY     : 千问英文官方名
        #   - DASHSCOPE_API_KEY: DashScope 官方文档推荐命名
        # 优先使用调用方显式传入的 api_key_env，其次尝试上述别名，最后兜底 OPENAI/DEEPSEEK
        fallback_list = [
            api_key_env,                          # 1. 显式指定（优先级最高）
            "QIANWEN_API_KEY",                    # 2. 千问中文拼音命名
            "QWEN_API_KEY",                       # 3. 千问英文官方命名
            "DASHSCOPE_API_KEY",                  # 4. DashScope 官方推荐命名
            "OPENAI_API_KEY",                     # 5. OpenAI 协议通用兜底
            "DEEPSEEK_API_KEY",                   # 6. DeepSeek 兼容端点兜底
        ]
        api_key: str | None = None
        used_env: str | None = None
        for env_name in fallback_list:
            if env_name:
                val = os.environ.get(env_name)
                if val:
                    api_key = val
                    used_env = env_name
                    break

        # 验证 API 密钥是否存在
        if not api_key:
            tried = ", ".join(e for e in fallback_list if e)
            raise ValueError(
                f"Embedding API key not found. Tried environment variables: {tried}"
            )

        # 日志记录：只打印读取的环境变量名，**绝不打印 Key 本身**（安全红线）
        # 这样用户可以一眼确认系统读到了他设置的 QIANWEN_API_KEY
        logging.getLogger(__name__).info(
            "embedding provider: using api_key from env=%s  model=%s  base_url=%s",
            used_env,
            model,
            base_url.rstrip("/"),
        )

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
    - config: RagConfig - RAG 配置（包含 embedding_model / embedding_base_url / embedding_api_key_env）
    - llm_base_url: str - LLM API 基础地址（保留用于兼容旧 fallback 逻辑）

    【返回值】
    - EmbeddingProvider: 嵌入服务提供者

    【base_url 优先级】
    1. config.embedding_base_url（配置文件中指定）
    2. 通义 dashscope 兼容端点（默认兜底，不再回退到 DeepSeek /anthropic）

    【api_key_env 读取规则】
    1. config.embedding_api_key_env 非空 → 以此为最高优先级尝试
    2. config.embedding_api_key_env 为空 → 走 EmbeddingProvider 内置兼容列表：
       QIANWEN_API_KEY / QWEN_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY

    【设计目的】
    提供灵活的配置方式，同时默认支持多种常见的通义 API Key 环境变量命名，
    避免用户因变量名不一致而报错。

    【示例】
    ```python
    config = RagConfig(
        embedding_model="text-embedding-v3",
        embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        embedding_api_key_env="",  # 留空 → 启用多命名兼容
    )
    provider = get_embedding_provider(config, "https://api.deepseek.com/anthropic")
    ```
    """
    # 获取配置中的 embedding_base_url
    base_url = config.embedding_base_url

    # 如果未显式配置 embedding_base_url，使用通义 dashscope 兼容端点作为默认。
    # 【原因】DeepSeek 不提供 /v1/embeddings 端点（返回 404），
    # 通义 dashscope 的 compatible-mode 兼容 OpenAI 协议，支持 text-embedding-v3。
    if not base_url:
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 决定传给 EmbeddingProvider 的首选 api_key_env：
    #   - 用户显式配置了 config.embedding_api_key_env → 用它（优先级最高）
    #   - 否则传空串，让 EmbeddingProvider 内部走完整的兼容兜底列表
    preferred_env = config.embedding_api_key_env or ""

    # 创建并返回 EmbeddingProvider
    # 未配置任何 API Key 时，EmbeddingProvider 构造会 raise ValueError，
    # 由 app.py 的 try/except 捕获后降级为 embedder=None（向量记忆降级，不影响长期记忆和主流程）
    return EmbeddingProvider(
        model=config.embedding_model,
        base_url=base_url,
        api_key_env=preferred_env,
    )
