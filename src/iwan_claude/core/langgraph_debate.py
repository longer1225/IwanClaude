"""
LangGraphDebateLoop 模块 - Worker-Critic 辩论执行引擎

【与其它引擎的区别】
- ReAct（langgraph_loop.py）：边想边做，chat → tools → chat → tools ... 循环
- Plan & Execute（langgraph_plan_execute.py）：先规划再执行，plan → execute → reflect
- Debate（本模块）：worker 回答 → critic 独立评判 → 不满意则 worker 改进 → 循环直到满意或达最大轮数

【工作流节点】
1. worker：回答用户问题（可调用工具），若有 critic 反馈则据此改进
2. critic：独立评判 worker 的回答是否满足需求（不调用工具）
3. end：整理最终结果，追加 assistant 消息到会话历史

【动态退出策略】
- critic 满意（SATISFIED）→ 立即退出（不等轮数）
- critic 判定 STUCK（worker 无法继续改进）→ 强制退出（避免无效循环）
- critic 需改进（NEEDS_IMPROVEMENT）→ 下一轮 worker 改进
- 达到最大轮数（默认 5）→ 强制退出

【路由逻辑】
- worker → critic：worker 回答成功后交给 critic 评判
- critic → worker：critic 认为需要改进且未达最大轮数，worker 重新回答
- critic → end：critic 满意 / STUCK / 达最大轮数 / 出错

【适用场景】
- 质量敏感任务：需要独立审查确保答案质量（如代码审查、文档撰写、复杂推理）
- 对标学术界 Self-Refine / Multi-Agent Debate / LLM-as-a-Judge 等方向

【面试亮点】
"实现了 4 种 Agent 引擎（Legacy / ReAct / Plan&Execute / Debate），通过配置切换。
Debate 模式采用 worker-critic 多智能体辩论，worker 回答问题后由独立的 critic agent 评判，
不满意则 worker 改进，最多 5 轮。critic 满意时立即退出（动态退出），还能判定 STUCK 强制结束
避免无效循环。这种模式对标学术界 Multi-Agent Debate，在质量敏感任务上比单 Agent 的 ReAct
模式回答质量更高。"
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


# 最大辩论轮数（worker→critic 为一轮），防止无限循环
# 提高到 5：给 worker 更多改进机会，critic 满意时仍会立即退出（动态退出）
_DEFAULT_MAX_ROUNDS = 5


# worker 角色指令：在 base system prompt 基础上追加，明确 worker 职责
_WORKER_ROLE = (
    "\n\n## Your Role: Worker\n"
    "You are the worker agent in a worker-critic debate. Your job is to answer the user's "
    "request as accurately and thoroughly as possible, using available tools when needed. "
    "If the critic has provided feedback, carefully address each point and improve your answer."
)

# critic 角色指令：纯评判，不调用工具，不注入记忆（避免偏见）
# 支持三种判定：SATISFIED（满意立即退出）/ NEEDS_IMPROVEMENT（worker 可改进）/ STUCK（worker 无法改进，强制退出）
_CRITIC_ROLE = (
    "You are an independent critic agent evaluating a worker's answer. "
    "Your job is to judge whether the worker's answer fully and correctly satisfies "
    "the user's original request. Be strict but fair. "
    "Respond in exactly one of these formats:\n"
    "- SATISFIED: <one-line summary of why the answer is good>\n"
    "- NEEDS_IMPROVEMENT: <specific issues that must be fixed>\n"
    "- STUCK: <reason why the worker cannot make further progress on this task>\n"
    "Use STUCK only when the worker has tried multiple times and the answer is not "
    "converging toward a correct solution (e.g., the worker is hallucinating, stuck "
    "in a loop, or the task is beyond the worker's capability). "
    "Do not call any tools. Do not write the answer yourself—only evaluate."
)


class DebateState(TypedDict):
    """
    Debate 工作流状态定义

    【字段说明】
    - messages: 会话历史；只有 _end_node 在此追加最终 assistant 回复，中间轮次不写入
    - worker_system / critic_system: 在 run() 预构建的角色 prompt
    - user_request: 原始用户目标（context.goal）
    - worker_answer: worker 最新回答
    - critic_feedback: critic 原始反馈文本
    - critic_verdict: 解析后的判定（satisfied / needs_improvement / stuck）
    - round: 已完成的 worker→critic 轮数（在 critic_node 递增）
    - max_rounds: 最大轮数（默认 5）
    - status: 运行状态
    - result: 最终结果
    - fail_reason: 失败原因
    - step: 事件计数（用于 StepStarted/StepFinished 追踪）
    """
    messages: list[dict[str, Any]]
    worker_system: str
    critic_system: str
    user_request: str
    worker_answer: str | None
    critic_feedback: str | None
    critic_verdict: Literal["satisfied", "needs_improvement", "stuck"] | None
    round: int
    max_rounds: int
    status: Literal["debating", "done", "failed"]
    result: str | None
    fail_reason: str | None
    step: int


class LangGraphDebateLoop:
    """
    Worker-Critic 辩论执行引擎

    【与其它引擎的关系】
    - 4 种引擎共享相同的依赖（provider, registry, bus, permission_manager）
    - 通过配置选择引擎（engine=debate）
    - 本类结构与 LangGraphPlanExecuteLoop 完全对齐，构造函数签名一致，方便零成本切换

    【使用方式】
    ```python
    loop = LangGraphDebateLoop(
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
        构造函数 - 初始化 Debate 引擎

        【参数说明】
        与 LangGraphPlanExecuteLoop 完全一致，方便切换引擎。
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
        构建 Worker-Critic 辩论工作流图

        【图结构】
        START → worker → worker_router → critic → critic_router → end → END
                           ↓                      ↓
                      (error→end)        (needs_improvement & round<max → worker)
                                              (satisfied / max_rounds / error → end)
        """
        workflow = StateGraph(DebateState)

        # 添加节点
        workflow.add_node("worker", self._worker_node)
        workflow.add_node("critic", self._critic_node)
        workflow.add_node("end", self._end_node)

        # 添加边
        workflow.add_edge(START, "worker")

        # worker 的条件路由：回答成功 → critic，失败 → end
        workflow.add_conditional_edges(
            "worker",
            self._worker_router,
            {
                "critic": "critic",
                "error": "end",
            },
        )

        # critic 的条件路由：满意 → done，需改进且未达上限 → worker，否则 → done
        workflow.add_conditional_edges(
            "critic",
            self._critic_router,
            {
                "worker": "worker",
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
    # 节点：worker（回答问题，可调用工具）
    # ==================================================================

    async def _worker_node(self, state: DebateState, config: Any | None = None) -> dict[str, Any]:
        """
        worker 节点：回答用户问题

        【执行流程】
        1. 用 state["messages"] 构建本地消息（不修改原 messages）
        2. 若有 critic_feedback，追加一条 user 消息要求改进
        3. 调用 LLM（可调用工具）
        4. 有工具调用则执行工具
        5. 更新 worker_answer，不修改 state["messages"]（保持会话历史清洁）
        """
        await self._bus.publish(StepStartedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        # 构建本地消息（深拷贝，不修改 state["messages"]）
        local_messages = [dict(m) for m in state["messages"]]

        # 若有 critic 反馈，追加 user 消息要求 worker 改进
        if state.get("critic_feedback"):
            local_messages.append({
                "role": "user",
                "content": (
                    f"## Critic Feedback\n{state['critic_feedback']}\n\n"
                    "Please improve your answer based on this feedback."
                ),
            })

        try:
            response = await self._provider.chat(
                messages=local_messages,
                tool_schemas=self._registry.tool_schemas(),
                bus=self._bus,
                run_id=self._run_id,
                step=state["step"],
                system=state["worker_system"],
            )
        except Exception as exc:
            log.error("Worker node failed: %s", exc)
            await self._bus.publish(StepFinishedEvent(
                run_id=self._run_id,
                step=state["step"],
                ts=_now(),
            ))
            return {"status": "failed", "fail_reason": f"Worker failed: {exc}"}

        # 提取回答文本
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
            "worker_answer": result_text,
            "status": "debating",
            "step": state["step"] + 1,
        }

    async def _execute_tools(self, tool_calls: list[ToolCallBlock], state: DebateState) -> str:
        """执行工具调用，返回格式化的结果文本（复用 plan_execute 模式）"""
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
    # 节点：critic（独立评判，不调用工具）
    # ==================================================================

    async def _critic_node(self, state: DebateState, config: Any | None = None) -> dict[str, Any]:
        """
        critic 节点：独立评判 worker 的回答

        【执行流程】
        1. 构建评判 prompt（含 user_request + worker_answer）
        2. 调用 LLM（不调用工具，不注入记忆——纯评判避免偏见）
        3. 解析评判结果（SATISFIED / NEEDS_IMPROVEMENT）
        4. 递增 round（router 不能改 state，必须在此递增）
        """
        await self._bus.publish(StepStartedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        # 构建评判 prompt（含 round 信息，让 critic 能基于进展判断 STUCK）
        round_num = state.get("round", 0) + 1
        eval_prompt = (
            f"## Original User Request\n{state['user_request']}\n\n"
            f"## Worker's Answer (round {round_num}/{state.get('max_rounds', _DEFAULT_MAX_ROUNDS)})\n"
            f"{state.get('worker_answer') or '(empty)'}\n\n"
            "Evaluate if the answer fully satisfies the request. "
            "Respond with either 'SATISFIED: <summary>' or 'NEEDS_IMPROVEMENT: <issues>'. "
            "If the worker is clearly stuck (no progress across rounds, hallucinating, or looping), "
            "respond with 'STUCK: <reason>'."
        )

        messages = [{"role": "user", "content": eval_prompt}]

        try:
            response = await self._provider.chat(
                messages=messages,
                tool_schemas=[],
                bus=self._bus,
                run_id=self._run_id,
                step=state["step"],
                system=state["critic_system"],
            )
        except Exception as exc:
            log.error("Critic node failed: %s", exc)
            await self._bus.publish(StepFinishedEvent(
                run_id=self._run_id,
                step=state["step"],
                ts=_now(),
            ))
            return {"status": "failed", "fail_reason": f"Critic failed: {exc}"}

        feedback = response.text or ""
        verdict = self._parse_verdict(feedback)

        await self._bus.publish(StepFinishedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        return {
            "critic_feedback": feedback,
            "critic_verdict": verdict,
            "round": state["round"] + 1,
            "step": state["step"] + 1,
        }

    def _parse_verdict(self, feedback: str) -> Literal["satisfied", "needs_improvement", "stuck"]:
        """
        解析 critic 的反馈文本，提取判定结果

        【解析规则】
        - 大小写不敏感匹配 SATISFIED / NEEDS_IMPROVEMENT / STUCK
        - STUCK 优先匹配（避免被 NEEDS_IMPROVEMENT 截断）
        - 未知输出默认 satisfied（安全结束，同 plan_execute 的 _reflect_router 策略）
        """
        upper = (feedback or "").upper()
        # STUCK 优先：避免 "STUCK" 被当作 "needs_improvement" 的兜底
        if "STUCK" in upper:
            return "stuck"
        if "NEEDS_IMPROVEMENT" in upper:
            return "needs_improvement"
        # 包含 SATISFIED 或未知输出，都视为满意（安全结束）
        return "satisfied"

    # ==================================================================
    # 路由
    # ==================================================================

    def _worker_router(self, state: DebateState) -> str:
        """
        worker 路由：判断 worker 是否成功

        - worker 失败 → error
        - worker 成功 → critic
        """
        if state.get("status") == "failed":
            return "error"
        return "critic"

    def _critic_router(self, state: DebateState) -> str:
        """
        critic 路由：判断是否需要再次辩论（动态退出）

        【动态退出策略】
        - critic 失败 → error
        - critic 满意（SATISFIED）→ done（立即退出，不等轮数）
        - critic 判定 STUCK（worker 无法继续改进）→ done（强制退出）
        - critic 认为需改进（NEEDS_IMPROVEMENT）且未达最大轮数 → worker（重新回答）
        - 达到最大轮数或未知判定 → done（强制/安全结束）
        """
        if state.get("status") == "failed":
            return "error"

        verdict = state.get("critic_verdict")
        # 动态退出：critic 满意或判定 worker 无法改进时立即结束
        if verdict == "satisfied" or verdict == "stuck":
            return "done"

        # 只有明确 needs_improvement 才考虑重辩
        if verdict == "needs_improvement":
            if state.get("round", 0) < state.get("max_rounds", _DEFAULT_MAX_ROUNDS):
                return "worker"
            # 达到最大轮数，强制结束
            log.warning("Debate reached max_rounds=%d, forcing completion", state.get("max_rounds"))
            return "done"

        # 未知判定（None 等）→ done（安全结束，同 plan_execute 的 _reflect_router 策略）
        return "done"

    # ==================================================================
    # 节点：结束
    # ==================================================================

    async def _end_node(self, state: DebateState, config: Any | None = None) -> dict[str, Any]:
        """
        结束节点：整理最终结果

        【执行流程】
        1. 如果有 fail_reason（之前节点失败），标记为 failed
        2. 否则提取 worker_answer 作为最终结果，标记为 done
        3. 【关键】将最终结果作为 assistant 消息追加到 state["messages"]
           runner.run_and_capture 通过 context.messages[prefill_len:] 把新增消息写入 session store，
           若不追加 assistant 消息，会话历史里就只有 user 消息，客户端拿不到回复。
        """
        if state.get("fail_reason"):
            result = state["fail_reason"]
            status = "failed"
        else:
            result = state.get("worker_answer") or "No result."
            status = "done"

        # 【关键】将最终结果作为 assistant 消息追加到消息历史
        new_messages = list(state["messages"]) + [{"role": "assistant", "content": result}]

        return {"status": status, "result": result, "messages": new_messages}

    # ==================================================================
    # 执行入口
    # ==================================================================

    async def run(self, context: ExecutionContext) -> None:
        """
        执行 Worker-Critic 辩论工作流

        【执行流程】
        1. 构建 worker/critic 角色 prompt（worker 注入 base + 记忆，critic 用纯评判指令）
        2. 构建初始状态
        3. 调用 graph.ainvoke() 执行工作流
        4. 将最终结果写入执行上下文（与 plan_execute 行为一致）
        """
        # 设置 run_id：优先用 context.run_id（每次运行唯一），兜底用 session_id
        self._run_id = context.run_id or self._session_id

        # 构建 base system prompt（注入 CLAUDE.md 和 AGENTS.md 项目上下文，与其它引擎保持一致）
        base_prompt = build_base_system_prompt(
            model_name=self._llm_model_name,
            has_rag=self._has_rag,
            claude_md_context=context.claude_md_context,
            agents_md_context=context.agents_md_context,
        )

        # worker 用 base prompt + 角色指令 + 记忆上下文（回答需要历史记忆）
        worker_system = base_prompt + _WORKER_ROLE
        if context.memory_context.strip():
            worker_system += "\n\n## Memory\n" + context.memory_context.strip()

        # critic 用纯评判指令（不注入 base prompt 和记忆——纯评判，避免偏见）
        critic_system = _CRITIC_ROLE

        # 努力等级：如果指定了 max_steps_override，覆盖 context 的 max_steps
        if self._effort_params.max_steps_override > 0:
            context.max_steps = self._effort_params.max_steps_override

        # 构建初始状态（使用 context.messages 以支持会话历史回放）
        initial_state: DebateState = {
            "messages": context.messages,
            "worker_system": worker_system,
            "critic_system": critic_system,
            "user_request": context.goal,
            "worker_answer": None,
            "critic_feedback": None,
            "critic_verdict": None,
            "round": 0,
            "max_rounds": _DEFAULT_MAX_ROUNDS,
            "status": "debating",
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

            # 【关键】同步 messages 回 context：_end_node 已把最终结果作为 assistant 消息追加到 state["messages"]，
            # 这里同步回 context.messages 后，runner 会通过 context.messages[prefill_len:]
            # 把 assistant 回复写入 session store，客户端才能从 history 拿到回复。
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
            log.error("Debate loop failed: %s", exc)
            context.status = "failed"
            context.reason = str(exc)
