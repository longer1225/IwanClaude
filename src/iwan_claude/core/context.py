"""
执行上下文模块 - 管理单个 Agent run 的状态和消息历史

【学习要点】
1. Dataclasses: 使用 @dataclass 创建数据容器
2. __post_init__: 在 __init__ 之后自动调用的初始化方法
3. 消息格式: 遵循 Anthropic API 的 messages 格式
4. 上下文注入: 通过 system_prompt() 方法注入全局、项目和会话上下文

【核心概念】
- ExecutionContext: 代表一次 Agent 执行的完整上下文
- messages: 消息历史，遵循 Anthropic API 格式
- context layers: 多层上下文（global, project, session notes）

【消息格式】
```python
[
    {"role": "user", "content": "你的任务是什么？"},
    {"role": "assistant", "content": [{"type": "tool_use", ...}]},
    {"role": "user", "content": [{"type": "tool_result", ...}]},
    {"role": "assistant", "content": "任务已完成！"},
]
```
"""
from __future__ import annotations

# dataclasses: 数据类装饰器
# typing: 类型提示
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    """
    执行上下文类 - 管理单个 Agent run 的完整状态
    
    这个类是 Agent 执行的核心数据结构，包含：
    1. 执行元数据（run_id, goal, max_steps）
    2. 消息历史（messages）
    3. 多层上下文（global, project, session notes）
    4. 执行状态（status, step, result）
    
    属性说明：
        run_id: 当前 run 的唯一标识符
        goal: 用户的原始目标/指令
        max_steps: 最大执行步骤数（防止无限循环）
        prefill_messages: 预填充的消息历史（从 session 恢复时使用）
        session_notes: 会话笔记（通过 note_save 工具保存的持久化信息）
        global_context: 全局上下文（系统级信息）
        project_context: 项目上下文（项目级信息，如 CLAUDE.md）
        claude_md_context: CLAUDE.md 文件内容
        messages: 消息历史（遵循 Anthropic API 格式）
        step: 当前步骤数
        status: 执行状态（running, success, failed）
        reason: 失败原因（仅在 status=failed 时有值）
        result: 执行结果摘要
        system_prompt_override: 自定义 system prompt（skill 或 subagent 使用）
    """
    run_id: str
    goal: str
    max_steps: int
    prefill_messages: list[dict[str, Any]] = field(default_factory=list)
    session_notes: str = ""
    global_context: str = ""
    project_context: str = ""
    claude_md_context: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    step: int = 0
    status: str = "running"  # "running" | "success" | "failed"
    reason: str | None = None
    result: str = ""
    # skill 或 subagent 角色可覆盖默认 system prompt
    system_prompt_override: str | None = None

    # 初始化消息历史，优先使用 session 完整回放内容
    def __post_init__(self) -> None:
        """
        后初始化方法 - 在 __init__ 完成后自动调用
        
        初始化消息历史的优先级：
        1. 如果有 prefill_messages（从 session 恢复），使用它
        2. 如果 messages 为空，创建初始用户消息
        """
        # 如果有预填充的消息（从 session 恢复时），使用深拷贝
        if self.prefill_messages:
            self.messages = [dict(m) for m in self.prefill_messages]
        # 如果消息历史为空，创建初始用户消息
        elif not self.messages:
            self.messages.append({"role": "user", "content": self.goal})

    # 返回当前 run 的 system prompt；有 override 时跳过 base，直接注入记忆层
    def system_prompt(self, base: str) -> str:
        """
        构建完整的 system prompt
        
        将多层上下文注入到 system prompt 中，优先级：
        1. system_prompt_override（自定义，用于 skill/subagent）
        2. base（默认 system prompt）
        
        然后追加：
        - Global Context（全局上下文）
        - Project Context（项目上下文）
        - Session Notes（会话笔记）
        
        参数：
            base: 基础 system prompt
            
        返回：
            str: 完整的 system prompt
        """
        # 确定基础 prompt（优先使用 override）
        parts = [self.system_prompt_override if self.system_prompt_override else base]
        
        # 追加全局上下文
        if self.global_context.strip():
            parts.append("\n\n## Global Context\n" + self.global_context.strip())
        
        # 追加项目上下文
        if self.project_context.strip():
            parts.append("\n\n## Project Context\n" + self.project_context.strip())
        
        # 追加会话笔记
        if self.session_notes.strip():
            parts.append(
                "\n\n## Session Notes\n"
                + self.session_notes.strip()
                + "\n\nRemember important durable facts by calling note_save."
            )
        
        # 合并所有部分
        return "".join(parts)

    # 将 LLM 响应的 content blocks 追加为 assistant 消息
    def add_assistant_message(self, content: list[Any]) -> None:
        """
        添加 assistant 消息到消息历史
        
        参数：
            content: LLM 响应的内容块列表
                     可以包含 text、tool_use 等类型
        """
        self.messages.append({"role": "assistant", "content": content})

    # 将工具调用结果追加为 user 消息；同一步的多个结果共享同一条消息
    def add_tool_result(
        self, tool_use_id: str, content: str, is_error: bool = False
    ) -> None:
        """
        添加工具调用结果到消息历史
        
        根据 Anthropic API 要求，tool_result 必须放在 user 消息中。
        同一步的多个工具结果会合并到同一条 user 消息中。
        
        参数：
            tool_use_id: 对应的工具调用 ID（用于匹配）
            content: 工具执行结果
            is_error: 是否为错误结果
        """
        # 创建 tool_result 内容块
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        
        # 如果是错误结果，添加 is_error 标记
        if is_error:
            block["is_error"] = True

        # 获取最后一条消息
        last = self.messages[-1] if self.messages else None
        
        # 判断是否可以合并到最后一条消息：
        # 1. 最后一条消息存在
        # 2. 最后一条消息是 user 角色
        # 3. 最后一条消息的内容是列表
        # 4. 列表非空
        # 5. 列表中所有内容块都是 tool_result 类型
        if (
            last is not None
            and last["role"] == "user"
            and isinstance(last["content"], list)
            and last["content"]
            and all(b.get("type") == "tool_result" for b in last["content"])
        ):
            # 合并到最后一条消息
            last["content"].append(block)
        else:
            # 创建新的 user 消息
            self.messages.append({"role": "user", "content": [block]})

    # 返回 True 表示 loop 应停止（状态不再是 running）
    def is_done(self) -> bool:
        """
        判断执行是否已完成
        
        返回：
            bool: True 表示执行已完成（success 或 failed），False 表示仍在运行
        """
        return self.status != "running"

    # 将 run 标记为成功
    def mark_success(self) -> None:
        """将执行状态标记为成功"""
        self.status = "success"

    # 将 run 标记为失败并记录原因
    def mark_failed(self, reason: str) -> None:
        """
        将执行状态标记为失败
        
        参数：
            reason: 失败原因描述
        """
        self.status = "failed"
        self.reason = reason
