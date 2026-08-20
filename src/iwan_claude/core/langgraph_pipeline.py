"""
LangGraphPipelineLoop 模块 - 多 Agent 流水线协作引擎

【与其它引擎的区别】
- ReAct（langgraph_loop.py）：边想边做，chat → tools → chat → tools ... 循环
- Plan & Execute（langgraph_plan_execute.py）：同一个 Agent 做 plan → execute → reflect
- Debate（langgraph_debate.py）：worker ↔ critic 两角色辩论循环
- Pipeline（本模块）：三个独立角色 Agent 流水线协作——Planner 规划 → Executor 执行 → Reviewer 审查

【工作流节点】
1. planner：分析任务，制定执行计划（不调用工具，纯规划）；若收到 reviewer 反馈则优化后续计划
2. executor：按计划调用工具执行（不规划，只执行）
3. reviewer：审查执行结果，判定是否达标（不调用工具，纯审查）
4. end：整理最终结果，追加 assistant 消息到会话历史

【路由逻辑 - 角色复用】
- planner → executor：planner 生成计划后交给 executor 执行
- executor → reviewer：executor 执行完成后交给 reviewer 审查
- reviewer → planner（首次返工）：reviewer 反馈回传给 planner，planner 优化后续计划
- reviewer → executor（后续返工）：executor 带反馈重新执行（避免无限重规划）
- reviewer → end：reviewer 满意 / 达最大轮数 / 出错

【适用场景】
- 复杂多步骤任务：需要规划→执行→审查的完整流程（如代码开发、数据分析、文档撰写）
- 角色分离场景：规划者不执行、执行者不规划、审查者不执行，各司其职
- 对标工业界多 Agent 协作框架（CrewAI Crew、AutoGen Group Chat 的简化版）

【面试亮点】
"实现了 5 种 Agent 引擎（Legacy / ReAct / Plan&Execute / Debate / Pipeline），通过配置切换。
Pipeline 模式采用三角色流水线协作——Planner 制定计划、Executor 调用工具执行、Reviewer
独立审查结果。首次返工时 reviewer 反馈回传给 planner 优化后续计划（角色复用），
后续返工直接给 executor 执行（避免无限重规划）。这种模式对标 CrewAI / AutoGen 的多 Agent
协作，通过角色分离和职责隔离提升了复杂任务的处理质量。"
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from iwan_claude.core.bus.events import StepFinishedEvent, StepStartedEvent
from iwan_claude.core.compact.compactor import Compactor
from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.effort import get_effort_params
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.base import LLMProvider
from iwan_claude.core.llm.types import LlmResponse, ToolCallBlock
from iwan_claude.core.permissions.manager import PermissionManager
from iwan_claude.core.system_prompt import build_base_system_prompt
from iwan_claude.core.tools.invocation import invoke_tool
from iwan_claude.core.tools.registry import ToolRegistry

log = logging.getLogger(__name__)


def _now() -> str:
    """获取当前 UTC 时间的 ISO 8601 格式字符串"""
    return datetime.now(UTC).isoformat()


# 最大返工轮数（executor→reviewer 为一轮），防止无限循环
_DEFAULT_MAX_ROUNDS = 2


# planner 角色指令：纯规划，不调用工具
_PLANNER_ROLE = (
    "\n\n## Your Role: Planner\n"
    "You are the planner agent in a multi-agent pipeline. Your job is to analyze the user's "
    "request and create a clear, actionable execution plan. Break down the task into concrete "
    "steps that the executor agent will follow. Do not execute anything yourself—only plan. "
    "Output your plan as a numbered list of steps."
)

# executor 角色指令：纯执行，调用工具
_EXECUTOR_ROLE = (
    "\n\n## Your Role: Executor\n"
    "You are the executor agent in a multi-agent pipeline. Your job is to execute the plan "
    "provided by the planner agent, using available tools as needed. Follow the plan steps "
    "carefully. If the reviewer has provided feedback, address each point and improve your "
    "execution. Output a summary of what you did and the results."
)

# reviewer 角色指令：纯审查，不调用工具
_REVIEWER_ROLE = (
    "You are an independent reviewer agent evaluating an executor's work. "
    "Your job is to judge whether the executor's work fully and correctly satisfies "
    "the user's original request, following the plan. Be strict but fair. "
    "Respond in exactly one of these formats:\n"
    "- APPROVED: <one-line summary of why the work is satisfactory>\n"
    "- NEEDS_REWORK: <specific issues that must be fixed>\n"
    "Do not call any tools. Do not do the work yourself—only evaluate."
)


class PipelineState(TypedDict):
    """
    Pipeline 工作流状态定义

    【字段说明】
    - messages: 会话历史；只有 _end_node 在此追加最终 assistant 回复
    - planner_system / executor_system / reviewer_system: 在 run() 预构建的角色 prompt
    - user_request: 原始用户目标（context.goal）
    - plan: planner 生成的执行计划
    - executor_result: executor 最新执行结果
    - reviewer_feedback: reviewer 原始反馈文本
    - reviewer_verdict: 解析后的判定（approved / needs_rework）
    - round: 已完成的 executor→reviewer 轮数（在 reviewer_node 递增）
    - max_rounds: 最大返工轮数（默认 2）
    - replanned: 是否已经重新规划过（避免无限重规划）
    - status: 运行状态
    - result: 最终结果
    - fail_reason: 失败原因
    - step: 事件计数（用于 StepStarted/StepFinished 追踪）
    """
    messages: list[dict[str, Any]]
    planner_system: str
    executor_system: str
    reviewer_system: str
    user_request: str
    plan: str | None
    executor_result: str | None
    reviewer_feedback: str | None
    reviewer_verdict: Literal["approved", "needs_rework"] | None
    round: int
    max_rounds: int
    replanned: bool
    status: Literal["planning", "executing", "reviewing", "done", "failed"]
    result: str | None
    fail_reason: str | None
    step: int


class LangGraphPipelineLoop:
    """
    多 Agent 流水线协作引擎

    【与其它引擎的关系】
    - 5 种引擎共享相同的依赖（provider, registry, bus, permission_manager）
    - 通过配置选择引擎（engine=pipeline）
    - 本类结构与 LangGraphDebateLoop 完全对齐，构造函数签名一致，方便零成本切换

    【使用方式】
    ```python
    loop = LangGraphPipelineLoop(
        provider=llm_provider,
        registry=tool_registry,
        bus=event_bus,
        permission_manager=perm_mgr,
    )
    await loop.run(context)
    ```
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
        构造函数 - 初始化 Pipeline 引擎

        【参数说明】
        与 LangGraphDebateLoop 完全一致，方便切换引擎。
        """
        self._provider = provider
        self._registry = registry
        self._bus = bus
        self._llm_model_name = llm_model_name
        self._permission_manager = permission_manager
        self._compactor = compactor
        self._compact_threshold = compact_threshold
        self._session_id = session_id
        self._run_id = ""  # 每次 run() 时从 context.run_id 设置，用于事件发布
        self._checkpointer = checkpointer
        self._has_rag = has_rag
        self._effort_params = get_effort_params(effort_level)

        self._graph = self._build_graph()

    # ==================================================================
    # 图构建
    # ==================================================================

    def _build_graph(self) -> Any:
        """
        构建多 Agent 流水线工作流图

        【图结构 - 角色复用】
        START → planner → planner_router → executor → executor_router → reviewer → reviewer_router → end → END
                           ↓                              ↓                          ↓                ↑
                      (error→end)                  (error→end)        (needs_rework & 首次 → planner)
                                                                         (needs_rework & 后续 → executor)
                                                                         (approved / max_rounds / error → end)
        """
        workflow = StateGraph(PipelineState)

        # 添加节点
        workflow.add_node("planner", self._planner_node)
        workflow.add_node("executor", self._executor_node)
        workflow.add_node("reviewer", self._reviewer_node)
        workflow.add_node("end", self._end_node)

        # 添加边
        workflow.add_edge(START, "planner")

        # planner 的条件路由：计划成功 → executor，失败 → end
        workflow.add_conditional_edges(
            "planner",
            self._planner_router,
            {
                "executor": "executor",
                "error": "end",
            },
        )

        # executor 的条件路由：执行成功 → reviewer，失败 → end
        workflow.add_conditional_edges(
            "executor",
            self._executor_router,
            {
                "reviewer": "reviewer",
                "error": "end",
            },
        )

        # reviewer 的条件路由（角色复用）：
        # - 满意 → done
        # - 需返工且未达上限：首次走 planner（重新规划），后续走 executor（带反馈执行）
        # - 达最大轮数 / 出错 → done / error
        workflow.add_conditional_edges(
            "reviewer",
            self._reviewer_router,
            {
                "planner": "planner",
                "executor": "executor",
                "done": "end",
                "error": "end",
            },
        )

        workflow.add_edge("end", END)

        # 编译
        compile_kwargs: dict[str, Any] = {}
        if self._checkpointer:
            compile_kwargs["checkpointer"] = self._checkpointer
        return workflow.compile(**compile_kwargs)

    # ==================================================================
    # 节点：planner（制定计划，不调用工具）
    # ==================================================================

    async def _planner_node(self, state: PipelineState, config: Any | None = None) -> dict[str, Any]:
        """
        planner 节点：分析任务，制定执行计划

        【角色复用】
        - 首次规划：基于 user_request 制定计划
        - 收到 reviewer 反馈后：基于反馈优化原计划（reviewer 反馈回传给 planner）
          让 planner 知道执行中遇到的问题，调整后续步骤
        """
        await self._bus.publish(StepStartedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        # 构建规划 prompt
        reviewer_feedback = state.get("reviewer_feedback")
        previous_plan = state.get("plan")
        previous_result = state.get("executor_result")

        if reviewer_feedback and previous_plan:
            # 角色复用：reviewer 反馈回传给 planner，让 planner 优化后续计划
            plan_prompt = (
                f"## User Request\n{state['user_request']}\n\n"
                f"## Previous Plan\n{previous_plan}\n\n"
                f"## Executor's Previous Result\n{previous_result or '(empty)'}\n\n"
                f"## Reviewer Feedback\n{reviewer_feedback}\n\n"
                "The reviewer has identified issues with the previous execution. "
                "Please revise the plan to address these issues. You may keep steps "
                "that worked, modify steps that failed, or add new steps. "
                "Output the revised plan as a numbered list of steps."
            )
            log.info("Planner revising plan based on reviewer feedback (round %d)", state.get("round", 0))
        else:
            # 首次规划
            plan_prompt = (
                f"## User Request\n{state['user_request']}\n\n"
                "Analyze this request and create a clear, actionable execution plan. "
                "Break down the task into concrete numbered steps. "
                "The executor agent will follow your plan to complete the task."
            )

        messages = [{"role": "user", "content": plan_prompt}]

        try:
            response = await self._provider.chat(
                messages=messages,
                tool_schemas=[],  # planner 不调用工具
                bus=self._bus,
                run_id=self._run_id,
                step=state["step"],
                system=state["planner_system"],
            )
        except Exception as exc:
            log.error("Planner node failed: %s", exc)
            await self._bus.publish(StepFinishedEvent(
                run_id=self._run_id,
                step=state["step"],
                ts=_now(),
            ))
            return {"status": "failed", "fail_reason": f"Planner failed: {exc}"}

        plan_text = response.text or ""

        await self._bus.publish(StepFinishedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        return {
            "plan": plan_text,
            "status": "executing",
            "step": state["step"] + 1,
            "replanned": bool(reviewer_feedback),  # 标记已重新规划过
        }

    # ==================================================================
    # 节点：executor（执行计划，可调用工具）
    # ==================================================================

    async def _executor_node(self, state: PipelineState, config: Any | None = None) -> dict[str, Any]:
        """
        executor 节点：按计划调用工具执行

        【执行流程】
        1. 构建 executor 消息：计划 + 可能的 reviewer 反馈
        2. 调用 LLM（可调用工具）
        3. 有工具调用则执行工具
        4. 更新 executor_result，不修改 state["messages"]
        """
        await self._bus.publish(StepStartedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        # 构建 executor 消息
        exec_prompt = f"## Execution Plan\n{state.get('plan') or '(no plan)'}\n\nExecute the plan now."

        local_messages: list[dict[str, Any]] = [{"role": "user", "content": exec_prompt}]

        # 若有 reviewer 反馈，追加 user 消息要求 executor 改进
        if state.get("reviewer_feedback"):
            local_messages.append({
                "role": "user",
                "content": (
                    f"## Reviewer Feedback\n{state['reviewer_feedback']}\n\n"
                    "Please re-execute the plan addressing these issues."
                ),
            })

        try:
            response = await self._provider.chat(
                messages=local_messages,
                tool_schemas=self._registry.tool_schemas(),
                bus=self._bus,
                run_id=self._run_id,
                step=state["step"],
                system=state["executor_system"],
            )
        except Exception as exc:
            log.error("Executor node failed: %s", exc)
            await self._bus.publish(StepFinishedEvent(
                run_id=self._run_id,
                step=state["step"],
                ts=_now(),
            ))
            return {"status": "failed", "fail_reason": f"Executor failed: {exc}"}

        # 提取执行结果文本
        result_text = response.text or ""

        # 如果有工具调用，执行工具并将结果附加到回答
        if response.tool_calls:
            tool_results = await self._execute_tools(response.tool_calls, state)
            result_text += "\n\n" + tool_results

        await self._bus.publish(StepFinishedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        return {
            "executor_result": result_text,
            "status": "reviewing",
            "step": state["step"] + 1,
        }

    async def _execute_tools(self, tool_calls: list[ToolCallBlock], state: PipelineState) -> str:
        """执行工具调用，返回格式化的结果文本（复用 debate 模式）"""
        results: list[str] = []
        for tc in tool_calls:
            try:
                # 权限检查
                if self._permission_manager:
                    allowed, reason = await self._permission_manager.check_and_wait(
                        tool_use_id=tc.id,
                        tool_name=tc.name,
                        params=tc.input,
                        session_id=self._session_id,
                    )
                    if not allowed:
                        results.append(f"Tool {tc.name} denied: {reason}")
                        continue

                result = await invoke_tool(self._registry, tc.name, tc.input)
                results.append(f"Tool {tc.name}: {result.content[:500]}")
            except Exception as exc:
                results.append(f"Tool {tc.name} error: {exc}")
        return "\n".join(results)

    # ==================================================================
    # 节点：reviewer（独立审查，不调用工具）
    # ==================================================================

    async def _reviewer_node(self, state: PipelineState, config: Any | None = None) -> dict[str, Any]:
        """
        reviewer 节点：独立审查 executor 的执行结果

        【执行流程】
        1. 构建审查 prompt（含 user_request + plan + executor_result）
        2. 调用 LLM（不调用工具，不注入记忆——纯审查避免偏见）
        3. 解析审查结果（APPROVED / NEEDS_REWORK）
        4. 递增 round
        """
        await self._bus.publish(StepStartedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        # 构建审查 prompt
        eval_prompt = (
            f"## Original User Request\n{state['user_request']}\n\n"
            f"## Execution Plan\n{state.get('plan') or '(no plan)'}\n\n"
            f"## Executor's Result\n{state.get('executor_result') or '(empty)'}\n\n"
            "Evaluate if the execution fully satisfies the original request following the plan. "
            "Respond with either 'APPROVED: <summary>' or 'NEEDS_REWORK: <issues>'."
        )

        messages = [{"role": "user", "content": eval_prompt}]

        try:
            response = await self._provider.chat(
                messages=messages,
                tool_schemas=[],
                bus=self._bus,
                run_id=self._run_id,
                step=state["step"],
                system=state["reviewer_system"],
            )
        except Exception as exc:
            log.error("Reviewer node failed: %s", exc)
            await self._bus.publish(StepFinishedEvent(
                run_id=self._run_id,
                step=state["step"],
                ts=_now(),
            ))
            return {"status": "failed", "fail_reason": f"Reviewer failed: {exc}"}

        feedback = response.text or ""
        verdict = self._parse_verdict(feedback)

        await self._bus.publish(StepFinishedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        return {
            "reviewer_feedback": feedback,
            "reviewer_verdict": verdict,
            "round": state["round"] + 1,
            "step": state["step"] + 1,
        }

    def _parse_verdict(self, feedback: str) -> Literal["approved", "needs_rework"]:
        """
        解析 reviewer 的反馈文本，提取判定结果

        【解析规则】
        - 大小写不敏感匹配 APPROVED / NEEDS_REWORK
        - 未知输出默认 approved（安全结束，同 debate 的 _parse_verdict 策略）
        """
        upper = (feedback or "").upper()
        if "NEEDS_REWORK" in upper:
            return "needs_rework"
        # 包含 APPROVED 或未知输出，都视为通过（安全结束）
        return "approved"

    # ==================================================================
    # 路由
    # ==================================================================

    def _planner_router(self, state: PipelineState) -> str:
        """planner 路由：计划成功 → executor，失败 → end"""
        if state.get("status") == "failed":
            return "error"
        return "executor"

    def _executor_router(self, state: PipelineState) -> str:
        """executor 路由：执行成功 → reviewer，失败 → end"""
        if state.get("status") == "failed":
            return "error"
        return "reviewer"

    def _reviewer_router(self, state: PipelineState) -> str:
        """
        reviewer 路由：判断是否需要返工（角色复用策略）

        【路由策略 - 角色复用】
        - reviewer 失败 → error
        - reviewer 通过（APPROVED）→ done
        - reviewer 认为需返工且未达最大轮数：
          - 首次返工（未重新规划过）→ planner（让 reviewer 反馈回传给 planner 优化计划）
          - 后续返工（已重新规划过）→ executor（直接带反馈执行，避免无限重规划）
        - 达到最大轮数或未知判定 → done（强制/安全结束）
        """
        if state.get("status") == "failed":
            return "error"

        verdict = state.get("reviewer_verdict")
        if verdict == "approved":
            return "done"

        # 只有明确 needs_rework 才考虑返工
        if verdict == "needs_rework":
            if state.get("round", 0) < state.get("max_rounds", _DEFAULT_MAX_ROUNDS):
                # 角色复用：首次返工回传给 planner 优化计划，后续直接给 executor
                if not state.get("replanned", False):
                    log.info("Reviewer feedback sent to planner for plan revision (round %d)",
                             state.get("round", 0))
                    return "planner"
                # 后续返工直接给 executor，避免无限重规划
                return "executor"
            # 达到最大轮数，强制结束
            log.warning("Pipeline reached max_rounds=%d, forcing completion", state.get("max_rounds"))
            return "done"

        # 未知判定（None 等）→ done（安全结束）
        return "done"

    # ==================================================================
    # 节点：结束
    # ==================================================================

    async def _end_node(self, state: PipelineState, config: Any | None = None) -> dict[str, Any]:
        """
        结束节点：整理最终结果

        【执行流程】
        1. 如果有 fail_reason（之前节点失败），标记为 failed
        2. 否则提取 executor_result 作为最终结果，标记为 done
        3. 【关键】将最终结果作为 assistant 消息追加到 state["messages"]
        """
        if state.get("fail_reason"):
            result = state["fail_reason"]
            status = "failed"
        else:
            result = state.get("executor_result") or "No result."
            status = "done"

        # 【关键】将最终结果作为 assistant 消息追加到消息历史
        new_messages = list(state["messages"]) + [{"role": "assistant", "content": result}]

        return {"status": status, "result": result, "messages": new_messages}

    # ==================================================================
    # 执行入口
    # ==================================================================

    async def run(self, context: ExecutionContext) -> None:
        """
        执行多 Agent 流水线工作流

        【执行流程】
        1. 构建 planner/executor/reviewer 角色 prompt
        2. 构建初始状态
        3. 调用 graph.ainvoke() 执行工作流
        4. 将最终结果写入执行上下文
        """
        # 设置 run_id：优先用 context.run_id（每次运行唯一），兜底用 session_id
        self._run_id = context.run_id or self._session_id

        # 构建 base system prompt（注入 CLAUDE.md 项目上下文）
        base_prompt = build_base_system_prompt(
            model_name=self._llm_model_name,
            has_rag=self._has_rag,
            claude_md_context=context.claude_md_context,
        )

        # planner 用 base prompt + 角色指令 + 记忆（规划需要历史记忆）
        planner_system = base_prompt + _PLANNER_ROLE
        if context.memory_context.strip():
            planner_system += "\n\n## Memory\n" + context.memory_context.strip()

        # executor 用 base prompt + 角色指令 + 记忆（执行需要历史记忆）
        executor_system = base_prompt + _EXECUTOR_ROLE
        if context.memory_context.strip():
            executor_system += "\n\n## Memory\n" + context.memory_context.strip()

        # reviewer 用纯审查指令（不注入 base prompt 和记忆——纯审查，避免偏见）
        reviewer_system = _REVIEWER_ROLE

        # 努力等级：如果指定了 max_steps_override，覆盖 context 的 max_steps
        if self._effort_params.max_steps_override > 0:
            context.max_steps = self._effort_params.max_steps_override

        # 构建初始状态
        initial_state: PipelineState = {
            "messages": context.messages,
            "planner_system": planner_system,
            "executor_system": executor_system,
            "reviewer_system": reviewer_system,
            "user_request": context.goal,
            "plan": None,
            "executor_result": None,
            "reviewer_feedback": None,
            "reviewer_verdict": None,
            "round": 0,
            "max_rounds": _DEFAULT_MAX_ROUNDS,
            "replanned": False,  # 是否已经重新规划过（避免无限重规划）
            "status": "planning",
            "result": None,
            "fail_reason": None,
            "step": 0,
        }

        # 执行配置
        run_config: dict[str, Any] = {}
        if self._session_id:
            run_config["configurable"] = {"thread_id": self._session_id, "run_id": self._run_id}

        try:
            # 执行工作流
            final_state = await self._graph.ainvoke(initial_state, run_config)

            # 【关键】同步 messages 回 context
            context.messages = final_state.get("messages", context.messages)
            context.step = final_state.get("step", context.step)

            # 写入执行上下文
            if final_state.get("status") == "done":
                context.result = final_state.get("result", "")
                context.status = "success"
            else:
                context.status = "failed"
                context.reason = final_state.get("result") or final_state.get("fail_reason", "Unknown error")

        except Exception as exc:
            log.error("Pipeline loop failed: %s", exc)
            context.status = "failed"
            context.reason = str(exc)
