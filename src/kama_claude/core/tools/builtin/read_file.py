# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 Path：用于文件路径操作
from pathlib import Path

# 导入工具基类：所有工具都继承自 BaseTool
from kama_claude.core.tools.base import BaseTool, ToolResult

# 最大读取大小：512 KB（防止读取过大文件导致 LLM 上下文溢出）
_MAX_BYTES = 512 * 1024  # 512 KB


# ReadFileTool：读取文件内容的工具
# 什么是读取文件？就是获取文件中的文本内容
# 为什么需要这个工具？因为 LLM 需要读取代码文件才能进行分析和修改
class ReadFileTool(BaseTool):
    # 工具名称：供 LLM 调用时使用
    name = "read_file"
    # 工具描述：告诉 LLM 这个工具的用途和使用方式
    description = (
        "Read the text content of a file. "
        "Path must be relative to the current working directory. "
        "Files larger than 512 KB are truncated."
    )
    # 输入参数 schema：定义 LLM 需要提供的参数
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            }
        },
        "required": ["path"],  # path 是必填参数
    }

    # 读取文件内容；超 512KB 截断；禁止 .. 路径遍历
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 获取路径参数（转换为字符串）
        path_str = str(params["path"])

        # 安全检查：禁止路径遍历攻击
        # 什么是路径遍历？就是通过 ../ 访问上级目录（如 ../../../etc/passwd）
        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # 转换为 Path 对象
        path = Path(path_str)
        # 读取文件字节内容（如果文件不存在会抛出 FileNotFoundError）
        raw = path.read_bytes()
        # 判断是否需要截断
        truncated = len(raw) > _MAX_BYTES
        # 解码为字符串（最多读取 _MAX_BYTES 字节）
        # errors="replace"：遇到无法解码的字符用 � 替换
        text = raw[:_MAX_BYTES].decode("utf-8", errors="replace")
        # 如果截断了，添加提示
        if truncated:
            text += "\n[truncated]"

        # 返回文件内容
        return ToolResult(content=text)
