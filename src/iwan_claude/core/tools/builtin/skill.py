from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.skills.loader import Skill, SkillLoader
from iwan_claude.core.tools.base import BaseTool, ToolResult


class SkillListParams(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SkillListTool(BaseTool):
    params_model = SkillListParams
    name = "skill_list"
    description = (
        "List all available skills. "
        "Returns skill names and descriptions from built-in, global, and project-local locations."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, skill_loader: SkillLoader) -> None:
        super().__init__()
        self._skill_loader = skill_loader

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        _ = SkillListParams.model_validate(params)
        skills = self._skill_loader.list_all_skills()

        if not skills:
            return ToolResult(content="No skills available.")

        lines: list[str] = []
        for skill in sorted(skills, key=lambda x: x.name):
            lines.append(f"=== {skill.name} ===")
            lines.append(f"Description: {skill.description}")
            if skill.allowed_tools:
                lines.append(f"Allowed Tools: {', '.join(skill.allowed_tools)}")
            lines.append("")

        return ToolResult(content="\n".join(lines))


class SkillInfoParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(description="Name of the skill to get information about")


class SkillInfoTool(BaseTool):
    params_model = SkillInfoParams
    name = "skill_info"
    description = (
        "Get detailed information about a specific skill. "
        "Returns name, description, allowed tools, and the system prompt template."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the skill to get information about",
            },
        },
        "required": ["name"],
    }

    def __init__(self, skill_loader: SkillLoader) -> None:
        super().__init__()
        self._skill_loader = skill_loader

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SkillInfoParams.model_validate(params)
        skill = self._skill_loader.resolve(p.name)

        if not skill:
            return ToolResult(content=f"Skill '{p.name}' not found.", is_error=True, error_type="runtime_error")

        lines: list[str] = []
        lines.append(f"=== {skill.name} ===")
        lines.append(f"Description: {skill.description}")
        if skill.allowed_tools:
            lines.append(f"Allowed Tools: {', '.join(skill.allowed_tools)}")
        lines.append("")
        lines.append("System Prompt Template:")
        lines.append("----------------------")
        lines.append(skill.system_prompt_template)

        return ToolResult(content="\n".join(lines))


class SkillCreateParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = Field(default=None, description="Name of the new skill (required if not using file_path)")
    description: str | None = Field(default=None, description="Description of the skill (required if not using file_path)")
    system_prompt: str | None = Field(default=None, description="System prompt template (use $ARGUMENTS placeholder, required if not using file_path)")
    allowed_tools: list[str] | None = Field(default=None, description="List of allowed tool names")
    file_path: str | None = Field(default=None, description="Path to a local SKILL.md file to import")


class SkillCreateTool(BaseTool):
    params_model = SkillCreateParams
    name = "skill_create"
    description = (
        "Create a new skill and save it to the project-local .iwan/skills directory. "
        "Supports two modes: (1) provide name, description, and system_prompt directly, "
        "or (2) use file_path to import from a local SKILL.md file. "
        "The skill will be available for use in the current project."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the new skill (required if not using file_path)",
            },
            "description": {
                "type": "string",
                "description": "Description of the skill (required if not using file_path)",
            },
            "system_prompt": {
                "type": "string",
                "description": "System prompt template (use $ARGUMENTS placeholder for user input, required if not using file_path)",
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of tool names allowed for this skill",
            },
            "file_path": {
                "type": "string",
                "description": "Optional: path to a local SKILL.md file to import as a skill",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SkillCreateParams.model_validate(params)

        skill_dir = Path(".iwan/skills")
        skill_dir.mkdir(parents=True, exist_ok=True)

        if p.file_path:
            source_file = Path(p.file_path).resolve()
            if not source_file.exists():
                return ToolResult(content=f"File not found: {source_file}", is_error=True, error_type="runtime_error")

            try:
                content = source_file.read_text(encoding="utf-8")
                import re

                frontmatter_re = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
                m = frontmatter_re.match(content)
                skill_name = p.name or source_file.stem

                if m:
                    front = m.group(1)
                    for line in front.splitlines():
                        if line.strip().startswith("name:"):
                            skill_name = line.strip()[len("name:"):].strip().strip('"').strip("'")
                            break

                dest_dir = skill_dir / skill_name
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / "SKILL.md"

                dest_path.write_text(content, encoding="utf-8")
                return ToolResult(content=f"Skill '{skill_name}' imported from {source_file} to {dest_path}.")
            except Exception as exc:
                return ToolResult(content=f"Failed to import skill: {exc}", is_error=True, error_type="runtime_error")

        if not p.name or not p.description or not p.system_prompt:
            return ToolResult(
                content="Missing required parameters: name, description, and system_prompt are required when file_path is not provided.",
                is_error=True,
                error_type="schema_error",
            )

        skill_file = skill_dir / f"{p.name}.md"
        if skill_file.exists():
            return ToolResult(content=f"Skill '{p.name}' already exists.", is_error=True, error_type="runtime_error")

        allowed_tools_lines = "\n".join(f"- {tool}" for tool in (p.allowed_tools or []))
        frontmatter = f"""---
name: {p.name}
description: {p.description}
allowed_tools:
{allowed_tools_lines}
---
""" if p.allowed_tools else f"""---
name: {p.name}
description: {p.description}
---
"""

        content = frontmatter + "\n" + p.system_prompt

        try:
            skill_file.write_text(content, encoding="utf-8")
            return ToolResult(content=f"Skill '{p.name}' created successfully at {skill_file}.")
        except Exception as exc:
            return ToolResult(content=f"Failed to create skill: {exc}", is_error=True, error_type="runtime_error")


class SkillDeleteParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(description="Name of the skill to delete")


class SkillDeleteTool(BaseTool):
    params_model = SkillDeleteParams
    name = "skill_delete"
    description = (
        "Delete a project-local skill. "
        "Only skills in .iwan/skills can be deleted (built-in and global skills are protected)."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the skill to delete",
            },
        },
        "required": ["name"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SkillDeleteParams.model_validate(params)

        skill_file = Path(f".iwan/skills/{p.name}.md")
        skill_dir = Path(f".iwan/skills/{p.name}/SKILL.md")

        if skill_file.exists():
            skill_file.unlink()
            return ToolResult(content=f"Skill '{p.name}' deleted successfully.")
        elif skill_dir.exists():
            import shutil

            shutil.rmtree(skill_file.parent)
            return ToolResult(content=f"Skill '{p.name}' deleted successfully.")
        else:
            return ToolResult(
                content=f"Skill '{p.name}' not found in project-local skills (.iwan/skills). "
                "Built-in and global skills cannot be deleted.",
                is_error=True,
                error_type="runtime_error",
            )


class SkillInstallParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(description="URL of the skill to install")
    global_install: bool = Field(default=False, description="Install globally (in ~/.iwan/skills) instead of project-local")


class SkillInstallTool(BaseTool):
    params_model = SkillInstallParams
    name = "skill_install"
    description = (
        "Install a skill from a URL. "
        "Supports: GitHub repositories (https://github.com/username/repo), "
        "ZIP files (https://example.com/skill.zip), "
        "and SKILL.md files (https://example.com/SKILL.md). "
        "By default installs to project-local .iwan/skills directory. "
        "Use global_install=true to install to ~/.iwan/skills."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL of the skill to install (GitHub repo, ZIP, or SKILL.md)",
            },
            "global_install": {
                "type": "boolean",
                "description": "Install globally instead of project-local (default: false)",
            },
        },
        "required": ["url"],
    }

    def __init__(self, skill_loader: SkillLoader) -> None:
        super().__init__()
        self._skill_loader = skill_loader

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SkillInstallParams.model_validate(params)

        result, success = await self._skill_loader.install_from_url(p.url, p.global_install)

        if success:
            return ToolResult(content=f"{result}\n\nNote: The skill will be available in the next session.")
        else:
            return ToolResult(content=result, is_error=True, error_type="runtime_error")