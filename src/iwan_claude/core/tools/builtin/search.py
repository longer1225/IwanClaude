"""
搜索工具模块 - 提供文件查找和内容搜索功能

【学习要点】
1. glob 模式匹配：使用 fnmatch 模块实现文件名通配符匹配
2. 正则表达式：使用 re 模块实现内容搜索
3. 递归遍历：使用 os.walk 遍历目录树
4. 路径规范化：处理 Windows 和 POSIX 路径差异
5. 排除模式：支持 include/exclude 过滤规则

【工具分类】
- FindFilesTool：按名称和内容查找文件
- GrepSearchTool：纯内容搜索（类似 grep -rn）

【glob 模式说明】
- *：匹配任意字符（不包括路径分隔符）
- **：匹配任意字符（包括路径分隔符，递归匹配）
- ?：匹配单个字符
- [abc]：匹配字符集中的任意一个
- [!abc]：匹配不在字符集中的任意字符

【安全机制】
- 路径遍历检测：禁止 ".." 出现在路径中
- 沙箱验证：搜索范围限制在允许的目录内
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from iwan_claude.core.sandbox import get_search_root, get_sandbox, validate_path
from iwan_claude.core.tools.base import BaseTool, ToolResult


def _validate_rel_root(root_str: str) -> Path:
    """
    验证搜索根路径 - 安全检查辅助函数

    【参数说明】
    - root_str: str - 根路径字符串

    【安全检查】
    - 检查路径中是否包含 ".."（路径遍历攻击）

    【返回值】
    - Path: 验证后的 Path 对象

    【异常处理】
    - PermissionError: 路径包含 ".." 时抛出
    """
    if ".." in Path(root_str).parts:
        raise PermissionError(f"path traversal not allowed in root: {root_str}")
    return Path(root_str)


def _simplify_pattern_basename(pat: str) -> str | None:
    """
    简化 glob 模式为基础名称匹配

    【学习要点】
    1. 模式标准化：将反斜杠转换为正斜杠
    2. 前缀移除：去除开头的 **/
    3. 后缀移除：去除结尾的 /**
    4. 验证：确保结果不包含路径分隔符

    【参数说明】
    - pat: str - 原始 glob 模式

    【返回值】
    - str | None: 简化后的基础名称模式，如果无法简化则返回 None

    【示例】
    - "**/*.py" → "*.py"
    - "**/test_*.py/**" → "test_*.py"
    - "src/**/*.py" → None（仍包含路径分隔符）
    """
    p = pat.replace("\\", "/")
    # 移除开头的 **/
    if p.startswith("**/"):
        p = p[3:]
    # 移除结尾的 /**
    if p.endswith("/**"):
        p = p[:-3]
    # 如果结果为空或仍包含斜杠，则不是简单的基础名称模式
    if "/" not in p and p:
        return p
    return None


def _matches_any_glob(name: str, patterns: list[str]) -> bool:
    """
    检查名称是否匹配任意 glob 模式

    【参数说明】
    - name: str - 要检查的名称（文件名或目录名）
    - patterns: list[str] - glob 模式列表

    【匹配逻辑】
    1. 使用 fnmatch 直接匹配
    2. 如果模式是 **/NAME/** 形式，提取基础名称后再匹配

    【返回值】
    - bool: 是否匹配任意模式
    """
    for pat in patterns:
        pat_norm = pat.replace("\\", "/")
        # 直接使用 fnmatch 匹配
        if fnmatch.fnmatch(name, pat_norm):
            return True
        # 对于 **/.git/** 这样的模式，也直接匹配基础名称
        basename_pat = _simplify_pattern_basename(pat_norm)
        if basename_pat is not None and fnmatch.fnmatch(name, basename_pat):
            return True
    return False


def _matches_any_path_glob(rel_path: str, patterns: list[str]) -> bool:
    """
    检查相对路径是否匹配任意路径 glob 模式

    【学习要点】
    1. Path.match vs fnmatch：Path.match 支持 ** 递归匹配，而 fnmatch 不支持
    2. 路径规范化：统一使用正斜杠，因为 Path.match 在 Windows 上也是 POSIX 风格
    3. 多重回退：如果 Path.match 失败，尝试其他匹配策略

    【参数说明】
    - rel_path: str - 相对路径
    - patterns: list[str] - 路径 glob 模式列表

    【匹配策略】
    1. Path.match：优先使用，支持 ** 递归匹配
    2. 特殊处理 **/*：视为匹配所有路径
    3. 基础名称匹配：对于 **/X/** 形式的模式
    4. fnmatch：对于不包含 ** 的模式

    【返回值】
    - bool: 是否匹配任意模式
    """
    # 规范化路径为正斜杠
    norm = rel_path.replace("\\", "/")
    for pat in patterns:
        pat_norm = pat.replace("\\", "/")
        # 使用 Path.match 匹配（支持 ** 递归）
        try:
            if Path(norm).match(pat_norm):
                return True
        except ValueError:
            pass
        # 回退 1：将 **/* 视为匹配所有非空路径
        if pat_norm == "**/*" and norm:
            return True
        # 回退 2：对于 **/X/** 形式的模式，提取基础名称后匹配
        basename_pat = _simplify_pattern_basename(pat_norm)
        if basename_pat is not None and fnmatch.fnmatch(norm, basename_pat):
            return True
        # 回退 3：对于不包含 ** 的模式，使用 fnmatch
        if "**" not in pat_norm and fnmatch.fnmatch(norm, pat_norm):
            return True
    return False


def _should_ignore(rel_path: str, name: str, exclude: list[str]) -> bool:
    """
    判断是否应该忽略某个路径

    【参数说明】
    - rel_path: str - 相对路径
    - name: str - 基础名称（文件名或目录名）
    - exclude: list[str] - 排除模式列表

    【判断逻辑】
    1. 检查名称是否匹配排除模式
    2. 检查路径是否匹配排除模式

    【返回值】
    - bool: 是否应该忽略
    """
    if _matches_any_glob(name, exclude):
        return True
    if _matches_any_path_glob(rel_path, exclude):
        return True
    return False


# ---------------------------------------------------------------------------
# 1. find_files: by name glob + optional content pattern (regex) + depth limit
# ---------------------------------------------------------------------------


class FindFilesParams(BaseModel):
    """
    文件查找参数模型

    【字段说明】
    - root: str - 搜索根目录，默认为 "."（当前目录）
    - name_pattern: str | None - 文件名 glob 模式（如 "*.py"），可选
    - name_pattern_case: Literal["sensitive", "insensitive"] - 文件名匹配是否大小写敏感
    - content_pattern: str | None - 文件内容正则表达式，可选
    - content_case: Literal["sensitive", "insensitive"] - 内容匹配是否大小写敏感
    - include: list[str] | None - 包含的路径模式列表（如 ["src/**/*.py"]）
    - exclude: list[str] - 排除的路径模式列表（默认排除常见缓存目录）
    - max_depth: int - 最大递归深度（默认 5）
    - max_results: int - 最大返回结果数（默认 50）
    - file_type: Literal["any", "file", "dir"] - 查找类型（文件/目录/任意）
    """
    model_config = ConfigDict(extra="ignore")
    root: str = "."
    name_pattern: str | None = None
    name_pattern_case: Literal["sensitive", "insensitive"] = "insensitive"
    content_pattern: str | None = None
    content_case: Literal["sensitive", "insensitive"] = "insensitive"
    include: list[str] | None = None
    exclude: list[str] = [
        ".git", "node_modules", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache",
        "dist", "build", ".iwan", ".ruff_cache",
    ]
    max_depth: int = 5
    max_results: int = 50
    file_type: Literal["any", "file", "dir"] = "any"


class FindFilesTool(BaseTool):
    """
    文件查找工具 - 按名称和内容查找文件

    【学习要点】
    1. 混合搜索：支持同时按名称和内容查找
    2. glob 转 regex：使用 fnmatch.translate 将 glob 模式转换为正则表达式
    3. 递归遍历：使用 os.walk 遍历目录树
    4. 深度控制：通过修改 dirnames 列表实现剪枝
    5. 结果限制：防止返回过多结果导致内存问题

    【使用示例】
    ```python
    tool = FindFilesTool()
    
    # 查找所有 .py 文件
    result = await tool.invoke({"name_pattern": "*.py"})
    
    # 查找包含 "checkpoint" 的文件
    result = await tool.invoke({"content_pattern": "checkpoint"})
    
    # 组合条件：查找 src 目录下包含 "test" 的 .py 文件
    result = await tool.invoke({
        "name_pattern": "*.py",
        "content_pattern": "test",
        "include": ["src/**"],
        "max_depth": 3
    })
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 验证根目录存在且为目录
    3. 验证搜索范围在沙箱内
    4. 编译正则表达式（如果提供了内容或名称模式）
    5. 递归遍历目录树
    6. 根据 include/exclude 过滤路径
    7. 根据名称模式匹配
    8. 根据内容模式匹配（如果提供）
    9. 收集结果并返回
    """
    params_model = FindFilesParams
    name = "find_files"
    description = (
        "Search the file tree under a root directory for files/directories matching a "
        "name glob pattern and/or containing a content regex. Returns a list of matches "
        "with short content snippets (when content pattern is given). "
        "All paths must be relative to CWD; excludes common build/cache dirs by default."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "root": {"type": "string", "default": ".", "description": "Search root (relative)."},
            "name_pattern": {
                "type": "string",
                "description": "Filename glob pattern, e.g. '*.py' or 'test_*.md'. Supports fnmatch syntax.",
            },
            "name_pattern_case": {
                "type": "string",
                "enum": ["sensitive", "insensitive"],
                "default": "insensitive",
            },
            "content_pattern": {
                "type": "string",
                "description": "Optional Python regex pattern to search inside file contents.",
            },
            "content_case": {
                "type": "string",
                "enum": ["sensitive", "insensitive"],
                "default": "insensitive",
            },
            "include": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional path globs that files must match (e.g. ['src/**/*.py']).",
            },
            "exclude": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Path/name globs to skip (defaults to common cache dirs).",
            },
            "max_depth": {"type": "integer", "default": 5},
            "max_results": {"type": "integer", "default": 50},
            "file_type": {
                "type": "string",
                "enum": ["any", "file", "dir"],
                "default": "any",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行文件查找操作

        【执行流程详解】
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 验证输入参数（Pydantic）                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 2. 验证根目录存在且为目录                                    │
        ├─────────────────────────────────────────────────────────────┤
        │ 3. 验证搜索范围在沙箱内（如果沙箱启用）                        │
        ├─────────────────────────────────────────────────────────────┤
        │ 4. 编译正则表达式（名称模式和内容模式）                        │
        ├─────────────────────────────────────────────────────────────┤
        │ 5. 使用 os.walk 递归遍历目录树                                │
        │    ├─ 深度控制：超过 max_depth 时清空 dirnames                │
        │    ├─ 排除过滤：移除匹配 exclude 模式的目录                   │
        │    └─ 名称匹配：使用编译后的正则表达式匹配                     │
        ├─────────────────────────────────────────────────────────────┤
        │ 6. 内容匹配（如果提供了 content_pattern）                      │
        ├─────────────────────────────────────────────────────────────┤
        │ 7. 收集结果（限制 max_results）                               │
        ├─────────────────────────────────────────────────────────────┤
        │ 8. 返回格式化结果                                            │
        └─────────────────────────────────────────────────────────────┘
        """
        # 1. 验证输入参数
        p = FindFilesParams.model_validate(params)
        root = _validate_rel_root(p.root)

        # 2. 验证根目录存在且为目录
        if not root.exists() or not root.is_dir():
            return ToolResult(
                content=f"find root does not exist or is not a directory: {p.root}",
                is_error=True,
                error_type="runtime_error",
            )

        # 3. 验证搜索范围在沙箱内（如果沙箱启用）
        if get_sandbox().enabled:
            search_root = get_search_root()
            try:
                root.resolve().relative_to(search_root)
            except ValueError:
                return ToolResult(
                    content=f"find root '{p.root}' is outside the allowed search area",
                    is_error=True,
                    error_type="permission_denied",
                )

        # 4. 编译正则表达式
        try:
            content_re: re.Pattern[str] | None = None
            if p.content_pattern:
                flags = 0 if p.content_case == "sensitive" else re.IGNORECASE
                content_re = re.compile(p.content_pattern, flags)

            name_re_flags = 0 if p.name_pattern_case == "sensitive" else re.IGNORECASE
            name_re: re.Pattern[str] | None = None
            if p.name_pattern:
                # 将 glob 模式转换为正则表达式
                name_re = re.compile(fnmatch.translate(p.name_pattern), name_re_flags)
        except re.error as exc:
            return ToolResult(
                content=f"invalid regex pattern: {exc}",
                is_error=True,
                error_type="schema_error",
            )

        # 定义匹配结果的数据结构
        @dataclass
        class Match:
            """匹配结果 - 存储路径、类型、大小和内容片段"""
            path: str
            type: str
            size: int | None
            content_snippets: list[str]

        matches: list[Match] = []
        root_resolved = root.resolve()
        max_depth = max(0, p.max_depth)

        # 相对路径计算函数
        def relpath(full: Path) -> str:
            try:
                return str(full.relative_to(root_resolved)) or "."
            except ValueError:
                return str(full)

        # 5. 递归遍历目录树
        try:
            for dirpath, dirnames, filenames in os.walk(root_resolved):
                # 规范化路径（解决 Windows 8.3 短名称问题）
                current_dir = Path(dirpath).resolve()

                # 计算当前深度
                try:
                    depth = len(current_dir.relative_to(root_resolved).parts)
                except ValueError:
                    depth = 0

                # 深度控制：超过 max_depth 时停止递归
                if depth >= max_depth:
                    dirnames[:] = []  # 清空 dirnames，os.walk 将不再递归
                    continue

                # 排除目录过滤
                excluded_dirs: list[str] = []
                for d in list(dirnames):
                    rel_d = relpath((current_dir / d).resolve())
                    if _should_ignore(rel_d, d, p.exclude):
                        excluded_dirs.append(d)
                # 从 dirnames 中移除排除的目录，os.walk 将不会进入这些目录
                for d in excluded_dirs:
                    dirnames.remove(d)

                # 收集要检查的项目（文件和/或目录）
                items_to_check: list[tuple[str, bool]] = []  # (name, is_file)
                if p.file_type in ("any", "dir"):
                    for d in dirnames:
                        items_to_check.append((d, False))
                if p.file_type in ("any", "file"):
                    for f in filenames:
                        items_to_check.append((f, True))

                # 检查每个项目
                for name, is_file in items_to_check:
                    # 规范化文件路径（解决 Windows 短名称问题）
                    full_path_unresolved = current_dir / name
                    full_path = full_path_unresolved.resolve() if is_file else full_path_unresolved
                    rel = relpath(full_path)

                    # 排除过滤
                    if _should_ignore(rel, name, p.exclude):
                        continue
                    # 包含过滤（如果提供了 include 模式）
                    if p.include and not _matches_any_path_glob(rel, p.include):
                        continue

                    # 名称匹配
                    name_ok = True
                    if name_re is not None:
                        name_ok = bool(name_re.match(name))
                    if not name_ok:
                        continue

                    # 内容匹配（如果提供了内容模式）
                    snippets: list[str] = []
                    size: int | None = None
                    try:
                        if is_file:
                            st = full_path.stat()
                            size = st.st_size
                            if content_re is not None:
                                snippets = _search_content(full_path, content_re, limit=3)
                                if not snippets:
                                    continue  # 内容不匹配，跳过
                    except (OSError, PermissionError):
                        pass

                    # 添加匹配结果
                    matches.append(Match(
                        path=rel,
                        type="file" if is_file else "dir",
                        size=size,
                        content_snippets=snippets,
                    ))

                    # 结果数量限制
                    if len(matches) >= p.max_results:
                        break
                if len(matches) >= p.max_results:
                    break

        except (OSError, PermissionError) as exc:
            return ToolResult(
                content=f"find aborted due to error: {exc}",
                is_error=True,
                error_type="runtime_error",
            )

        # 6. 返回结果
        if not matches:
            return ToolResult(content="find_files: no matches")

        # 格式化输出
        lines = [f"find_files: {len(matches)} matches (max_results={p.max_results}) under '{p.root}'", "---"]
        for m in matches:
            size_str = f" size={_human(m.size)}" if m.size is not None else ""
            lines.append(f"- [{m.type}]{size_str} {m.path}")
            for snip in m.content_snippets:
                lines.append(f"    ... {snip}")
        return ToolResult(content="\n".join(lines))


# ---------------------------------------------------------------------------
# 2. grep_search: pure content search with ripgrep-like interface (pure Python fallback)
# ---------------------------------------------------------------------------


class GrepSearchParams(BaseModel):
    """
    内容搜索参数模型

    【字段说明】
    - pattern: str - 搜索模式（正则表达式或固定字符串）
    - root: str - 搜索根目录，默认为 "."（当前目录）
    - include: list[str] - 包含的路径模式列表（默认 ["**/*"]）
    - exclude: list[str] - 排除的路径模式列表（默认排除常见缓存目录）
    - case_sensitive: bool - 是否大小写敏感（仅正则模式有效）
    - fixed_string: bool - 是否将模式视为固定字符串（而非正则表达式）
    - max_matches: int - 最大匹配数（默认 200）
    - max_file_bytes: int - 跳过大于此大小的文件（默认 2MB）
    """
    model_config = ConfigDict(extra="ignore")
    pattern: str
    root: str = "."
    include: list[str] = ["**/*"]
    exclude: list[str] = [
        "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/__pycache__/**",
        "**/*.pyc", "**/.mypy_cache/**", "**/.pytest_cache/**", "**/dist/**",
        "**/build/**", "**/.iwan/**", "**/.ruff_cache/**",
    ]
    case_sensitive: bool = False
    fixed_string: bool = False
    max_matches: int = 200
    max_file_bytes: int = 2 * 1024 * 1024  # 2 MB


class GrepSearchTool(BaseTool):
    """
    内容搜索工具 - 使用正则表达式或固定字符串搜索文件内容

    【学习要点】
    1. 策略模式：使用 _Matcher 接口和两种实现（_ReMatcher 和 _FixedMatcher）
    2. 纯 Python 实现：不依赖外部 grep 命令，跨平台兼容
    3. 二进制文件处理：通过 errors="replace" 优雅处理非文本文件
    4. 行号追踪：记录匹配行的行号，便于定位

    【使用示例】
    ```python
    tool = GrepSearchTool()
    
    # 正则模式：搜索包含 "checkpoint" 的行
    result = await tool.invoke({"pattern": "checkpoint"})
    
    # 固定字符串模式：搜索字面量 "test()"
    result = await tool.invoke({"pattern": "test()", "fixed_string": True})
    
    # 大小写敏感搜索
    result = await tool.invoke({"pattern": "Checkpoint", "case_sensitive": True})
    ```

    【输出格式】
    ```
    grep_search: 10 matches (max=200) pattern='checkpoint' under '.'
    ---
    src/core/runner.py:45:     checkpointer = self._init_checkpointer()
    src/core/langgraph_loop.py:12: from langgraph.checkpoint import AsyncSqliteSaver
    ...
    ```
    """
    params_model = GrepSearchParams
    name = "grep_search"
    description = (
        "Search file contents recursively using a regex (or fixed string) pattern. "
        "Equivalent to grep -rn. Respects include/exclude path globs; skips binary files "
        "by failing decode gracefully. All paths relative to CWD."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex (or fixed string) to search for."},
            "root": {"type": "string", "default": ".", "description": "Search root (relative)."},
            "include": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["**/*"],
                "description": "Path globs to include.",
            },
            "exclude": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Path globs to exclude (cache dirs by default).",
            },
            "case_sensitive": {
                "type": "boolean",
                "default": False,
                "description": "Match case-sensitively (regex mode only).",
            },
            "fixed_string": {
                "type": "boolean",
                "default": False,
                "description": "Treat pattern as literal substring instead of regex.",
            },
            "max_matches": {"type": "integer", "default": 200},
            "max_file_bytes": {"type": "integer", "default": 2097152, "description": "Skip files larger than this (bytes)."},
        },
        "required": ["pattern"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行内容搜索操作

        【执行流程】
        1. 验证输入参数（Pydantic）
        2. 验证根目录存在且为目录
        3. 验证搜索范围在沙箱内（如果沙箱启用）
        4. 创建匹配器（正则模式或固定字符串模式）
        5. 使用 os.walk 递归遍历目录树
        6. 根据 include/exclude 过滤路径
        7. 跳过过大的文件
        8. 逐行搜索匹配内容
        9. 收集结果并返回
        """
        # 1. 验证输入参数
        p = GrepSearchParams.model_validate(params)
        root = _validate_rel_root(p.root)

        # 2. 验证根目录存在且为目录
        if not root.exists() or not root.is_dir():
            return ToolResult(
                content=f"grep root does not exist or is not a directory: {p.root}",
                is_error=True,
                error_type="runtime_error",
            )

        # 3. 验证搜索范围在沙箱内（如果沙箱启用）
        if get_sandbox().enabled:
            search_root = get_search_root()
            try:
                root.resolve().relative_to(search_root)
            except ValueError:
                return ToolResult(
                    content=f"grep root '{p.root}' is outside the allowed search area",
                    is_error=True,
                    error_type="permission_denied",
                )

        # 4. 创建匹配器
        try:
            if p.fixed_string:
                # 固定字符串模式：直接进行子串匹配
                matcher: _Matcher = _FixedMatcher(p.pattern, case_sensitive=p.case_sensitive)
            else:
                # 正则表达式模式：编译正则表达式
                flags = 0 if p.case_sensitive else re.IGNORECASE
                matcher = _ReMatcher(re.compile(p.pattern, flags))
        except re.error as exc:
            return ToolResult(
                content=f"invalid grep regex: {exc}",
                is_error=True,
                error_type="schema_error",
            )

        root_resolved = root.resolve()

        # 相对路径计算函数
        def rel(full: Path) -> str:
            try:
                return str(full.relative_to(root_resolved)) or "."
            except ValueError:
                return str(full)

        results: list[str] = []
        total_matches = 0

        # 5. 递归遍历目录树
        try:
            for dirpath, dirnames, filenames in os.walk(root_resolved):
                # 规范化路径（解决 Windows 8.3 短名称问题）
                current = Path(dirpath).resolve()

                # 目录过滤：同时检查 exclude 和 include
                pruned_dirs: list[str] = []
                for d in list(dirnames):
                    rel_d = rel(current / d)
                    if _should_ignore(rel_d, d, p.exclude) or not _any_glob_include(rel_d, p.include):
                        pruned_dirs.append(d)
                for d in pruned_dirs:
                    dirnames.remove(d)

                # 搜索每个文件
                for fname in filenames:
                    full = (current / fname).resolve()
                    rel_p = rel(full)

                    # 排除过滤
                    if _should_ignore(rel_p, fname, p.exclude):
                        continue
                    # 包含过滤
                    if not _any_glob_include(rel_p, p.include):
                        continue

                    # 跳过过大的文件
                    try:
                        st = full.stat()
                    except OSError:
                        continue
                    if st.st_size > p.max_file_bytes:
                        continue

                    # 逐行搜索
                    try:
                        with full.open("r", encoding="utf-8", errors="replace") as fh:
                            for line_no, line in enumerate(fh, start=1):
                                if matcher.search(line):
                                    total_matches += 1
                                    # 格式：路径:行号: 内容（截取前 200 字符）
                                    results.append(
                                        f"{rel_p}:{line_no}: {line.rstrip()[:200]}"
                                    )
                                    if total_matches >= p.max_matches:
                                        break
                    except (OSError, PermissionError):
                        continue

                    if total_matches >= p.max_matches:
                        break
                if total_matches >= p.max_matches:
                    break

        except (OSError, PermissionError) as exc:
            return ToolResult(
                content=f"grep aborted due to error: {exc}",
                is_error=True,
                error_type="runtime_error",
            )

        # 6. 返回结果
        if not results:
            return ToolResult(content="grep_search: no matches")

        header = f"grep_search: {total_matches} matches (max={p.max_matches}) pattern='{p.pattern}' under '{p.root}'"
        return ToolResult(content=header + "\n---\n" + "\n".join(results))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _Matcher:
    """
    匹配器接口 - 定义搜索匹配的统一接口

    【设计模式】策略模式
    通过不同的实现类实现不同的匹配策略：
    - _ReMatcher：正则表达式匹配
    - _FixedMatcher：固定字符串匹配

    【子类实现要求】
    必须实现 search 方法，返回布尔值表示是否匹配
    """
    def search(self, line: str) -> bool:
        raise NotImplementedError


class _ReMatcher(_Matcher):
    """
    正则表达式匹配器 - 使用正则表达式进行匹配

    【参数说明】
    - pattern: re.Pattern[str] - 编译后的正则表达式

    【使用示例】
    ```python
    matcher = _ReMatcher(re.compile(r"test.*pattern"))
    result = matcher.search("this is a test pattern")  # True
    ```
    """
    def __init__(self, pattern: re.Pattern[str]) -> None:
        self._re = pattern

    def search(self, line: str) -> bool:
        """
        检查行是否匹配正则表达式

        【参数说明】
        - line: str - 要检查的行

        【返回值】
        - bool: 是否匹配
        """
        return bool(self._re.search(line))


class _FixedMatcher(_Matcher):
    """
    固定字符串匹配器 - 使用子串匹配进行搜索

    【学习要点】
    1. 大小写处理：提前将模式和目标行转换为小写（如果不区分大小写）
    2. 性能优化：避免每次调用都进行大小写转换

    【参数说明】
    - needle: str - 要搜索的字符串
    - case_sensitive: bool - 是否区分大小写

    【使用示例】
    ```python
    matcher = _FixedMatcher("test", case_sensitive=False)
    result = matcher.search("This is a Test")  # True（不区分大小写）
    ```
    """
    def __init__(self, needle: str, case_sensitive: bool) -> None:
        # 提前处理大小写，避免每次搜索都转换
        self._needle = needle if case_sensitive else needle.lower()
        self._case_sensitive = case_sensitive

    def search(self, line: str) -> bool:
        """
        检查行是否包含固定字符串

        【参数说明】
        - line: str - 要检查的行

        【返回值】
        - bool: 是否包含目标字符串
        """
        target = line if self._case_sensitive else line.lower()
        return self._needle in target


def _search_content(path: Path, pattern: re.Pattern[str], limit: int = 3) -> list[str]:
    """
    搜索文件内容并返回匹配的片段

    【参数说明】
    - path: Path - 要搜索的文件路径
    - pattern: re.Pattern[str] - 正则表达式模式
    - limit: int - 最大返回片段数（默认 3）

    【返回值】
    - list[str]: 匹配的行片段列表，格式为 "line 行号: 内容"

    【处理逻辑】
    1. 打开文件并逐行读取
    2. 对每一行进行正则匹配
    3. 如果匹配，截取前 160 字符作为片段
    4. 返回最多 limit 个片段
    """
    snippets: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, start=1):
                if pattern.search(line):
                    snippet = line.strip()
                    if len(snippet) > 160:
                        snippet = snippet[:157] + "..."
                    snippets.append(f"line {lineno}: {snippet}")
                    if len(snippets) >= limit:
                        break
    except (OSError, PermissionError):
        return []
    return snippets


def _any_glob_include(rel_path: str, include: list[str]) -> bool:
    """
    检查路径是否匹配任意包含模式

    【参数说明】
    - rel_path: str - 相对路径
    - include: list[str] - 包含模式列表

    【特殊处理】
    - 如果 include 为空，返回 True（匹配所有）
    - 如果 include 包含 "**/*"，也匹配所有

    【返回值】
    - bool: 是否匹配任意包含模式
    """
    if not include:
        return True
    return any(_matches_any_path_glob(rel_path, [pat]) for pat in include)


def _human(num: int | None) -> str:
    """
    将字节数转换为人类可读的格式

    【参数说明】
    - num: int | None - 字节数，None 表示未知

    【转换规则】
    - 小于 1024：显示为 B
    - 1024-1024^2：显示为 KB
    - 1024^2-1024^3：显示为 MB
    - 1024^3-1024^4：显示为 GB
    - 大于等于 1024^4：显示为 TB

    【示例】
    - 1024 → "1KB"
    - 1536 → "1KB"（向下取整）
    - 1048576 → "1MB"
    """
    if num is None:
        return "?"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < step:
            return f"{num:.0f}{unit}"
        num //= int(step)
    return f"{num}TB"
