"""
文档生成工具模块 - 提供 API 文档生成、README 更新和变更日志功能

【学习要点】
1. 文档生成：从 Python 源代码提取类和函数信息，生成 Markdown 文档
2. README 更新：支持按章节更新或追加内容
3. 变更日志：自动添加带时间戳和类型标签的变更记录
4. 正则匹配：使用正则表达式提取代码结构信息

【工具分类】
- GenerateDocsTool：从源码生成 API 文档
- UpdateReadmeTool：更新项目 README.md
- ChangelogTool：管理 CHANGELOG.md

【设计模式】
- 简单工厂模式：根据参数动态生成文档内容
- 正则表达式解析：提取代码结构信息
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大输出字节数：64 KB，防止返回过多内容
_MAX_OUTPUT_BYTES = 64 * 1024


class GenerateDocsParams(BaseModel):
    """
    文档生成参数模型

    【字段说明】
    - source_path: str | None - 源代码目录路径，默认 "src"
    - output_path: str | None - 文档输出目录路径，默认 "docs"
    """
    model_config = ConfigDict(extra="ignore")
    source_path: str | None = Field(default=None, description="Path to source code")
    output_path: str | None = Field(default=None, description="Path to save documentation")


class GenerateDocsTool(BaseTool):
    """
    API 文档生成工具 - 从 Python 源代码自动生成 Markdown 文档

    【学习要点】
    1. 递归遍历：使用 rglob("*.py") 递归查找所有 Python 文件
    2. 模块名计算：将相对路径转换为 Python 模块名（替换 / 为 .）
    3. 正则解析：使用正则表达式提取类和函数定义
    4. 文档结构：生成层次化的 Markdown 文档

    【使用示例】
    ```python
    tool = GenerateDocsTool()
    
    # 使用默认路径
    result = await tool.invoke({})
    
    # 指定自定义路径
    result = await tool.invoke({
        "source_path": "src/my_module",
        "output_path": "docs/api"
    })
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 解析源路径和输出路径（默认 src/ 和 docs/）
    3. 验证源路径存在
    4. 创建输出目录（如果不存在）
    5. 遍历所有 Python 文件
    6. 提取类和函数信息
    7. 生成 Markdown 文档
    8. 写入 index.md 文件
    """
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
        """
        执行文档生成操作

        【参数说明】
        - params: dict - 工具调用参数，包含 source_path 和 output_path

        【返回值】
        - ToolResult: 包含生成的文档路径和预览内容
        """
        p = GenerateDocsParams.model_validate(params)

        # 使用默认值或用户指定的路径
        source_path = p.source_path or "src"
        output_path = p.output_path or "docs"

        # 解析为绝对路径
        source = Path(source_path).resolve()
        output = Path(output_path).resolve()

        # 验证源路径存在
        if not source.exists():
            return ToolResult(content=f"Source path not found: {source}", is_error=True, error_type="runtime_error")

        # 创建输出目录（如果不存在）
        output.mkdir(parents=True, exist_ok=True)

        # 生成文档内容
        doc_content = self._generate_docs(source)
        index_file = output / "index.md"
        index_file.write_text(doc_content, encoding="utf-8")

        # 返回结果（包含前 2000 字符预览）
        return ToolResult(content=f"Documentation generated at: {index_file}\n\n{doc_content[:2000]}...")

    def _generate_docs(self, source_path: Path) -> str:
        """
        生成文档内容（核心逻辑）

        【参数说明】
        - source_path: Path - 源代码目录路径

        【处理流程】
        1. 创建文档标题
        2. 递归查找所有 .py 文件
        3. 对每个文件：
           - 计算模块名（路径 → 模块名）
           - 读取文件内容
           - 使用正则提取类和函数
           - 生成对应章节
        4. 返回完整文档内容

        【正则模式说明】
        - func_pattern: 匹配 def 函数定义
        - class_pattern: 匹配 class 类定义

        【返回值】
        - str: 完整的 Markdown 文档内容
        """
        lines = []
        lines.append("# API Documentation")
        lines.append("")

        # 递归查找所有 Python 文件并排序
        py_files = sorted(source_path.rglob("*.py"))
        for py_file in py_files:
            # 计算相对路径和模块名
            rel_path = py_file.relative_to(source_path)
            # 将路径转换为模块名：src/core/tools.py → src.core.tools
            module_name = str(rel_path).replace("/", ".").replace(".py", "")

            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                # 跳过无法读取的文件
                continue

            # 添加模块章节
            lines.append(f"## {module_name}")
            lines.append(f"*File: `{rel_path}`*")
            lines.append("")

            # 导入正则模块（函数内部导入，避免模块加载时的副作用）
            import re
            # 匹配函数定义：def func_name(...)
            func_pattern = r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
            # 匹配类定义：class ClassName(...)
            class_pattern = r"class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*"

            # 提取所有类和函数名
            classes = re.findall(class_pattern, content)
            functions = re.findall(func_pattern, content)

            # 添加类章节
            for cls in classes:
                lines.append(f"### Class: `{cls}`")
                lines.append("")

            # 添加函数章节（排除魔术方法）
            for func in functions:
                if func not in ["__init__", "__str__", "__repr__", "__eq__"]:
                    lines.append(f"### Function: `{func}()`")
                    lines.append("")

            # 添加分隔线
            lines.append("---")
            lines.append("")

        return "\n".join(lines)


class UpdateReadmeParams(BaseModel):
    """
    README 更新参数模型

    【字段说明】
    - section: str | None - 要更新的章节标题，可选
    - content: str | None - 新内容，可选

    【参数组合】
    - section + content: 更新指定章节的内容
    - content 单独：直接追加内容到文件末尾
    """
    model_config = ConfigDict(extra="ignore")
    section: str | None = Field(default=None, description="Section to update")
    content: str | None = Field(default=None, description="New content")


class UpdateReadmeTool(BaseTool):
    """
    README 更新工具 - 更新项目 README.md 文件

    【学习要点】
    1. 章节定位：使用正则表达式定位指定章节
    2. 内容替换：支持更新已有章节或添加新章节
    3. 文件创建：如果 README.md 不存在，自动创建
    4. 追加模式：支持直接追加内容到文件末尾

    【使用示例】
    ```python
    tool = UpdateReadmeTool()
    
    # 更新已有章节
    result = await tool.invoke({
        "section": "Features",
        "content": "- Feature 1\n- Feature 2"
    })
    
    # 添加新章节
    result = await tool.invoke({
        "section": "Installation",
        "content": "pip install myproject"
    })
    
    # 直接追加内容
    result = await tool.invoke({
        "content": "Additional notes..."
    })
    ```

    【正则模式说明】
    - 匹配模式：`## <section>\s*\n.*?(?=\n## |\Z)`
    - `## <section>`：匹配章节标题
    - `\s*\n`：匹配标题后的空白和换行
    - `.*?`：非贪婪匹配章节内容
    - `(?=\n## |\Z)`：正向前瞻，匹配下一个章节或文件末尾
    """
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
        """
        执行 README 更新操作

        【执行流程】
        1. 验证输入参数（Pydantic）
        2. 检查 README.md 是否存在，不存在则创建
        3. 读取当前内容
        4. 根据参数执行更新：
           - section + content：查找并替换章节内容
           - content 单独：追加到文件末尾
        5. 写入更新后的内容

        【返回值】
        - ToolResult: 操作结果
        """
        p = UpdateReadmeParams.model_validate(params)

        # 定位 README.md 文件
        readme_path = Path("README.md")
        # 如果文件不存在，创建默认内容
        if not readme_path.exists():
            readme_path.write_text("# Project README\n", encoding="utf-8")

        try:
            content = readme_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Failed to read README: {exc}", is_error=True, error_type="runtime_error")

        # 根据参数执行不同的更新操作
        if p.section and p.content:
            # 更新指定章节
            import re
            # 构建正则模式：匹配章节标题和内容
            pattern = rf"## {re.escape(p.section)}\s*\n.*?(?=\n## |\Z)"
            if re.search(pattern, content, re.DOTALL):
                # 章节已存在，替换内容
                content = re.sub(pattern, f"## {p.section}\n\n{p.content}", content, flags=re.DOTALL)
            else:
                # 章节不存在，追加新章节
                content += f"\n## {p.section}\n\n{p.content}\n"
        elif p.content:
            # 直接追加内容到文件末尾
            content += f"\n{p.content}\n"

        # 写入更新后的内容
        readme_path.write_text(content, encoding="utf-8")
        return ToolResult(content="README.md updated successfully.")


class ChangelogParams(BaseModel):
    """
    变更日志参数模型

    【字段说明】
    - message: str - 变更描述信息（必填）
    - type: str - 变更类型，默认 "feature"

    【支持的变更类型】
    - feature: 新功能（✨）
    - bugfix: 修复 bug（🐛）
    - improvement: 改进（🔧）
    - documentation: 文档（📝）
    """
    model_config = ConfigDict(extra="ignore")
    message: str = Field(description="Changelog message")
    type: str = Field(default="feature", description="Change type: feature, bugfix, improvement, documentation")


class ChangelogTool(BaseTool):
    """
    变更日志工具 - 向 CHANGELOG.md 添加变更记录

    【学习要点】
    1. 自动创建：如果 CHANGELOG.md 不存在，自动创建
    2. 类型标签：根据变更类型添加对应的 emoji 标签
    3. 时间戳：自动添加当前日期（YYYY-MM-DD 格式）
    4. 插入位置：新记录插入到文件开头（标题下方）

    【使用示例】
    ```python
    tool = ChangelogTool()
    
    # 添加新功能记录
    result = await tool.invoke({
        "message": "Add API documentation generation",
        "type": "feature"
    })
    
    # 添加 bug 修复记录
    result = await tool.invoke({
        "message": "Fix file path traversal vulnerability",
        "type": "bugfix"
    })
    
    # 使用默认类型（feature）
    result = await tool.invoke({
        "message": "Update dependencies"
    })
    ```

    【输出格式】
    ```
    # Changelog

    - [2024-01-15] ✨ Add API documentation generation
    - [2024-01-14] 🐛 Fix file path traversal vulnerability
    ```
    """
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
        """
        执行变更日志更新操作

        【执行流程】
        1. 验证输入参数（Pydantic）
        2. 检查 CHANGELOG.md 是否存在，不存在则创建
        3. 读取当前内容
        4. 生成变更记录条目（包含日期和类型标签）
        5. 插入到文件开头（标题下方）
        6. 写入更新后的内容

        【返回值】
        - ToolResult: 操作结果
        """
        p = ChangelogParams.model_validate(params)

        # 定位 CHANGELOG.md 文件
        changelog_path = Path("CHANGELOG.md")
        # 如果文件不存在，创建默认内容
        if not changelog_path.exists():
            changelog_path.write_text("# Changelog\n\n", encoding="utf-8")

        try:
            content = changelog_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Failed to read changelog: {exc}", is_error=True, error_type="runtime_error")

        # 获取当前日期（YYYY-MM-DD 格式）
        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")

        # 变更类型对应的 emoji 映射
        type_emoji = {
            "feature": "✨",
            "bugfix": "🐛",
            "improvement": "🔧",
            "documentation": "📝",
        }

        # 生成变更记录条目
        entry = f"- [{date}] {type_emoji.get(p.type, '')} {p.message}\n"

        # 插入到文件开头（标题下方）
        if content.startswith("# Changelog"):
            # 拆分并插入到第 2 行（标题行之后）
            lines = content.split("\n")
            lines.insert(2, entry)
            content = "\n".join(lines)
        else:
            # 非标准格式，直接追加到开头
            content = entry + content

        # 写入更新后的内容
        changelog_path.write_text(content, encoding="utf-8")
        return ToolResult(content="CHANGELOG.md updated successfully.")