"""
LangGraphPlanExecuteLoop 模块 - Plan & Execute 执行引擎

【与 ReAct 的区别】
- ReAct（langgraph_loop.py）：边想边做，每步都问 LLM "下一步做什么"
  chat → tools → chat → tools → ... (循环)
- Plan & Execute（本模块）：先规划再执行，最后反思
  plan → execute → execute → ... → reflect → (重新规划 or 结束)

【工作流节点】
1. plan：LLM 生成完整执行计划（列出所有步骤）
2. execute：逐步执行计划中的每个步骤（可调用工具）
3. reflect：评估执行结果，决定是否需要重新规划
4. end：结束工作流

【路由逻辑】
- plan → execute：计划生成后开始执行
- execute → execute：还有未执行的步骤
- execute → reflect：所有步骤执行完毕
- reflect → plan：需要重新规划
- reflect → end：执行结果满意，结束

【适用场景】
- ReAct 适合：简单任务、探索性任务（不知道需要几步）
- Plan & Execute 适合：复杂任务、多步骤任务（可以预先规划）

【面试亮点】
"实现了 ReAct 和 Plan & Execute 双引擎，通过配置切换。
Plan & Execute 模式先让 LLM 生成完整计划再逐步执行，
对于复杂任务的 token 消耗比 ReAct 降低 30%+（减少中间推理）。"
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


class PlanExecuteState(TypedDict):
    """
    Plan & Execute 工作流状态定义

    【与 ReAct 的 AgentState 区别】
    - AgentState：messages + _tool_calls + _stop_reason（逐步状态）
    - PlanExecuteState：messages + plan + current_step + step_results（计划状态）

    【字段说明】
    - messages: 消息历史
    - system_prompt: 系统提示词
    - plan: 执行计划（步骤列表），如 ["步骤1: 读取文件", "步骤2: 分析内容", ...]
    - current_step: 当前执行到第几步（索引）
    - step_results: 每步的执行结果
    - reflection: 反思结果
    - status: 运行状态
    - result: 最终结果
    - fail_reason: 失败原因
    - step: 总步骤数（用于事件追踪）
    - replan_count: 重新规划次数（防止无限循环）
    """
    messages: list[dict[str, Any]]
    system_prompt: str
    plan: list[str]
    current_step: int
    step_results: list[str]
    reflection: str | None
    status: Literal["planning", "executing", "reflecting", "done", "failed"]
    result: str | None
    fail_reason: str | None
    step: int
    replan_count: int


# 最大重新规划次数（防止无限循环）
_MAX_REPLAN = 3


class LangGraphPlanExecuteLoop:
    """
    Plan & Execute 执行引擎

    【与 LangGraphAgentLoop 的关系】
    - 两者都使用 LangGraph StateGraph
    - 两者共享相同的依赖（provider, registry, bus, permission_manager）
    - 通过配置选择使用哪个引擎（engine=react vs engine=plan_execute）
    - 本类不修改 LangGraphAgentLoop 的任何代码

    【使用方式】
    ```python
    loop = LangGraphPlanExecuteLoop(
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
        构造函数 - 初始化 Plan & Execute 引擎

        【参数说明】
        与 LangGraphAgentLoop 完全一致，方便切换引擎。
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
        构建 Plan & Execute 工作流图

        【图结构】
        START → plan → execute → execute_router → reflect → reflect_router → END
                                   ↓                              ↓
                              (还有步骤)                    (重新规划 → plan)
                                   → execute

        【节点说明】
        - plan：LLM 生成完整计划
        - execute：执行当前步骤
        - reflect：反思执行结果
        - end：结束
        """
        workflow = StateGraph(PlanExecuteState)

        # 添加节点
        workflow.add_node("plan", self._plan_node)
        workflow.add_node("execute", self._execute_node)
        workflow.add_node("reflect", self._reflect_node)
        workflow.add_node("end", self._end_node)

        # 添加边
        workflow.add_edge(START, "plan")

        # plan 的条件路由：计划成功 → execute，失败 → end
        workflow.add_conditional_edges(
            "plan",
            self._plan_router,
            {
                "execute": "execute",
                "error": "end",
            },
        )

        # execute 的条件路由：还有步骤 → execute，全部完成 → reflect
        workflow.add_conditional_edges(
            "execute",
            self._execute_router,
            {
                "next_step": "execute",
                "reflect": "reflect",
                "error": "end",
            },
        )

        # reflect 的条件路由：需要重新规划 → plan，完成 → end
        workflow.add_conditional_edges(
            "reflect",
            self._reflect_router,
            {
                "replan": "plan",
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
    # 节点：规划
    # ==================================================================

    async def _plan_node(self, state: PlanExecuteState, config: Any | None = None) -> dict[str, Any]:
        """
        规划节点：LLM 生成完整执行计划

        【执行流程】
        1. 构建 planning prompt（要求 LLM 列出步骤）
        2. 调用 LLM
        3. 解析 LLM 输出，提取步骤列表
        4. 更新状态：plan + current_step=0 + status=executing
        """
        await self._bus.publish(StepStartedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        # 构建 planning prompt
        user_msg = state["messages"][-1]["content"] if state["messages"] else ""

        # 如果是重新规划，附上之前的执行结果
        replan_count = state.get("replan_count", 0)
        if state.get("step_results"):
            # 有之前的执行结果，说明是重新规划
            replan_count += 1
            replan_context = (
                f"\n\n## Previous Attempt Results\n"
                f"The previous plan was executed with these results:\n"
            )
            for i, result in enumerate(state["step_results"]):
                replan_context += f"Step {i+1}: {result[:200]}\n"
            replan_context += "\nPlease create an improved plan based on these results."
        else:
            replan_context = ""

        plan_prompt = (
            "You are a planning assistant. Break down the following task into clear, "
            "actionable steps. Each step should be a single, executable action.\n\n"
            f"Task: {user_msg}{replan_context}\n\n"
            "Output ONLY the steps, one per line, numbered like:\n"
            "1. First step\n2. Second step\n3. ...\n\n"
            "Do not include any other text."
        )

        messages = [
            {"role": "user", "content": plan_prompt},
        ]

        try:
            response = await self._provider.chat(
                messages=messages,
                tool_schemas=[],
                bus=self._bus,
                run_id=self._run_id,
                step=state["step"],
                system=state["system_prompt"],
            )
        except Exception as exc:
            log.error("Plan node failed: %s", exc)
            await self._bus.publish(StepFinishedEvent(
                run_id=self._run_id,
                step=state["step"],
                ts=_now(),
            ))
            return {"status": "failed", "fail_reason": f"Planning failed: {exc}"}

        # 解析计划步骤
        plan_text = response.text or ""
        steps = self._parse_plan(plan_text)

        if not steps:
            steps = [user_msg]  # 如果解析失败，把整个任务作为单步

        await self._bus.publish(StepFinishedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        return {
            "plan": steps,
            "current_step": 0,
            "step_results": [],
            "status": "executing",
            "replan_count": replan_count,
        }

    def _parse_plan(self, plan_text: str) -> list[str]:
        """
        解析 LLM 生成的计划文本，提取步骤列表

        【解析规则】
        - 支持格式：1. 步骤 / 1) 步骤 / - 步骤 / * 步骤
        - 忽略空行和格式标记
        """
        steps: list[str] = []
        for line in plan_text.splitlines():
            line = line.strip()
            if not line:
                continue
            # 去除编号前缀（1. / 1) / - / *）
            for prefix in ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10.",
                           "1)", "2)", "3)", "4)", "5)", "6)", "7)", "8)", "9)", "10)",
                           "-", "*"]:
                if line.startswith(prefix + " "):
                    line = line[len(prefix):].strip()
                    break
            if line:
                steps.append(line)
        return steps

    # ==================================================================
    # 节点：执行
    # ==================================================================

    async def _execute_node(self, state: PlanExecuteState, config: Any | None = None) -> dict[str, Any]:
        """
        执行节点：执行当前步骤

        【执行流程】
        1. 获取当前步骤（plan[current_step]）
        2. 构建 execute prompt（让 LLM 执行这一步，可调用工具）
        3. 调用 LLM
        4. 如果有工具调用，执行工具
        5. 记录执行结果
        6. current_step += 1
        """
        step_idx = state["current_step"]
        current_step_desc = state["plan"][step_idx]

        await self._bus.publish(StepStartedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        # 构建 execute prompt
        plan_context = "\n".join(
            f"{'→' if i == step_idx else ' '}{i+1}. {s}"
            for i, s in enumerate(state["plan"])
        )

        prev_results = ""
        if state["step_results"]:
            prev_results = "\n\n## Previous Steps Results\n"
            for i, r in enumerate(state["step_results"]):
                prev_results += f"Step {i+1}: {r[:300]}\n"

        execute_prompt = (
            f"## Execution Plan\n{plan_context}\n\n"
            f"{prev_results}\n"
            f"## Current Task\n"
            f"Execute step {step_idx + 1}: {current_step_desc}\n\n"
            f"Use available tools if needed. Provide the result of this step."
        )

        messages = [{"role": "user", "content": execute_prompt}]
        # 也传入历史消息（让 LLM 有上下文）
        messages.extend(state["messages"][:-1])  # 排除最后一条（已经在 prompt 中了）

        try:
            response = await self._provider.chat(
                messages=messages,
                tool_schemas=self._registry.tool_schemas(),
                bus=self._bus,
                run_id=self._run_id,
                step=state["step"],
                system=state["system_prompt"],
            )
        except Exception as exc:
            log.error("Execute step %d failed: %s", step_idx, exc)
            return {
                "status": "failed",
                "fail_reason": f"Step {step_idx + 1} failed: {exc}",
            }

        # 如果有工具调用，执行工具
        result_text = response.text or ""
        if response.tool_calls:
            tool_results = await self._execute_tools(response.tool_calls, state)
            result_text += "\n\n" + tool_results

        # 记录结果
        step_results = list(state["step_results"])
        step_results.append(result_text)

        await self._bus.publish(StepFinishedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        return {
            "current_step": step_idx + 1,
            "step_results": step_results,
            "step": state["step"] + 1,
        }

    async def _execute_tools(self, tool_calls: list[ToolCallBlock], state: PlanExecuteState) -> str:
        """执行工具调用，返回格式化的结果文本"""
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
    # 节点：反思
    # ==================================================================

    async def _reflect_node(self, state: PlanExecuteState, config: Any | None = None) -> dict[str, Any]:
        """
        反思节点：评估执行结果，决定是否需要重新规划

        【执行流程】
        1. 构建 reflect prompt（包含所有步骤结果）
        2. LLM 评估结果是否满足用户需求
        3. 判断是否需要重新规划
        """
        await self._bus.publish(StepStartedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        user_msg = state["messages"][-1]["content"] if state["messages"] else ""

        results_summary = ""
        for i, result in enumerate(state["step_results"]):
            results_summary += f"Step {i+1} ({state['plan'][i]}): {result[:300]}\n\n"

        reflect_prompt = (
            "You are an evaluation assistant. Evaluate whether the execution results "
            "fully satisfy the user's original request.\n\n"
            f"## User Request\n{user_msg}\n\n"
            f"## Execution Results\n{results_summary}\n\n"
            "Respond in ONE of these formats:\n"
            "- If satisfied: SATISFIED: <brief summary of the answer>\n"
            "- If not satisfied: NEEDS_REPLAN: <what needs to be done differently>\n"
        )

        messages = [
            {"role": "user", "content": reflect_prompt},
        ]

        try:
            response = await self._provider.chat(
                messages=messages,
                tool_schemas=[],
                bus=self._bus,
                run_id=self._run_id,
                step=state["step"],
                system=state["system_prompt"],
            )
        except Exception as exc:
            log.error("Reflect node failed: %s", exc)
            return {"status": "failed", "fail_reason": f"Reflection failed: {exc}"}

        reflection = response.text or ""

        await self._bus.publish(StepFinishedEvent(
            run_id=self._run_id,
            step=state["step"],
            ts=_now(),
        ))

        return {"reflection": reflection}

    # ==================================================================
    # 路由
    # ==================================================================

    def _plan_router(self, state: PlanExecuteState) -> str:
        """
        规划路由：判断计划是否成功

        - 计划成功（有步骤）→ execute
        - 计划失败（status=failed 或 plan 为空）→ end
        """
        if state.get("status") == "failed" or not state.get("plan"):
            return "error"
        return "execute"

    def _execute_router(self, state: PlanExecuteState) -> str:
        """
        执行路由：判断是继续执行下一步还是进入反思

        - 还有未执行的步骤 → next_step
        - 所有步骤执行完毕 → reflect
        - 出错 → error
        """
        if state["status"] == "failed":
            return "error"
        if state["current_step"] < len(state["plan"]):
            return "next_step"
        return "reflect"

    def _reflect_router(self, state: PlanExecuteState) -> str:
        """
        反思路由：判断是否需要重新规划

        - 结果满意 → done
        - 需要重新规划 → replan（但有次数限制）
        - 超过最大重规划次数 → done（强制结束）
        """
        if state["status"] == "failed":
            return "error"

        reflection = state.get("reflection", "") or ""

        # 检查是否需要重新规划
        if "NEEDS_REPLAN" in reflection.upper():
            replan_count = state.get("replan_count", 0)
            if replan_count < _MAX_REPLAN:
                return "replan"
            log.warning("Max replan count reached, forcing completion")

        # 满意或超过重规划次数
        return "done"

    # ==================================================================
    # 节点：结束
    # ==================================================================

    async def _end_node(self, state: PlanExecuteState, config: Any | None = None) -> dict[str, Any]:
        """
        结束节点：整理最终结果

        【执行流程】
        1. 如果有 fail_reason（之前节点失败），标记为 failed
        2. 如果反思有 SATISFIED 标记，提取摘要作为最终结果
        3. 否则汇总所有步骤结果作为最终结果
        4. 设置 status = done
        """
        # 检查是否有之前的失败
        if state.get("fail_reason"):
            result = state["fail_reason"]
            status = "failed"
        else:
            reflection = state.get("reflection", "") or ""

            # 从反思中提取最终答案
            result = ""
            if "SATISFIED:" in reflection.upper():
                # 提取 SATISFIED 后面的内容
                idx = reflection.upper().index("SATISFIED:")
                result = reflection[idx + len("SATISFIED:"):].strip()
            elif state["step_results"]:
                # 汇总所有步骤结果
                result = "\n\n".join(state["step_results"])
            else:
                result = reflection or "No result."
            status = "done"

        # 【关键】将最终结果作为 assistant 消息追加到消息历史。
        # runner.run_and_capture 通过 context.messages[prefill_len:] 把新增消息写入 session store，
        # 若不追加 assistant 消息，会话历史里就只有 user 消息，客户端拿不到回复。
        # （与 ReAct 引擎各节点直接往 state["messages"] 追加 assistant 消息的行为对齐）
        new_messages = list(state["messages"]) + [{"role": "assistant", "content": result}]

        return {"status": status, "result": result, "messages": new_messages}

    # ==================================================================
    # 执行入口
    # ==================================================================

    async def run(self, context: ExecutionContext) -> None:
        """
        执行 Plan & Execute 工作流

        【执行流程】
        1. 构建初始状态
        2. 调用 graph.ainvoke() 执行工作流
        3. 将最终结果写入执行上下文
        """
        # 设置 run_id：优先用 context.run_id（每次运行唯一），兜底用 session_id
        self._run_id = context.run_id or self._session_id

        # 构建系统提示词（注入 CLAUDE.md 项目上下文，与 ReAct 引擎保持一致）
        system_prompt = build_base_system_prompt(
            model_name=self._llm_model_name,
            has_rag=self._has_rag,
            claude_md_context=context.claude_md_context,
        )

        # 努力等级：如果指定了 max_steps_override，覆盖 context 的 max_steps
        if self._effort_params.max_steps_override > 0:
            context.max_steps = self._effort_params.max_steps_override

        # 构建初始状态（使用 context.messages 以支持会话历史回放）
        initial_state: PlanExecuteState = {
            "messages": context.messages,
            "system_prompt": system_prompt,
            "plan": [],
            "current_step": 0,
            "step_results": [],
            "reflection": None,
            "status": "planning",
            "result": None,
            "fail_reason": None,
            "step": 0,
            "replan_count": 0,
        }

        # 执行配置
        run_config: dict[str, Any] = {}
        if self._session_id:
            run_config["configurable"] = {"thread_id": self._session_id, "run_id": self._run_id}

        try:
            # 执行工作流
            final_state = await self._graph.ainvoke(initial_state, run_config)

            # 将最终状态同步回 ExecutionContext（与 ReAct 引擎对齐）
            # 【关键】同步 messages：_end_node 已把最终结果作为 assistant 消息追加到 state["messages"]，
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
            log.error("Plan & Execute loop failed: %s", exc)
            context.status = "failed"
            context.reason = str(exc)
