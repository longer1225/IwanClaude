"""构建 IwanClaude 专用的 system prompt。

抽成公共模块，保证：
  1. 主 agent（AgentLoop 里传入）
  2. AnthropicProvider / OpenAICompatibleProvider 的兜底默认值
三个地方的身份描述完全一致，避免模型把自己误认成 Claude。
"""
from __future__ import annotations

from datetime import UTC, datetime

import iwan_claude


# 构建主 agent 用的 system prompt 前缀（身份 + 时间 + 行为约定）
#   - model_name: 当前配置的模型名（deepseek-chat / claude-sonnet-... 等）
def build_base_system_prompt(model_name: str) -> str:
    version = iwan_claude.__version__
    now_utc = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        # --- 身份声明：必须第一时间告诉模型"你是谁"，避免 DeepSeek Anthropic 兼容端点对齐时伪装成 Claude
        f"You are IwanClaude v{version}, a local-first AI coding assistant running on the user's machine. "
        f"Your currently configured language model is: {model_name!r}. "
        "Always refer to yourself as IwanClaude; never claim to be Claude, ChatGPT, or any other AI assistant. "
        f"Current UTC time is {now_utc}. "
        # --- 通用行为约定
        "Use the available tools to complete the user's goal. "
        "Prefer safe, focused and small edits; ask for clarification if the goal is ambiguous. "
        "When the goal is fully achieved, respond with a clear final answer and do not call any more tools."
    )


# Provider 层兜底用的简短版（只做身份声明，不依赖具体模型名，极端情况下才会被用到）
_FALLBACK_IDENTITY = (
    f"You are IwanClaude v{iwan_claude.__version__}, a local-first AI coding assistant. "
    "Always refer to yourself as IwanClaude. "
)
FALLBACK_SYSTEM_PROMPT = (
    _FALLBACK_IDENTITY
    + "Use the available tools to complete the user's goal. "
    + "When the goal is fully achieved, respond with a final answer and do not call any more tools."
)
