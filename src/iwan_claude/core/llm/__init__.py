from __future__ import annotations

from typing import Any

from iwan_claude.core.config import LlmConfig
from iwan_claude.core.llm.base import LLMProvider
from iwan_claude.core.llm.openai_compat import OpenAICompatibleProvider
from iwan_claude.core.llm.provider import AnthropicProvider
from iwan_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats

__all__ = [
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "LLMProvider",
    "LlmResponse",
    "ToolCallBlock",
    "UsageStats",
    "create_provider_from_config",
]


# 统一创建 LLM Provider 的入口：根据 LlmConfig.provider 自动选 Anthropic 或 OpenAI 兼容实现
#   - model_override: 若传入，则覆盖 config.llm.default_model
#   - anthropic_client / http_client: 测试时注入 mock
def create_provider_from_config(
    llm_config: LlmConfig,
    *,
    model_override: str | None = None,
    anthropic_client: Any = None,
    http_client: Any = None,
) -> LLMProvider:
    model = model_override or llm_config.default_model
    base_url = llm_config.base_url or None  # 空字符串转 None，避免 SDK 以为要传空串

    if llm_config.provider == "openai_compatible":
        # 纯 OpenAI /chat/completions 协议：给通义兼容模式、智谱、Ollama 等没有 Anthropic 端点的厂商用
        return OpenAICompatibleProvider(
            model=model,
            base_url=llm_config.base_url,
            api_key_env=llm_config.api_key_env,
            context_window=llm_config.context_window,
            http_client=http_client,
        )

    # 默认 anthropic（DeepSeek 用这个最简单！填 base_url=https://api.deepseek.com/anthropic 即可）
    return AnthropicProvider(
        model=model,
        api_key_env=llm_config.api_key_env,
        base_url=base_url,
        context_window_override=llm_config.context_window,
        client=anthropic_client,
    )
