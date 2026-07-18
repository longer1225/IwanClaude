from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_BYTES = 64 * 1024


class GenerateDocsParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source_path: str | None = Field(default=None, description="Path to source code")
    output_path: str | None = Field(default=None, description="Path to save documentation")


class GenerateDocsTool(BaseTool):
    params_model = GenerateDocsParams
    name = "generate_docs"
    description = (
        "Generate API documentation from Python code. "
        "Extracts docstrings and generates markdown documentation."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Optional: path to source code (default: src/)",
            },
            "output_path": {
                "type": "string",
                "description": "Optional: path to save documentation (default: docs/)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = GenerateDocsParams.model_validate(params)

        source_path = p.source_path or "src"
        output_path = p.output_path or "docs"

        source = Path(source_path).resolve()
        output = Path(output_path).resolve()

        if not source.exists():
            return ToolResult(content=f"Source path not found: {source}", is_error=True, error_type="runtime_error")

        output.mkdir(parents=True, exist_ok=True)

        doc_content = self._generate_docs(source)
        index_file = output / "index.md"
        index_file.write_text(doc_content, encoding="utf-8")

        return ToolResult(content=f"Documentation generated at: {index_file}\n\n{doc_content[:2000]}...")

    def _generate_docs(self, source_path: Path) -> str:
        lines = []
        lines.append("# API Documentation")
        lines.append("")

        py_files = sorted(source_path.rglob("*.py"))
        for py_file in py_files:
            rel_path = py_file.relative_to(source_path)
            module_name = str(rel_path).replace("/", ".").replace(".py", "")

            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            lines.append(f"## {module_name}")
            lines.append(f"*File: `{rel_path}`*")
            lines.append("")

            import re
            func_pattern = r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
            class_pattern = r"class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*"

            classes = re.findall(class_pattern, content)
            functions = re.findall(func_pattern, content)

            for cls in classes:
                lines.append(f"### Class: `{cls}`")
                lines.append("")

            for func in functions:
                if func not in ["__init__", "__str__", "__repr__", "__eq__"]:
                    lines.append(f"### Function: `{func}()`")
                    lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)


class UpdateReadmeParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    section: str | None = Field(default=None, description="Section to update")
    content: str | None = Field(default=None, description="New content")


class UpdateReadmeTool(BaseTool):
    params_model = UpdateReadmeParams
    name = "update_readme"
    description = (
        "Update the project README.md file. "
        "Can update specific sections or add new content."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "Optional: section title to update",
            },
            "content": {
                "type": "string",
                "description": "Optional: new content to add",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = UpdateReadmeParams.model_validate(params)

        readme_path = Path("README.md")
        if not readme_path.exists():
            readme_path.write_text("# Project README\n", encoding="utf-8")

        try:
            content = readme_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Failed to read README: {exc}", is_error=True, error_type="runtime_error")

        if p.section and p.content:
            import re
            pattern = rf"## {re.escape(p.section)}\s*\n.*?(?=\n## |\Z)"
            if re.search(pattern, content, re.DOTALL):
                content = re.sub(pattern, f"## {p.section}\n\n{p.content}", content, flags=re.DOTALL)
            else:
                content += f"\n## {p.section}\n\n{p.content}\n"
        elif p.content:
            content += f"\n{p.content}\n"

        readme_path.write_text(content, encoding="utf-8")
        return ToolResult(content="README.md updated successfully.")


class ChangelogParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(description="Changelog message")
    type: str = Field(default="feature", description="Change type: feature, bugfix, improvement, documentation")


class ChangelogTool(BaseTool):
    params_model = ChangelogParams
    name = "changelog"
    description = (
        "Add an entry to CHANGELOG.md. "
        "Supports different change types: feature, bugfix, improvement, documentation."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Changelog entry message",
            },
            "type": {
                "type": "string",
                "description": "Change type: feature, bugfix, improvement, documentation",
            },
        },
        "required": ["message"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = ChangelogParams.model_validate(params)

        changelog_path = Path("CHANGELOG.md")
        if not changelog_path.exists():
            changelog_path.write_text("# Changelog\n\n", encoding="utf-8")

        try:
            content = changelog_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Failed to read changelog: {exc}", is_error=True, error_type="runtime_error")

        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")

        type_emoji = {
            "feature": "✨",
            "bugfix": "🐛",
            "improvement": "🔧",
            "documentation": "📝",
        }

        entry = f"- [{date}] {type_emoji.get(p.type, '')} {p.message}\n"

        if content.startswith("# Changelog"):
            lines = content.split("\n")
            lines.insert(2, entry)
            content = "\n".join(lines)
        else:
            content = entry + content

        changelog_path.write_text(content, encoding="utf-8")
        return ToolResult(content="CHANGELOG.md updated successfully.")