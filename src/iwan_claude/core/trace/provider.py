"""
追踪提供者

该模块实现了 LLM 调用的追踪功能，通过装饰器模式包裹真实的 LLMProvider。

核心组件：
- TracingProvider: 追踪提供者，包裹 LLMProvider，记录 API 调用

设计要点：
- 使用装饰器模式（Decorator Pattern）包裹 LLMProvider
- 在每次 chat() 调用前后记录追踪信息
- 支持是否包含完整 payload（消息内容、工具定义等）
- 记录 API 调用延迟（latency）
"""

from __future__ import annotations

import dataclasses
import time
from datetime import UTC, datetime
from typing import Any

from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.base import LLMProvider
from iwan_claude.core.llm.types import LlmResponse
from iwan_claude.core.trace.record import TraceRecord
from iwan_claude.core.trace.writer import TraceWriter


def _now() -> str:
    """
    获取当前 UTC 时间的 ISO 8601 格式字符串

    返回：
        str: 当前 UTC 时间的 ISO 8601 格式字符串
    """
    return datetime.now(UTC).isoformat()


class TracingProvider:
    """
    追踪提供者

    包裹真实的 LLMProvider，在每次 chat() 调用前后记录追踪信息。

    工作原理：
    1. 调用前：记录 CORE→LLM 的 API 请求，包含消息数量、工具数量等
    2. 调用真实 provider 的 chat() 方法
    3. 记录调用延迟
    4. 调用后：记录 LLM→CORE 的 API 响应，包含停止原因、文本、工具调用、使用量等
    5. 返回响应结果

    设计模式：
    - 装饰器模式（Decorator Pattern）：在不修改原类的情况下扩展功能
    - 代理模式（Proxy Pattern）：代理真实 provider 的调用

    使用示例：
        >>> provider = AnthropicProvider(api_key="...")
        >>> trace_writer = TraceWriter(Path("traces/llm.jsonl"))
        >>> await trace_writer.start()
        >>> traced_provider = TracingProvider(provider, trace_writer)
        >>> response = await traced_provider.chat(messages, tools, bus, run_id)
    """

    def __init__(
        self,
        inner: LLMProvider,
        trace: TraceWriter,
        *,
        include_payload: bool = True,
    ) -> None:
        """
        初始化追踪提供者

        参数：
            inner: 真实的 LLMProvider 实例
            trace: 追踪写入器，用于记录追踪信息
            include_payload: 是否包含完整的请求和响应 payload，默认为 True

        属性：
            _inner: 真实的 LLMProvider 实例
            _trace: 追踪写入器实例
            _include_payload: 是否包含完整 payload

        使用示例：
            >>> traced_provider = TracingProvider(provider, trace_writer)
            >>> traced_provider = TracingProvider(provider, trace_writer, include_payload=False)
        """
        self._inner = inner
        self._trace = trace
        self._include_payload = include_payload

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
        调用 LLM 并记录追踪信息

        调用真实 provider 的 chat() 方法，并在调用前后记录追踪信息。

        参数：
            messages: 消息列表
            tool_schemas: 工具定义列表
            bus: 事件总线
            run_id: 运行 ID
            step: 步骤编号，默认为 0
            system: 系统提示词，可选

        返回：
            LlmResponse: LLM 响应对象

        追踪记录内容：
        - 请求记录（CORE→LLM）：
          - 包含完整 payload 时：消息列表、工具定义、系统提示词
          - 不包含时：消息数量、工具数量
        - 响应记录（LLM→CORE）：
          - 停止原因、文本、工具调用、使用量、延迟（毫秒）

        实现步骤：
        1. 构建请求追踪数据
        2. 发布请求追踪记录
        3. 记录开始时间
        4. 调用真实 provider 的 chat() 方法
        5. 计算延迟
        6. 构建响应追踪数据
        7. 发布响应追踪记录
        8. 返回响应结果

        使用示例：
            >>> response = await traced_provider.chat(messages, tools, bus, run_id, step=1)
        """
        call_data: dict[str, Any]
        if self._include_payload:
            call_data = {"messages": messages, "tool_schemas": tool_schemas, "system": system}
        else:
            call_data = {
                "message_count": len(messages),
                "tool_count": len(tool_schemas),
            }

        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="CORE→LLM",
                layer="llm",
                kind="api_call",
                run_id=run_id,
                step=step,
                data=call_data,
            )
        )

        t0 = time.monotonic()
        result = await self._inner.chat(
            messages, tool_schemas, bus, run_id, step=step, system=system
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        resp_data: dict[str, Any]
        if self._include_payload:
            resp_data = {
                "stop_reason": result.stop_reason,
                "text": result.text,
                "tool_calls": [dataclasses.asdict(tc) for tc in result.tool_calls],
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }
        else:
            resp_data = {
                "stop_reason": result.stop_reason,
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }

        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="LLM→CORE",
                layer="llm",
                kind="api_response",
                run_id=run_id,
                step=step,
                data=resp_data,
            )
        )

        return result
