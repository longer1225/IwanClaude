"""
目录列表工具 - 以树状格式列出目录内容

【学习要点】
1. 递归遍历：使用递归函数遍历目录树
2. 树状显示：使用 Unicode 字符绘制目录树（├──、└──、│）
3. 深度限制：防止遍历过深导致性能问题
4. 条目限制：防止返回过多内容导致内存问题
5. 排序：先显示目录，再显示文件，按名称排序

【树状字符说明】
- ├──：非最后一个条目
- └──：最后一个条目
- │   ：连接符，用于表示层级关系

【输出示例】
```
src/
├── core/
│   ├── app.py
│   └── tools/
│       ├── __init__.py
│       └── base.py
└── cli/
    └── main.py
```
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.sandbox import validate_path
from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大递归深度
_MAX_DEPTH = 4
# 最大条目数量
_MAX_ENTRIES = 200


class ListDirParams(BaseModel):
    """
    目录列表参数模型

    【字段说明】
    - path: str - 目录路径，默认为 "."（当前目录）
    - max_depth: int - 递归深度，范围 1-4，默认 2
    """
    model_config = ConfigDict(extra="ignore")
    path: str = "."
    max_depth: int = Field(default=2, ge=1, le=_MAX_DEPTH)


class ListDirTool(BaseTool):
    """
    目录列表工具 - 以树状格式列出目录内容

    【学习要点】
    1. Pydantic 参数校验：使用 Field 定义参数约束（ge=1, le=_MAX_DEPTH）
    2. 递归遍历：使用内部函数 _walk 递归遍历目录
    3. 状态管理：使用 nonlocal 声明共享变量（count）
    4. 树状绘制：根据条目位置选择不同的连接符

    【使用示例】
    ```python
    tool = ListDirTool()
    
    # 列出当前目录（深度 2）
    result = await tool.invoke({})
    
    # 列出指定目录（深度 3）
    result = await tool.invoke({"path": "src", "max_depth": 3})
    ```

    【输入参数】
    - path: str - 相对路径（相对于当前工作目录），默认为 "."
    - max_depth: int - 递归深度，范围 1-4，默认 2

    【输出结果】
    - 树状格式的目录列表字符串
    - 超过 200 条目时会截断并显示 "(truncated)"
    """
    params_model = ListDirParams
    name = "list_dir"
    description = (
        "List the contents of a directory as a tree. "
        "Path must be relative to the current working directory. "
        "Hidden entries (starting with .) are included. "
        f"Maximum depth is {_MAX_DEPTH}, maximum total entries is {_MAX_ENTRIES}."
    )
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
        "required": [],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行目录列表操作

        【执行流程】
        1. 验证输入参数（Pydantic）
        2. 验证路径是否在沙箱允许范围内
        3. 检查路径是否包含 ".."（路径遍历攻击）
        4. 验证路径是否存在且为目录
        5. 初始化结果列表和计数器
        6. 递归遍历目录树（_walk 函数）
        7. 返回树状格式的结果

        【异常处理】
        - FileNotFoundError: 路径不存在时抛出
        - NotADirectoryError: 路径不是目录时抛出
        - PermissionError: 路径包含 ".." 时抛出
        - ValueError: 路径不在沙箱范围内时抛出（由 validate_path 抛出）
        """
        # 1. 验证输入参数
        p = ListDirParams.model_validate(params)
        path_str = p.path
        max_depth = p.max_depth

        # 2. 验证路径是否在沙箱允许范围内
        validate_path(path_str, "read")

        # 3. 检查路径遍历攻击
        if ".." in Path(path_str).parts:
            raise PermissionError(f"path traversal not allowed: {path_str}")

        # 4. 验证路径是否存在且为目录
        root = Path(path_str)
        if not root.exists():
            raise FileNotFoundError(f"no such directory: {path_str}")
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {path_str}")

        # 5. 初始化结果列表和计数器
        lines: list[str] = [str(root) + "/"]  # 根目录行
        count = 0  # 条目计数器

        def _walk(directory: Path, depth: int, prefix: str) -> None:
            """
            递归遍历目录 - 内部辅助函数

            【参数说明】
            - directory: Path - 当前要遍历的目录
            - depth: int - 当前递归深度（从 1 开始）
            - prefix: str - 当前行的前缀字符串（用于绘制树状结构）

            【设计要点】
            1. nonlocal count：声明 count 为非局部变量，允许在嵌套函数中修改
            2. 深度控制：depth > max_depth 时停止递归
            3. 条目限制：count >= _MAX_ENTRIES 时停止并添加截断标记
            4. 排序：先按是否为文件排序（目录在前），再按名称排序
            5. 连接符选择：最后一个条目用 └──，其他用 ├──
            6. 前缀扩展：根据是否为最后一个条目选择不同的前缀扩展
            """
            nonlocal count

            # 检查深度和条目限制
            if depth > max_depth or count >= _MAX_ENTRIES:
                return

            # 获取目录条目并排序（目录在前，文件在后，按名称排序）
            entries = sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name))

            for i, entry in enumerate(entries):
                # 检查条目限制
                if count >= _MAX_ENTRIES:
                    lines.append(f"{prefix}... (truncated)")
                    return

                # 选择连接符：最后一个条目用 └──，其他用 ├──
                connector = "└── " if i == len(entries) - 1 else "├── "
                # 目录添加 "/" 后缀
                suffix = "/" if entry.is_dir() else ""
                # 添加当前行
                lines.append(f"{prefix}{connector}{entry.name}{suffix}")
                count += 1

                # 如果是目录且未达到最大深度，继续递归
                if entry.is_dir() and depth < max_depth:
                    # 前缀扩展：最后一个条目用空格，其他用 │
                    extension = "    " if i == len(entries) - 1 else "│   "
                    _walk(entry, depth + 1, prefix + extension)

        # 6. 开始递归遍历（从深度 1 开始，前缀为空）
        _walk(root, 1, "")

        # 7. 返回结果
        return ToolResult(content="\n".join(lines))
