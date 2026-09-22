"""
LLM 模块 - 统一的 LLM Provider 接口和实现

【学习要点】
1. 工厂模式：使用 create_provider_from_config() 根据配置创建对应的 Provider
2. 协议接口：使用 Protocol 定义 LLMProvider 接口，实现多态
3. 模块导出：使用 __all__ 控制模块的公共 API
4. 依赖注入：支持注入 mock client 用于测试

【核心组件】
- LLMProvider: LLM Provider 协议接口
- AnthropicProvider: Anthropic API 兼容的 Provider
- OpenAICompatibleProvider: OpenAI API 兼容的 Provider
- LlmResponse: LLM 响应类型
- ToolCallBlock: 工具调用块类型
- UsageStats: 使用统计类型
- create_provider_from_config(): 根据配置创建 Provider 的工厂函数

【支持的 Provider】
1. anthropic: Anthropic API 兼容（DeepSeek、Claude 等）
2. openai_compatible: OpenAI API 兼容（通义、智谱、Ollama 等）
"""
from __future__ import annotations

# typing：类型提示
from typing import Any

# 导入配置类
from iwan_claude.core.config import LlmConfig

# 导入 Provider 接口和实现
from iwan_claude.core.llm.base import LLMProvider
from iwan_claude.core.llm.openai_compat import OpenAICompatibleProvider
from iwan_claude.core.llm.provider import AnthropicProvider

# 导入类型定义
from iwan_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats
from iwan_claude.core.llm.vision import filter_vision_blocks, has_image_content

# 模块公共 API 导出列表
# 控制 from iwan_claude.core.llm import * 时能导入哪些名称
__all__ = [
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "LLMProvider",
    "LlmResponse",
    "ToolCallBlock",
    "UsageStats",
    "create_provider_from_config",
    "filter_vision_blocks",
    "has_image_content",
]


def create_provider_from_config(
    llm_config: LlmConfig,
    *,
    model_override: str | None = None,
    anthropic_client: Any = None,
    http_client: Any = None,
) -> LLMProvider:
    """
    根据配置创建 LLM Provider（工厂函数）
    
    【学习要点】
    1. 工厂模式：根据配置动态选择创建哪个 Provider
    2. 配置优先：model_override 可以覆盖配置中的默认模型
    3. 测试支持：允许注入 mock client 用于单元测试
    4. 空值处理：空字符串转 None，避免 SDK 误解
    
    参数：
        llm_config: LLM 配置（包含 provider 类型、模型名称、API URL 等）
        model_override: 模型名称覆盖（可选，用于临时切换模型）
        anthropic_client: 自定义 Anthropic client（可选，用于测试）
        http_client: 自定义 HTTP client（可选，用于测试）
    
    返回值：
        LLMProvider: 实现了 LLMProvider 协议的 Provider 实例
    
    【选择逻辑】
    1. 如果 provider == "openai_compatible"：创建 OpenAICompatibleProvider
    2. 默认：创建 AnthropicProvider（DeepSeek 用这个最简单）
    """
    # 确定模型名称：优先使用覆盖值，否则使用配置中的默认模型
    model = model_override or llm_config.default_model
    
    # 处理 base_url：空字符串转 None，避免 SDK 以为要传空串
    base_url = llm_config.base_url or None

    # 根据配置选择 Provider
    if llm_config.provider == "openai_compatible":
        # OpenAI /chat/completions 协议
        # 适用于：通义兼容模式、智谱、Ollama 等没有 Anthropic 端点的厂商
        return OpenAICompatibleProvider(
            model=model,
            base_url=llm_config.base_url,
            api_key_env=llm_config.api_key_env,
            context_window=llm_config.context_window,
            http_client=http_client,
        )

    # 默认使用 Anthropic Provider
    # DeepSeek 用这个最简单！填 base_url=https://api.deepseek.com/anthropic 即可
    return AnthropicProvider(
        model=model,
        api_key_env=llm_config.api_key_env,
        base_url=base_url,
        context_window_override=llm_config.context_window,
        client=anthropic_client,
    )
