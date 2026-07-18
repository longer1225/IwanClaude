from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT = 60

IS_WINDOWS = sys.platform == "win32"


class GitStatusParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str = Field(default=".", description="Path to the git repository")


class GitStatusTool(BaseTool):
    params_model = GitStatusParams
    name = "git_status"
    description = (
        "Show the working tree status of a git repository. "
        "Returns staged/unstaged changes, untracked files, and branch information."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = GitStatusParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "status",
                "--porcelain",
                "-b",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            if "not a git repository" in err_msg:
                return ToolResult(content=f"Not a git repository: {repo_path}", is_error=True, error_type="runtime_error")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        output = stdout.decode("utf-8", errors="replace")
        if not output.strip():
            return ToolResult(content="No changes in the working tree (clean)")
        return ToolResult(content=output)


class GitLogParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str = Field(default=".", description="Path to the git repository")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of commits to show")


class GitLogTool(BaseTool):
    params_model = GitLogParams
    name = "git_log"
    description = (
        "Show recent git commit history. "
        "Returns commit hash, author, date, and message for each commit."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of commits to show (default: 10, max: 50)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = GitLogParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "log",
                "--oneline",
                f"-n{p.limit}",
                "--format=%h %ad %s (%an)",
                "--date=short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        output = stdout.decode("utf-8", errors="replace")
        if not output.strip():
            return ToolResult(content="No commits found")
        return ToolResult(content=output)


class GitDiffParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str = Field(default=".", description="Path to the git repository")
    staged: bool = Field(default=False, description="Show staged changes instead of working tree")
    file: str | None = Field(default=None, description="Show diff for a specific file")


class GitDiffTool(BaseTool):
    params_model = GitDiffParams
    name = "git_diff"
    description = (
        "Show changes between commits, commit and working tree, or staged changes. "
        "Output is truncated at 64 KB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
            "staged": {
                "type": "boolean",
                "description": "Show staged changes (default: false)",
            },
            "file": {
                "type": "string",
                "description": "Optional: show diff for a specific file",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = GitDiffParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        args = ["-C", str(repo_path), "diff"]
        if p.staged:
            args.append("--cached")
        if p.file:
            args.append(p.file)

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        output = stdout.decode("utf-8", errors="replace")
        truncated = len(stdout) > _MAX_OUTPUT_BYTES
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"
        if not output.strip():
            return ToolResult(content="No changes to show")
        return ToolResult(content=output)


class GitCommitParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str = Field(default=".", description="Path to the git repository")
    message: str = Field(description="Commit message")
    all: bool = Field(default=True, description="Stage all modified and deleted files")


class GitCommitTool(BaseTool):
    params_model = GitCommitParams
    name = "git_commit"
    description = (
        "Create a new git commit with staged changes. "
        "By default stages all modified/deleted files before committing."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
            "message": {
                "type": "string",
                "description": "Commit message",
            },
            "all": {
                "type": "boolean",
                "description": "Stage all modified and deleted files (default: true)",
            },
        },
        "required": ["message"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = GitCommitParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        if p.all:
            try:
                add_proc = await asyncio.create_subprocess_exec(
                    "git",
                    "-C",
                    str(repo_path),
                    "add",
                    "-A",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(add_proc.communicate(), timeout=_DEFAULT_TIMEOUT)
                if add_proc.returncode != 0:
                    return ToolResult(
                        content=f"Failed to stage files: exit code {add_proc.returncode}",
                        is_error=True,
                        error_type="runtime_error",
                    )
            except Exception as exc:
                return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "commit",
                "-m",
                p.message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            if "nothing to commit" in err_msg:
                return ToolResult(content="Nothing to commit (working tree clean)", is_error=True, error_type="runtime_error")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        output = stdout.decode("utf-8", errors="replace")
        return ToolResult(content=output)


class GitCheckoutParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str = Field(default=".", description="Path to the git repository")
    target: str = Field(description="Branch name or commit hash to checkout")


class GitCheckoutTool(BaseTool):
    params_model = GitCheckoutParams
    name = "git_checkout"
    description = (
        "Switch branches or restore working tree files. "
        "Can checkout a branch name or commit hash."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
            "target": {
                "type": "string",
                "description": "Branch name or commit hash to checkout",
            },
        },
        "required": ["target"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = GitCheckoutParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "checkout",
                p.target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        output = stdout.decode("utf-8", errors="replace")
        return ToolResult(content=output or f"Checked out to: {p.target}")