from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT = 120


class PipManageParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: str = Field(description="Action: install, uninstall, freeze, list, upgrade")
    package: str | None = Field(default=None, description="Package name (for install/uninstall/upgrade)")


class PipManageTool(BaseTool):
    params_model = PipManageParams
    name = "pip_manage"
    description = (
        "Manage Python packages using pip. "
        "Supported actions: install, uninstall, freeze, list, upgrade."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action to perform: install, uninstall, freeze, list, upgrade",
            },
            "package": {
                "type": "string",
                "description": "Optional: package name (required for install, uninstall, upgrade)",
            },
        },
        "required": ["action"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = PipManageParams.model_validate(params)

        action = p.action.lower()

        if action in ["install", "uninstall", "upgrade"] and not p.package:
            return ToolResult(content="Package name is required for install, uninstall, and upgrade", is_error=True, error_type="schema_error")

        try:
            if action == "install":
                proc = await asyncio.create_subprocess_exec(
                    "pip", "install", p.package or "",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif action == "uninstall":
                proc = await asyncio.create_subprocess_exec(
                    "pip", "uninstall", "-y", p.package or "",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif action == "freeze":
                proc = await asyncio.create_subprocess_exec(
                    "pip", "freeze",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif action == "list":
                proc = await asyncio.create_subprocess_exec(
                    "pip", "list",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif action == "upgrade":
                proc = await asyncio.create_subprocess_exec(
                    "pip", "install", "--upgrade", p.package or "",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                return ToolResult(content=f"Unknown action: {action}", is_error=True, error_type="schema_error")

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)

            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\nSTDERR:\n" + stderr.decode("utf-8", errors="replace")

            if len(output) > _MAX_OUTPUT_BYTES:
                output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

            return ToolResult(content=output)
        except FileNotFoundError:
            return ToolResult(content="pip not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True, error_type="runtime_error")


class DependencyCheckParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    requirements_file: str | None = Field(default=None, description="Path to requirements.txt")


class DependencyCheckTool(BaseTool):
    params_model = DependencyCheckParams
    name = "dependency_check"
    description = (
        "Check for outdated dependencies. "
        "Compares installed packages with latest versions on PyPI."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "requirements_file": {
                "type": "string",
                "description": "Optional: path to requirements.txt (default: requirements.txt)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = DependencyCheckParams.model_validate(params)

        req_file = p.requirements_file or "requirements.txt"
        req_path = Path(req_file).resolve()

        if not req_path.exists():
            return ToolResult(content=f"Requirements file not found: {req_path}", is_error=True, error_type="runtime_error")

        try:
            proc = await asyncio.create_subprocess_exec(
                "pip", "list", "--outdated",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)

            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\nSTDERR:\n" + stderr.decode("utf-8", errors="replace")

            if len(output) > _MAX_OUTPUT_BYTES:
                output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

            return ToolResult(content=output)
        except FileNotFoundError:
            return ToolResult(content="pip not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True, error_type="runtime_error")