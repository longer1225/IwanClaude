"""
LLM 类型定义 - 定义 LLM 响应相关的数据结构

【学习要点】
1. dataclass：Python 3.7+ 引入，自动生成 __init__、__repr__、__eq__ 等方法
2. 默认值：使用 field(default_factory=...) 设置复杂类型的默认值
3. 类型提示：明确属性类型，便于代码阅读和类型检查
4. 数据契约：定义 Provider 和调用方之间的数据交换格式

【核心类型】
- UsageStats: LLM 使用统计（token 消耗、缓存使用、上下文占用率）
- ToolCallBlock: 工具调用块（ID、名称、输入参数）
- LlmResponse: LLM 响应（停止原因、工具调用、文本内容、使用统计）
"""
from __future__ import annotations

# dataclass：数据类装饰器
from dataclasses import dataclass, field


@dataclass
class UsageStats:
    """
    LLM 使用统计数据类
    
    【学习要点】
    1. Token 统计：input_tokens 和 output_tokens 分别记录输入和输出 token 数
    2. 缓存统计：cache_read_input_tokens 和 cache_creation_input_tokens 记录缓存使用
    3. 上下文占用率：context_pct 表示当前上下文占模型最大上下文的百分比（0.0-1.0）
    
    属性：
        input_tokens: 输入 token 数
        output_tokens: 输出 token 数
        cache_read_input_tokens: 从缓存读取的输入 token 数（用于计算缓存节省）
        cache_creation_input_tokens: 创建缓存的输入 token 数
        context_pct: 上下文占用率（0.0-1.0），用于判断是否需要压缩
    """
    input_tokens: int                    # 输入 token 数
    output_tokens: int                   # 输出 token 数
    cache_read_input_tokens: int = 0     # 从缓存读取的输入 token 数
    cache_creation_input_tokens: int = 0 # 创建缓存的输入 token 数
    context_pct: float = 0.0            # 上下文占用率（0.0-1.0）


@dataclass
class ToolCallBlock:
    """
    工具调用块数据类
    
    【学习要点】
    1. 工具调用格式：每个工具调用包含 ID、名称和输入参数
    2. ID 关联：tool_use_id 用于关联工具调用和工具结果
    3. 输入参数：input 是字典，包含工具所需的所有参数
    
    属性：
        id: 工具调用 ID（用于关联工具结果）
        name: 工具名称（必须与工具注册表中的名称一致）
        input: 工具输入参数（字典格式）
    
    【使用场景】
    LLM 返回工具调用时，会生成一个或多个 ToolCallBlock，
    调用方根据 name 查找工具，使用 input 作为参数执行工具，
    执行完成后使用 id 关联工具结果。
    """
    id: str                              # 工具调用 ID（用于关联结果）
    name: str                            # 工具名称
    input: dict[str, object]             # 工具输入参数


@dataclass
class LlmResponse:
    """
    LLM 响应数据类
    
    【学习要点】
    1. 停止原因：stop_reason 表示 LLM 停止生成的原因
    2. 工具调用：tool_calls 是工具调用列表（可能为空）
    3. 文本内容：text 是纯文本响应（可能为空）
    4. 使用统计：usage 记录 token 消耗和上下文占用率
    5. Thinking Blocks：thinking_blocks 用于扩展思考模式
    
    属性：
        stop_reason: 停止原因（"end_turn" | "tool_use" | "max_tokens" | ...）
        tool_calls: 工具调用列表（默认空列表）
        text: 纯文本响应（默认空字符串）
        usage: 使用统计（默认 None）
        thinking_blocks: 思考块列表（用于扩展思考模式，默认空列表）
    
    【停止原因说明】
    - "end_turn": LLM 表示对话结束，返回了最终答案
    - "tool_use": LLM 请求调用工具
    - "max_tokens": 达到 token 限制
    - "error": 发生错误
    
    【重要设计点】
    thinking_blocks 必须原样保留在对话历史中，
    用于支持扩展思考模式（extended thinking），
    让模型能够展示推理过程。
    """
    stop_reason: str                     # 停止原因（"end_turn" | "tool_use" | ...）
    tool_calls: list[ToolCallBlock] = field(default_factory=list)  # 工具调用列表
    text: str = ""                       # 纯文本响应
    usage: UsageStats | None = None      # 使用统计
    # thinking blocks from extended thinking — must be preserved verbatim in conversation history
    thinking_blocks: list[dict[str, object]] = field(default_factory=list)  # 思考块列表
