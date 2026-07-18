from __future__ import annotations

import asyncio
import sys

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT = 30

IS_WINDOWS = sys.platform == "win32"


class ProcessListParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    filter: str | None = Field(default=None, description="Filter processes by name")
    limit: int = Field(default=50, ge=1, le=200, description="Maximum number of processes to show")


class ProcessListTool(BaseTool):
    params_model = ProcessListParams
    name = "process_list"
    description = (
        "List running processes on the system. "
        "Returns process ID, name, and memory usage. "
        "Can filter by process name and limit results."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "Optional: filter processes by name (case-insensitive)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of processes to show (default: 50, max: 200)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = ProcessListParams.model_validate(params)

        try:
            if IS_WINDOWS:
                command = (
                    f"Get-Process | Select-Object Id,Name,WorkingSet64,StartTime | "
                    f"Sort-Object WorkingSet64 -Descending | Select-Object -First {p.limit} | "
                    f"Format-Table -AutoSize"
                )
                ps_command = _quote_powershell(command)
                proc = await asyncio.create_subprocess_exec(
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    ps_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            else:
                command = f"ps aux --sort=-%mem | head -{p.limit}"
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        output = stdout_bytes.decode("utf-8", errors="replace")
        truncated = len(stdout_bytes) > _MAX_OUTPUT_BYTES
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        if p.filter:
            lines = output.splitlines()
            filtered = [lines[0]] if lines else []
            for line in lines[1:]:
                if p.filter.lower() in line.lower():
                    filtered.append(line)
            output = "\n".join(filtered)

        if not output.strip():
            return ToolResult(content="No processes found")
        return ToolResult(content=output)


def _quote_powershell(command: str) -> str:
    escaped = command.replace("'", "''")
    return f"& {{{escaped}}}"