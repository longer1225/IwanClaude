from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult


class ListCheckpointsParams(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ListCheckpointsTool(BaseTool):
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
        super().__init__()
        self._checkpointer_getter = checkpointer_getter
        self._session_id_getter = session_id_getter

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        _ = ListCheckpointsParams.model_validate(params)
        checkpointer = self._checkpointer_getter()
        session_id = self._session_id_getter() or "default"

        if checkpointer is None:
            return ToolResult(
                content="Checkpointer not available. Checkpoints are only available when using LangGraph engine with checkpoint backend enabled (memory or sqlite).",
                is_error=False,
            )

        try:
            checkpoints_iter = checkpointer.alist(
                {"configurable": {"thread_id": session_id}}
            )
            checkpoints = []
            async for cp_tuple in checkpoints_iter:
                checkpoints.append(cp_tuple)
            
            if not checkpoints:
                return ToolResult(content="No checkpoints found.")

            result_lines = ["Available checkpoints:"]
            for cp_tuple in checkpoints:
                configurable = cp_tuple.config.get("configurable", {})
                checkpoint_id = configurable.get("checkpoint_id", "")
                step = cp_tuple.metadata.get("step", 0)
                timestamp = cp_tuple.checkpoint.get("ts", "")

                channel_values = cp_tuple.checkpoint.get("channel_values", {})
                if "messages" in channel_values:
                    msgs = channel_values["messages"]
                    if msgs and isinstance(msgs, list) and len(msgs) > 0:
                        last_msg = msgs[-1]
                        content = last_msg.get("content", "")
                        if isinstance(content, str):
                            summary = content[:80] + "..." if len(content) > 80 else content
                        else:
                            summary = f"step={step}"
                    else:
                        summary = f"step={step}"
                else:
                    summary = f"step={step}"

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
    model_config = ConfigDict(extra="ignore")
    checkpoint_id: str = Field(description="The ID of the checkpoint to restore")


class RestoreCheckpointTool(BaseTool):
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
        super().__init__()
        self._checkpointer_getter = checkpointer_getter
        self._session_getter = session_getter
        self._session_id_getter = session_id_getter

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = RestoreCheckpointParams.model_validate(params)
        checkpointer = self._checkpointer_getter()
        session_id = self._session_id_getter() or "default"

        if checkpointer is None:
            return ToolResult(
                content="Checkpointer not available. Checkpoints are only available when using LangGraph engine with checkpoint backend enabled (memory or sqlite).",
                is_error=False,
            )

        try:
            checkpoint_tuple = await checkpointer.aget_tuple(
                {"configurable": {"thread_id": session_id, "checkpoint_id": p.checkpoint_id}}
            )

            if checkpoint_tuple is None:
                return ToolResult(
                    content=f"Checkpoint with ID '{p.checkpoint_id}' not found.",
                    is_error=True,
                    error_type="not_found",
                )

            state = checkpoint_tuple.checkpoint.get("channel_values", {})
            messages = state.get("messages", [])
            step = state.get("step", 0)

            session = self._session_getter()
            if session is not None:
                session.messages = messages

            return ToolResult(
                content=f"Successfully restored checkpoint '{p.checkpoint_id}'. "
                f"The conversation has been reverted to step {step} with {len(messages)} messages.",
            )
        except Exception as exc:
            return ToolResult(content=f"Error restoring checkpoint: {exc}", is_error=True, error_type="runtime_error")