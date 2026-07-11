# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 Path：用于文件路径操作
from pathlib import Path

# 导入工具基类：所有工具都继承自 BaseTool
from kama_claude.core.tools.base import BaseTool, ToolResult

# 最大写入大小：1 MB（防止写入过大内容）
_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


# WriteFileTool：写入文件内容的工具
# 什么是写入文件？就是把文本内容保存到文件中
# 为什么需要这个工具？因为 LLM 需要创建或修改文件来完成任务（如写代码、写报告）
class WriteFileTool(BaseTool):
    # 工具名称：供 LLM 调用时使用
    name = "write_file"
    # 工具描述：告诉 LLM 这个工具的用途和使用方式
    description = (
        "Write text content to a file, creating it (and any parent directories) if it "
        "does not exist, or overwriting it if it does. "
        "Path must be relative to the current working directory. "
        "Content size is limited to 1 MB."
    )
    # 输入参数 schema：定义 LLM 需要提供的参数
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            },
            "content": {
                "type": "string",
                "description": "Text content to write.",
            },
        },
        "required": ["path", "content"],  # path 和 content 都是必填参数
    }

    # 写入文件内容；超 1MB 拒绝；禁止 .. 路径遍历；自动创建父目录
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 获取路径参数（转换为字符串）
        path_str = str(params["path"])
        # 获取内容参数（转换为字符串）
        content = str(params["content"])

        # 安全检查：禁止路径遍历攻击
        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # 编码为字节（用于检查大小）
        encoded = content.encode("utf-8")
        # 检查内容大小是否超过限制
        if len(encoded) > _MAX_BYTES:
            return ToolResult(
                content=f"content too large: {len(encoded)} bytes (limit 1 MB)",
                is_error=True,
                error_type="runtime_error",
            )

        # 转换为 Path 对象
        path = Path(path_str)
        # 创建父目录（如果不存在）
        # parents=True：创建所有必要的父目录
        # exist_ok=True：如果目录已存在，不报错
        path.parent.mkdir(parents=True, exist_ok=True)
        # 写入文件内容（覆盖模式）
        path.write_text(content, encoding="utf-8")

        # 返回写入成功的消息
        return ToolResult(content=f"wrote {len(encoded)} bytes to {path_str}")
