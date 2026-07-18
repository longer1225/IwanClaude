from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_BYTES = 64 * 1024


@dataclass
class AgentRole:
    name: str
    description: str
    tools: list[str]
    system_prompt: str


_ROLES = {
    "analyst": AgentRole(
        name="analyst",
        description="代码分析专家，负责理解代码结构、识别问题",
        tools=["read_file", "search_knowledge", "review_code", "lint_code"],
        system_prompt="你是一位资深代码分析师。专注于理解代码结构、识别潜在问题、提供改进建议。",
    ),
    "developer": AgentRole(
        name="developer",
        description="开发工程师，负责编写和修改代码",
        tools=["edit_by_lines", "edit_by_search", "write_file", "run_python"],
        system_prompt="你是一位高效的开发工程师。专注于编写高质量代码、实现功能需求、修复bug。",
    ),
    "tester": AgentRole(
        name="tester",
        description="测试工程师，负责编写和运行测试",
        tools=["generate_tests", "run_tests", "test_coverage"],
        system_prompt="你是一位专业测试工程师。专注于编写全面的测试用例、确保代码质量。",
    ),
    "reviewer": AgentRole(
        name="reviewer",
        description="代码审查专家，负责审查代码质量",
        tools=["git_diff", "review_code", "security_scan"],
        system_prompt="你是一位代码审查专家。专注于审查代码质量、安全性、性能和可维护性。",
    ),
    "architect": AgentRole(
        name="architect",
        description="架构师，负责系统设计和规划",
        tools=["list_dir", "find_files", "search_knowledge", "generate_docs"],
        system_prompt="你是一位资深架构师。专注于系统设计、技术选型、架构规划。",
    ),
}


class AssignRoleParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str = Field(description="Agent role: analyst, developer, tester, reviewer, architect")


class AssignRoleTool(BaseTool):
    params_model = AssignRoleParams
    name = "assign_role"
    description = (
        "Assign a role to the current agent. "
        "Roles determine the system prompt and allowed tools. "
        "Available roles: analyst, developer, tester, reviewer, architect."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "role": {
                "type": "string",
                "description": "Agent role to assign",
            },
        },
        "required": ["role"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = AssignRoleParams.model_validate(params)

        role = _ROLES.get(p.role.lower())
        if role is None:
            return ToolResult(
                content=f"Unknown role: {p.role}. Available roles: {', '.join(_ROLES.keys())}",
                is_error=True,
                error_type="schema_error",
            )

        result = f"Role assigned: {role.name}\n"
        result += f"Description: {role.description}\n"
        result += f"Tools: {', '.join(role.tools)}\n"
        result += "\nNote: The role will take effect in the next session."

        return ToolResult(content=result)


class ListRolesTool(BaseTool):
    name = "list_roles"
    description = "List all available agent roles with their descriptions and tools."
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        lines = []
        lines.append("Available Roles")
        lines.append("=" * 60)

        for name, role in _ROLES.items():
            lines.append(f"\n## {name}")
            lines.append(f"**Description:** {role.description}")
            lines.append(f"**Tools:** {', '.join(role.tools)}")

        return ToolResult(content="\n".join(lines))


class ShareKnowledgeParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    target_agent: str | None = Field(default=None, description="Target agent name or ID")
    content: str = Field(description="Knowledge content to share")


class ShareKnowledgeTool(BaseTool):
    params_model = ShareKnowledgeParams
    name = "share_knowledge"
    description = (
        "Share knowledge with another agent. "
        "If target_agent is not specified, broadcasts to all agents."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "target_agent": {
                "type": "string",
                "description": "Optional: target agent name or ID",
            },
            "content": {
                "type": "string",
                "description": "Knowledge content to share",
            },
        },
        "required": ["content"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = ShareKnowledgeParams.model_validate(params)

        if p.target_agent:
            return ToolResult(content=f"Knowledge shared with agent '{p.target_agent}':\n\n{p.content}")
        else:
            return ToolResult(content=f"Knowledge broadcast to all agents:\n\n{p.content}")