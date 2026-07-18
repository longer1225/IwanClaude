from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_CONTENT_LENGTH = 1024 * 1024


class AddContextParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_path: str = Field(description="Path to the file to add as context")
    start_line: int | None = Field(default=None, description="Start line number (inclusive)")
    end_line: int | None = Field(default=None, description="End line number (inclusive)")


class AddContextTool(BaseTool):
    params_model = AddContextParams
    name = "add_context"
    description = (
        "Add file content to the conversation context. "
        "This allows the AI to see the file content without reading it again. "
        "Similar to Claude Code's /add command or @filename mention. "
        "Use this when you want to reference a file's content in your conversation. "
        "Content is truncated at 1MB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to add as context",
            },
            "start_line": {
                "type": "integer",
                "description": "Optional: start line number (inclusive)",
            },
            "end_line": {
                "type": "integer",
                "description": "Optional: end line number (inclusive)",
            },
        },
        "required": ["file_path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = AddContextParams.model_validate(params)

        file_path = Path(p.file_path).resolve()
        if not file_path.exists():
            return ToolResult(content=f"File not found: {file_path}", is_error=True, error_type="runtime_error")

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                content=f"Cannot read file: {file_path} (binary or non-UTF-8 encoding)",
                is_error=True,
                error_type="runtime_error",
            )
        except Exception as exc:
            return ToolResult(content=f"Failed to read file: {exc}", is_error=True, error_type="runtime_error")

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        start = p.start_line or 1
        end = p.end_line or total_lines

        if start < 1:
            start = 1
        if end > total_lines:
            end = total_lines
        if start > end:
            return ToolResult(content="Start line must be less than or equal to end line", is_error=True, error_type="schema_error")

        selected_lines = lines[start - 1:end]
        selected_content = "".join(selected_lines)

        if len(selected_content) > _MAX_CONTENT_LENGTH:
            selected_content = selected_content[:_MAX_CONTENT_LENGTH] + "\n[truncated]"

        header = f"=== {file_path} (lines {start}-{end}) ==="
        return ToolResult(content=f"{header}\n\n{selected_content}")