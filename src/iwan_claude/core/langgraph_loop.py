"""
LangGraphAgentLoop 模块 - 使用 LangGraph 构建的高级执行引擎

【学习要点】
1. LangGraph 工作流：使用 StateGraph 定义状态机，支持节点和边
2. 状态管理：使用 TypedDict 定义状态结构，支持类型检查
3. 条件路由：根据状态值动态决定下一步执行哪个节点
4. Checkpoint：自动保存状态快照，支持中断和恢复
5. 节点职责分离：每个节点只负责一个特定任务

【核心概念】
- StateGraph：LangGraph 的核心类，用于定义工作流图
- Node：工作流中的一个步骤，接收状态并返回更新后的状态
- Edge：连接节点的边，定义执行顺序
- Conditional Edge：条件边，根据状态决定路由
- Checkpointer：状态持久化管理器

【工作流节点】
1. chat：调用 LLM 获取响应
2. tools：执行工具调用
3. compact：压缩会话历史
4. end：结束工作流

【路由逻辑】
- chat → tools：当 stop_reason 为 tool_use
- chat → compact：当上下文过长或 max_tokens
- chat → end：当 stop_reason 为 end_turn 或 error
- tools → chat：工具执行完成后继续对话
- tools → compact：工具执行后上下文过长
- tools → end：工具执行出错
- compact → chat：压缩后继续对话
- compact → end：压缩后结束
"""
from __future__ import annotations

# asyncio：异步 I/O 框架
import asyncio
# LangGraph 核心类：StateGraph 用于定义工作流，START 和 END 是特殊节点
from langgraph.graph import END, START, StateGraph
from typing import Any, Literal, TypedDict

# 导入事件类型
from iwan_claude.core.bus.events import StepFinishedEvent, StepStartedEvent

# 导入核心组件
from iwan_claude.core.compact.compactor import Compactor       # 会话压缩器
from iwan_claude.core.context import ExecutionContext           # 执行上下文
from iwan_claude.core.effort import get_effort_params           # 努力等级参数查询
from iwan_claude.core.events.bus import EventBus                # 事件总线
from iwan_claude.core.llm.base import LLMProvider               # LLM 提供者接口
from iwan_claude.core.llm.types import ToolCallBlock            # LLM 响应类型
# 消息块构造共享工具（与 plan_execute/debate/pipeline 引擎共用）
from iwan_claude.core.message_blocks import (
    _add_tool_result_to_messages,
    _assistant_msg_from_response,
    _extract_last_assistant_text,
    _extract_user_goal,
    _now,
)
from iwan_claude.core.permissions.manager import PermissionManager  # 权限管理器
from iwan_claude.core.run_registry import pop_steers, steer_as_message  # 运行中修正队列
from iwan_claude.core.system_prompt import build_base_system_prompt  # 构建基础 system prompt
from iwan_claude.core.tools.invocation import invoke_tool       # 工具调用函数
from iwan_claude.core.tools.registry import ToolRegistry        # 工具注册表
import logging

# 获取当前模块的日志记录器
log = logging.getLogger(__name__)

# 单次 run 允许的最大压缩次数，防止 compact↔chat 死循环
_MAX_COMPACT_PER_RUN = 2


class AgentState(TypedDict):
    """
    LangGraph 工作流状态定义
    
    【学习要点】
    1. TypedDict：Python 3.8+ 引入，用于定义字典类型的结构
    2. 状态字段：所有工作流节点共享同一个状态对象
    3. 前缀下划线：以下划线开头的字段是内部字段，不对外暴露
    4. Literal 类型：限制字段值只能是指定的枚举值
    
    【字段说明】
    messages: 消息历史，格式为 [{"role": "user"|"assistant", "content": ...}]
    system_prompt: 系统提示词，在整个工作流中保持不变
    step: 当前步骤数
    result: 最终结果文本（仅在 success 状态时有值）
    status: 运行状态（running、success、failed）
    fail_reason: 失败原因（仅在 failed 状态时有值）
    _stop_reason: LLM 返回的停止原因（内部字段）
    _tool_calls: LLM 请求的工具调用列表（内部字段）
    _usage: LLM 调用的使用信息（内部字段）
    """
    messages: list[dict[str, Any]]        # 消息历史
    system_prompt: str                    # 系统提示词
    step: int                             # 当前步骤数
    result: str | None                    # 最终结果
    status: Literal["running", "success", "failed"]  # 运行状态
    fail_reason: str | None               # 失败原因
    _stop_reason: str | None              # LLM 停止原因（内部）
    _tool_calls: list[ToolCallBlock] | None  # 工具调用列表（内部）
    _usage: Any | None                    # LLM 使用信息（内部）
    _reflect_count: int                   # 反思次数（防止无限反思）
    max_steps: int                        # 本次 run 的最大步数（0 表示不限制）
    _files_read: int                      # 已读取文件数（effort 限制，内部）
    _compact_count: int                   # 本次 run 已压缩次数（防止 compact 死循环，内部）
    # 【学习要点】checkpoint↔run 交叉索引：configurable 里的自定义键（run_id）不会被
    # checkpointer 持久化，只有状态通道会进 checkpoint。把 run_id 做成通道，
    # 每个 checkpoint 天然标注"属于哪次运行"，回溯面板才能按 run 分组。
    run_id: str                           # 本次运行 ID（checkpoint 归属标注）


class LangGraphAgentLoop:
    """
    LangGraphAgentLoop 类 - 使用 LangGraph 构建的高级执行引擎
    
    【学习要点】
    1. 工作流定义：在构造函数中构建 StateGraph，定义节点和边
    2. 状态持久化：通过 checkpointer 实现状态快照和恢复
    3. 异步执行：使用 ainoke() 异步执行工作流
    4. 节点职责分离：每个节点只负责一个特定任务
    
    【核心优势】
    - 状态自动持久化：每次节点执行后自动保存状态
    - 支持中断和恢复：可以在任意节点中断，稍后恢复执行
    - 可视化：可以导出工作流图进行可视化分析
    - 可测试：每个节点可以单独测试
    """
    
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
        checkpointer: Any = None,
        has_rag: bool = False,
        effort_level: str = "medium",
    ) -> None:
        """
        构造函数 - 初始化 LangGraph 执行引擎

        参数：
            provider: LLM 提供者，负责调用 LLM API
            registry: 工具注册表，管理可用工具
            bus: 事件总线，用于发布和订阅系统事件
            llm_model_name: LLM 模型名称，用于构建 system prompt
            permission_manager: 权限管理器，控制工具调用权限（可选）
            compactor: 会话压缩器，用于自动压缩会话历史（可选）
            compact_threshold: 压缩阈值（字符数），当消息总长度超过此值时触发压缩
            session_id: 会话 ID，用于 checkpointer 的 thread_id（可选）
            checkpointer: LangGraph Checkpointer，实现状态持久化（可选）
            has_rag: 是否启用 RAG 功能，影响 system prompt 的构建
            effort_level: 努力等级（minimal / low / medium / high / max），控制执行深度
        
        【初始化流程】
        1. 保存所有依赖到实例属性
        2. 调用 _build_graph() 构建工作流图
        3. 编译工作流，生成可执行的 graph 对象
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
        
        # 压缩阈值：当消息总长度超过此值时触发压缩
        self._compact_threshold = compact_threshold
        
        # 会话 ID：用于 checkpointer 的 thread_id
        self._session_id = session_id
        
        # Checkpointer：实现状态持久化和恢复
        self._checkpointer = checkpointer
        
        # 是否启用 RAG：影响 system prompt 的构建
        self._has_rag = has_rag

        # 努力等级参数：控制文件读取数、验证轮数、搜索深度等
        self._effort_params = get_effort_params(effort_level)

        # 构建并编译工作流图
        self._graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """
        构建 LangGraph 工作流图
        
        【学习要点】
        1. StateGraph 创建：使用 StateGraph(状态类型) 创建工作流
        2. 节点添加：使用 add_node(name, func) 添加节点
        3. 边添加：使用 add_edge(from, to) 添加无条件边
        4. 条件边：使用 add_conditional_edges(node, router, mapping) 添加条件边
        5. 编译：使用 compile() 编译工作流，生成可执行对象
        
        【工作流图结构】
        START → chat → tools → chat → ... (循环)
                      ↓         ↓
                      → compact → chat → ...
                      ↓
                      → reflect → (done → end / continue → chat)
                      ↓
                      → end → END

        【节点说明】
        - chat：调用 LLM 获取响应
        - tools：执行工具调用
        - compact：压缩会话历史
        - reflect：反思节点 - 评估任务是否真正完成，防止草率结束
        - end：结束工作流

        返回值：
            StateGraph: 编译后的工作流对象，可通过 ainvoke() 执行
        """
        # 创建 StateGraph，指定状态类型为 AgentState
        workflow = StateGraph(AgentState)

        # ========== 添加节点 ==========

        # chat 节点：调用 LLM 获取响应
        workflow.add_node("chat", self._chat_node)

        # tools 节点：执行工具调用
        workflow.add_node("tools", self._tools_node)

        # compact 节点：压缩会话历史
        workflow.add_node("compact", self._compact_node)

        # reflect 节点：反思 - LLM 结束对话后评估是否真正完成任务
        workflow.add_node("reflect", self._reflect_node)

        # end 节点：结束工作流
        workflow.add_node("end", self._end_node)

        # ========== 添加边 ==========

        # 从 START 开始，直接进入 chat 节点
        workflow.add_edge(START, "chat")

        # chat 节点的条件路由：根据 _chat_router 返回值决定下一步
        workflow.add_conditional_edges(
            "chat",
            self._chat_router,  # 路由函数
            {
                "tool_use": "tools",              # LLM 返回工具调用
                "max_tokens_tool_use": "tools",    # token 限制但有工具调用
                "compact": "compact",              # 需要压缩
                "end_turn": "reflect",             # 对话结束 → 先反思再决定
                "error": "end",                   # 出错
                "max_steps": "end",               # 达到步数上限 → 结束（end 标记 failed）
            },
        )

        # tools 节点的条件路由：根据 _tools_router 返回值决定下一步
        workflow.add_conditional_edges(
            "tools",
            self._tools_router,
            {
                "chat": "chat",                   # 工具执行完成，继续对话
                "compact": "compact",             # 需要压缩
                "error": "end",                   # 出错
            },
        )

        # compact 节点的条件路由：根据 _compact_router 返回值决定下一步
        workflow.add_conditional_edges(
            "compact",
            self._compact_router,
            {
                "chat": "chat",                   # 压缩后继续对话
                "end": "end",                     # 压缩后结束
            },
        )

        # reflect 节点的条件路由：评估后决定继续还是结束
        workflow.add_conditional_edges(
            "reflect",
            self._reflect_router,
            {
                "end": "end",                     # 任务完成 → 结束
                "chat": "chat",                   # 任务未完成 → 继续对话
            },
        )

        # 从 end 节点到 END（特殊节点，表示工作流结束）
        workflow.add_edge("end", END)

        # 编译工作流，传入 checkpointer 实现状态持久化
        return workflow.compile(checkpointer=self._checkpointer)

    async def run(self, context: ExecutionContext) -> None:
        """
        执行 LangGraph 工作流
        
        【学习要点】
        1. 状态转换：将 ExecutionContext 转换为 LangGraph AgentState
        2. 配置传递：通过 config 参数传递 thread_id 和 run_id
        3. 异步执行：使用 ainvoke() 异步执行工作流
        4. 结果同步：将最终状态同步回 ExecutionContext
        
        参数：
            context: 执行上下文，包含消息历史、运行状态等
        
        【执行流程】
        1. 构建 system prompt
        2. 创建初始状态（AgentState）
        3. 构建配置（包含 thread_id 和 run_id）
        4. 调用 graph.ainvoke() 执行工作流
        5. 将最终状态同步回 ExecutionContext
        """
        # 构建 system prompt（包含模型名称、RAG 状态、CLAUDE.md 上下文和 AGENTS.md 指导）
        system_prompt = build_base_system_prompt(
            self._llm_model_name,
            has_rag=self._has_rag,
            claude_md_context=context.claude_md_context,
            agents_md_context=context.agents_md_context,
        )

        # 努力等级：如果指定了 max_steps_override，覆盖 context 的 max_steps
        if self._effort_params.max_steps_override > 0:
            context.max_steps = self._effort_params.max_steps_override

        # 创建初始状态：将 ExecutionContext 转换为 LangGraph AgentState
        initial_state: AgentState = {
            "messages": context.messages,                     # 消息历史
            "system_prompt": system_prompt,                  # 系统提示词
            "step": context.step,                             # 当前步骤数
            "result": context.result,                         # 初始结果
            "status": "running" if not context.is_done() else context.status,  # 初始状态
            "fail_reason": context.reason,                    # 初始失败原因
            "_stop_reason": None,                             # LLM 停止原因（内部）
            "_tool_calls": None,                              # 工具调用列表（内部）
            "_usage": None,                                   # LLM 使用信息（内部）
            "_reflect_count": 0,                              # 反思计数初始化
            "max_steps": context.max_steps,                   # 步数上限（防无限循环）
            "_files_read": 0,                                 # 文件读取计数初始化
            "_compact_count": 0,                              # 压缩计数初始化
            "run_id": context.run_id,                         # checkpoint 归属标注（回溯按 run 分组）
        }

        # 确定 thread_id：优先使用 session_id，否则使用 run_id
        thread_id = self._session_id if self._session_id else context.run_id
        
        # 构建配置：包含 thread_id（用于 checkpointer）和 run_id（用于事件发布）
        config = {"configurable": {"thread_id": thread_id, "run_id": context.run_id}}

        try:
            # 异步执行工作流，返回最终状态
            final_state = await self._graph.ainvoke(initial_state, config)
        except Exception as exc:
            # 工作流执行失败，记录日志并标记失败
            log.error("LangGraph run failed", exc_info=True)
            context.mark_failed(reason=str(exc))
            return

        # 将最终状态同步回 ExecutionContext
        context.step = final_state["step"]
        context.messages = final_state["messages"]
        context.status = final_state["status"]
        context.result = final_state["result"]
        context.reason = final_state["fail_reason"]

    async def _chat_node(self, state: AgentState, config: Any | None = None) -> dict[str, Any]:
        """
        chat 节点 - 调用 LLM 获取响应
        
        【学习要点】
        1. 节点函数签名：接收 state 和 config，返回更新后的状态
        2. config 参数：包含 configurable 字典，传递 thread_id 和 run_id
        3. 事件发布：节点执行前后发布事件，便于跟踪
        4. 状态更新：使用字典展开语法（**state）更新状态
        
        参数：
            state: 当前工作流状态
            config: 配置字典（包含 thread_id 和 run_id）
        
        返回值：
            dict: 更新后的状态字典
        
        【节点职责】
        1. 从 config 中提取 run_id
        2. 增加步骤计数器
        3. 发布 StepStartedEvent
        4. 调用 LLM 获取响应
        5. 将响应转换为消息并添加到消息历史
        6. 发布 StepFinishedEvent
        7. 返回更新后的状态
        """
        # 从 config 中提取 run_id（用于事件发布）
        run_id = ""
        if config is not None and isinstance(config, dict):
            run_id = config.get("configurable", {}).get("run_id", "")

        # 步数守卫：达到 max_steps 上限时不再调用 LLM，直接走向结束
        max_steps = state.get("max_steps", 0)
        if max_steps > 0 and state["step"] + 1 > max_steps:
            return {**state, "_stop_reason": "max_steps", "_tool_calls": None}

        # 消费运行中修正（run.steer）：在下一次模型调用前注入对话历史
        # 【设计】只注入为 user 消息，随轨迹进入消息历史——回合边界消费，
        # 不打断正在执行的工具调用，与 Claude Code 的 in-flight steering 一致
        steers = pop_steers(run_id)
        if steers:
            state["messages"] = state["messages"] + [steer_as_message(s) for s in steers]

        # 增加步骤计数器
        state["step"] += 1

        # 发布步骤开始事件
        await self._bus.publish(
            StepStartedEvent(run_id=run_id, step=state["step"], ts=_now())
        )

        try:
            # 调用 LLM 获取响应
            response = await self._provider.chat(
                messages=state["messages"],           # 消息历史
                tool_schemas=self._registry.tool_schemas(),  # 工具 schema
                bus=self._bus,                       # 事件总线
                run_id=run_id,                       # 运行 ID
                step=state["step"],                  # 当前步骤
                system=state["system_prompt"],       # 系统提示词
            )
        except Exception as exc:
            # LLM 调用失败，发布步骤结束事件并返回错误状态
            await self._bus.publish(
                StepFinishedEvent(run_id=run_id, step=state["step"], ts=_now())
            )
            return {
                **state,
                "status": "failed",
                "fail_reason": str(exc),
                "_stop_reason": "error",
            }

        # 将 LLM 响应转换为消息格式并添加到消息历史
        new_messages = state["messages"] + [_assistant_msg_from_response(response)]

        # 发布步骤结束事件
        await self._bus.publish(
            StepFinishedEvent(run_id=run_id, step=state["step"], ts=_now())
        )

        # 返回更新后的状态
        return {
            **state,
            "messages": new_messages,       # 更新消息历史
            "_stop_reason": response.stop_reason,  # LLM 停止原因
            "_tool_calls": response.tool_calls,    # 工具调用列表
            "_usage": response.usage,              # LLM 使用信息
        }

    def _chat_router(self, state: AgentState) -> str:
        """
        chat 节点路由函数 - 根据状态决定下一步
        
        【学习要点】
        1. 路由函数签名：接收 state，返回节点名称字符串
        2. 优先级顺序：按优先级检查不同条件
        3. 条件路由：根据 LLM 停止原因和上下文长度决定路由
        
        参数：
            state: 当前工作流状态
        
        返回值：
            str: 下一个节点名称（tool_use、max_tokens_tool_use、compact、end_turn、error）
        
        【路由逻辑】
        1. tool_use → "tools"：LLM 返回了工具调用
        2. max_tokens + 有工具调用 → "max_tokens_tool_use"：token 限制但有工具调用
        3. end_turn → "end"：对话结束
        4. error → "end"：出错
        5. 上下文过长 → "compact"：需要压缩
        6. max_tokens → "compact"：token 限制需要压缩
        7. 默认 → "end_turn"：结束对话
        """
        sr = state.get("_stop_reason")

        # 步数守卫触发：直接结束（end 节点会标记为 failed）
        if sr == "max_steps":
            return "max_steps"
        # LLM 返回了工具调用
        if sr == "tool_use":
            return "tool_use"
        # token 限制但有工具调用
        elif sr == "max_tokens" and state.get("_tool_calls"):
            return "max_tokens_tool_use"
        # 对话结束
        elif sr == "end_turn":
            return "end_turn"
        # 出错
        elif sr == "error":
            return "error"

        # 检查是否需要压缩（上下文过长）
        if self._needs_compact(state):
            return "compact"

        # token 限制需要压缩
        if sr == "max_tokens":
            return "compact"

        # 默认：结束对话
        return "end_turn"

    # 判断当前历史是否触发压缩：0<threshold<=1 按上下文占用率，>1 按字符数（兼容旧配置）
    def _needs_compact(self, state: AgentState) -> bool:
        if self._compactor is None or self._compact_threshold <= 0:
            return False
        # 压缩次数达到上限后不再触发，防止 compact↔chat 死循环
        if state.get("_compact_count", 0) >= _MAX_COMPACT_PER_RUN:
            return False
        if self._compact_threshold <= 1:
            usage = state.get("_usage")
            pct = getattr(usage, "context_pct", 0.0) if usage is not None else 0.0
            return pct >= self._compact_threshold
        total_len = sum(len(str(m.get("content", ""))) for m in state["messages"])
        return total_len > self._compact_threshold

    async def _tools_node(self, state: AgentState, config: Any | None = None) -> dict[str, Any]:
        """
        tools 节点 - 并行执行工具调用

        【学习要点】
        1. 并行执行：同一 turn 的独立工具调用并行执行，提升复杂任务速度
        2. 顺序保持：结果按 tool_calls 原始顺序添加到消息历史
        3. effort 限制：文件读取限制在并行执行前检查（避免超限）
        4. 权限隔离：每个工具调用的权限检查由 invoke_tool 内部处理

        【并行安全性】
        LLM 在同一 turn 调用的工具默认是独立的（如同时读 3 个文件）。
        如果 LLM 想要顺序依赖（如先读再编辑），会在下一 turn 调用。

        参数：
            state: 当前工作流状态
            config: 配置字典（包含 thread_id 和 run_id）

        返回值：
            dict: 更新后的状态字典（包含执行后的消息历史）
        """
        # 从 config 中提取 run_id
        run_id = ""
        if config is not None and isinstance(config, dict):
            run_id = config.get("configurable", {}).get("run_id", "")

        # 获取工具调用列表（如果没有则为空列表）
        tool_calls = state.get("_tool_calls") or []
        # 复制消息历史（避免直接修改原状态）
        new_messages = state["messages"]
        # 努力等级限制：记录已读取的文件数
        files_read_count = state.get("_files_read", 0)

        # 第一阶段：串行检查 effort level（决定哪些可以执行，避免并行超限）
        # 生成 (tool_call, can_execute) 列表
        pending: list[ToolCallBlock] = []
        skip_results: list[tuple[str, object]] = []  # (tc_id, error_result)
        for tc in tool_calls:
            if self._effort_params.max_files_read > 0 and tc.name in ("read_file", "list_dir", "file_exists", "file_stat"):
                if files_read_count >= self._effort_params.max_files_read:
                    # 超限，构造错误结果
                    skip_results.append((
                        tc.id,
                        type("R", (), {"content": f"Error: effort level limit reached ({self._effort_params.max_files_read} files max). "
                                                  "Increase effort level to read more files.",
                                        "is_error": True})()
                    ))
                    continue
                files_read_count += 1
            pending.append(tc)

        # 第二阶段：并行执行所有允许的工具调用
        # 用 asyncio.gather 并行执行，保持顺序
        async def _exec_one(tc: ToolCallBlock) -> object:
            return await invoke_tool(
                self._registry,
                tc,
                self._bus,
                run_id,
                permission_manager=self._permission_manager,
                session_id=self._session_id,
            )

        if pending:
            results = await asyncio.gather(*[_exec_one(tc) for tc in pending], return_exceptions=False)
        else:
            results = []

        # 第三阶段：按原始顺序将结果添加到消息历史
        # 先添加并行执行的结果（按 pending 顺序）
        for tc, result in zip(pending, results, strict=False):
            new_messages = _add_tool_result_to_messages(new_messages, tc.id, result)

        # 再添加被 effort 限制跳过的结果
        for tc_id, err_result in skip_results:
            new_messages = _add_tool_result_to_messages(new_messages, tc_id, err_result)

        # 返回更新后的状态（仅更新消息历史）
        return {**state, "messages": new_messages, "_files_read": files_read_count}

    def _tools_router(self, state: AgentState) -> str:
        """
        tools 节点路由函数 - 根据状态决定下一步
        
        参数：
            state: 当前工作流状态
        
        返回值：
            str: 下一个节点名称（chat、compact、error）
        
        【路由逻辑】
        1. error → "end"：出错
        2. 上下文过长 → "compact"：需要压缩
        3. 默认 → "chat"：继续对话
        """
        sr = state.get("_stop_reason")

        # 出错
        if sr == "error":
            return "error"

        # 检查是否需要压缩（上下文过长）
        if self._needs_compact(state):
            return "compact"

        # 默认：继续对话
        return "chat"

    async def _compact_node(self, state: AgentState, config: Any | None = None) -> dict[str, Any]:
        """
        compact 节点 - 压缩会话历史
        
        【学习要点】
        1. 临时上下文：创建临时 ExecutionContext 用于压缩
        2. 错误处理：压缩失败时记录警告并继续执行
        3. 状态更新：压缩后更新消息历史
        
        参数：
            state: 当前工作流状态
            config: 配置字典（包含 thread_id 和 run_id）
        
        返回值：
            dict: 更新后的状态字典（包含压缩后的消息历史）
        
        【节点职责】
        1. 检查压缩器是否存在
        2. 创建临时 ExecutionContext
        3. 执行压缩
        4. 返回压缩后的状态
        """
        # 如果压缩器不存在，直接返回原状态
        if self._compactor is None:
            return {**state}

        # 从 config 中提取 run_id
        run_id = ""
        if config is not None and isinstance(config, dict):
            run_id = config.get("configurable", {}).get("run_id", "")
        
        # 创建临时 ExecutionContext（压缩器需要 ExecutionContext 类型）
        temp_ctx = ExecutionContext(
            run_id=run_id,
            goal="",
            max_steps=state["step"],
        )
        # 将消息历史复制到临时上下文
        temp_ctx.messages = state["messages"]

        try:
            # 执行会话压缩
            await self._compactor.compact(temp_ctx, self._provider)
        except Exception as exc:
            # 压缩失败，记录警告并返回原状态（继续执行）；计数仍递增，避免反复重试
            log.warning("Compaction failed, continuing without compact", exc_info=True)
            return {**state, "_compact_count": state.get("_compact_count", 0) + 1}

        # 返回压缩后的状态（更新消息历史）；递增压缩计数防止死循环
        return {
            **state,
            "messages": temp_ctx.messages,
            "_compact_count": state.get("_compact_count", 0) + 1,
        }

    def _compact_router(self, state: AgentState) -> str:
        """
        compact 节点路由函数 - 根据状态决定下一步
        
        参数：
            state: 当前工作流状态
        
        返回值：
            str: 下一个节点名称（chat、end）
        
        【路由逻辑】
        1. end_turn → "end"：对话结束
        2. 默认 → "chat"：继续对话
        """
        sr = state.get("_stop_reason")
        
        # 对话结束
        if sr == "end_turn":
            return "end"
        
        # 默认：继续对话
        return "chat"

    # 最大反思次数（防止 LLM 反复认为"未完成"导致无限循环）
    _MAX_REFLECT = 2

    async def _reflect_node(self, state: AgentState, config: Any | None = None) -> dict[str, Any]:
        """
        反思节点 - LLM 结束对话后评估任务是否真正完成

        【设计目的】
        ReAct 引擎在 LLM 返回 end_turn（不调用工具）时就结束，
        但有时 LLM 过早结束（任务只完成了一半）。
        反思节点在结束前做一次评估：任务是否真正完成？

        【执行流程】
        1. 已反思次数 >= 上限 → 直接通过（返回 "done" 让路由走 end）
        2. 构建反思 prompt：用户目标 + 当前回答
        3. 调用 LLM 评估（不使用工具）
        4. 解析评估结果（DONE / CONTINUE）
        5. 如果需要继续，追加 user 消息提示 Agent 继续
        """
        reflect_count = state.get("_reflect_count", 0)

        # 防止无限反思
        if reflect_count >= self._MAX_REFLECT:
            return {"_reflect_count": reflect_count + 1}

        # 从 config 中提取 run_id
        run_id = ""
        if config is not None and isinstance(config, dict):
            run_id = config.get("configurable", {}).get("run_id", "")

        # 提取最后一条 assistant 消息作为评估对象
        last_assistant = ""
        for msg in reversed(state["messages"]):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str):
                    last_assistant = content
                elif isinstance(content, list):
                    last_assistant = " ".join(
                        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                    )
                break

        # 从消息历史中提取用户原始任务
        user_goal = _extract_user_goal(state["messages"])

        # 构建反思 prompt
        reflect_prompt = (
            "你是一个任务审查者。请评估以下回答是否完整地解决了用户的任务。\n\n"
            f"## 用户原始任务\n{user_goal}\n\n"
            f"## Agent 的回答（最后一条）\n{last_assistant[:2000]}\n\n"
            "请判断：\n"
            "- 如果回答完整解决了任务，回复 DONE\n"
            "- 如果任务还需要更多步骤（如还需要调用工具、修改代码等），回复 CONTINUE\n"
            "只回复 DONE 或 CONTINUE。"
        )

        messages = [{"role": "user", "content": reflect_prompt}]

        try:
            response = await self._provider.chat(
                messages=messages,
                tool_schemas=[],
                bus=self._bus,
                run_id=run_id,
                step=state["step"],
                system="You are a task completion evaluator. Reply with only DONE or CONTINUE.",
            )
            verdict = (response.text or "").strip().upper()
        except Exception as exc:
            log.warning("reflect node failed: %s", exc)
            verdict = "DONE"

        # 解析判断
        if "CONTINUE" in verdict and reflect_count < self._MAX_REFLECT:
            # 任务未完成，追加提示让 Agent 继续（创建新列表避免修改原状态）
            new_messages = list(state["messages"]) + [{
                "role": "user",
                "content": "你的任务还没有完成。请继续执行，直到任务目标完全实现。",
            }]
            return {"_reflect_count": reflect_count + 1, "messages": new_messages}

        # 任务完成或达到反思上限
        return {"_reflect_count": reflect_count + 1}

    def _reflect_router(self, state: AgentState) -> str:
        """
        反思路由 - 根据反思结果决定继续还是结束

        - 反思后有新增的 user 消息（CONTINUE）→ 回到 chat 继续
        - 否则 → 结束
        """
        # 如果最后一条消息是 user（反思节点追加的"继续"提示）→ 回到 chat
        if state["messages"] and state["messages"][-1].get("role") == "user":
            return "chat"
        # 否则结束
        return "end"

    async def _end_node(self, state: AgentState, config: Any | None = None) -> dict[str, Any]:
        """
        end 节点 - 结束工作流
        
        【学习要点】
        1. 状态确定：根据停止原因和当前状态确定最终状态
        2. 结果提取：从消息历史中提取最后一条 assistant 消息作为结果
        
        参数：
            state: 当前工作流状态
            config: 配置字典（包含 thread_id 和 run_id）
        
        返回值：
            dict: 最终状态字典（包含 status 和 result）
        
        【节点职责】
        1. 检查是否出错
        2. 如果出错，设置状态为 failed
        3. 如果成功，提取最后一条 assistant 消息作为结果
        """
        sr = state.get("_stop_reason")

        # 达到步数上限：标记失败并说明原因（effort 等级或配置控制）
        if sr == "max_steps":
            return {
                **state,
                "status": "failed",
                "fail_reason": f"max_steps exceeded (step={state['step']})",
            }

        # 如果出错或当前状态为 failed
        if sr == "error" or state["status"] == "failed":
            return {**state, "status": "failed"}

        # 如果成功，提取最后一条 assistant 消息作为结果
        return {**state, "status": "success", "result": _extract_last_assistant_text(state["messages"])}
