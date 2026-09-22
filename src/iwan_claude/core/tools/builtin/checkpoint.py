"""Checkpoint 工具模块

这个模块实现了两个核心工具：
1. ListCheckpointsTool - 列出当前会话的所有检查点
2. RestoreCheckpointTool - 将对话状态恢复到指定检查点

**Checkpoint 概念详解：**
Checkpoint（检查点）是 LangGraph 引擎的核心特性，用于保存对话的中间状态。
每个检查点包含：
- checkpoint_id: 唯一标识符
- step: 当前步骤编号
- timestamp: 创建时间戳
- channel_values: 所有通道的值（包括 messages 等）

**工作原理：**
- LangGraph 在每次状态更新时自动创建检查点
- 检查点存储在后端（memory 或 sqlite）
- 通过 thread_id 关联到具体会话
- 恢复检查点会将当前会话的消息列表替换为检查点中的消息

**使用场景：**
- 对话回溯：回到之前的对话状态重新开始
- 错误恢复：在出现错误时恢复到上一个正常状态
- 分支探索：尝试不同的对话路径后恢复原状

**注意事项：**
- 仅当使用 LangGraph 引擎且配置了检查点后端时可用
- 检查点工具需要注入 checkpointer 和 session 的 getter 函数
- 恢复检查点会覆盖当前会话的消息历史

**使用示例：**
```python
# 列出检查点
result = await list_checkpoints_tool.invoke({})

# 恢复到指定检查点
result = await restore_checkpoint_tool.invoke({
    "checkpoint_id": "abc123"
})
```
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult


class ListCheckpointsParams(BaseModel):
    """列出检查点参数模型

    这个工具不需要任何参数，配置为空模型即可。
    """
    model_config = ConfigDict(extra="ignore")


class ListCheckpointsTool(BaseTool):
    """检查点列表工具

    用于列出当前会话的所有检查点，显示每个检查点的 ID、步骤、时间和摘要。
    
    **设计模式：依赖注入**
    通过构造函数注入 checkpointer 和 session_id 的 getter 函数，实现延迟获取。
    这样设计的好处是：
    1. 工具实例可以在 checkpointer 创建前初始化
    2. 支持动态获取最新的 checkpointer 实例
    3. 解耦工具与具体实现
    """
    params_model = ListCheckpointsParams
    name = "list_checkpoints"
    description = (
        "List all checkpoints for the current session. "
        "Checkpoints are snapshots of the conversation state that can be restored. "
        "Only available when using LangGraph engine with checkpoint backend enabled."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, checkpointer_getter: Any, session_id_getter: Any) -> None:
        """构造函数：注入依赖 getter

        Args:
            checkpointer_getter: 返回 checkpointer 实例的函数，None 表示未启用检查点
            session_id_getter: 返回当前会话 ID 的函数
        """
        super().__init__()
        self._checkpointer_getter = checkpointer_getter
        self._session_id_getter = session_id_getter

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """列出当前会话的所有检查点

        **执行流程：**
        1. 验证参数（无参数）
        2. 获取 checkpointer 和 session_id
        3. 检查 checkpointer 是否可用
        4. 调用 checkpointer.alist() 获取检查点迭代器
        5. 遍历检查点，提取关键信息（ID、步骤、时间、摘要）
        6. 格式化输出结果
        
        **检查点数据结构：**
        每个检查点是一个元组，包含：
        - config: 配置信息，包含 configurable.thread_id 和 configurable.checkpoint_id
        - metadata: 元数据，包含 step 等信息
        - checkpoint: 检查点数据，包含 ts（时间戳）和 channel_values（通道值）
        
        **摘要提取逻辑：**
        从 channel_values 中获取 messages 列表，取最后一条消息的内容作为摘要。
        如果没有消息，则使用 step 编号作为摘要。
        
        Args:
            params: 空字典，无参数
            
        Returns:
            ToolResult: 包含检查点列表的结果对象，格式为：
                        Available checkpoints:
                        
                        - ID: xxx
                          Step: 1
                          Time: 2024-01-01T12:00:00
                          Summary: 用户消息内容...
        """
        # 验证参数（虽然无参数，但仍需调用验证方法保持一致性）
        _ = ListCheckpointsParams.model_validate(params)
        
        # 延迟获取 checkpointer 和 session_id
        checkpointer = self._checkpointer_getter()
        session_id = self._session_id_getter() or "default"

        # 检查 checkpointer 是否可用
        if checkpointer is None:
            return ToolResult(
                content="Checkpointer not available. Checkpoints are only available when using LangGraph engine with checkpoint backend enabled (memory or sqlite).",
                is_error=False,
            )

        try:
            # 调用 checkpointer.alist() 获取检查点异步迭代器
            # 注意：alist() 返回的是 async_generator，必须使用 async for 遍历
            checkpoints_iter = checkpointer.alist(
                {"configurable": {"thread_id": session_id}}
            )
            checkpoints = []
            # 异步遍历检查点
            async for cp_tuple in checkpoints_iter:
                checkpoints.append(cp_tuple)
            
            # 检查是否有检查点
            if not checkpoints:
                return ToolResult(content="No checkpoints found.")

            # 格式化检查点列表
            result_lines = ["Available checkpoints:"]
            for cp_tuple in checkpoints:
                # 从配置中提取 checkpoint_id
                configurable = cp_tuple.config.get("configurable", {})
                checkpoint_id = configurable.get("checkpoint_id", "")
                
                # 从元数据中提取步骤编号
                step = cp_tuple.metadata.get("step", 0)
                
                # 从检查点数据中提取时间戳
                timestamp = cp_tuple.checkpoint.get("ts", "")

                # 从通道值中提取消息摘要
                channel_values = cp_tuple.checkpoint.get("channel_values", {})
                if "messages" in channel_values:
                    msgs = channel_values["messages"]
                    if msgs and isinstance(msgs, list) and len(msgs) > 0:
                        last_msg = msgs[-1]
                        content = last_msg.get("content", "")
                        # 限制摘要长度为 80 字符
                        if isinstance(content, str):
                            summary = content[:80] + "..." if len(content) > 80 else content
                        else:
                            summary = f"step={step}"
                    else:
                        summary = f"step={step}"
                else:
                    summary = f"step={step}"

                # 添加格式化的检查点信息
                result_lines.append(
                    f"- ID: {checkpoint_id}"
                    f"\n  Step: {step}"
                    f"\n  Time: {timestamp}"
                    f"\n  Summary: {summary}"
                )

            return ToolResult(content="\n\n".join(result_lines))
        except Exception as exc:
            return ToolResult(content=f"Error listing checkpoints: {exc}", is_error=True, error_type="runtime_error")


class RestoreCheckpointParams(BaseModel):
    """恢复检查点参数模型

    必须提供要恢复的检查点 ID。
    """
    model_config = ConfigDict(extra="ignore")
    # 要恢复的检查点 ID
    checkpoint_id: str = Field(description="The ID of the checkpoint to restore")


class RestoreCheckpointTool(BaseTool):
    """检查点恢复工具

    用于将对话状态恢复到指定的检查点，会覆盖当前会话的消息历史。
    
    **恢复机制：**
    1. 根据 checkpoint_id 获取检查点数据
    2. 从检查点的 channel_values 中提取 messages
    3. 将当前会话的 messages 替换为检查点中的 messages
    4. 返回恢复成功的提示信息
    
    **注意事项：**
    - 恢复操作会覆盖当前会话的所有消息
    - 仅能恢复当前会话的检查点
    - 如果检查点不存在，返回错误信息
    """
    params_model = RestoreCheckpointParams
    name = "restore_checkpoint"
    description = (
        "Restore the conversation state to a previous checkpoint. "
        "This will revert the conversation to the state at that checkpoint. "
        "Only available when using LangGraph engine with checkpoint backend enabled."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "checkpoint_id": {
                "type": "string",
                "description": "The ID of the checkpoint to restore",
            },
        },
        "required": ["checkpoint_id"],
    }

    def __init__(self, checkpointer_getter: Any, session_getter: Any, session_id_getter: Any) -> None:
        """构造函数：注入依赖 getter

        Args:
            checkpointer_getter: 返回 checkpointer 实例的函数
            session_getter: 返回当前会话对象的函数，用于修改消息历史
            session_id_getter: 返回当前会话 ID 的函数
        """
        super().__init__()
        self._checkpointer_getter = checkpointer_getter
        self._session_getter = session_getter
        self._session_id_getter = session_id_getter

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """恢复对话状态到指定检查点

        **执行流程：**
        1. 验证参数，获取 checkpoint_id
        2. 获取 checkpointer、session 和 session_id
        3. 检查 checkpointer 是否可用
        4. 调用 checkpointer.aget_tuple() 获取检查点数据
        5. 检查检查点是否存在
        6. 从检查点中提取 messages 和 step
        7. 更新会话的 messages
        8. 返回恢复成功的提示信息
        
        Args:
            params: 包含 checkpoint_id 的参数字典
            
        Returns:
            ToolResult: 恢复结果，包含检查点 ID、步骤和消息数量
        """
        # 验证参数并获取 checkpoint_id
        p = RestoreCheckpointParams.model_validate(params)
        
        # 延迟获取依赖
        checkpointer = self._checkpointer_getter()
        session_id = self._session_id_getter() or "default"

        # 检查 checkpointer 是否可用
        if checkpointer is None:
            return ToolResult(
                content="Checkpointer not available. Checkpoints are only available when using LangGraph engine with checkpoint backend enabled (memory or sqlite).",
                is_error=False,
            )

        try:
            # 根据 checkpoint_id 获取检查点元组
            checkpoint_tuple = await checkpointer.aget_tuple(
                {"configurable": {"thread_id": session_id, "checkpoint_id": p.checkpoint_id}}
            )

            # 检查检查点是否存在
            if checkpoint_tuple is None:
                return ToolResult(
                    content=f"Checkpoint with ID '{p.checkpoint_id}' not found.",
                    is_error=True,
                    error_type="not_found",
                )

            # 从检查点中提取状态数据
            state = checkpoint_tuple.checkpoint.get("channel_values", {})
            messages = state.get("messages", [])
            step = state.get("step", 0)

            # 更新会话的消息历史
            session = self._session_getter()
            if session is not None:
                session.messages = messages

            # 返回恢复成功的提示信息
            return ToolResult(
                content=f"Successfully restored checkpoint '{p.checkpoint_id}'. "
                f"The conversation has been reverted to step {step} with {len(messages)} messages.",
            )
        except Exception as exc:
            return ToolResult(content=f"Error restoring checkpoint: {exc}", is_error=True, error_type="runtime_error")
