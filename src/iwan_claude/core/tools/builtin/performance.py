from __future__ import annotations

import asyncio
import cProfile
import pstats
from io import StringIO
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_BYTES = 64 * 1024


class ProfileCodeParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_path: str = Field(description="Path to the Python file to profile")
    function_name: str | None = Field(default=None, description="Specific function to profile")


class ProfileCodeTool(BaseTool):
    params_model = ProfileCodeParams
    name = "profile_code"
    description = (
        "Profile Python code performance. "
        "Uses cProfile to identify performance bottlenecks."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the Python file to profile",
            },
            "function_name": {
                "type": "string",
                "description": "Optional: specific function to profile",
            },
        },
        "required": ["file_path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = ProfileCodeParams.model_validate(params)

        file_path = Path(p.file_path).resolve()
        if not file_path.exists():
            return ToolResult(content=f"File not found: {file_path}", is_error=True, error_type="runtime_error")

        try:
            pr = cProfile.Profile()
            pr.enable()

            if p.function_name:
                import importlib.util
                spec = importlib.util.spec_from_file_location("profile_module", str(file_path))
                if spec is None:
                    return ToolResult(content=f"Failed to load module: {file_path}", is_error=True, error_type="runtime_error")
                module = importlib.util.module_from_spec(spec)
                if spec.loader is None:
                    return ToolResult(content=f"Module has no loader: {file_path}", is_error=True, error_type="runtime_error")
                spec.loader.exec_module(module)
                func = getattr(module, p.function_name, None)
                if func:
                    func()
                else:
                    return ToolResult(content=f"Function '{p.function_name}' not found", is_error=True, error_type="runtime_error")
            else:
                exec(file_path.read_text(encoding="utf-8"))

            pr.disable()

            s = StringIO()
            ps = pstats.Stats(pr, stream=s).sort_stats(pstats.SortKey.TIME)
            ps.print_stats(20)

            output = s.getvalue()
            if len(output) > _MAX_OUTPUT_BYTES:
                output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

            return ToolResult(content=output)
        except Exception as exc:
            return ToolResult(content=f"Error profiling code: {exc}", is_error=True, error_type="runtime_error")