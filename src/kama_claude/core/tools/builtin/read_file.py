# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入 Path 类，用于文件路径操作（比字符串更安全、更强大）
from pathlib import Path

# 导入 BaseTool（工具基类）和 ToolResult（工具结果数据类）
from kama_claude.core.tools.base import BaseTool, ToolResult

# 最大读取字节数：512 KB（512 * 1024 字节）
# 为什么要限制大小？
#   1. 防止读取过大的文件导致内存溢出
#   2. 限制 LLM 的输入长度（避免超出上下文窗口）
#   3. 提高响应速度
_MAX_BYTES = 512 * 1024


# ReadFileTool 类：读取文件内容的工具
# 继承自 BaseTool，必须实现 invoke 方法
# 这是一个具体的工具实现，AI 可以调用它来读取文件
class ReadFileTool(BaseTool):
    # 工具名称：AI 通过这个名称识别工具，如 "read_file"
    name = "read_file"
    
    # 工具描述：AI 通过这个描述理解工具的用途和限制
    # 描述中说明了：
    #   1. 功能：读取文件的文本内容
    #   2. 路径要求：必须是相对当前工作目录的路径
    #   3. 大小限制：超过 512 KB 的文件会被截断
    description = (
        "Read the text content of a file. "
        "Path must be relative to the current working directory. "
        "Files larger than 512 KB are truncated."
    )
    
    # 工具输入参数的 JSON Schema：告诉 AI 需要传什么参数
    # Schema 结构：
    #   type: "object" - 参数是一个对象
    #   properties - 对象的属性定义
    #     path - 文件路径，字符串类型，有描述
    #   required - 必填参数列表，path 是必填的
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

    # 读取文件内容；超 512KB 截断；禁止 .. 路径遍历
    # 这是工具的核心执行方法，必须实现（因为 BaseTool 要求）
    # 函数作用：根据参数读取文件内容，返回 ToolResult
    # 传参：params - 工具调用参数，字典格式，包含 "path" 键
    # 返回值：ToolResult - 工具执行结果，包含文件内容
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 从参数中获取路径字符串
        path_str = str(params["path"])

        # ====================== 安全检查：防止路径遍历攻击 ======================
        # 什么是路径遍历攻击？就是通过 ".." 访问上级目录的文件
        # 例如：path = "../../etc/passwd" 可以读取系统敏感文件
        # Path(path_str).parts 返回路径的各个组成部分
        # 检查是否有 ".."，如果有则抛出权限错误
        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # 将路径字符串转换为 Path 对象（更安全的路径操作）
        path = Path(path_str)
        
        # 读取文件的原始字节内容
        # 如果文件不存在，会抛出 FileNotFoundError（由 invoke_tool 捕获并处理）
        raw = path.read_bytes()
        
        # 判断文件是否超过最大限制
        truncated = len(raw) > _MAX_BYTES
        
        # 截取前 _MAX_BYTES 字节，解码为 UTF-8 文本
        # errors="replace" 表示遇到无法解码的字符时用 "�" 替换
        text = raw[:_MAX_BYTES].decode("utf-8", errors="replace")
        
        # 如果文件被截断，在末尾添加标记
        if truncated:
            text += "\n[truncated]"

        # 返回成功的工具结果，内容是文件文本
        return ToolResult(content=text)
