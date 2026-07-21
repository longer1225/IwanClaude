"""
system_prompt 模块 - 构建 IwanClaude 专用的 system prompt

【学习要点】
1. System Prompt 设计：清晰定义 Agent 的身份、行为准则和能力边界
2. 公共模块：抽成独立模块，确保多个地方使用一致的身份描述
3. 条件拼接：根据配置动态拼接不同的 prompt 部分
4. 版本管理：自动获取包版本，确保身份声明中的版本号正确

【核心组件】
- build_base_system_prompt(): 构建完整的 system prompt（身份 + 时间 + 行为约定 + RAG 引导 + 项目上下文）
- _RAG_GUIDANCE: RAG 功能的引导提示词
- FALLBACK_SYSTEM_PROMPT: Provider 层的兜底 prompt

【使用场景】
1. AgentLoop 和 LangGraphAgentLoop：传入完整的 system prompt
2. AnthropicProvider / OpenAICompatibleProvider：极端情况下使用兜底 prompt
3. 确保模型不会把自己误认成 Claude 或其他 AI 助手
"""
from __future__ import annotations

# datetime：日期时间处理（用于获取当前 UTC 时间）
from datetime import UTC, datetime

# 导入 iwan_claude 包，用于获取版本号
import iwan_claude


# RAG 功能的引导提示词
# 当启用 RAG 时，会追加到 system prompt 中
_RAG_GUIDANCE = """
[Knowledge Retrieval Guidance]
You have access to a local knowledge base (RAG) indexed from files under the project.
Before answering questions that:
  (a) require details about existing code/documentation you haven't seen,
  (b) reference specific symbols, filenames, or sections you're unsure about,
  (c) involve tasks spanning more than 2 files,
FIRST call search_knowledge(query) with a concise semantic query, get relevant context,
THEN reason and use tools. Do NOT guess API signatures or code contents from memory.
When you find stale/incorrect search results, call index_knowledge to refresh the index.
"""


def build_base_system_prompt(model_name: str, *, has_rag: bool = False, claude_md_context: str = "") -> str:
    """
    构建主 Agent 使用的完整 system prompt
    
    【学习要点】
    1. 身份声明：必须第一时间告诉模型"你是谁"，避免模型伪装成其他 AI
    2. 动态内容：版本号、模型名称、当前时间都是动态生成的
    3. 条件拼接：根据配置决定是否添加 RAG 引导和项目上下文
    4. 行为约定：明确告诉模型应该如何行事
    
    参数：
        model_name: 当前配置的模型名称（如 deepseek-chat、claude-sonnet-3.5 等）
        has_rag: 是否启用了 RAG 功能（默认 False）
        claude_md_context: CLAUDE.md 文件的渲染内容（默认空字符串）
    
    返回值：
        str: 完整的 system prompt 字符串
    
    【Prompt 结构】
    1. 身份声明：告诉模型它是 IwanClaude，运行在用户本地
    2. 模型信息：告诉模型当前使用的语言模型
    3. 时间信息：当前 UTC 时间（帮助模型理解时效性）
    4. 行为约定：告诉模型如何完成用户目标
    5. RAG 引导（可选）：如果启用 RAG，添加检索引导
    6. 项目上下文（可选）：如果有 CLAUDE.md，添加项目上下文
    
    【重要设计点】
    - 必须强调"Always refer to yourself as IwanClaude"，避免模型把自己误认成 Claude
    - 使用 f-string 动态插入版本号和模型名称
    - 使用 UTC 时间，避免时区问题
    - 条件拼接 RAG 引导和项目上下文，减少不必要的 token 消耗
    """
    # 获取当前版本号（从 iwan_claude 包的 __version__ 属性）
    version = iwan_claude.__version__
    
    # 获取当前 UTC 时间（格式：YYYY-MM-DD HH:MM UTC）
    now_utc = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    
    # 构建基础 prompt
    base = (
        # --- 身份声明：必须第一时间告诉模型"你是谁"
        # 避免 DeepSeek Anthropic 兼容端点对齐时伪装成 Claude
        f"You are IwanClaude v{version}, a local-first AI coding assistant running on the user's machine. "
        f"Your currently configured language model is: {model_name!r}. "
        "Always refer to yourself as IwanClaude; never claim to be Claude, ChatGPT, or any other AI assistant. "
        f"Current UTC time is {now_utc}. "
        
        # --- 通用行为约定
        "Use the available tools to complete the user's goal. "
        "Prefer safe, focused and small edits; ask for clarification if the goal is ambiguous. "
        "When the goal is fully achieved, respond with a clear final answer and do not call any more tools."
    )
    
    # 如果启用了 RAG，追加 RAG 引导
    if has_rag:
        base += _RAG_GUIDANCE
    
    # 如果有 CLAUDE.md 内容，追加项目上下文
    if claude_md_context:
        base += f"\n\n[Project Context from CLAUDE.md]\n{claude_md_context}"
    
    # 返回完整的 system prompt
    return base


# Provider 层兜底用的简短版身份声明
# 只做身份声明，不依赖具体模型名，极端情况下才会被用到
_FALLBACK_IDENTITY = (
    f"You are IwanClaude v{iwan_claude.__version__}, a local-first AI coding assistant. "
    "Always refer to yourself as IwanClaude. "
)

# Provider 层的兜底 system prompt
# 当没有传入自定义 system prompt 时使用
FALLBACK_SYSTEM_PROMPT = (
    _FALLBACK_IDENTITY
    + "Use the available tools to complete the user's goal. "
    + "When the goal is fully achieved, respond with a final answer and do not call any more tools."
)
