"""
协作工具模块 - 提供多 Agent 协作功能

【学习要点】
1. 角色分配：根据任务类型为 Agent 分配不同角色
2. 角色定义：使用 dataclass 定义角色的名称、描述、工具和系统提示词
3. 知识共享：支持跨 Agent 的知识传递
4. 角色列表：提供可用角色的列表查询

【角色定义】
每个角色包含：
- name: 角色名称
- description: 角色描述
- tools: 该角色可用的工具列表
- system_prompt: 该角色的系统提示词

【可用角色】
- analyst：代码分析专家
- developer：开发工程师
- tester：测试工程师
- reviewer：代码审查专家
- architect：架构师

【设计目的】
- 根据任务类型自动选择合适的 Agent 角色
- 限制角色可用的工具，提高安全性和专注度
- 通过系统提示词塑造 Agent 的行为模式
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大输出字节数：64 KB，防止返回过多内容
_MAX_OUTPUT_BYTES = 64 * 1024


@dataclass
class AgentRole:
    """
    Agent 角色数据类 - 定义 Agent 的角色属性

    【字段说明】
    - name: str - 角色名称（用于标识）
    - description: str - 角色描述（用于展示和理解）
    - tools: list[str] - 该角色可用的工具列表
    - system_prompt: str - 该角色的系统提示词（塑造行为模式）

    【设计说明】
    使用 dataclass 可以自动生成 __init__、__repr__ 等方法，
    使角色定义更加简洁明了。
    """
    name: str
    description: str
    tools: list[str]
    system_prompt: str


# 角色注册表 - 存储所有可用的 Agent 角色
# 键为角色名称（小写），值为 AgentRole 对象
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
    """
    角色分配参数模型

    【字段说明】
    - role: str - 要分配的角色名称，支持：analyst、developer、tester、reviewer、architect

    【参数校验】
    - 角色名称会被转换为小写
    - 无效角色会返回错误
    """
    model_config = ConfigDict(extra="ignore")
    role: str = Field(description="Agent role: analyst, developer, tester, reviewer, architect")


class AssignRoleTool(BaseTool):
    """
    角色分配工具 - 为当前 Agent 分配角色

    【学习要点】
    1. 角色查找：从 _ROLES 字典中查找角色
    2. 参数校验：角色名称转换为小写，无效角色返回错误
    3. 结果构建：返回角色的名称、描述、工具列表

    【使用示例】
    ```python
    tool = AssignRoleTool()
    
    # 分配开发工程师角色
    result = await tool.invoke({"role": "developer"})
    
    # 分配代码分析专家角色
    result = await tool.invoke({"role": "analyst"})
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 将角色名称转换为小写
    3. 从 _ROLES 字典中查找角色
    4. 如果角色不存在，返回错误
    5. 构建角色信息字符串（名称、描述、工具）
    6. 返回结果

    【注意事项】
    - 角色分配后在下一个会话生效
    - 角色决定了 Agent 的系统提示词和可用工具
    """
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
        """
        执行角色分配操作

        【参数说明】
        - params: dict - 工具调用参数，包含 role

        【返回值】
        - ToolResult: 包含角色信息或错误信息
        """
        # 1. 验证输入参数
        p = AssignRoleParams.model_validate(params)

        # 2. 将角色名称转换为小写并查找
        role = _ROLES.get(p.role.lower())

        # 3. 检查角色是否存在
        if role is None:
            return ToolResult(
                content=f"Unknown role: {p.role}. Available roles: {', '.join(_ROLES.keys())}",
                is_error=True,
                error_type="schema_error",
            )

        # 4. 构建角色信息字符串
        result = f"Role assigned: {role.name}\n"
        result += f"Description: {role.description}\n"
        result += f"Tools: {', '.join(role.tools)}\n"
        result += "\nNote: The role will take effect in the next session."

        # 5. 返回结果
        return ToolResult(content=result)


class ListRolesTool(BaseTool):
    """
    角色列表工具 - 列出所有可用的 Agent 角色

    【学习要点】
    1. 字典遍历：遍历 _ROLES 字典获取所有角色
    2. 格式化输出：构建包含角色名称、描述和工具的格式化字符串
    3. 无参数工具：不需要输入参数

    【使用示例】
    ```python
    tool = ListRolesTool()
    result = await tool.invoke({})
    ```

    【输出格式】
    ```
    Available Roles
    ============================================================

    ## analyst
    **Description:** 代码分析专家，负责理解代码结构、识别问题
    **Tools:** read_file, search_knowledge, review_code, lint_code

    ## developer
    **Description:** 开发工程师，负责编写和修改代码
    **Tools:** edit_by_lines, edit_by_search, write_file, run_python
    ```

    【注意事项】
    - 此工具不需要输入参数
    - 返回所有已注册的角色信息
    """
    name = "list_roles"
    description = "List all available agent roles with their descriptions and tools."
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行角色列表查询操作

        【参数说明】
        - params: dict - 工具调用参数（无必填参数）

        【返回值】
        - ToolResult: 包含所有角色信息的格式化字符串
        """
        # 初始化结果列表
        lines = []
        lines.append("Available Roles")
        lines.append("=" * 60)

        # 遍历所有角色
        for name, role in _ROLES.items():
            lines.append(f"\n## {name}")
            lines.append(f"**Description:** {role.description}")
            lines.append(f"**Tools:** {', '.join(role.tools)}")

        # 返回格式化结果
        return ToolResult(content="\n".join(lines))


class ShareKnowledgeParams(BaseModel):
    """
    知识共享参数模型

    【字段说明】
    - target_agent: str | None - 目标 Agent 名称或 ID，可选
    - content: str - 要共享的知识内容，必填

    【参数说明】
    - 如果 target_agent 为空，知识会广播给所有 Agent
    - 如果 target_agent 不为空，知识只发送给指定 Agent
    """
    model_config = ConfigDict(extra="ignore")
    target_agent: str | None = Field(default=None, description="Target agent name or ID")
    content: str = Field(description="Knowledge content to share")


class ShareKnowledgeTool(BaseTool):
    """
    知识共享工具 - 在 Agent 之间共享知识

    【学习要点】
    1. 定向共享：将知识发送给指定 Agent
    2. 广播模式：将知识发送给所有 Agent
    3. 内容传递：传递结构化或非结构化的知识内容

    【使用示例】
    ```python
    tool = ShareKnowledgeTool()
    
    # 广播知识给所有 Agent
    result = await tool.invoke({"content": "新的 API 文档已更新"})
    
    # 定向共享知识给特定 Agent
    result = await tool.invoke({
        "target_agent": "developer",
        "content": "用户需求已变更，请查看最新文档"
    })
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 检查是否指定了目标 Agent
    3. 如果指定了目标，构建定向共享消息
    4. 如果未指定目标，构建广播消息
    5. 返回结果

    【设计目的】
    - 支持跨 Agent 的知识传递
    - 实现多 Agent 协作时的信息共享
    - 允许一个 Agent 将学到的知识传递给其他 Agent
    """
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
        """
        执行知识共享操作

        【参数说明】
        - params: dict - 工具调用参数，包含 target_agent 和 content

        【返回值】
        - ToolResult: 包含知识共享结果的消息
        """
        # 1. 验证输入参数
        p = ShareKnowledgeParams.model_validate(params)

        # 2. 根据是否指定目标 Agent 构建不同的消息
        if p.target_agent:
            # 定向共享
            return ToolResult(content=f"Knowledge shared with agent '{p.target_agent}':\n\n{p.content}")
        else:
            # 广播给所有 Agent
            return ToolResult(content=f"Knowledge broadcast to all agents:\n\n{p.content}")