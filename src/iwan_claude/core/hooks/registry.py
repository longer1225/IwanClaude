"""
hook 注册表：按 事件+matcher 索引，聚合多 hook 裁定，经 EventBus 广播观测事件

【学习要点】
1. 聚合序 = 严格度序：deny > ask > allow > none——任一 hook 说"不行"就不行，
   这是"多外部裁判"场景下唯一不会互相打架的结合律（对齐官方 multi-hook 语义）。
2. PreToolUse 命中 DENY 即短路：后续 hook 通常是为前一次裁定做审计/通知的，
   工具都不会执行了，让它们跑反而制造噪音。
3. PostToolUse 不看 decision：工具已经执行，撤销为时已晚——hook 在这里的
   合法权力只有"追加警告文本给模型"（exit 2 的 stderr），裁定值被忽略。
"""
from __future__ import annotations

import time
from typing import Any

from iwan_claude.core.hooks.runner import HookDecision, HookOutcome, run_hook
from iwan_claude.core.hooks.spec import HookSpec

# 严格度排序（聚合取最严格者）；NONE 最低
_SEVERITY: dict[HookDecision, int] = {
    HookDecision.NONE: 0,
    HookDecision.ALLOW: 1,
    HookDecision.ASK: 2,
    HookDecision.DENY: 3,
}

# 聚合一组裁定：取最严格；全弃权返回 NONE
def aggregate(outcomes: list[HookOutcome]) -> HookOutcome:
    """
    多 hook 结果取最严格；空列表/全弃权 → NONE（对权限链透明）
    """
    best = HookOutcome(HookDecision.NONE, "", "", None)
    for o in outcomes:
        if _SEVERITY[o.decision] > _SEVERITY[best.decision]:
            best = o
    return best


class HookRegistry:
    """
    hook 集合的运行时形态：匹配、执行、聚合、广播

    【设计目的】
    PermissionManager 与工具执行路径都只持有这一个对象（可为 None），
    生命周期点的语义差异（Pre 短路 / Post 追加警告）封装在方法内部，
    调用方不需要理解退出码协议。
    """
    def __init__(self, hooks: list[HookSpec], *, bus: Any = None) -> None:
        """
        初始化注册表

        【参数说明】
        - hooks: 已通过 spec 校验的 hook 列表
        - bus: EventBus（可选）；提供时每个实际执行过的 hook 广播一条
          hook.evaluated 事件（TUI 观测性优先），None 时静默
        """
        self._hooks = list(hooks)
        self._bus = bus

    # 是否一条 hook 都没有（决定调用方能否完全跳过本层）
    def is_empty(self) -> bool:
        return not self._hooks

    # 按事件名 + matcher 筛选适用的 hook 规格（保持配置顺序=确定性执行顺序）
    def _matching(self, event: str, tool_name: str) -> list[HookSpec]:
        return [
            h for h in self._hooks
            if h.event == event and (h.matcher == "*" or h.matcher == tool_name)
        ]

    # 构造传给 hook 的 stdin JSON 载荷（对齐官方 schema，方便生态脚本移植）
    def _payload(
        self, event: str, tool_name: str, params: dict[str, Any],
        session_id: str, run_id: str,
    ) -> dict[str, Any]:
        return {
            "hook_event_name": event,
            "tool_name": tool_name,
            "tool_input": params,
            "session_id": session_id,
            "run_id": run_id,
        }

    # 广播单个 hook 的裁定（bus 缺省 / 事件模型未注入时静默跳过）
    async def _emit(self, spec: HookSpec, event: str, outcome: HookOutcome,
                    tool_name: str, session_id: str, run_id: str, elapsed_ms: int) -> None:
        if self._bus is None:
            return
        from iwan_claude.core.bus.events import HookEvaluatedEvent
        await self._bus.publish(HookEvaluatedEvent(
            run_id=run_id,
            session_id=session_id,
            tool_name=tool_name,
            hook_event=event,
            command=" ".join(spec.argv),
            decision=outcome.decision.value,
            reason=outcome.reason[:200],
            elapsed_ms=elapsed_ms,
            ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        ))

    # PreToolUse：工具执行 + 权限链之前的第一道闸；返回聚合裁定
    async def run_pre_tool_use(
        self, tool_name: str, params: dict[str, Any],
        session_id: str = "", run_id: str = "",
    ) -> HookOutcome:
        """
        执行全部匹配的 PreToolUse hook，聚合为最严格裁定（DENY 短路）
        """
        outcomes: list[HookOutcome] = []
        for spec in self._matching("PreToolUse", tool_name):
            payload = self._payload("PreToolUse", tool_name, params, session_id, run_id)
            t0 = time.monotonic()
            outcome = await run_hook(spec, payload)
            outcomes.append(outcome)
            await self._emit(spec, "PreToolUse", outcome, tool_name,
                             session_id, run_id, int((time.monotonic() - t0) * 1000))
            if outcome.decision == HookDecision.DENY:
                break  # 短路：已被硬阻断，后续 hook 没有执行对象了
        return aggregate(outcomes)

    # PostToolUse：工具成功执行后运行；返回应回灌给模型的警告文本（可为空串）
    async def run_post_tool_use(
        self, tool_name: str, params: dict[str, Any], tool_output: str,
        session_id: str = "", run_id: str = "",
    ) -> str:
        """
        执行全部匹配的 PostToolUse hook；exit 2 的 stderr 拼成追加警告返回

        【设计】裁决值在 Post 阶段被刻意忽略——"事后 hook"无权撤销已发生的
        执行（官方同款）；它唯一能影响模型的方式是 stderr 回灌。
        """
        warnings: list[str] = []
        for spec in self._matching("PostToolUse", tool_name):
            payload = self._payload("PostToolUse", tool_name, params, session_id, run_id)
            payload["tool_output"] = tool_output[:4000]  # 控制 stdin 体积
            t0 = time.monotonic()
            outcome = await run_hook(spec, payload)
            await self._emit(spec, "PostToolUse", outcome, tool_name,
                             session_id, run_id, int((time.monotonic() - t0) * 1000))
            if outcome.decision == HookDecision.DENY and outcome.stderr_to_model:
                warnings.append(outcome.stderr_to_model)
        return "\n".join(f"[PostToolUse hook] {w}" for w in warnings)
