# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入 os 模块，用于读取环境变量（API key）
import os
# 导入 datetime 模块，用于获取当前 UTC 时间
from datetime import UTC, datetime
# 导入 Any 类型，用于类型注解（当类型不确定时使用）
from typing import Any

# 导入 anthropic 库，这是 Anthropic 公司提供的官方 API 客户端
import anthropic

# 导入 LLM 相关的事件模型：模型选择事件、Token 事件、使用量事件
from kama_claude.core.bus.events import LlmModelSelectedEvent, LlmTokenEvent, LlmUsageEvent
# 导入 EventBus 事件总线，用于发布事件
from kama_claude.core.events.bus import EventBus
# 导入 LLM 相关的类型定义：响应、工具调用、使用统计
from kama_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats

# 系统提示词（System Prompt）：告诉 AI 它是什么角色，应该怎么做
# 什么是系统提示词？它是一段固定的指令，告诉 AI 助手的身份、行为准则和任务要求
# 这里的指令是：你是一个有帮助的 AI 助手，使用可用的工具完成用户目标，目标完成后给出最终答案
_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. "
    "Use the available tools to complete the user's goal. "
    "When the goal is fully achieved, respond with a final answer and do not call any more tools."
)


# 获取当前 UTC 时间的 ISO 8601 格式字符串（如 "2026-05-11T07:31:14.022Z"）
# 为什么用 UTC 时间？因为它是标准时间，不受时区影响，便于日志记录和跨机器协作
def _now() -> str:
    return datetime.now(UTC).isoformat()


# AnthropicProvider 类：LLM 提供者的具体实现，用于调用 Anthropic 的 Claude API
# 什么是 LLM 提供者？它是一个封装了 LLM API 调用的类，提供统一的接口供上层使用
# 这里实现了对 Anthropic Claude 模型的调用
class AnthropicProvider:
    # 初始化方法：创建 Anthropic API 客户端
    # 函数作用：初始化 API 客户端，保存模型名称
    # 传参：
    #   model - 要使用的模型名称，如 "claude-3-sonnet-20240229"
    #   client - 可选的客户端实例，用于测试时注入 Mock 客户端（跳过真实 API 调用）
    # 返回值：None
    def __init__(self, model: str, client: Any = None) -> None:
        # 如果没有传入客户端实例（正常使用场景）
        if client is None:
            # 从环境变量中读取 Anthropic API Key
            # 什么是 API Key？它是访问 API 的凭证，就像一把钥匙，没有它就无法调用 API
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            
            # 如果 API Key 没有设置，抛出异常并退出程序
            # 这是一种防御性编程：在使用前检查必要的配置
            if not api_key:
                raise SystemExit("ANTHROPIC_API_KEY not set")
            
            # 创建 Anthropic 的异步客户端
            # 为什么用异步客户端？因为 API 调用是网络请求，异步可以提高性能
            self._client: Any = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            # 如果传入了客户端实例（测试场景），直接使用
            self._client = client
        
        # 保存模型名称，供后续调用使用
        self._model = model

    # 流式调用 Anthropic API，逐 token 发布事件并返回 LlmResponse
    # 函数作用：调用 Claude API，获取 AI 的回复
    # 传参：
    #   messages - 消息历史列表，包含用户问题、AI 回复、工具结果等
    #   tool_schemas - 可用工具的 JSON Schema 列表，告诉 AI 有哪些工具可用
    #   bus - 事件总线，用于发布 LLM 相关事件（如 token 输出、使用量等）
    #   run_id - 运行 ID，用于关联事件
    # 返回值：LlmResponse - LLM 响应对象，包含文本、工具调用、停止原因等
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
    ) -> LlmResponse:
        # 发布模型选择事件：告诉外界"我选择了哪个模型"
        # 这对于监控和日志记录很重要，可以追踪每次调用使用的模型
        await bus.publish(
            LlmModelSelectedEvent(run_id=run_id, model=self._model, strategy="static", ts=_now())
        )

        # 构建系统提示词列表
        # Anthropic API 的 system 参数需要是一个列表，每个元素是一个内容块
        # {"type": "text", "text": "..."} 是文本块的格式
        # cache_control: {"type": "ephemeral"} 表示这段文本不会被缓存（因为是动态的）
        system: list[dict[str, object]] = [
            {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ]

        # 处理工具 Schema：为最后一个工具添加缓存控制
        # 为什么要这样做？因为 Anthropic API 要求最后一个工具定义使用 ephemeral 缓存
        tools: list[dict[str, object]] = list(tool_schemas)
        if tools:
            # 获取最后一个工具的副本（避免修改原始数据）
            last = dict(tools[-1])
            # 添加缓存控制
            last["cache_control"] = {"type": "ephemeral"}
            # 替换最后一个工具
            tools = tools[:-1] + [last]

        # 构建 API 调用的关键字参数
        kwargs: dict[str, object] = {
            "model": self._model,           # 模型名称
            "max_tokens": 4096,            # 最大输出 token 数（限制回复长度）
            "system": system,              # 系统提示词
            "messages": messages,          # 消息历史
        }
        # 如果有工具，添加到参数中
        if tools:
            kwargs["tools"] = tools

        # 用于收集流式输出的文本片段
        text_parts: list[str] = []

        # 使用流式 API 调用：逐 token 获取 AI 的回复
        # 什么是流式调用？就是不等待完整回复，而是随着 AI 生成，逐字获取
        # 好处：用户可以看到实时的打字效果，体验更好
        async with self._client.messages.stream(**kwargs) as stream:
            # 遍历流式输出的文本流
            async for text in stream.text_stream:
                # 发布 token 事件：告诉外界"AI 输出了一个 token"
                # 这让 StdoutPrinter 可以实时显示 AI 的回复
                await bus.publish(LlmTokenEvent(run_id=run_id, token=text, ts=_now()))
                # 将 token 添加到文本片段列表中
                text_parts.append(text)
            
            # 获取最终的完整消息（包含工具调用等信息）
            final_message = await stream.get_final_message()

        # 提取使用量信息：输入 token 数、输出 token 数
        usage = final_message.usage
        # 获取缓存相关的 token 数（用于计算缓存节省）
        # getattr 是安全获取属性的方法，如果属性不存在返回默认值 0
        cache_read: int = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create: int = getattr(usage, "cache_creation_input_tokens", 0) or 0

        # 发布使用量事件：告诉外界"这次调用消耗了多少 token"
        # 这对于成本监控和优化很重要
        await bus.publish(
            LlmUsageEvent(
                run_id=run_id,
                input_tokens=usage.input_tokens,          # 输入 token 数（用户发送的内容）
                output_tokens=usage.output_tokens,        # 输出 token 数（AI 生成的内容）
                cache_read_input_tokens=cache_read,       # 从缓存读取的 token 数
                cache_creation_input_tokens=cache_create, # 创建缓存的 token 数
                ts=_now(),                                # 时间戳
            )
        )

        # 提取工具调用：遍历最终消息的内容块
        tool_calls: list[ToolCallBlock] = []
        for block in final_message.content:
            # 如果内容块类型是工具调用
            if block.type == "tool_use":
                # 创建 ToolCallBlock 对象
                tool_calls.append(
                    ToolCallBlock(id=block.id, name=block.name, input=dict(block.input))
                )

        # 返回 LlmResponse 对象，包含所有关键信息
        return LlmResponse(
            # 停止原因：为什么 AI 停止了？可能是 "end_turn"（结束对话）或 "tool_use"（调用工具）
            stop_reason=final_message.stop_reason or "end_turn",
            # 工具调用列表：AI 请求调用的工具
            tool_calls=tool_calls,
            # 完整的文本回复：将所有文本片段拼接成完整字符串
            text="".join(text_parts),
            # 使用量统计：供上层使用
            usage=UsageStats(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
            ),
        )
