# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 dataclasses：用于将 dataclass 对象转换为字典
import dataclasses
# 导入 time：用于计算 API 调用延迟
import time
# 导入 datetime：用于生成时间戳
from datetime import UTC, datetime
# 导入 Any：类型注解，表示任意类型
from typing import Any

# 导入 EventBus：事件总线
from kama_claude.core.events.bus import EventBus
# 导入 LLMProvider：LLM 提供者的抽象接口
from kama_claude.core.llm.base import LLMProvider
# 导入 LlmResponse：LLM 响应类型
from kama_claude.core.llm.types import LlmResponse
# 导入 TraceRecord：系统级追踪记录模型
from kama_claude.core.trace.record import TraceRecord
# 导入 TraceWriter：非阻塞的追踪记录写入器
from kama_claude.core.trace.writer import TraceWriter


# 返回当前时间的 ISO 格式字符串（UTC 时区）
# 用于生成追踪记录的时间戳
def _now() -> str:
    return datetime.now(UTC).isoformat()


# TracingProvider 类：LLMProvider 的包装器（Wrapper）
# 什么是包装器？就是在不修改原有代码的情况下，为其添加额外功能
# 这里的额外功能是：记录每次 LLM API 调用的请求和响应
class TracingProvider:
    # 初始化方法：接收一个真实的 LLMProvider 和一个 TraceWriter
    # inner：真实的 LLMProvider（如 AnthropicProvider）
    # trace：追踪记录写入器
    # include_payload：是否包含完整的请求/响应数据（默认是）
    def __init__(
        self,
        inner: LLMProvider,          # 被包装的真实 LLMProvider
        trace: TraceWriter,          # 追踪记录写入器
        *,
        include_payload: bool = True,  # 是否记录完整的请求/响应体
    ) -> None:
        # 保存被包装的真实 provider
        self._inner = inner
        # 保存追踪写入器
        self._trace = trace
        # 保存是否包含完整数据的配置
        self._include_payload = include_payload

    # chat 方法：包装真实 provider 的 chat 方法，添加追踪记录
    # 参数和返回值与 LLMProvider.chat() 完全一致
    async def chat(
        self,
        messages: list[dict[str, object]],     # 消息列表
        tool_schemas: list[dict[str, object]], # 工具 Schema 列表
        bus: EventBus,                         # 事件总线
        run_id: str,                           # 运行 ID
        *,
        step: int = 0,                         # 步骤号（可选）
    ) -> LlmResponse:
        # 准备要记录的请求数据
        call_data: dict[str, Any]
        if self._include_payload:
            # 如果包含完整数据，记录 messages 和 tool_schemas
            call_data = {"messages": messages, "tool_schemas": tool_schemas}
        else:
            # 如果不包含完整数据，只记录数量（保护隐私）
            call_data = {
                "message_count": len(messages),
                "tool_count": len(tool_schemas),
            }

        # ========== 追踪埋点 1：记录 CORE→LLM 请求 ==========
        self._trace.emit(
            TraceRecord(
                ts=_now(),                           # 时间戳
                direction="CORE→LLM",                # 数据流向：核心→LLM
                layer="llm",                         # 所在层：LLM 层
                kind="api_call",                     # 记录类型：API 调用
                run_id=run_id,                       # 关联的运行 ID
                step=step,                           # 关联的步骤号
                data=call_data,                      # 请求数据
            )
        )

        # 记录开始时间（用于计算延迟）
        t0 = time.monotonic()
        # 调用真实 provider 的 chat 方法
        # 这里才是真正的 API 调用！
        result = await self._inner.chat(messages, tool_schemas, bus, run_id, step=step)
        # 计算延迟（毫秒）
        latency_ms = int((time.monotonic() - t0) * 1000)

        # 准备要记录的响应数据
        resp_data: dict[str, Any]
        if self._include_payload:
            # 如果包含完整数据，记录 stop_reason、text、tool_calls、usage
            resp_data = {
                "stop_reason": result.stop_reason,
                "text": result.text,
                "tool_calls": [dataclasses.asdict(tc) for tc in result.tool_calls],
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }
        else:
            # 如果不包含完整数据，只记录 stop_reason、usage 和延迟
            resp_data = {
                "stop_reason": result.stop_reason,
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }

        # ========== 追踪埋点 2：记录 LLM→CORE 响应 ==========
        self._trace.emit(
            TraceRecord(
                ts=_now(),                           # 时间戳
                direction="LLM→CORE",                # 数据流向：LLM→核心
                layer="llm",                         # 所在层：LLM 层
                kind="api_response",                 # 记录类型：API 响应
                run_id=run_id,                       # 关联的运行 ID
                step=step,                           # 关联的步骤号
                data=resp_data,                      # 响应数据
            )
        )

        # 返回真实 provider 的结果（不修改结果）
        return result
