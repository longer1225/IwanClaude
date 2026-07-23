"""
AgentLoop 模块 - Legacy 执行引擎，实现经典的 Plan-Act-Observe 循环

【学习要点】
1. Plan-Act-Observe 模式：Agent 执行的经典循环模式
   - Plan：调用 LLM 生成计划和工具调用
   - Act：执行工具调用
   - Observe：观察结果并更新上下文
2. 事件驱动：每一步开始和结束都发布事件，便于跟踪和调试
3. 错误处理：区分不同类型的错误（LLM 错误、工具错误、取消操作）
4. 会话压缩：当上下文过长时自动压缩，避免超出模型上下文限制
5. 终止条件：支持多种终止条件（end_turn、max_steps、cancelled、llm_error）

【核心流程】
1. 检查是否达到终止条件
2. 调用 LLM 获取响应（Plan 阶段）
3. 解析响应并更新上下文（Observe 阶段）
4. 执行工具调用（Act 阶段）
5. 检查压缩条件并执行压缩
6. 重复循环直到终止
"""
from __future__ import annotations

# asyncio：异步 I/O 框架
# datetime：日期时间处理
# TYPE_CHECKING：类型检查时才导入，避免循环导入
import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

# 导入事件类型
from iwan_claude.core.bus.events import StepFinishedEvent, StepStartedEvent

# 导入核心组件
from iwan_claude.core.compact.compactor import Compactor       # 会话压缩器
from iwan_claude.core.context import ExecutionContext           # 执行上下文
from iwan_claude.core.effort import get_effort_params           # 努力等级参数查询
from iwan_claude.core.events.bus import EventBus                # 事件总线
from iwan_claude.core.llm.base import LLMProvider               # LLM 提供者接口
from iwan_claude.core.permissions.manager import PermissionManager  # 权限管理器
from iwan_claude.core.system_prompt import build_base_system_prompt  # 构建基础 system prompt
from iwan_claude.core.tools.invocation import invoke_tool       # 工具调用函数
from iwan_claude.core.tools.registry import ToolRegistry        # 工具注册表
import logging

# 类型检查时的导入（避免循环导入）
if TYPE_CHECKING:
    pass


# 获取当前模块的日志记录器
log = logging.getLogger(__name__)

def _now() -> str:
    """
    获取当前 UTC 时间的 ISO 8601 格式字符串
    
    返回值：
        str: 格式如 "2024-01-01T12:00:00+00:00" 的时间字符串
    """
    return datetime.now(UTC).isoformat()


class AgentLoop:
    """
    AgentLoop 类 - Legacy 执行引擎，实现经典的 Plan-Act-Observe 循环
    
    【学习要点】
    1. 依赖注入：通过构造函数注入所有依赖，便于测试和扩展
    2. 关键字参数：使用 * 强制使用关键字参数，提高代码可读性
    3. 模块化设计：将不同功能拆分为独立组件（Provider、Registry、Bus 等）
    
    【核心组件】
    - provider: LLM 提供者，负责调用 LLM API
    - registry: 工具注册表，管理可用工具
    - bus: 事件总线，发布和订阅系统事件
    - permission_manager: 权限管理器，控制工具调用权限（可选）
    - compactor: 会话压缩器，自动压缩过长的会话历史（可选）
    """
    
    # 初始化循环所需依赖：LLM provider、工具注册表、事件总线，以及可选的权限管理器、压缩器和 session ID
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        bus: EventBus,
        *,
        llm_model_name: str = "",
        permission_manager: PermissionManager | None = None,
        compactor: Compactor | None = None,
        compact_threshold: float = 0.0,
        session_id: str = "",
        has_rag: bool = False,
        effort_level: str = "medium",
    ) -> None:
        """
        构造函数 - 注入执行循环所需的所有依赖

        参数：
            provider: LLM 提供者，负责调用 LLM API
            registry: 工具注册表，管理可用工具
            bus: 事件总线，用于发布和订阅系统事件
            llm_model_name: LLM 模型名称，用于构建 system prompt
            permission_manager: 权限管理器，控制工具调用权限（可选）
            compactor: 会话压缩器，用于自动压缩会话历史（可选）
            compact_threshold: 压缩阈值（0.0-1.0），当上下文占用率超过此值时触发压缩
            session_id: 会话 ID，用于权限检查和会话压缩（可选）
            has_rag: 是否启用 RAG 功能，影响 system prompt 的构建
            effort_level: 努力等级（minimal / low / medium / high / max），控制执行深度
        """
        # LLM 提供者：负责调用 LLM API
        self._provider = provider

        # 工具注册表：管理所有可用工具
        self._registry = registry

        # 事件总线：用于发布步骤开始和结束事件
        self._bus = bus

        # LLM 模型名称：用于构建 system prompt
        self._llm_model_name = llm_model_name

        # 权限管理器：控制工具调用权限
        self._permission_manager = permission_manager

        # 会话压缩器：自动压缩过长的会话历史
        self._compactor = compactor

        # 压缩阈值：当上下文占用率超过此值时触发压缩
        self._compact_threshold = compact_threshold

        # 会话 ID：用于权限检查和会话压缩
        self._session_id = session_id

        # 是否启用 RAG：影响 system prompt 的构建
        self._has_rag = has_rag

        # 努力等级参数：控制文件读取数、验证轮数、搜索深度等
        self._effort_params = get_effort_params(effort_level)

    async def run(self, context: ExecutionContext) -> None:
        """
        执行 Plan-Act-Observe 循环，直到上下文达到终止条件
        
        【学习要点】
        1. Plan-Act-Observe 模式：
           - Plan：调用 LLM 生成计划和工具调用
           - Act：执行工具调用
           - Observe：观察结果并更新上下文
        2. 事件发布：每一步开始和结束都发布事件，便于跟踪和调试
        3. 错误处理：区分不同类型的错误（LLM 错误、工具错误、取消操作）
        4. 终止条件：支持多种终止条件（end_turn、max_steps、cancelled、llm_error）
        5. 会话压缩：当上下文过长时自动压缩，避免超出模型上下文限制
        
        参数：
            context: 执行上下文，包含消息历史、运行状态等
        
        【执行流程】
        1. 检查终止条件：while not context.is_done()
        2. 发布 StepStartedEvent
        3. [Plan] 调用 LLM 获取响应
        4. [Observe] 解析响应并更新上下文（thinking、text、tool_use）
        5. [Act] 执行工具调用（如果有）
        6. 检查终止条件（end_turn、max_steps）
        7. 检查压缩条件并执行压缩
        8. 发布 StepFinishedEvent
        9. 重复循环
        """
        # 主循环：直到上下文达到终止条件
        # 如果努力等级指定了 max_steps_override，使用它替换 context 的 max_steps
        if self._effort_params.max_steps_override > 0:
            context.max_steps = self._effort_params.max_steps_override
        # 记录已读取的文件数（用于努力等级限制）
        files_read_count = 0

        while not context.is_done():
            # 增加步骤计数器
            context.step += 1
            
            # 发布步骤开始事件
            await self._bus.publish(
                StepStartedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )

            # ========== [Plan] 阶段：调用 LLM ==========
            try:
                response = await self._provider.chat(
                    messages=context.messages,                  # 消息历史
                    tool_schemas=self._registry.tool_schemas(), # 工具 schema 列表
                    bus=self._bus,                              # 事件总线（用于发布 LLM 调用事件）
                    run_id=context.run_id,                      # 运行 ID
                    step=context.step,                          # 当前步骤
                    # 构建 system prompt（包含模型名称、RAG 状态和 CLAUDE.md 上下文）
                    system=context.system_prompt(
                            build_base_system_prompt(self._llm_model_name, has_rag=self._has_rag, claude_md_context=context.claude_md_context)
                        ),
                )
            except asyncio.CancelledError:
                # 用户取消操作：标记失败并重新抛出异常
                context.mark_failed("cancelled")
                raise
            except Exception:
                # LLM 调用失败：记录日志并标记失败
                logging.getLogger(__name__).exception(
                    "LLM call failed run_id=%s step=%d", context.run_id, context.step
                )
                context.mark_failed("llm_error")
                break

            # ========== [Observe] 阶段：解析响应并更新上下文 ==========
            # thinking blocks 必须放在最前面，用于扩展思考模式
            blocks: list[dict[str, object]] = list(response.thinking_blocks)
            
            # 如果有文本响应，添加 text block
            if response.text:
                blocks.append({"type": "text", "text": response.text})
            
            # 如果有工具调用，添加 tool_use block
            for tc in response.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                )
            
            # 将 assistant 消息添加到上下文
            context.add_assistant_message(blocks)

            # ========== [Act] 阶段：执行工具调用 ==========
            if response.stop_reason == "tool_use":
                # LLM 返回了工具调用，执行每个工具
                for tc in response.tool_calls:
                    # 努力等级限制：如果达到最大文件读取数，跳过后续读操作
                    if self._effort_params.max_files_read > 0 and tc.name in ("read_file", "list_dir", "file_exists", "file_stat"):
                        if files_read_count >= self._effort_params.max_files_read:
                            context.add_tool_result(
                                tc.id,
                                f"Error: effort level limit reached ({self._effort_params.max_files_read} files max). "
                                "Increase effort level to read more files.",
                                is_error=True,
                            )
                            continue
                        files_read_count += 1
                    result = await invoke_tool(
                        self._registry,              # 工具注册表
                        tc,                          # 工具调用请求
                        self._bus,                   # 事件总线
                        context.run_id,              # 运行 ID
                        permission_manager=self._permission_manager,  # 权限管理器
                        session_id=self._session_id,                  # 会话 ID
                    )
                    # 将工具执行结果添加到上下文
                    context.add_tool_result(tc.id, result.content, is_error=result.is_error)
            elif response.stop_reason == "max_tokens" and response.tool_calls:
                # 输出 token 限制被触发，工具调用不完整
                # 添加合成错误结果，保持对话平衡
                for tc in response.tool_calls:
                    context.add_tool_result(
                        tc.id,
                        "Error: output token limit reached before this tool call could be completed. "
                        "Please break the task into smaller steps and try again.",
                        is_error=True,
                    )

            # ========== 终止条件检查 ==========
            # end_turn 优先于 max_steps（如果在同一步骤同时达到）
            if response.stop_reason == "end_turn":
                # LLM 表示对话结束
                context.result = response.text or ""
                context.mark_success()
            elif context.step >= context.max_steps:
                # 达到最大步骤数
                context.mark_failed("exceeded_max_steps")

            # ========== 会话压缩检查 ==========
            # 工具结果追加完毕（messages 末尾为 user）后检查压缩
            # 仅在 run 继续时触发，此时压缩结果 [user_summary, assistant_ack]
            # 对下一次 LLM 调用是合法输入
            if (
                not context.is_done()                               # 运行尚未结束
                and response.stop_reason == "tool_use"              # 上一步是工具调用
                and self._compactor is not None                     # 压缩器存在
                and self._compact_threshold > 0                     # 压缩阈值大于 0
                and response.usage is not None                      # 有使用信息
                and response.usage.context_pct >= self._compact_threshold  # 上下文占用率超过阈值
            ):
                # 执行会话压缩
                await self._compactor.compact(context, self._provider)

            # 发布步骤结束事件
            await self._bus.publish(
                StepFinishedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )
