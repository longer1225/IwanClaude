# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入 asyncio 库，用于异步操作和超时控制
import asyncio
# 导入 time 库，用于计算工具执行耗时
import time
# 导入 datetime 模块，用于获取当前 UTC 时间
from datetime import UTC, datetime
# 导入 cast 函数，用于类型转换（告诉类型检查器某个值的具体类型）
from typing import cast

# 导入工具调用相关的事件模型：开始、完成、失败事件
from kama_claude.core.bus.events import (
    ToolCallFailedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)
# 导入 EventBus 事件总线，用于发布事件
from kama_claude.core.events.bus import EventBus
# 导入 ToolCallBlock，封装工具调用的信息（名称、ID、参数）
from kama_claude.core.llm.types import ToolCallBlock
# 导入 ToolResult，工具执行结果的数据类
from kama_claude.core.tools.base import ToolResult
# 导入 ToolRegistry，工具注册表，用于查找工具
from kama_claude.core.tools.registry import ToolRegistry

# 默认超时时间：10 秒
# 什么是超时？就是如果工具执行时间超过这个时间，就强制停止并返回超时错误
_DEFAULT_TIMEOUT: float = 10.0


# 获取当前 UTC 时间的 ISO 8601 格式字符串（如 "2026-05-11T07:31:14.022Z"）
def _now() -> str:
    return datetime.now(UTC).isoformat()


# 发布 ToolCallFailedEvent 并返回对应 ToolResult
# 这是一个辅助函数，用于统一处理工具调用失败的情况
# 函数作用：发布失败事件，同时返回失败的 ToolResult
# 传参：
#   bus - 事件总线，用于发布失败事件
#   run_id - 运行 ID，用于关联事件
#   tool_call - 工具调用信息（包含工具名称、ID、参数）
#   error_type - 错误类型："runtime_error"、"timeout"、"schema_error"
#   error_message - 详细的错误消息
#   elapsed_ms - 工具执行耗时（毫秒）
# 返回值：ToolResult - 失败的工具结果
async def _fail(
    bus: EventBus,
    run_id: str,
    tool_call: ToolCallBlock,
    error_type: str,
    error_message: str,
    elapsed_ms: int,
) -> ToolResult:
    # 发布工具调用失败事件，通知所有订阅者
    await bus.publish(
        ToolCallFailedEvent(
            run_id=run_id,              # 运行 ID
            tool_use_id=tool_call.id,   # 工具调用 ID（关联开始和结束事件）
            tool_name=tool_call.name,   # 工具名称
            error_type=error_type,     # 错误类型
            error_message=error_message, # 错误消息
            elapsed_ms=elapsed_ms,     # 耗时
            ts=_now(),                 # 时间戳
        )
    )
    # 返回失败的 ToolResult，让上层知道工具调用失败了
    return ToolResult(content=error_message, is_error=True, error_type=error_type)


# 校验参数、限时调用工具、发布进度事件，返回 ToolResult（不抛异常）
# 这是工具调用的核心函数，负责整个工具调用的流程
# 函数作用：执行工具调用，处理所有可能的错误，始终返回 ToolResult（不抛出异常）
# 传参：
#   registry - 工具注册表，用于查找工具
#   tool_call - 工具调用信息（包含工具名称、ID、参数）
#   bus - 事件总线，用于发布事件
#   run_id - 运行 ID，用于关联事件
#   timeout - 超时时间，默认为 10 秒
# 返回值：ToolResult - 工具执行结果（成功或失败）
# 为什么不抛异常？因为工具调用失败不应该终止整个 Agent 运行，应该让 AI 决定怎么处理
async def invoke_tool(
    registry: ToolRegistry,
    tool_call: ToolCallBlock,
    bus: EventBus,
    run_id: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> ToolResult:
    # 记录开始时间，用于计算执行耗时
    t0 = time.monotonic()

    # 发布工具调用开始事件，通知所有订阅者："工具调用开始了"
    await bus.publish(
        ToolCallStartedEvent(
            run_id=run_id,              # 运行 ID
            tool_use_id=tool_call.id,   # 工具调用 ID（用于关联事件）
            tool_name=tool_call.name,   # 工具名称
            params=dict(tool_call.input), # 工具调用参数
            ts=_now(),                 # 时间戳
        )
    )

    # 内部函数：计算从开始到现在的耗时（毫秒）
    # 使用闭包，捕获外部的 t0 变量
    def elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    # ====================== 第一步：查找工具 ======================
    # 根据工具名称在注册表中查找工具实例
    tool = registry.get(tool_call.name)
    
    # 如果工具不存在，返回失败结果
    if tool is None:
        return await _fail(
            bus, run_id, tool_call,
            "runtime_error", f"unknown tool: {tool_call.name}", elapsed(),
        )

    # ====================== 第二步：参数校验 ======================
    # 从工具的 input_schema 中获取必填参数列表
    # cast 是类型转换，告诉类型检查器 "required" 字段是 list[str] 类型
    required: list[str] = cast(list[str], tool.input_schema.get("required", []))
    
    # 找出缺失的必填参数
    # 遍历必填参数列表，检查哪些参数没有在 tool_call.input 中提供
    missing = [p for p in required if p not in tool_call.input]
    
    # 如果有缺失的参数，返回失败结果
    if missing:
        return await _fail(
            bus, run_id, tool_call,
            "schema_error", f"missing required parameters: {', '.join(missing)}", elapsed(),
        )

    # ====================== 第三步：执行工具 ======================
    # 使用 try-except 捕获所有可能的错误
    try:
        # 使用 asyncio.wait_for 执行工具调用，并设置超时
        # 什么是 asyncio.wait_for？它会等待协程完成，但如果超过 timeout 时间还没完成，就抛出 TimeoutError
        result = await asyncio.wait_for(tool.invoke(dict(tool_call.input)), timeout=timeout)
        
        # 计算耗时
        ms = elapsed()
        
        # 如果工具执行结果本身是错误（工具内部返回的错误）
        if result.is_error:
            return await _fail(
                bus, run_id, tool_call,
                result.error_type or "runtime_error", result.content, ms,
            )
        
        # 工具执行成功，发布工具调用完成事件
        await bus.publish(
            ToolCallFinishedEvent(
                run_id=run_id,              # 运行 ID
                tool_use_id=tool_call.id,   # 工具调用 ID
                tool_name=tool_call.name,   # 工具名称
                elapsed_ms=ms,             # 耗时
                ts=_now(),                 # 时间戳
            )
        )
        
        # 返回成功的工具结果
        return result
    
    # 捕获超时错误（工具执行时间过长）
    except TimeoutError:
        return await _fail(
            bus, run_id, tool_call,
            "timeout", f"tool timed out after {timeout}s", elapsed(),
        )
    
    # 捕获其他所有异常（工具执行过程中抛出的任何错误）
    except Exception as exc:
        return await _fail(
            bus, run_id, tool_call,
            "runtime_error", str(exc), elapsed(),
        )
