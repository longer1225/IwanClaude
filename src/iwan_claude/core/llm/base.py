"""
LLM Provider 协议接口 - 定义所有 LLM Provider 必须实现的方法

【学习要点】
1. Protocol：Python 3.8+ 引入，用于定义协议（类似接口）
2. 鸭子类型：只要实现了协议中的方法，就视为实现了该协议
3. 抽象接口：定义统一的接口，不同 Provider 可以有不同实现
4. 异步方法：chat 方法是 async 方法，支持异步调用

【协议设计原则】
1. 最小化接口：只定义必要的方法，保持简洁
2. 标准化参数：统一的参数格式，便于调用方使用
3. 流式支持：通过 EventBus 发布进度事件，支持流式输出
4. 返回值统一：所有 Provider 返回相同的 LlmResponse 类型
"""
from __future__ import annotations

# Protocol：用于定义协议（类似接口）
from typing import Protocol

# 导入事件总线
from iwan_claude.core.events.bus import EventBus

# 导入响应类型
from iwan_claude.core.llm.types import LlmResponse


class LLMProvider(Protocol):
    """
    LLM Provider 协议接口
    
    【学习要点】
    1. Protocol 定义：使用 class ...(Protocol) 定义协议
    2. 方法签名：只定义方法签名，不实现具体逻辑
    3. 异步方法：使用 async def 定义异步方法
    4. 关键字参数：使用 * 强制使用关键字参数（step 和 system）
    
    【实现类】
    - AnthropicProvider: Anthropic API 兼容实现
    - OpenAICompatibleProvider: OpenAI API 兼容实现
    
    【方法说明】
    chat(): 流式调用 LLM 并发布进度事件，返回完整响应
    """
    
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> LlmResponse:
        """
        流式调用 LLM 并发布进度事件，返回完整响应
        
        参数：
            messages: 消息历史，格式为 [{"role": "user"|"assistant", "content": ...}]
            tool_schemas: 工具 schema 列表，用于告诉 LLM 可用的工具
            bus: 事件总线，用于发布进度事件（TokenDeltaEvent）
            run_id: 运行 ID，用于事件关联
            step: 当前步骤数（默认 0）
            system: 系统提示词（可选）
        
        返回值：
            LlmResponse: LLM 响应对象，包含停止原因、工具调用、文本内容和使用统计
        
        【实现要求】
        1. 必须支持流式调用，通过 bus.publish() 发布 TokenDeltaEvent
        2. 必须处理工具调用，返回正确的 tool_calls
        3. 必须计算使用统计（input_tokens、output_tokens、context_pct）
        4. 必须支持 system prompt 覆盖
        """
        ...
