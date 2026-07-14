"""验证 DeepSeek/OpenAI 兼容改造 + Windows/iwan 改造"""
from __future__ import annotations

import sys
sys.path.insert(0, "src")
import os


def main() -> int:
    print("=== 1. Config 新字段验证 ===")
    from iwan_claude.core.config import LlmConfig, get_config
    cfg = LlmConfig()
    print(f"  provider       = {cfg.provider!r}")
    print(f"  base_url       = {cfg.base_url!r}")
    print(f"  api_key_env    = {cfg.api_key_env!r}")
    print(f"  context_window = {cfg.context_window!r}")
    print(f"  default_model  = {cfg.default_model!r}")
    assert cfg.provider == "anthropic"
    assert cfg.context_window == 128_000
    print("  [OK]")

    print()
    print("=== 2. create_provider_from_config 入口 ===")
    from iwan_claude.core.llm import create_provider_from_config
    from iwan_claude.core.llm.provider import AnthropicProvider
    os.environ.pop("ANTHROPIC_API_KEY", None)
    prov = AnthropicProvider("claude-sonnet-4-6", client=object())
    print(f"  AnthropicProvider mock client：{type(prov).__name__} [OK]")

    print()
    print("=== 3. OpenAICompatibleProvider + 消息转换 ===")
    from iwan_claude.core.llm.openai_compat import (
        OpenAICompatibleProvider,
        _convert_messages_to_openai,
        _convert_tools_to_openai,
    )
    print("  import [OK]")

    msgs = [
        {"role": "user", "content": "Hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "OK, I will list files"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "bash",
                    "input": {"command": "ls"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "a.txt\nb.py",
                }
            ],
        },
    ]
    converted = _convert_messages_to_openai(msgs, system="You are helpful")
    assert converted[0]["role"] == "system", converted[0]
    assert converted[1]["content"] == "Hi"
    assert "tool_calls" in converted[2], converted[2]
    assert converted[2]["tool_calls"][0]["function"]["name"] == "bash"
    assert converted[3]["role"] == "tool"
    assert converted[3]["content"] == "a.txt\nb.py"
    print("  _convert_messages_to_openai [OK]")

    tools = [
        {
            "name": "bash",
            "description": "run shell",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
            },
        }
    ]
    oa_tools = _convert_tools_to_openai(tools)
    assert oa_tools[0]["type"] == "function"
    assert oa_tools[0]["function"]["name"] == "bash"
    assert oa_tools[0]["function"]["parameters"]["properties"]["command"]["type"] == "string"
    print("  _convert_tools_to_openai [OK]")

    print()
    print("=== 4. core.app + runner 导入 ===")
    from iwan_claude.core import app as _  # noqa: F401
    print("  core.app [OK]")
    from iwan_claude.core import runner as _2  # noqa: F401
    print("  core.runner [OK]")

    print()
    print("=== 5. get_config() 读 LLM DeepSeek 环境变量 ===")
    os.environ["IWAN_LLM_PROVIDER"] = "openai_compatible"
    os.environ["IWAN_LLM_BASE_URL"] = "https://api.deepseek.com/v1"
    os.environ["IWAN_LLM_API_KEY_ENV"] = "DEEPSEEK_API_KEY"
    os.environ["IWAN_LLM_DEFAULT_MODEL"] = "deepseek-chat"
    os.environ["IWAN_LLM_CONTEXT_WINDOW"] = "64000"
    real_cfg = get_config()
    print(f"  provider       = {real_cfg.llm.provider!r}")
    print(f"  base_url       = {real_cfg.llm.base_url!r}")
    print(f"  api_key_env    = {real_cfg.llm.api_key_env!r}")
    print(f"  default_model  = {real_cfg.llm.default_model!r}")
    print(f"  context_window = {real_cfg.llm.context_window!r}")
    assert real_cfg.llm.provider == "openai_compatible"
    assert real_cfg.llm.base_url == "https://api.deepseek.com/v1"
    assert real_cfg.llm.api_key_env == "DEEPSEEK_API_KEY"
    assert real_cfg.llm.default_model == "deepseek-chat"
    assert real_cfg.llm.context_window == 64_000
    print("  env override [OK]")

    # 清理 env
    for k in ("IWAN_LLM_PROVIDER", "IWAN_LLM_BASE_URL", "IWAN_LLM_API_KEY_ENV",
              "IWAN_LLM_DEFAULT_MODEL", "IWAN_LLM_CONTEXT_WINDOW"):
        os.environ.pop(k, None)

    print()
    print("=" * 60)
    print("ALL VERIFICATIONS PASSED ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
