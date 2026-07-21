"""
上下文管理工具模块 - 提供文件内容添加到对话上下文的功能

【学习要点】
1. 内容添加：将文件内容添加到对话上下文，供 AI 后续参考
2. 行范围选择：支持指定文件的行范围
3. 大小限制：内容限制为 1MB，避免上下文过长
4. 错误处理：处理文件不存在、编码错误等情况

【设计目的】
- 类似 Claude Code 的 /add 命令或 @filename 引用
- 允许 AI 在不重复读取文件的情况下引用文件内容
- 支持查看文件的特定部分

【使用场景】
- 当需要让 AI 关注某个文件的特定部分时
- 当文件较大但只需要其中一部分时
- 当需要在多个对话轮次中保持文件内容可见时
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大内容长度：1 MB，防止添加过多内容到上下文
_MAX_CONTENT_LENGTH = 1024 * 1024


class AddContextParams(BaseModel):
    """
    添加上下文参数模型

    【字段说明】
    - file_path: str - 要添加到上下文的文件路径，必填
    - start_line: int | None - 起始行号（包含），默认 1
    - end_line: int | None - 结束行号（包含），默认文件末尾

    【行号说明】
    - 行号从 1 开始（1-based）
    - 如果 start_line 小于 1，自动调整为 1
    - 如果 end_line 大于总行数，自动调整为总行数
    - 如果 start_line 大于 end_line，返回错误
    """
    model_config = ConfigDict(extra="ignore")
    file_path: str = Field(description="Path to the file to add as context")
    start_line: int | None = Field(default=None, description="Start line number (inclusive)")
    end_line: int | None = Field(default=None, description="End line number (inclusive)")


class AddContextTool(BaseTool):
    """
    添加上下文工具 - 将文件内容添加到对话上下文

    【学习要点】
    1. 文件读取：使用 Path.read_text() 读取文件内容
    2. 行范围选择：支持指定文件的行范围
    3. 内容限制：超过 1MB 的内容会被截断
    4. 错误处理：处理文件不存在、编码错误、参数错误等情况

    【使用示例】
    ```python
    tool = AddContextTool()
    
    # 添加整个文件
    result = await tool.invoke({"file_path": "src/main.py"})
    
    # 添加指定行范围
    result = await tool.invoke({
        "file_path": "src/main.py",
        "start_line": 10,
        "end_line": 50
    })
    
    # 只添加前 20 行
    result = await tool.invoke({
        "file_path": "src/main.py",
        "end_line": 20
    })
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 检查文件是否存在
    3. 读取文件内容（UTF-8 编码）
    4. 处理行范围参数
    5. 提取指定行范围的内容
    6. 处理大小限制（超过 1MB 截断）
    7. 生成带标题的输出
    8. 返回结果

    【输出格式】
    ```
    === /path/to/file.py (lines 10-50) ===

    def my_function():
        """Function docstring"""
        return True
    ```

    【注意事项】
    - 文件路径使用绝对路径（resolve()）
    - 二进制文件或非 UTF-8 编码文件会返回错误
    - 内容添加到对话上下文后，AI 可以在后续对话中引用
    """
    params_model = AddContextParams
    name = "add_context"
    description = (
        "Add file content to the conversation context. "
        "This allows the AI to see the file content without reading it again. "
        "Similar to Claude Code's /add command or @filename mention. "
        "Use this when you want to reference a file's content in your conversation. "
        "Content is truncated at 1MB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to add as context",
            },
            "start_line": {
                "type": "integer",
                "description": "Optional: start line number (inclusive)",
            },
            "end_line": {
                "type": "integer",
                "description": "Optional: end line number (inclusive)",
            },
        },
        "required": ["file_path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行添加上下文操作

        【参数说明】
        - params: dict - 工具调用参数，包含 file_path、start_line、end_line

        【执行流程详解】
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 验证输入参数（Pydantic）                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 2. 检查文件是否存在                                          │
        │    └─ 不存在 → 返回错误                                       │
        ├─────────────────────────────────────────────────────────────┤
        │ 3. 读取文件内容（UTF-8 编码）                                  │
        │    ├─ UnicodeDecodeError → 返回错误（二进制文件）               │
        │    └─ 其他异常 → 返回错误                                     │
        ├─────────────────────────────────────────────────────────────┤
        │ 4. 处理行范围参数：                                           │
        │    ├─ start_line 默认 1，小于 1 调整为 1                      │
        │    ├─ end_line 默认总行数，大于总行数调整为总行数               │
        │    └─ start_line > end_line → 返回错误                        │
        ├─────────────────────────────────────────────────────────────┤
        │ 5. 提取指定行范围的内容                                       │
        ├─────────────────────────────────────────────────────────────┤
        │ 6. 处理大小限制（超过 1MB 截断）                               │
        ├─────────────────────────────────────────────────────────────┤
        │ 7. 生成带标题的输出                                           │
        ├─────────────────────────────────────────────────────────────┤
        │ 8. 返回结果                                                  │
        └─────────────────────────────────────────────────────────────┘

        【异常处理】
        - FileNotFoundError: 文件不存在
        - UnicodeDecodeError: 文件不是 UTF-8 编码或二进制文件
        - ValueError: 行范围参数错误

        【返回值】
        - ToolResult: 包含文件内容和标题
        """
        # 1. 验证输入参数
        p = AddContextParams.model_validate(params)

        # 2. 检查文件是否存在（使用绝对路径）
        file_path = Path(p.file_path).resolve()
        if not file_path.exists():
            return ToolResult(content=f"File not found: {file_path}", is_error=True, error_type="runtime_error")

        # 3. 读取文件内容
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 文件不是 UTF-8 编码或二进制文件
            return ToolResult(
                content=f"Cannot read file: {file_path} (binary or non-UTF-8 encoding)",
                is_error=True,
                error_type="runtime_error",
            )
        except Exception as exc:
            # 其他读取异常
            return ToolResult(content=f"Failed to read file: {exc}", is_error=True, error_type="runtime_error")

        # 4. 处理行范围参数
        # splitlines(keepends=True) 保留每行的换行符
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        # 设置默认值
        start = p.start_line or 1
        end = p.end_line or total_lines

        # 边界检查
        if start < 1:
            start = 1
        if end > total_lines:
            end = total_lines
        if start > end:
            return ToolResult(content="Start line must be less than or equal to end line", is_error=True, error_type="schema_error")

        # 5. 提取指定行范围的内容（Python 切片是左闭右开，所以 end 不需要 +1）
        selected_lines = lines[start - 1:end]
        selected_content = "".join(selected_lines)

        # 6. 处理大小限制
        if len(selected_content) > _MAX_CONTENT_LENGTH:
            selected_content = selected_content[:_MAX_CONTENT_LENGTH] + "\n[truncated]"

        # 7. 生成带标题的输出
        header = f"=== {file_path} (lines {start}-{end}) ==="

        # 8. 返回结果
        return ToolResult(content=f"{header}\n\n{selected_content}")