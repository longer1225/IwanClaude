"""Skill 工具模块

这个模块实现了完整的 Skill 管理工具集，包括：
1. SkillListTool - 列出所有可用的 Skill
2. SkillInfoTool - 获取指定 Skill 的详细信息
3. SkillCreateTool - 创建新的 Skill
4. SkillDeleteTool - 删除项目本地 Skill
5. SkillInstallTool - 从 URL 安装 Skill

**Skill 概念详解：**
Skill 是一种预设的角色定义和工作流程模板，包含：
- name: Skill 名称，用于标识和调用
- description: 描述，用于自动匹配和展示
- invocation: 触发方式（manual/auto/both）
- icon: 图标标识
- keywords: 关键词，用于自动匹配
- allowed_tools: 允许使用的工具列表
- system_prompt_template: 系统提示词模板，使用 $ARGUMENTS 占位符

**Skill 加载优先级：**
1. 项目本地：.iwan/skills/
2. 用户全局：~/.iwan/skills/
3. 内置技能：包内置的技能目录

**触发方式：**
- manual（手动）：用户必须输入 /skill_name 才能触发
- auto（自动）：根据用户输入的关键词自动匹配触发
- both（两者皆可）：支持手动和自动两种方式

**使用示例：**
```python
# 列出所有技能
result = await skill_list_tool.invoke({})

# 获取技能详情
result = await skill_info_tool.invoke({"name": "code_review"})

# 创建技能
result = await skill_create_tool.invoke({
    "name": "my_skill",
    "description": "我的自定义技能",
    "system_prompt": "你是一个专业的助手，$ARGUMENTS",
    "invocation": "auto",
    "keywords": ["关键词1", "关键词2"]
})

# 从 URL 安装技能
result = await skill_install_tool.invoke({
    "url": "https://github.com/user/skill-repo",
    "global_install": False
})
```
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.skills.loader import Skill, SkillLoader
from iwan_claude.core.tools.base import BaseTool, ToolResult


class SkillListParams(BaseModel):
    """列出技能参数模型

    这个工具不需要任何参数，配置为空模型即可。
    """
    model_config = ConfigDict(extra="ignore")


class SkillListTool(BaseTool):
    """技能列表工具

    用于列出所有可用的 Skill，包括内置、全局和项目本地的技能。
    
    **设计模式：依赖注入**
    通过构造函数注入 SkillLoader 实例，实现技能的加载和管理。
    """
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
        """构造函数：注入 SkillLoader

        Args:
            skill_loader: SkillLoader 实例，用于加载和管理技能
        """
        super().__init__()
        self._skill_loader = skill_loader

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """列出所有可用的技能

        **执行流程：**
        1. 验证参数（无参数）
        2. 调用 skill_loader.list_all_skills() 获取所有技能
        3. 检查是否有技能
        4. 按技能名称排序
        5. 格式化输出技能信息（图标、名称、描述、触发方式、关键词、允许工具）
        
        **触发方式映射：**
        - manual -> 手动
        - auto -> 自动
        - both -> 手动/自动
        
        Args:
            params: 空字典，无参数
            
        Returns:
            ToolResult: 包含技能列表的结果对象
        """
        # 验证参数（虽然无参数，但仍需调用验证方法保持一致性）
        _ = SkillListParams.model_validate(params)
        
        # 获取所有技能
        skills = self._skill_loader.list_all_skills()

        # 检查是否有技能
        if not skills:
            return ToolResult(content="No skills available.")

        # 格式化技能列表
        lines: list[str] = []
        # 按技能名称排序
        for skill in sorted(skills, key=lambda x: x.name):
            # 将触发方式枚举值转换为中文
            invocation_str = {
                "manual": "手动",
                "auto": "自动",
                "both": "手动/自动",
            }.get(skill.invocation.value, "手动")
            
            # 添加技能信息
            lines.append(f"{skill.icon} === {skill.name} ===")
            lines.append(f"  描述: {skill.description}")
            lines.append(f"  触发: {invocation_str}")
            if skill.keywords:
                lines.append(f"  关键词: {', '.join(skill.keywords)}")
            if skill.allowed_tools:
                lines.append(f"  允许工具: {', '.join(skill.allowed_tools)}")
            lines.append("")

        return ToolResult(content="\n".join(lines))


class SkillInfoParams(BaseModel):
    """获取技能详情参数模型

    必须提供技能名称。
    """
    model_config = ConfigDict(extra="ignore")
    # 要查询的技能名称
    name: str = Field(description="Name of the skill to get information about")


class SkillInfoTool(BaseTool):
    """技能详情工具

    用于获取指定 Skill 的详细信息，包括系统提示词模板。
    
    **信息展示内容：**
    - 图标和名称
    - 描述
    - 触发方式
    - 关键词（如果有）
    - 允许工具（如果有）
    - 系统提示词模板
    """
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
        """构造函数：注入 SkillLoader

        Args:
            skill_loader: SkillLoader 实例，用于加载和解析技能
        """
        super().__init__()
        self._skill_loader = skill_loader

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """获取指定技能的详细信息

        **执行流程：**
        1. 验证参数，获取技能名称
        2. 调用 skill_loader.resolve() 解析技能
        3. 检查技能是否存在
        4. 格式化输出技能详细信息
        5. 包含系统提示词模板
        
        Args:
            params: 包含 name 的参数字典
            
        Returns:
            ToolResult: 包含技能详细信息的结果对象
        """
        # 验证参数并获取技能名称
        p = SkillInfoParams.model_validate(params)
        
        # 解析技能
        skill = self._skill_loader.resolve(p.name)

        # 检查技能是否存在
        if not skill:
            return ToolResult(content=f"Skill '{p.name}' not found.", is_error=True, error_type="runtime_error")

        # 将触发方式枚举值转换为中文描述
        invocation_str = {
            "manual": "手动触发（需输入 /skill_name）",
            "auto": "自动触发（根据关键词匹配）",
            "both": "手动/自动触发",
        }.get(skill.invocation.value, "手动触发")

        # 格式化技能详情
        lines: list[str] = []
        lines.append(f"{skill.icon} === {skill.name} ===")
        lines.append(f"描述: {skill.description}")
        lines.append(f"触发方式: {invocation_str}")
        if skill.keywords:
            lines.append(f"关键词: {', '.join(skill.keywords)}")
        if skill.allowed_tools:
            lines.append(f"允许工具: {', '.join(skill.allowed_tools)}")
        lines.append("")
        lines.append("系统提示词模板:")
        lines.append("----------------")
        lines.append(skill.system_prompt_template)

        return ToolResult(content="\n".join(lines))


class SkillCreateParams(BaseModel):
    """创建技能参数模型

    支持两种创建模式：
    1. 直接提供参数：name、description、system_prompt（必填）
    2. 从文件导入：file_path（可选）
    
    其他可选参数：allowed_tools、invocation、icon、keywords
    """
    model_config = ConfigDict(extra="ignore")
    
    # 技能名称（不使用 file_path 时必填）
    name: str | None = Field(default=None, description="Name of the new skill (required if not using file_path)")
    # 技能描述（不使用 file_path 时必填）
    description: str | None = Field(default=None, description="Description of the skill (required if not using file_path)")
    # 系统提示词模板（不使用 file_path 时必填，使用 $ARGUMENTS 占位符）
    system_prompt: str | None = Field(default=None, description="System prompt template (use $ARGUMENTS placeholder, required if not using file_path)")
    # 允许使用的工具列表
    allowed_tools: list[str] | None = Field(default=None, description="List of allowed tool names")
    # 本地 SKILL.md 文件路径（用于导入）
    file_path: str | None = Field(default=None, description="Path to a local SKILL.md file to import")
    # 触发方式，默认为 manual
    invocation: str | None = Field(default="manual", description="Invocation type: manual/auto/both")
    # 技能图标，默认为 ⚡
    icon: str | None = Field(default="⚡", description="Emoji icon for the skill")
    # 关键词列表，用于自动匹配
    keywords: list[str] | None = Field(default=None, description="List of keywords for auto-matching")


class SkillCreateTool(BaseTool):
    """技能创建工具

    支持两种创建模式：
    1. 直接创建：提供 name、description、system_prompt 等参数
    2. 文件导入：从本地 SKILL.md 文件导入
    
    **文件导入流程：**
    1. 解析文件路径
    2. 读取文件内容
    3. 使用正则表达式提取 frontmatter 中的 name
    4. 将文件复制到 .iwan/skills/{skill_name}/SKILL.md
    
    **直接创建流程：**
    1. 验证必填参数
    2. 检查技能是否已存在
    3. 构建 frontmatter
    4. 组合 frontmatter 和系统提示词
    5. 写入 .iwan/skills/{name}.md
    
    **Frontmatter 格式：**
    ```yaml
    name: skill_name
    description: skill_description
    invocation: manual
    icon: ⚡
    keywords:
      - keyword1
      - keyword2
    allowed_tools:
      - tool1
      - tool2
    ---
    
    system_prompt_template
    ```
    """
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
            "invocation": {
                "type": "string",
                "default": "manual",
                "enum": ["manual", "auto", "both"],
                "description": "Invocation type: manual (only /skill_name), auto (auto-match), both (both modes)",
            },
            "icon": {
                "type": "string",
                "default": "⚡",
                "description": "Emoji icon for the skill (e.g., 🔍, 📝, 🎯)",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of keywords for auto-matching (e.g., ['代码审查', 'code review'])",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """创建新的技能

        **执行流程：**
        1. 验证参数
        2. 创建技能目录（如果不存在）
        3. 如果提供了 file_path，执行文件导入
        4. 如果没有提供 file_path，验证必填参数并直接创建
        
        **文件导入逻辑：**
        - 解析源文件路径
        - 检查文件是否存在
        - 使用正则表达式提取 frontmatter 中的 name
        - 将文件内容写入目标位置
        
        **直接创建逻辑：**
        - 检查技能是否已存在
        - 构建 YAML frontmatter
        - 组合 frontmatter 和系统提示词
        - 写入文件
        
        Args:
            params: 包含创建技能所需参数的字典
            
        Returns:
            ToolResult: 创建结果，包含成功或失败信息
        """
        # 验证参数
        p = SkillCreateParams.model_validate(params)

        # 创建技能目录（如果不存在）
        skill_dir = Path(".iwan/skills")
        skill_dir.mkdir(parents=True, exist_ok=True)

        # 模式一：从文件导入
        if p.file_path:
            # 解析源文件路径
            source_file = Path(p.file_path).resolve()
            # 检查文件是否存在
            if not source_file.exists():
                return ToolResult(content=f"File not found: {source_file}", is_error=True, error_type="runtime_error")

            try:
                # 读取文件内容
                content = source_file.read_text(encoding="utf-8")
                import re

                # 使用正则表达式匹配 frontmatter
                # 匹配格式：---\n...\n---
                frontmatter_re = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
                m = frontmatter_re.match(content)
                
                # 优先使用 frontmatter 中的 name，其次使用参数中的 name，最后使用文件名
                skill_name = p.name or source_file.stem

                # 从 frontmatter 中提取 name
                if m:
                    front = m.group(1)
                    for line in front.splitlines():
                        if line.strip().startswith("name:"):
                            skill_name = line.strip()[len("name:"):].strip().strip('"').strip("'")
                            break

                # 创建目标目录和文件路径
                dest_dir = skill_dir / skill_name
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / "SKILL.md"

                # 写入文件内容
                dest_path.write_text(content, encoding="utf-8")
                return ToolResult(content=f"Skill '{skill_name}' imported from {source_file} to {dest_path}.")
            except Exception as exc:
                return ToolResult(content=f"Failed to import skill: {exc}", is_error=True, error_type="runtime_error")

        # 模式二：直接创建 - 验证必填参数
        if not p.name or not p.description or not p.system_prompt:
            return ToolResult(
                content="Missing required parameters: name, description, and system_prompt are required when file_path is not provided.",
                is_error=True,
                error_type="schema_error",
            )

        # 检查技能是否已存在
        skill_file = skill_dir / f"{p.name}.md"
        if skill_file.exists():
            return ToolResult(content=f"Skill '{p.name}' already exists.", is_error=True, error_type="runtime_error")

        # 构建 frontmatter 内容
        lines = [
            f"name: {p.name}",
            f"description: {p.description}",
            f"invocation: {p.invocation}",
            f"icon: {p.icon}",
        ]
        # 添加关键词列表（如果有）
        if p.keywords:
            lines.append("keywords:")
            for kw in p.keywords:
                lines.append(f"  - {kw}")
        # 添加允许工具列表（如果有）
        if p.allowed_tools:
            lines.append("allowed_tools:")
            for tool in p.allowed_tools:
                lines.append(f"  - {tool}")

        # 组合 frontmatter 和系统提示词
        frontmatter = "---\n" + "\n".join(lines) + "\n---"
        content = frontmatter + "\n\n" + p.system_prompt

        # 写入文件
        try:
            skill_file.write_text(content, encoding="utf-8")
            return ToolResult(content=f"Skill '{p.name}' created successfully at {skill_file}.")
        except Exception as exc:
            return ToolResult(content=f"Failed to create skill: {exc}", is_error=True, error_type="runtime_error")


class SkillDeleteParams(BaseModel):
    """删除技能参数模型

    必须提供要删除的技能名称。
    """
    model_config = ConfigDict(extra="ignore")
    # 要删除的技能名称
    name: str = Field(description="Name of the skill to delete")


class SkillDeleteTool(BaseTool):
    """技能删除工具

    用于删除项目本地的 Skill。
    
    **安全限制：**
    - 仅能删除项目本地技能（.iwan/skills/ 目录下）
    - 内置技能和全局技能受到保护，不能删除
    
    **删除逻辑：**
    1. 检查 .iwan/skills/{name}.md 是否存在，存在则删除
    2. 检查 .iwan/skills/{name}/SKILL.md 是否存在，存在则删除整个目录
    3. 如果都不存在，返回错误信息
    """
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
        """删除指定的技能

        **执行流程：**
        1. 验证参数，获取技能名称
        2. 检查技能文件是否存在（两种格式）
        3. 删除技能文件或目录
        4. 返回删除结果
        
        **注意事项：**
        - 仅能删除项目本地技能
        - 内置技能和全局技能不能删除
        
        Args:
            params: 包含 name 的参数字典
            
        Returns:
            ToolResult: 删除结果，包含成功或失败信息
        """
        # 验证参数并获取技能名称
        p = SkillDeleteParams.model_validate(params)

        # 构建技能文件路径（两种格式）
        skill_file = Path(f".iwan/skills/{p.name}.md")
        skill_dir = Path(f".iwan/skills/{p.name}/SKILL.md")

        # 删除技能文件（格式一：单文件）
        if skill_file.exists():
            skill_file.unlink()
            return ToolResult(content=f"Skill '{p.name}' deleted successfully.")
        # 删除技能目录（格式二：目录结构）
        elif skill_dir.exists():
            import shutil
            # 删除整个目录
            shutil.rmtree(skill_file.parent)
            return ToolResult(content=f"Skill '{p.name}' deleted successfully.")
        else:
            # 技能不存在
            return ToolResult(
                content=f"Skill '{p.name}' not found in project-local skills (.iwan/skills). "
                "Built-in and global skills cannot be deleted.",
                is_error=True,
                error_type="runtime_error",
            )


class SkillInstallParams(BaseModel):
    """安装技能参数模型

    支持从 URL 安装技能，可选全局安装。
    """
    model_config = ConfigDict(extra="ignore")
    # 技能的 URL（GitHub 仓库、ZIP 文件或 SKILL.md 文件）
    url: str = Field(description="URL of the skill to install")
    # 是否全局安装（安装到 ~/.iwan/skills），默认为 False（项目本地安装）
    global_install: bool = Field(default=False, description="Install globally (in ~/.iwan/skills) instead of project-local")


class SkillInstallTool(BaseTool):
    """技能安装工具

    用于从 URL 安装 Skill，支持多种来源：
    - GitHub 仓库：https://github.com/username/repo
    - ZIP 文件：https://example.com/skill.zip
    - SKILL.md 文件：https://example.com/SKILL.md
    
    **安装位置：**
    - 默认：项目本地 .iwan/skills/
    - global_install=True：用户全局 ~/.iwan/skills/
    
    **设计模式：委托模式**
    实际安装逻辑委托给 SkillLoader.install_from_url() 方法处理。
    """
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
        """构造函数：注入 SkillLoader

        Args:
            skill_loader: SkillLoader 实例，用于执行安装操作
        """
        super().__init__()
        self._skill_loader = skill_loader

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """从 URL 安装技能

        **执行流程：**
        1. 验证参数，获取 URL 和安装位置
        2. 调用 skill_loader.install_from_url() 执行安装
        3. 根据返回结果返回成功或失败信息
        
        **注意事项：**
        - 安装后需要重启会话才能生效
        - 支持 GitHub 仓库、ZIP 文件和 SKILL.md 文件
        
        Args:
            params: 包含 url 和 global_install 的参数字典
            
        Returns:
            ToolResult: 安装结果，包含成功或失败信息
        """
        # 验证参数
        p = SkillInstallParams.model_validate(params)

        # 委托给 SkillLoader 执行安装
        result, success = await self._skill_loader.install_from_url(p.url, p.global_install)

        # 根据安装结果返回相应的 ToolResult
        if success:
            return ToolResult(content=f"{result}\n\nNote: The skill will be available in the next session.")
        else:
            return ToolResult(content=result, is_error=True, error_type="runtime_error")
