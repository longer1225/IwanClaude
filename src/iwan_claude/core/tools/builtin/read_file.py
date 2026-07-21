"""
文件读取工具 - 读取指定路径的文件内容

【学习要点】
1. 安全检查：禁止路径遍历（..），防止访问沙箱外的文件
2. 大小限制：限制读取 512KB，避免内存占用过大
3. 编码处理：使用 UTF-8 解码，遇到非法字符用 replace 替换
4. 沙箱验证：通过 validate_path 确保文件在允许的路径范围内

【安全机制】
- 路径遍历检测：检查路径中是否包含 ".."
- 沙箱验证：validate_path 确保文件在工作目录内
- 大小限制：防止读取过大文件导致内存溢出
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from iwan_claude.core.sandbox import validate_path
from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大读取字节数：512 KB
_MAX_BYTES = 512 * 1024


class ReadFileParams(BaseModel):
    """
    读取文件参数模型

    【字段说明】
    - path: str - 要读取的文件路径，必须是相对路径
    """
    model_config = ConfigDict(extra="ignore")
    path: str


class ReadFileTool(BaseTool):
    """
    文件读取工具 - 读取指定路径的文件内容

    【学习要点】
    1. Pydantic 参数校验：使用 ReadFileParams 验证输入参数
    2. 异步方法：invoke 方法是 async，支持异步 I/O
    3. 错误处理：直接抛出异常（如 FileNotFoundError、PermissionError），
       由 invoke_tool 统一捕获并转换为 ToolResult
    4. 截断处理：超过 512KB 的文件会被截断，并添加 [truncated] 标记

    【使用示例】
    ```python
    tool = ReadFileTool()
    result = await tool.invoke({"path": "src/main.py"})
    print(result.content)  # 文件内容
    ```

    【输入参数】
    - path: str - 相对路径（相对于当前工作目录）

    【输出结果】
    - 文件文本内容（最多 512KB）
    - 如果文件不存在，返回错误
    - 如果路径包含 ".."，返回权限错误
    """
    params_model = ReadFileParams
    name = "read_file"
    description = (
        "Read the text content of a file. "
        "Path must be relative to the current working directory. "
        "Files larger than 512 KB are truncated."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            }
        },
        "required": ["path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行文件读取操作

        【执行流程】
        1. 使用 Pydantic 验证输入参数
        2. 验证路径是否在沙箱允许范围内
        3. 检查路径是否包含 ".."（路径遍历攻击）
        4. 读取文件内容（二进制方式）
        5. 截断超过 512KB 的内容
        6. 解码为 UTF-8 文本
        7. 返回结果

        【异常处理】
        - FileNotFoundError: 文件不存在时抛出
        - PermissionError: 路径包含 ".." 时抛出
        - ValueError: 路径不在沙箱范围内时抛出（由 validate_path 抛出）
        """
        # 1. 验证并提取参数
        path_str = ReadFileParams.model_validate(params).path

        # 2. 验证路径是否在沙箱允许范围内
        validate_path(path_str, "read")

        # 3. 检查路径遍历攻击
        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # 4. 读取文件内容
        path = Path(path_str)
        raw = path.read_bytes()  # 文件不存在时抛出 FileNotFoundError

        # 5. 处理大小限制
        truncated = len(raw) > _MAX_BYTES
        text = raw[:_MAX_BYTES].decode("utf-8", errors="replace")

        # 6. 添加截断标记
        if truncated:
            text += "\n[truncated]"

        # 7. 返回成功结果
        return ToolResult(content=text)
