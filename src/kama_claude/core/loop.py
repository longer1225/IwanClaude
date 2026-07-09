# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入 asyncio 库，用于异步 I/O 操作和处理任务取消
import asyncio
# 导入 datetime 模块，用于获取当前 UTC 时间
from datetime import UTC, datetime

# 导入步骤开始和步骤结束事件模型，用于发布步骤相关事件
from kama_claude.core.bus.events import StepFinishedEvent, StepStartedEvent
# 导入 ExecutionContext 执行上下文类，用于保存和管理运行状态
from kama_claude.core.context import ExecutionContext
# 导入 EventBus 事件总线，用于发布和订阅事件
from kama_claude.core.events.bus import EventBus
# 导入 LLMProvider 基类，定义 LLM 提供者接口（用于调用大语言模型）
from kama_claude.core.llm.base import LLMProvider
# 导入 invoke_tool 函数，用于执行工具调用
from kama_claude.core.tools.invocation import invoke_tool
# 导入 ToolRegistry 工具注册表，用于管理和获取可用工具的信息
from kama_claude.core.tools.registry import ToolRegistry


# 获取当前 UTC 时间的 ISO 8601 格式字符串（如 "2026-05-11T07:31:14.022Z"）
# 为什么用 UTC 时间？因为它是标准时间，不受时区影响，便于日志记录和跨机器协作
def _now() -> str:
    return datetime.now(UTC).isoformat()


# AgentLoop 类：Agent 的核心执行循环
# 什么是 Agent 循环？简单说就是让 AI 不断思考（plan）→ 行动（act）→ 观察（observe）的过程
# 这个循环会一直运行，直到达到目标或超过最大步骤数
class AgentLoop:
    # 初始化方法：组装循环所需的三个核心依赖
    # 函数作用：创建 AgentLoop 实例，保存依赖供后续循环使用
    # 传参：
    #   provider - LLMProvider 实例，用于调用大语言模型（AI 大脑）
    #   registry - ToolRegistry 实例，管理可用的工具（AI 的手脚）
    #   bus - EventBus 实例，用于发布事件（通知外界发生了什么）
    # 返回值：None
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        bus: EventBus,
    ) -> None:
        # 保存 LLM 提供者（AI 大脑）
        self._provider = provider
        # 保存工具注册表（AI 的手脚）
        self._registry = registry
        # 保存事件总线（消息广播系统）
        self._bus = bus

    # 驱动 plan→act→observe 循环直到上下文终止；CancelledError 向上传播
    # 函数作用：执行完整的 Agent 循环，直到任务完成或失败
    # 传参：context - ExecutionContext 实例，保存运行状态和消息历史
    # 返回值：None
    # 什么是 plan→act→observe？
    #   plan（规划）：调用 LLM，让它分析当前情况，决定下一步做什么
    #   act（行动）：如果 LLM 决定调用工具，执行工具
    #   observe（观察）：记录 LLM 的回复和工具执行结果，更新上下文
    async def run(self, context: ExecutionContext) -> None:
        # 主循环：只要上下文没有终止（status != "running"），就继续执行
        # context.is_done() 返回 True 时表示运行结束（成功或失败）
        while not context.is_done():
            # 步骤计数加 1（从 1 开始）
            # step 用于追踪当前是第几步，也用于判断是否超过最大步骤数
            context.step += 1

            # 发布步骤开始事件，通知所有订阅者："第 X 步开始了"
            # 这些事件会被 StdoutPrinter（终端输出）和 EventWriter（文件记录）捕获
            await self._bus.publish(
                StepStartedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )

            # ====================== [plan] 阶段：调用 LLM ======================
            # 什么是 plan 阶段？就是让 AI 思考：根据当前消息历史，我应该做什么？
            # AI 可能的回答：
            #   1. 直接回答用户（stop_reason = "end_turn"）
            #   2. 调用工具获取信息（stop_reason = "tool_use"）
            try:
                # 调用 LLM 的 chat 方法，传入：
                #   messages - 消息历史（包含用户问题、之前的对话、工具结果）
                #   tool_schemas - 可用工具的 JSON Schema（告诉 AI 有哪些工具可用）
                #   bus - 事件总线（LLM 会通过它发布 token 事件、使用量事件等）
                #   run_id - 运行 ID（用于关联事件）
                response = await self._provider.chat(
                    messages=context.messages,
                    tool_schemas=self._registry.tool_schemas(),
                    bus=self._bus,
                    run_id=context.run_id,
                )
            except asyncio.CancelledError:
                # 如果收到取消信号（如用户按 Ctrl+C），标记为取消并重新抛出异常
                # 为什么要重新抛出？因为上层（runner.py）需要知道被取消了
                context.mark_failed("cancelled")
                raise
            except Exception:
                # 如果 LLM 调用失败（如网络错误、API 错误），标记为 LLM 错误并退出循环
                context.mark_failed("llm_error")
                break

            # ====================== [observe] 阶段：记录 LLM 回复 ======================
            # 什么是 observe 阶段？就是记录 AI 的思考过程，更新上下文，供下一步使用
            # 构建 assistant 消息的内容块列表
            blocks: list[dict[str, object]] = []

            # 如果 LLM 返回了文本内容，添加文本块
            # 文本块格式：{"type": "text", "text": "AI 的回答"}
            if response.text:
                blocks.append({"type": "text", "text": response.text})

            # 如果 LLM 返回了工具调用请求，为每个工具调用添加工具使用块
            # 工具调用块格式：{"type": "tool_use", "id": "工具调用ID", "name": "工具名", "input": {"参数"}}
            for tc in response.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                )

            # 将构建好的内容块追加到上下文的消息历史中
            # 这一步非常重要：它让 AI 在下一步能看到自己之前的回答
            context.add_assistant_message(blocks)

            # ====================== [act] 阶段：执行工具调用 ======================
            # 什么是 act 阶段？就是执行 AI 决定调用的工具
            # response.stop_reason == "tool_use" 表示 AI 决定调用工具
            if response.stop_reason == "tool_use":
                # 遍历所有工具调用请求
                for tc in response.tool_calls:
                    # 调用 invoke_tool 函数执行工具
                    # 传入：
                    #   registry - 工具注册表（用于查找工具）
                    #   tc - 工具调用请求（包含工具名和参数）
                    #   bus - 事件总线（用于发布工具调用相关事件）
                    #   run_id - 运行 ID
                    result = await invoke_tool(
                        self._registry, tc, self._bus, context.run_id
                    )

                    # 将工具执行结果追加到上下文的消息历史中
                    # is_error=True 表示工具执行失败，False 表示成功
                    context.add_tool_result(tc.id, result.content, is_error=result.is_error)

            # ====================== 终止检查 ======================
            # 判断循环是否应该结束：
            # 1. 如果 AI 决定结束对话（stop_reason == "end_turn"），标记为成功
            # 2. 如果超过最大步骤数，标记为失败
            # 注意：end_turn 的优先级高于 max_steps，如果同一步骤同时满足，优先成功

            # AI 决定结束对话（直接回答了用户的问题）
            if response.stop_reason == "end_turn":
                context.mark_success()
            # 超过最大步骤数（防止无限循环）
            elif context.step >= context.max_steps:
                context.mark_failed("exceeded_max_steps")

            # 发布步骤结束事件，通知所有订阅者："第 X 步结束了"
            await self._bus.publish(
                StepFinishedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )
