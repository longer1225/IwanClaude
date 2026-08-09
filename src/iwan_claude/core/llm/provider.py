"""
Anthropic Provider - Anthropic API 兼容的 LLM Provider 实现

【学习要点】
1. API 封装：封装 Anthropic SDK，提供统一的 chat() 接口
2. 流式调用：使用 stream() 方法实现流式输出
3. 错误重试：网络中断时自动重试，带指数退避
4. 事件发布：通过 EventBus 发布进度事件（模型选择、token、使用统计）
5. 多厂商支持：通过 base_url 参数支持 DeepSeek 等兼容 Anthropic API 的厂商

【核心特性】
- 原生支持 Anthropic Messages API
- 通过 base_url 支持 DeepSeek（https://api.deepseek.com/anthropic）
- 自动重试机制（最多 3 次，指数退避）
- 流式输出，逐 token 发布事件
- 完整的使用统计（token 消耗、缓存使用、上下文占用率）
"""
from __future__ import annotations

# asyncio：异步 I/O 框架
# logging：日志记录
# os：操作系统接口（读取环境变量）
# datetime：日期时间处理
# typing：类型提示
import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

# Anthropic SDK：用于调用 Anthropic API
import anthropic
# httpx：HTTP 客户端（用于检测网络错误）
import httpx

# 导入事件类型
from iwan_claude.core.bus.events import LlmModelSelectedEvent, LlmTokenEvent, LlmUsageEvent

# 导入核心组件
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats
from iwan_claude.core.system_prompt import FALLBACK_SYSTEM_PROMPT

# 模型上下文窗口映射表
# key: 模型名称，value: 最大 token 数
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-opus-4-7": 200_000,
}

# 最大重试次数
_MAX_STREAM_RETRIES = 3

# 重试退避时间（秒）：1s, 2s, 4s
_RETRY_BACKOFF_S = (1.0, 2.0, 4.0)

# 获取当前模块的日志记录器
log = logging.getLogger(__name__)


def _context_window(model: str) -> int:
    """
    返回指定模型的最大上下文窗口 token 数
    
    参数：
        model: 模型名称
    
    返回值：
        int: 最大上下文窗口 token 数（默认 200_000）
    
    【设计说明】
    使用字典映射模型名称到上下文窗口大小，
    如果模型不在映射表中，默认使用 200_000。
    """
    return _MODEL_CONTEXT_WINDOWS.get(model, 200_000)


# Provider 层兜底 system prompt
# 极端情况下才会用到：主循环忘传 system 时
# 优先使用 loop / runner 里的完整版
_SYSTEM_PROMPT = FALLBACK_SYSTEM_PROMPT


def _now() -> str:
    """
    返回当前 UTC 时间的 ISO 8601 格式字符串
    
    返回值：
        str: 格式如 "2024-01-01T12:00:00+00:00" 的时间字符串
    """
    return datetime.now(UTC).isoformat()


class AnthropicProvider:
    """
    Anthropic Provider 类 - Anthropic API 兼容的 LLM Provider 实现
    
    【学习要点】
    1. 多厂商支持：通过 base_url 参数支持 DeepSeek 等兼容 Anthropic API 的厂商
    2. API Key 管理：从环境变量读取，支持自定义环境变量名
    3. 依赖注入：支持注入 mock client 用于测试
    4. 上下文窗口：支持配置覆盖硬编码的默认值
    
    【支持的厂商】
    - Anthropic: 原生支持（默认 base_url）
    - DeepSeek: 通过 base_url=https://api.deepseek.com/anthropic 支持
    - 其他兼容 Anthropic Messages API 的厂商
    """
    
    def __init__(
        self,
        model: str,
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: str | None = None,
        context_window_override: int | None = None,
        client: Any = None,
    ) -> None:
        """
        构造函数 - 初始化 Anthropic 客户端
        
        参数：
            model: 模型名称
                   DeepSeek 用 deepseek-chat / deepseek-reasoner，
                   或直接传 claude-* 让后端自动映射
            api_key_env: API Key 从哪个环境变量读（默认 ANTHROPIC_API_KEY）
            base_url: 若传入，则覆盖默认的 api.anthropic.com
                      DeepSeek 填 https://api.deepseek.com/anthropic
            context_window_override: 若传入，则覆盖硬编码的 _MODEL_CONTEXT_WINDOWS
            client: 测试时可注入 mock client，跳过 API Key 检查
        
        【初始化流程】
        1. 保存上下文窗口覆盖值
        2. 如果没有传入 client，创建 AsyncAnthropic 客户端：
           - 从环境变量读取 API Key
           - 检查 API Key 是否存在
           - 构造客户端（支持自定义 base_url）
        3. 如果传入了 client，直接使用（用于测试）
        4. 保存模型名称
        """
        # 保存上下文窗口覆盖值
        self._context_window_override = context_window_override
        
        # 如果没有传入 client，创建真实的 AsyncAnthropic 客户端
        if client is None:
            # 从环境变量读取 API Key
            # 优先使用配置的环境变量名，其次使用 ANTHROPIC_API_KEY
            api_key = os.environ.get(api_key_env) or os.environ.get("ANTHROPIC_API_KEY")
            
            # 检查 API Key 是否存在
            if not api_key:
                raise SystemExit(f"{api_key_env} (or ANTHROPIC_API_KEY) not set")
            
            # 【ASCII 校验】
            # HTTP 头（x-api-key）必须是 ASCII，否则 httpx 在构建 headers 时会抛
            # UnicodeEncodeError: 'ascii' codec can't encode characters ... 且不指出具体哪个字段。
            # 这里尽早检查，给出用户可读的错误（并标出第一个非法字符位置）。
            non_ascii = [(i, c) for i, c in enumerate(api_key) if ord(c) > 127]
            if non_ascii:
                bad_pos, bad_ch = non_ascii[0]
                raise SystemExit(
                    f"API key from env[{api_key_env}] contains non-ASCII char "
                    f"'{bad_ch}' (U+{ord(bad_ch):04X}) at position {bad_pos}. "
                    "Most likely you accidentally pasted Chinese prompt text / markdown quotes into the env var. "
                    "Re-run: $env:DEEPSEEK_API_KEY = 'sk-xxxxxxxx' "
                    "(use single straight quotes, no backticks / Chinese)."
                )
            
            # 构造客户端参数
            client_kwargs: dict[str, Any] = {"api_key": api_key}
            
            # 如果传入了 base_url，添加到客户端参数
            # 关键：DeepSeek 填了 base_url=https://api.deepseek.com/anthropic 就能用原生 Anthropic Messages API
            if base_url:
                client_kwargs["base_url"] = base_url
            
            # 创建 AsyncAnthropic 客户端
            self._client: Any = anthropic.AsyncAnthropic(**client_kwargs)
        else:
            # 使用传入的 client（用于测试）
            self._client = client
        
        # 保存模型名称
        self._model = model

    def _effective_context_window(self) -> int:
        """
        返回有效的上下文窗口大小
        
        【学习要点】
        1. 优先级：配置覆盖 > 硬编码映射 > 默认值
        2. 零值检查：确保覆盖值大于 0 才使用
        
        返回值：
            int: 有效的上下文窗口 token 数
        """
        # 如果有配置覆盖且大于 0，使用覆盖值
        if self._context_window_override and self._context_window_override > 0:
            return self._context_window_override
        # 否则从映射表中查找
        return _context_window(self._model)

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
        流式调用 Anthropic API，逐 token 发布事件并返回 LlmResponse
        
        【学习要点】
        1. 流式调用：使用 stream() 方法实现流式输出，逐 token 发布事件
        2. 错误重试：网络中断时自动重试，带指数退避（最多 3 次）
        3. 事件发布：通过 EventBus 发布模型选择、token 和使用统计事件
        4. 消息格式：Anthropic Messages API 的特殊格式（system blocks、tool schemas）
        5. Thinking Blocks：支持扩展思考模式，原样保留在对话历史中
        
        参数：
            messages: 消息历史，格式为 [{"role": "user"|"assistant", "content": ...}]
            tool_schemas: 工具 schema 列表，用于告诉 LLM 可用的工具
            bus: 事件总线，用于发布进度事件
            run_id: 运行 ID，用于事件关联
            step: 当前步骤数（默认 0）
            system: 系统提示词（可选）
        
        返回值：
            LlmResponse: LLM 响应对象，包含停止原因、工具调用、文本内容和使用统计
        
        【执行流程】
        1. 发布 LlmModelSelectedEvent（模型选择事件）
        2. 构建 system blocks 和工具列表
        3. 构建 API 请求参数
        4. 发起流式调用（带重试机制）
        5. 逐 token 收集文本并发布 LlmTokenEvent
        6. 获取最终消息，解析工具调用和思考块
        7. 计算使用统计并发布 LlmUsageEvent
        8. 返回 LlmResponse
        """
        # 发布模型选择事件（用于 TUI 显示当前模型）
        await bus.publish(
            LlmModelSelectedEvent(run_id=run_id, model=self._model, strategy="static", ts=_now())
        )

        # 构建 system blocks
        # Anthropic API 要求 system prompt 是一个列表，每个元素是一个 block
        # cache_control 设置为 ephemeral，表示不缓存 system prompt
        system_blocks: list[dict[str, object]] = [
            {
                "type": "text",
                "text": system or _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
        ]

        # 构建工具列表
        # Anthropic API 要求最后一个工具的 cache_control 设置为 ephemeral
        tools: list[dict[str, object]] = list(tool_schemas)
        if tools:
            last = dict(tools[-1])
            last["cache_control"] = {"type": "ephemeral"}
            tools = tools[:-1] + [last]

        # 构建 API 请求参数
        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": 8192,
            "system": system_blocks,
            "messages": messages,
        }
        # 如果有工具，添加到请求参数
        if tools:
            kwargs["tools"] = tools

        # 存储流式返回的文本部分
        text_parts: list[str] = []
        # 存储最终消息（包含完整的 tool_calls、usage 等）
        final_message: Any = None

        # 带重试的流式调用
        for attempt in range(1, _MAX_STREAM_RETRIES + 1):
            text_parts = []
            try:
                # 使用上下文管理器发起流式调用
                async with self._client.messages.stream(**kwargs) as stream:
                    # 逐 token 读取文本流
                    async for text in stream.text_stream:
                        # 只在第一次尝试时发布 token 事件，避免 TUI 重复显示
                        if attempt == 1:
                            await bus.publish(LlmTokenEvent(run_id=run_id, token=text, ts=_now()))
                        # 收集文本部分
                        text_parts.append(text)
                    # 获取最终消息（包含完整信息）
                    final_message = await stream.get_final_message()
                # 成功，退出循环
                break
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as exc:
                # 网络错误，检查是否是最后一次尝试
                if attempt == _MAX_STREAM_RETRIES:
                    # 最后一次尝试失败，记录错误并抛出异常
                    log.error(
                        "stream failed after %d attempts run_id=%s step=%d: %s",
                        _MAX_STREAM_RETRIES, run_id, step, exc,
                    )
                    raise
                # 获取退避时间并等待
                delay = _RETRY_BACKOFF_S[attempt - 1]
                log.warning(
                    "stream dropped (attempt %d/%d) run_id=%s step=%d: %s — retrying in %.0fs",
                    attempt, _MAX_STREAM_RETRIES, run_id, step, exc, delay,
                )
                await asyncio.sleep(delay)

        # 确保最终消息不为 None
        assert final_message is not None

        # 解析使用统计
        usage = final_message.usage
        # 缓存读取的 token 数（如果 API 不支持，默认为 0）
        cache_read: int = getattr(usage, "cache_read_input_tokens", 0) or 0
        # 缓存创建的 token 数（如果 API 不支持，默认为 0）
        cache_create: int = getattr(usage, "cache_creation_input_tokens", 0) or 0
        # 计算上下文占用率
        ctx_window = max(self._effective_context_window(), 1)
        context_pct = min(usage.input_tokens / ctx_window, 1.0)

        # 发布使用统计事件
        await bus.publish(
            LlmUsageEvent(
                run_id=run_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                context_pct=context_pct,
                ts=_now(),
            )
        )

        # 解析工具调用和思考块
        tool_calls: list[ToolCallBlock] = []
        thinking_blocks: list[dict[str, object]] = []
        for block in final_message.content:
            if block.type == "tool_use":
                # 工具调用块
                tool_calls.append(
                    ToolCallBlock(id=block.id, name=block.name, input=dict(block.input))
                )
            elif block.type == "thinking":
                # 思考块（扩展思考模式）
                # thinking blocks must be passed back verbatim in subsequent requests
                thinking_blocks.append({"type": "thinking", "thinking": block.thinking, "signature": block.signature})

        # 返回 LlmResponse
        return LlmResponse(
            stop_reason=final_message.stop_reason or "end_turn",
            tool_calls=tool_calls,
            text="".join(text_parts),
            thinking_blocks=thinking_blocks,
            usage=UsageStats(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                context_pct=context_pct,
            ),
        )
