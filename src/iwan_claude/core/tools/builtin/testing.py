from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT = 180


class GenerateTestsParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_path: str = Field(description="Path to the file to generate tests for")
    output_path: str | None = Field(default=None, description="Path to save test file")


class GenerateTestsTool(BaseTool):
    params_model = GenerateTestsParams
    name = "generate_tests"
    description = (
        "Generate unit tests for a Python file. "
        "Analyzes the code and generates comprehensive test cases using pytest."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to generate tests for",
            },
            "output_path": {
                "type": "string",
                "description": "Optional: path to save the test file (defaults to tests/test_<filename>.py)",
            },
        },
        "required": ["file_path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = GenerateTestsParams.model_validate(params)

        file_path = Path(p.file_path).resolve()
        if not file_path.exists():
            return ToolResult(content=f"File not found: {file_path}", is_error=True, error_type="runtime_error")

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Failed to read file: {exc}", is_error=True, error_type="runtime_error")

        functions, classes = self._extract_functions_and_classes(content)

        if not functions and not classes:
            return ToolResult(content="No functions or classes found to test.")

        test_content = self._generate_test_code(file_path.stem, functions, classes)

        if p.output_path:
            output_path = Path(p.output_path).resolve()
        else:
            output_path = Path("tests") / f"test_{file_path.stem}.py"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(test_content, encoding="utf-8")

        return ToolResult(content=f"Tests generated at: {output_path}\n\n{test_content}")

    def _extract_functions_and_classes(self, content: str) -> tuple[list[str], list[str]]:
        functions = []
        classes = []
        import re

        func_pattern = r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
        class_pattern = r"class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*"

        for match in re.finditer(func_pattern, content):
            func_name = match.group(1)
            if func_name not in ["__init__", "__str__", "__repr__", "__eq__"]:
                functions.append(func_name)

        for match in re.finditer(class_pattern, content):
            class_name = match.group(1)
            classes.append(class_name)

        return functions, classes

    def _generate_test_code(self, module_name: str, functions: list[str], classes: list[str]) -> str:
        lines = []
        lines.append("import pytest")
        lines.append(f"from {module_name} import {', '.join(functions + classes)}")
        lines.append("")
        lines.append("")

        for func in functions:
            lines.append(f"def test_{func}():")
            lines.append(f"    \"\"\"Test {func} function\"\"\"")
            lines.append(f"    result = {func}()")
            lines.append(f"    assert result is not None")
            lines.append("")

        for cls in classes:
            lines.append(f"def test_{cls}_instantiation():")
            lines.append(f"    \"\"\"Test {cls} class instantiation\"\"\"")
            lines.append(f"    instance = {cls}()")
            lines.append(f"    assert isinstance(instance, {cls})")
            lines.append("")

        return "\n".join(lines)


class RunTestsParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    test_path: str | None = Field(default=None, description="Path to test file or directory")
    verbose: bool = Field(default=False, description="Verbose output")


class RunTestsTool(BaseTool):
    params_model = RunTestsParams
    name = "run_tests"
    description = (
        "Run pytest tests. "
        "Can run tests in a specific file or entire test directory."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "test_path": {
                "type": "string",
                "description": "Optional: path to test file or directory (default: tests/)",
            },
            "verbose": {
                "type": "boolean",
                "description": "Optional: verbose output (default: false)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = RunTestsParams.model_validate(params)

        test_path = p.test_path or "tests"
        target_path = Path(test_path).resolve()

        if not target_path.exists():
            return ToolResult(content=f"Path not found: {target_path}", is_error=True, error_type="runtime_error")

        try:
            args = ["pytest", str(target_path)]
            if p.verbose:
                args.append("-v")

            proc = await asyncio.create_subprocess_exec(
                *args,
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
            return ToolResult(content="pytest not installed, skipping.", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=f"Error running tests: {exc}", is_error=True, error_type="runtime_error")


class TestCoverageParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source_path: str | None = Field(default=None, description="Path to source code")


class TestCoverageTool(BaseTool):
    params_model = TestCoverageParams
    name = "test_coverage"
    description = (
        "Run tests with coverage analysis. "
        "Generates a coverage report showing which lines are covered by tests."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Optional: path to source code (default: src/)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = TestCoverageParams.model_validate(params)

        source_path = p.source_path or "src"
        target_path = Path(source_path).resolve()

        if not target_path.exists():
            return ToolResult(content=f"Path not found: {target_path}", is_error=True, error_type="runtime_error")

        try:
            proc = await asyncio.create_subprocess_exec(
                "coverage",
                "run",
                "-m",
                "pytest",
                "tests/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)

            proc2 = await asyncio.create_subprocess_exec(
                "coverage",
                "report",
                "-m",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=_DEFAULT_TIMEOUT)

            output = stdout2.decode("utf-8", errors="replace")
            if stderr2:
                output += "\nSTDERR:\n" + stderr2.decode("utf-8", errors="replace")

            if len(output) > _MAX_OUTPUT_BYTES:
                output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

            return ToolResult(content=output)
        except FileNotFoundError:
            return ToolResult(content="coverage not installed, skipping.", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=f"Error running coverage: {exc}", is_error=True, error_type="runtime_error")