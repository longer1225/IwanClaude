# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 Path：用于文件路径操作
from pathlib import Path

# 导入工具基类：所有工具都继承自 BaseTool
from kama_claude.core.tools.base import BaseTool, ToolResult

# 最大递归深度：4 层（防止目录树过深）
_MAX_DEPTH = 4
# 最大条目数：200 个（防止输出过多）
_MAX_ENTRIES = 200


# ListDirTool：列出目录内容的工具
# 什么是目录列表？就是显示文件夹中有哪些文件和子文件夹
# 为什么需要这个工具？因为 LLM 需要了解项目结构才能进行分析
class ListDirTool(BaseTool):
    # 工具名称：供 LLM 调用时使用
    name = "list_dir"
    # 工具描述：告诉 LLM 这个工具的用途和使用方式
    description = (
        "List the contents of a directory as a tree. "
        "Path must be relative to the current working directory. "
        "Hidden entries (starting with .) are included. "
        f"Maximum depth is {_MAX_DEPTH}, maximum total entries is {_MAX_ENTRIES}."
    )
    # 输入参数 schema：定义 LLM 需要提供的参数
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the directory (default '.').",
            },
            "max_depth": {
                "type": "integer",
                "description": f"How many levels deep to recurse (default 2, max {_MAX_DEPTH}).",
            },
        },
        "required": [],  # 所有参数都是可选的
    }

    # 以树状格式列出目录内容，深度和条数有上限
    # 什么是树状格式？就是像文件管理器一样显示目录层级结构
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 获取路径参数（默认为当前目录）
        path_str = str(params.get("path") or ".")
        # 获取最大深度参数（默认 2，最大 4）
        max_depth = min(int(str(params.get("max_depth") or 2)), _MAX_DEPTH)

        # 安全检查：禁止路径遍历攻击
        # 什么是路径遍历？就是通过 ../ 访问上级目录（如 ../../../etc/passwd）
        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # 转换为 Path 对象
        root = Path(path_str)
        # 检查路径是否存在
        if not root.exists():
            raise FileNotFoundError(f"no such directory: {path_str}")
        # 检查是否是目录
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {path_str}")

        # 初始化结果列表（第一行是根目录）
        lines: list[str] = [str(root) + "/"]
        # 计数（用于限制条目数）
        count = 0

        # 递归遍历目录（内部函数）
        # 为什么用内部函数？因为需要访问外部变量 lines 和 count
        def _walk(directory: Path, depth: int, prefix: str) -> None:
            # nonlocal：表示使用外部函数的变量
            nonlocal count
            # 如果超过最大深度或最大条目数，停止遍历
            if depth > max_depth or count >= _MAX_ENTRIES:
                return
            # 获取目录下所有条目，按"文件在前，目录在后"排序
            entries = sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name))
            # 遍历每个条目
            for i, entry in enumerate(entries):
                # 如果超过最大条目数，添加截断提示并退出
                if count >= _MAX_ENTRIES:
                    lines.append(f"{prefix}... (truncated)")
                    return
                # 判断是最后一个条目还是中间条目（用于显示不同的连接符）
                connector = "└── " if i == len(entries) - 1 else "├── "
                # 目录添加 / 后缀，文件不加
                suffix = "/" if entry.is_dir() else ""
                # 添加到结果列表
                lines.append(f"{prefix}{connector}{entry.name}{suffix}")
                # 计数加 1
                count += 1
                # 如果是目录且还没达到最大深度，递归遍历
                if entry.is_dir() and depth < max_depth:
                    # 根据是否是最后一个条目，添加不同的前缀
                    extension = "    " if i == len(entries) - 1 else "│   "
                    _walk(entry, depth + 1, prefix + extension)

        # 开始递归遍历
        _walk(root, 1, "")
        # 返回结果（用换行符连接所有行）
        return ToolResult(content="\n".join(lines))
