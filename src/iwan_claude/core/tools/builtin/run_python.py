from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import venv
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.sandbox import validate_path
from iwan_claude.core.tools.base import BaseTool, ToolResult


_MAX_OUTPUT_BYTES = 128 * 1024  # 128 KB
_DEFAULT_TIMEOUT = 60
_IS_WINDOWS = sys.platform == "win32"

_FILE_OP_FUNCTIONS = {
    "open", "open_file", "file",
    "__import__('os').open", "__import__('pathlib').Path",
}

_FILE_OP_PATTERNS = [
    re.compile(r'\bopen\s*\(\s*["\']([^"\']+)["\']'),
    re.compile(r'\bPath\s*\(\s*["\']([^"\']+)["\']'),
    re.compile(r'\bos\.path\.\w+\s*\(\s*["\']([^"\']+)["\']'),
    re.compile(r'\bos\.(?:mkdir|makedirs|rmdir|remove|unlink|rename)\s*\(\s*["\']([^"\']+)["\']'),
]


class RunPythonParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str
    args: list[str] = []
    stdin: str = ""
    requirements: list[str] = []
    timeout: int = Field(default=_DEFAULT_TIMEOUT, ge=1, le=300)
    use_venv: bool = True
    venv_dir: str | None = None
    work_dir: str | None = None
    capture_stderr: bool = True


class RunPythonTool(BaseTool):
    params_model = RunPythonParams
    name = "run_python"
    description = (
        "Execute a Python code snippet in an isolated subprocess. Optional pip "
        "requirements are installed into a fresh or reused venv (default on). "
        "Use work_dir to set CWD (relative to project CWD). Supports stdin, argv, "
        "and configurable timeout. Captured stdout (and optionally stderr) is "
        "truncated at 128 KB. Path traversal is forbidden."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source code to execute."},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "String arguments passed as sys.argv[1:].",
            },
            "stdin": {
                "type": "string",
                "default": "",
                "description": "Text piped to the script on stdin.",
            },
            "requirements": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Pip requirement specifiers to install before running.",
            },
            "timeout": {
                "type": "integer",
                "default": _DEFAULT_TIMEOUT,
                "description": "Seconds before the process is killed (max 300).",
            },
            "use_venv": {
                "type": "boolean",
                "default": True,
                "description": "If true, run inside a venv (recommended for isolation).",
            },
            "venv_dir": {
                "type": "string",
                "description": (
                    "Optional relative path of a reusable venv. If omitted a temp "
                    "directory is used and deleted after execution."
                ),
            },
            "work_dir": {
                "type": "string",
                "description": "Optional relative working directory for the process.",
            },
            "capture_stderr": {
                "type": "boolean",
                "default": True,
                "description": "Merge stderr into the captured output.",
            },
        },
        "required": ["code"],
    }

    metadata: ClassVar[dict[str, str]] = {"category": "execute"}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = RunPythonParams.model_validate(params)

        for pattern in _FILE_OP_PATTERNS:
            for match in pattern.finditer(p.code):
                file_path = match.group(1)
                try:
                    validate_path(file_path, "execute")
                except PermissionError:
                    return ToolResult(
                        content=f"sandbox access denied: Python code attempts to access file outside sandbox: '{file_path}'",
                        is_error=True,
                        error_type="permission_denied",
                    )

        work_path: Path | None = None
        if p.work_dir is not None:
            if ".." in Path(p.work_dir).parts:
                return ToolResult(
                    content=f"work_dir path traversal not allowed: {p.work_dir}",
                    is_error=True,
                    error_type="permission_denied",
                )
            try:
                validate_path(p.work_dir, "execute")
            except PermissionError:
                return ToolResult(
                    content=f"sandbox access denied: work_dir '{p.work_dir}' is outside sandbox",
                    is_error=True,
                    error_type="permission_denied",
                )
            work_path = Path(p.work_dir)
            if not work_path.exists():
                return ToolResult(
                    content=f"work_dir does not exist: {p.work_dir}",
                    is_error=True,
                    error_type="runtime_error",
                )

        venv_path: Path | None = None
        owns_temp_venv = False
        temp_root: tempfile.TemporaryDirectory[str] | None = None
        try:
            if p.use_venv:
                if p.venv_dir is not None:
                    if ".." in Path(p.venv_dir).parts:
                        return ToolResult(
                            content=f"venv_dir path traversal not allowed: {p.venv_dir}",
                            is_error=True,
                            error_type="permission_denied",
                        )
                    venv_path = Path(p.venv_dir)
                    if not venv_path.exists():
                        try:
                            venv.create(str(venv_path), with_pip=True, clear=False)
                        except Exception as exc:
                            return ToolResult(
                                content=f"failed to create venv at {p.venv_dir}: {exc}",
                                is_error=True,
                                error_type="runtime_error",
                            )
                else:
                    temp_root = tempfile.TemporaryDirectory(prefix="iwan_py_")
                    venv_path = Path(temp_root.name)
                    owns_temp_venv = True
                    try:
                        venv.create(str(venv_path), with_pip=True, clear=False)
                    except Exception as exc:
                        return ToolResult(
                            content=f"failed to create temp venv: {exc}",
                            is_error=True,
                            error_type="runtime_error",
                        )

            interpreter = _resolve_python(venv_path)

            if p.requirements:
                install_ok = await _pip_install(
                    interpreter, list(p.requirements), timeout=max(300, p.timeout)
                )
                if not install_ok.success:
                    return ToolResult(
                        content=(
                            f"pip install failed for {p.requirements}\n"
                            f"---\n{install_ok.output}"
                        ),
                        is_error=True,
                        error_type="runtime_error",
                    )

            script_path: Path
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as fh:
                fh.write(p.code)
                script_path = Path(fh.name)
            try:
                proc_cwd = str(work_path.resolve()) if work_path is not None else None
                argv = [interpreter, str(script_path), *list(p.args)]
                stderr_target = (
                    asyncio.subprocess.STDOUT
                    if p.capture_stderr
                    else asyncio.subprocess.DEVNULL
                )
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *argv,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=stderr_target,
                        stdin=asyncio.subprocess.PIPE,
                        cwd=proc_cwd,
                        env=_child_env(os.environ.copy(), venv_path),
                    )
                except Exception as exc:
                    return ToolResult(
                        content=f"failed to spawn python subprocess: {exc}",
                        is_error=True,
                        error_type="runtime_error",
                    )

                try:
                    stdout_bytes, _ = await asyncio.wait_for(
                        proc.communicate(
                            p.stdin.encode("utf-8") if p.stdin else None
                        ),
                        timeout=p.timeout,
                    )
                except TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    return ToolResult(
                        content=f"[timeout after {p.timeout}s]",
                        is_error=True,
                        error_type="timeout",
                    )
            finally:
                try:
                    script_path.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            if owns_temp_venv and temp_root is not None:
                try:
                    temp_root.cleanup()
                except Exception:
                    pass

        output = (stdout_bytes or b"").decode("utf-8", errors="replace")
        truncated = len(stdout_bytes or b"") > _MAX_OUTPUT_BYTES
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        returncode = proc.returncode or 0
        if returncode != 0:
            return ToolResult(
                content=f"[exit {returncode}]\n{output}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=output or "[no output]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_python(venv_path: Path | None) -> str:
    if venv_path is None:
        return sys.executable
    if _IS_WINDOWS:
        candidate = venv_path / "Scripts" / "python.exe"
    else:
        candidate = venv_path / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _child_env(env: dict[str, str], venv_path: Path | None) -> dict[str, str]:
    if venv_path is None:
        return env
    env["VIRTUAL_ENV"] = str(venv_path.resolve())
    if _IS_WINDOWS:
        scripts = str(venv_path.resolve() / "Scripts")
        env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    else:
        bin_dir = str(venv_path.resolve() / "bin")
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONHOME", None)
    return env


async def _pip_install(
    interpreter: str, requirements: list[str], timeout: int
) -> _InstallResult:
    if not requirements:
        return _InstallResult(success=True, output="")
    try:
        proc = await asyncio.create_subprocess_exec(
            interpreter,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            *requirements,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:
        return _InstallResult(success=False, output=str(exc))
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return _InstallResult(success=False, output="[pip install timed out]")
    out = (stdout_bytes or b"").decode("utf-8", errors="replace")
    if (proc.returncode or 0) != 0:
        return _InstallResult(success=False, output=out)
    return _InstallResult(success=True, output=out)


class _InstallResult:
    def __init__(self, success: bool, output: str) -> None:
        self.success = success
        self.output = output
