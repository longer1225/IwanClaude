from __future__ import annotations

import asyncio
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT = 120


class ReviewCodeParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_path: str = Field(description="Path to the file to review")
    focus: str | None = Field(default=None, description="Focus area: security, performance, readability, or maintainability")


class ReviewCodeTool(BaseTool):
    params_model = ReviewCodeParams
    name = "review_code"
    description = (
        "Review code quality and provide suggestions for improvement. "
        "Analyzes code for issues in security, performance, readability, and maintainability. "
        "Can focus on specific areas when specified."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to review",
            },
            "focus": {
                "type": "string",
                "description": "Optional focus area: security, performance, readability, or maintainability",
            },
        },
        "required": ["file_path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = ReviewCodeParams.model_validate(params)

        file_path = Path(p.file_path).resolve()
        if not file_path.exists():
            return ToolResult(content=f"File not found: {file_path}", is_error=True, error_type="runtime_error")

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Failed to read file: {exc}", is_error=True, error_type="runtime_error")

        issues = []
        lines = content.splitlines()

        for i, line in enumerate(lines, start=1):
            if p.focus in (None, "security"):
                security_issues = self._check_security(line, i)
                issues.extend(security_issues)

            if p.focus in (None, "performance"):
                perf_issues = self._check_performance(line, i)
                issues.extend(perf_issues)

            if p.focus in (None, "readability"):
                read_issues = self._check_readability(line, i)
                issues.extend(read_issues)

            if p.focus in (None, "maintainability"):
                maint_issues = self._check_maintainability(line, i)
                issues.extend(maint_issues)

        if not issues:
            return ToolResult(content=f"No issues found in {file_path}.")

        lines = []
        lines.append(f"Code Review for: {file_path}")
        lines.append("=" * 60)
        for issue in sorted(issues, key=lambda x: x[0]):
            line_num, severity, message = issue
            lines.append(f"[{severity}] Line {line_num}: {message}")
        lines.append("")
        lines.append(f"Total issues found: {len(issues)}")

        return ToolResult(content="\n".join(lines))

    def _check_security(self, line: str, line_num: int) -> list[tuple[int, str, str]]:
        issues = []
        if re.search(r"(password|secret|token)\s*=\s*[\"'].*[\"']", line, re.IGNORECASE):
            issues.append((line_num, "HIGH", "Hardcoded secret detected"))
        if re.search(r"subprocess\.run\([^)]*shell=True", line):
            issues.append((line_num, "HIGH", "Potential shell injection vulnerability"))
        if re.search(r"eval\(", line):
            issues.append((line_num, "HIGH", "Use of eval() is insecure"))
        if re.search(r"pickle\.load\(", line):
            issues.append((line_num, "HIGH", "Deserialization vulnerability"))
        return issues

    def _check_performance(self, line: str, line_num: int) -> list[tuple[int, str, str]]:
        issues = []
        if re.search(r"\.append\(", line) and "for" in line:
            issues.append((line_num, "MEDIUM", "Consider using list comprehension"))
        if re.search(r"for.*in.*range\(", line):
            issues.append((line_num, "LOW", "Consider enumerate() instead of range(len())"))
        return issues

    def _check_readability(self, line: str, line_num: int) -> list[tuple[int, str, str]]:
        issues = []
        if len(line) > 120:
            issues.append((line_num, "LOW", f"Line too long ({len(line)} chars)"))
        if re.search(r"[a-z][A-Z][a-z]", line):
            issues.append((line_num, "LOW", "Mixed case detected, consider consistent naming"))
        return issues

    def _check_maintainability(self, line: str, line_num: int) -> list[tuple[int, str, str]]:
        issues = []
        if re.search(r"TODO|FIXME|XXX", line):
            issues.append((line_num, "MEDIUM", "Unresolved TODO comment"))
        if re.search(r"pass\s*$", line):
            issues.append((line_num, "LOW", "Empty block with pass"))
        return issues


class LintCodeParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_path: str | None = Field(default=None, description="Path to the file to lint")
    directory: str | None = Field(default=None, description="Directory to lint")


class LintCodeTool(BaseTool):
    params_model = LintCodeParams
    name = "lint_code"
    description = (
        "Run code linting using ruff and mypy. "
        "Can lint a single file or entire directory. "
        "Returns linting errors and warnings."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Optional: path to the file to lint",
            },
            "directory": {
                "type": "string",
                "description": "Optional: directory to lint (default: current directory)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = LintCodeParams.model_validate(params)

        target = p.file_path or p.directory or "."
        target_path = Path(target).resolve()

        if not target_path.exists():
            return ToolResult(content=f"Path not found: {target_path}", is_error=True, error_type="runtime_error")

        results = []

        results.append("=== Ruff Linting ===")
        try:
            proc = await asyncio.create_subprocess_exec(
                "ruff",
                "check",
                str(target_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
            output = stdout.decode("utf-8", errors="replace")
            if output.strip():
                results.append(output)
            else:
                results.append("No issues found.")
        except FileNotFoundError:
            results.append("ruff not installed, skipping.")
        except Exception as exc:
            results.append(f"Error: {exc}")

        results.append("")
        results.append("=== Mypy Type Checking ===")
        try:
            proc = await asyncio.create_subprocess_exec(
                "mypy",
                "--ignore-missing-imports",
                str(target_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
            output = stdout.decode("utf-8", errors="replace")
            if output.strip():
                results.append(output)
            else:
                results.append("No issues found.")
        except FileNotFoundError:
            results.append("mypy not installed, skipping.")
        except Exception as exc:
            results.append(f"Error: {exc}")

        output = "\n".join(results)
        if len(output) > _MAX_OUTPUT_BYTES:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        return ToolResult(content=output)


class SecurityScanParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_path: str | None = Field(default=None, description="Path to the file to scan")
    directory: str | None = Field(default=None, description="Directory to scan")


class SecurityScanTool(BaseTool):
    params_model = SecurityScanParams
    name = "security_scan"
    description = (
        "Scan code for security vulnerabilities. "
        "Detects SQL injection, XSS, hardcoded secrets, and other security issues."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Optional: path to the file to scan",
            },
            "directory": {
                "type": "string",
                "description": "Optional: directory to scan (default: current directory)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SecurityScanParams.model_validate(params)

        target = p.file_path or p.directory or "."
        target_path = Path(target).resolve()

        if not target_path.exists():
            return ToolResult(content=f"Path not found: {target_path}", is_error=True, error_type="runtime_error")

        if target_path.is_file():
            files = [target_path]
        else:
            files = list(target_path.rglob("*.py"))

        vulnerabilities = []

        for file in files:
            try:
                content = file.read_text(encoding="utf-8")
                lines = content.splitlines()
                for i, line in enumerate(lines, start=1):
                    vulns = self._scan_line(line, i, file.name)
                    for severity, message in vulns:
                        vulnerabilities.append((severity, str(file), i, message))
            except Exception:
                continue

        if not vulnerabilities:
            return ToolResult(content="No security vulnerabilities found.")

        lines = []
        lines.append("Security Scan Results")
        lines.append("=" * 60)
        lines.append(f"Scanned {len(files)} files")
        lines.append("")

        for severity, filepath, line_num, message in sorted(vulnerabilities, key=lambda x: x[0]):
            lines.append(f"[{severity}] {filepath}:{line_num} - {message}")

        lines.append("")
        lines.append(f"Total vulnerabilities: {len(vulnerabilities)}")

        return ToolResult(content="\n".join(lines))

    def _scan_line(self, line: str, line_num: int, filename: str) -> list[tuple[str, str]]:
        vulns = []
        if re.search(r"(password|secret|token|api_key)\s*=\s*[\"'].*[\"']", line, re.IGNORECASE):
            vulns.append(("CRITICAL", "Hardcoded secret detected"))
        if re.search(r"subprocess\.run\([^)]*shell=True", line):
            vulns.append(("CRITICAL", "Shell injection vulnerability"))
        if re.search(r"eval\(", line):
            vulns.append(("HIGH", "Use of eval() is insecure"))
        if re.search(r"pickle\.load\(", line) or re.search(r"pickle\.loads\(", line):
            vulns.append(("CRITICAL", "Deserialization vulnerability"))
        if re.search(r"exec\(", line):
            vulns.append(("HIGH", "Use of exec() is insecure"))
        if re.search(r"(sql|query)\s*=\s*f?[\"'].*\{.*\}.*[\"']", line, re.IGNORECASE):
            vulns.append(("HIGH", "Potential SQL injection"))
        if re.search(r"(input|raw_input)\(", line):
            vulns.append(("MEDIUM", "Use of input() may be unsafe"))
        if re.search(r"open\(.*[\"']w[\"']", line):
            vulns.append(("MEDIUM", "Potential path traversal"))
        return vulns