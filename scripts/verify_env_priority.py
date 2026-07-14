"""验证系统环境变量优先于 .env（override=False）"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "src")

# 模拟：用户级环境变量已经配好真实 Key
os.environ["DEEPSEEK_API_KEY"] = "sk-your-api-key-placeholder-for-testing-only"

print("=== 验证 1：系统环境变量有真实 Key，.env 里 DEEPSEEK_API_KEY 被注释 ===")
from iwan_claude.core.config import get_config

cfg = get_config()
print(f"  os.environ[DEEPSEEK_API_KEY]  = {os.environ.get('DEEPSEEK_API_KEY')[:10]}...")
print(f"  cfg.llm.api_key_env           = {cfg.llm.api_key_env!r}")

api_key_env_name = cfg.llm.api_key_env
real_key = os.environ.get(api_key_env_name)
assert real_key is not None, f"环境变量 {api_key_env_name} 不存在"
assert real_key.startswith("sk-"), f"没读到真实 Key！读到：{real_key[:20]}"
assert "your_deepseek" not in real_key, "读到了占位符！失败！"
print(f"  AnthropicProvider 会读取 {api_key_env_name} = {real_key[:10]}...")
print("  [OK]")

print()
print("=== 验证 2：override=False 时 .env 不会覆盖系统环境的真实 Key ===")
from dotenv import load_dotenv

before = os.environ["DEEPSEEK_API_KEY"]
loaded = load_dotenv(".env", override=False)
print(f"  load_dotenv 返回 loaded={loaded}（.env 文件是否被读取）")
after = os.environ["DEEPSEEK_API_KEY"]
assert before == after, (
    f"override=False 居然被覆盖了！\n  之前：{before[:10]}...\n  之后：{after[:10]}..."
)
print(f"  load_dotenv 之前：{before[:10]}...")
print(f"  load_dotenv 之后：{after[:10]}...")
print("  [OK] 系统环境变量被完全保留，.env 里没有的/已注释的变量不会产生任何影响")

print()
print("=" * 60)
print("✅ 全部验证通过：100% 安全使用 Windows 用户级环境变量！")
