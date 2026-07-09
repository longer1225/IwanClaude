# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 dataclass 相关工具，用于定义数据类
from dataclasses import dataclass, field
# 导入 Any 类型，用于类型注解
from typing import Any


# ExecutionContext 数据类：保存一次 agent run 的完整执行上下文
# 包含运行状态、消息历史、步骤计数等信息
@dataclass
class ExecutionContext:
    # 运行的唯一 ID，用于标识这次执行
    run_id: str
    # 用户指定的目标字符串，如 "总结 README.md"
    goal: str
    # 最大步骤数，超过后会终止执行（防止无限循环）
    max_steps: int
    # 消息历史列表，用于存储对话记录，默认为空列表
    messages: list[dict[str, Any]] = field(default_factory=list)
    # 当前步骤编号，从 0 开始，每完成一步递增
    step: int = 0
    # 运行状态："running"（运行中）、"success"（成功）、"failed"（失败），默认为 "running"
    status: str = "running"
    # 失败原因（可选），当 status 为 "failed" 时记录失败原因
    reason: str | None = None

    # 初始化后钩子：在 dataclass 初始化完成后自动执行
    # 函数作用：将用户目标作为第一条消息写入消息历史
    def __post_init__(self) -> None:
        # 如果消息历史为空（首次创建）
        if not self.messages:
            # 添加用户消息，格式符合 LLM API 的对话格式
            self.messages.append({"role": "user", "content": self.goal})

    # 将 LLM 响应的内容块追加为 assistant 消息
    # 函数作用：记录 LLM 的回复，用于后续对话上下文
    # 传参：content - LLM 返回的内容块列表（可能包含文本、工具调用等）
    # 返回值：None
    def add_assistant_message(self, content: list[Any]) -> None:
        # 添加 assistant 角色的消息到消息历史
        self.messages.append({"role": "assistant", "content": content})

    # 将工具调用结果追加为 user 消息；同一步的多个结果共享同一条消息
    # 函数作用：记录工具执行的结果，用于后续对话上下文
    # 传参：
    #   tool_use_id - 工具调用的唯一 ID，用于关联工具调用和结果
    #   content - 工具执行结果的内容
    #   is_error - 是否为错误结果，默认为 False
    # 返回值：None
    def add_tool_result(
        self, tool_use_id: str, content: str, is_error: bool = False
    ) -> None:
        # 构建工具结果块
        block: dict[str, Any] = {
            "type": "tool_result",       # 块类型标识
            "tool_use_id": tool_use_id,  # 关联的工具调用 ID
            "content": content,          # 工具执行结果
        }
        # 如果是错误结果，添加 is_error 标记
        if is_error:
            block["is_error"] = True

        # 获取最后一条消息（如果存在）
        last = self.messages[-1] if self.messages else None
        # 判断是否可以将工具结果追加到最后一条消息中
        # 条件：最后一条消息存在、角色是 user、内容是列表、列表非空、列表中所有块都是 tool_result 类型
        if (
            last is not None
            and last["role"] == "user"
            and isinstance(last["content"], list)
            and last["content"]
            and all(b.get("type") == "tool_result" for b in last["content"])
        ):
            # 将工具结果块追加到最后一条消息的内容列表中
            last["content"].append(block)
        else:
            # 创建新的 user 消息，将工具结果作为内容
            self.messages.append({"role": "user", "content": [block]})

    # 返回 True 表示 loop 应停止（状态不再是 running）
    # 函数作用：判断当前运行是否已经结束
    # 返回值：bool - True 表示已结束，False 表示仍在运行
    def is_done(self) -> bool:
        return self.status != "running"

    # 将 run 标记为成功
    # 函数作用：更新状态为 success，表示运行成功完成
    # 返回值：None
    def mark_success(self) -> None:
        self.status = "success"

    # 将 run 标记为失败并记录原因
    # 函数作用：更新状态为 failed，并记录失败原因
    # 传参：reason - 失败原因字符串
    # 返回值：None
    def mark_failed(self, reason: str) -> None:
        self.status = "failed"
        self.reason = reason
