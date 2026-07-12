# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 dataclass 和 field：用于定义数据类
from dataclasses import dataclass, field
# 导入 Any：类型提示
from typing import Any


# ExecutionContext 数据类：存储一次 agent run 的执行状态
# 什么是执行上下文？就是 agent 在执行过程中需要的所有状态信息
# 包括消息历史、当前步骤、运行状态、结果等
@dataclass
class ExecutionContext:
    # 当前 run 的唯一标识
    run_id: str
    # 用户的目标（本次 run 的 goal）
    goal: str
    # 最大步骤数（防止无限循环）
    max_steps: int
    # 预填充的消息（来自 session 的对话历史）
    prefill_messages: list[dict[str, Any]] = field(default_factory=list)
    # session 笔记（从 notes.md 读取，注入到 system prompt）
    session_notes: str = ""
    # 消息历史（传给 LLM 的 messages 参数）
    messages: list[dict[str, Any]] = field(default_factory=list)
    # 当前步骤（从 0 开始）
    step: int = 0
    # 运行状态（running、success、failed）
    status: str = "running"
    # 失败原因（仅当 status 为 failed 时有值）
    reason: str | None = None
    # 最终结果（成功时的文本结果）
    result: str = ""

    # 初始化消息历史（dataclass 的后置初始化方法）
    # 优先级：prefill_messages > 现有 messages > 默认用户消息
    def __post_init__(self) -> None:
        # 如果有预填充的消息（来自 session 对话历史），使用它们
        if self.prefill_messages:
            self.messages = [dict(m) for m in self.prefill_messages]
        # 如果没有消息，创建一条默认的用户消息（包含 goal）
        elif not self.messages:
            self.messages.append({"role": "user", "content": self.goal})

    # 返回当前 run 的 system prompt
    # 如果有 session notes，将其注入到 system prompt 中
    # 什么是 system prompt？就是告诉 LLM 如何行为的指令
    def system_prompt(self, base: str) -> str:
        # 如果没有笔记，直接返回基础 prompt
        if not self.session_notes.strip():
            return base
        # 如果有笔记，将笔记追加到 system prompt 中
        return (
            base
            + "\n\n## Session Notes\n"
            + self.session_notes.strip()
            + "\n\nRemember important durable facts by calling note_save."
        )

    # 将 LLM 响应的内容追加为 assistant 消息
    # assistant 消息：LLM 生成的回复
    def add_assistant_message(self, content: list[Any]) -> None:
        self.messages.append({"role": "assistant", "content": content})

    # 将工具调用结果追加为 user 消息
    # 同一步的多个工具结果共享同一条用户消息（Anthropic API 的要求）
    def add_tool_result(
        self, tool_use_id: str, content: str, is_error: bool = False
    ) -> None:
        # 创建工具结果块
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,  # 关联的工具调用 ID
            "content": content,          # 工具返回的内容
        }
        # 如果是错误结果，标记 is_error
        if is_error:
            block["is_error"] = True

        # 获取最后一条消息
        last = self.messages[-1] if self.messages else None
        # 如果最后一条消息是用户消息，且内容是工具结果列表
        # 将新结果追加到现有列表中（同一步的多个结果共享一条消息）
        if (
            last is not None
            and last["role"] == "user"
            and isinstance(last["content"], list)
            and last["content"]
            and all(b.get("type") == "tool_result" for b in last["content"])
        ):
            last["content"].append(block)
        else:
            # 否则，创建一条新的用户消息
            self.messages.append({"role": "user", "content": [block]})

    # 判断 run 是否应该停止（状态不再是 running）
    def is_done(self) -> bool:
        return self.status != "running"

    # 将 run 标记为成功
    def mark_success(self) -> None:
        self.status = "success"

    # 将 run 标记为失败并记录原因
    def mark_failed(self, reason: str) -> None:
        self.status = "failed"
        self.reason = reason
