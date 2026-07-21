"""
文本编辑器工具模块 - 提供高级文件编辑功能

【学习要点】
1. 行级编辑：支持按行范围替换、插入、删除操作
2. 搜索替换：支持精确字符串搜索替换，带歧义保护
3. 行尾处理：智能检测和保留文件的行尾符（\r\n、\n、\r）
4. 备份机制：自动创建 .iwan/backups/<ts>__<path>.bak 备份文件
5. 分页浏览：支持按页查看大文件

【工具分类】
- ViewFileTool：查看文件（支持分页和行范围）
- EditByLinesTool：按行范围编辑（核心编辑原语）
- EditBySearchTool：搜索替换编辑（带歧义保护）
- InsertAtLineTool：在指定行插入文本
- DeleteLinesTool：删除指定行范围

【行尾符处理】
- 自动检测文件的行尾符类型（\r\n、\n、\r）
- 替换内容自动适配文件的行尾符约定
- 保留文件末尾是否有换行符的约定

【备份机制】
- 默认在编辑前创建备份文件
- 备份路径：.iwan/backups/<UTC时间戳>__<路径>.bak
- 支持通过 backup=False 跳过备份
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.sandbox import validate_path
from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大编辑器负载限制：2 MB，防止处理过大文件
_MAX_BYTES = 2 * 1024 * 1024
# 备份文件存储目录
_BACKUP_SUBDIR = Path(".iwan") / "backups"


# ═══════════════════════════════════════════════════════════════════════════════
# 共享辅助函数
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_rel_path(path_str: str, operation: str = "access") -> Path:
    """
    验证相对路径 - 安全检查辅助函数

    【参数说明】
    - path_str: str - 路径字符串
    - operation: str - 操作类型（access/read/write/delete）

    【安全检查】
    1. 检查路径中是否包含 ".."（路径遍历攻击）
    2. 验证路径是否在沙箱允许范围内

    【返回值】
    - Path: 验证后的 Path 对象

    【异常处理】
    - PermissionError: 路径包含 ".." 时抛出
    - ValueError: 路径不在沙箱范围内时抛出（由 validate_path 抛出）
    """
    if ".." in Path(path_str).parts:
        raise PermissionError(f"path traversal not allowed: {path_str}")
    validate_path(path_str, operation)
    return Path(path_str)


def _backup_timestamp() -> str:
    """
    生成备份时间戳字符串

    【时间戳格式】
    YYYYMMDDTHHMMSSffffffZ
    - YYYY: 年份
    - MM: 月份
    - DD: 日期
    - T: 时间分隔符
    - HH: 小时
    - MM: 分钟
    - SS: 秒
    - ffffff: 微秒
    - Z: UTC 时区标记

    【示例输出】
    "20240115T103045123456Z"
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _backup_destination(cwd: Path, rel_source: Path) -> Path:
    """
    生成备份文件目标路径

    【参数说明】
    - cwd: Path - 当前工作目录
    - rel_source: Path - 源文件的相对路径

    【路径格式】
    .iwan/backups/<时间戳>__<路径>.bak
    - 路径中的目录分隔符替换为下划线，避免创建子目录

    【示例】
    src/main.py → .iwan/backups/20240115T103045123456Z__src_main.py.bak
    """
    ts = _backup_timestamp()
    # 将路径部件用下划线连接，确保备份文件在同一目录下
    safe = "_".join(rel_source.parts) if rel_source.parts else "root"
    backup_dir = cwd / _BACKUP_SUBDIR
    return backup_dir / f"{ts}__{safe}.bak"


def make_backup(cwd: Path, rel_source: Path) -> Path:
    """
    创建文件备份

    【参数说明】
    - cwd: Path - 当前工作目录
    - rel_source: Path - 源文件的相对路径

    【执行流程】
    1. 验证源文件存在
    2. 生成备份文件路径
    3. 创建备份目录（如果不存在）
    4. 使用 shutil.copy2 复制文件（保留元数据）
    5. 返回备份文件的绝对路径

    【返回值】
    - Path: 备份文件的绝对路径

    【异常处理】
    - FileNotFoundError: 源文件不存在时抛出
    """
    src = cwd / rel_source
    if not src.exists():
        raise FileNotFoundError(src)
    dst = _backup_destination(cwd, rel_source)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # shutil.copy2 保留文件元数据（修改时间、权限等）
    shutil.copy2(src, dst)
    return dst


def split_preserve_endings(text: str) -> list[str]:
    """
    将文本分割为行，保留每行的原始行尾符

    【核心特性】
    - 完美往返：join_preserve_endings(split_preserve_endings(t)) == t
    - 支持三种行尾符：\r\n（Windows）、\n（Unix）、\r（旧Mac）
    - 最后一行如果没有行尾符，保持原样

    【算法流程】
    ┌─────────────────────────────────────────────────────────────┐
    │ 1. 初始化索引 i=0                                           │
    ├─────────────────────────────────────────────────────────────┤
    │ 2. 找到当前行的结束位置 j（遇到 \r 或 \n）                    │
    ├─────────────────────────────────────────────────────────────┤
    │ 3. 处理行尾符：                                              │
    │    ├─ 如果是 \r，检查下一个字符是否是 \n                      │
    │    ├─ 如果是 \r\n，跳过两个字符                              │
    │    └─ 如果是单独的 \r 或 \n，跳过一个字符                     │
    ├─────────────────────────────────────────────────────────────┤
    │ 4. 将 text[i:k] 添加到结果列表                               │
    ├─────────────────────────────────────────────────────────────┤
    │ 5. 更新 i=k，继续循环                                        │
    └─────────────────────────────────────────────────────────────┘

    【参数说明】
    - text: str - 原始文本

    【返回值】
    - list[str]: 每行包含原始行尾符的字符串列表

    【示例】
    - "line1\r\nline2\nline3" → ["line1\r\n", "line2\n", "line3"]
    - "hello" → ["hello"]（无行尾符）
    """
    lines: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        # 找到行尾位置（遇到 \r 或 \n 为止）
        j = i
        while j < n and text[j] not in "\r\n":
            j += 1
        # 处理行尾符
        k = j
        if k < n and text[k] == "\r":
            k += 1  # 跳过 \r
        if k < n and text[k] == "\n":
            k += 1  # 如果前面是 \r，跳过 \n（\r\n 情况）
        lines.append(text[i:k])
        i = k
    return lines


def join_preserve_endings(lines: list[str]) -> str:
    """
    将行列表重新组合为文本，保留原始行尾符

    【参数说明】
    - lines: list[str] - 每行包含原始行尾符的字符串列表

    【返回值】
    - str: 完整文本

    【示例】
    - ["line1\r\n", "line2\n"] → "line1\r\nline2\n"
    """
    return "".join(lines)


def _strip_eol(line: str) -> str:
    """
    移除行尾符

    【处理逻辑】
    - 如果行以 \r\n 结尾，移除最后两个字符
    - 如果行以 \r 或 \n 结尾，移除最后一个字符
    - 否则保持原样

    【参数说明】
    - line: str - 原始行

    【返回值】
    - str: 移除行尾符后的行内容

    【示例】
    - "hello\r\n" → "hello"
    - "world\n" → "world"
    - "no_eol" → "no_eol"
    """
    if line.endswith("\r\n"):
        return line[:-2]
    if line and line[-1] in "\r\n":
        return line[:-1]
    return line


def _has_eol(line: str) -> bool:
    """
    检查行是否有行尾符

    【参数说明】
    - line: str - 要检查的行

    【返回值】
    - bool: 是否以 \r 或 \n 结尾

    【示例】
    - "hello\r\n" → True
    - "world" → False
    """
    return line.endswith(("\r", "\n"))


def _detect_eol(lines: list[str]) -> str:
    """
    检测文件的行尾符类型

    【检测优先级】
    1. \r\n（Windows）：优先检测
    2. \n（Unix）：其次检测
    3. \r（旧Mac）：最后检测
    4. 默认：如果没有检测到任何行尾符，返回 \n

    【参数说明】
    - lines: list[str] - 行列表（由 split_preserve_endings 生成）

    【返回值】
    - str: 检测到的行尾符

    【示例】
    - ["line1\r\n", "line2\r\n"] → "\r\n"
    - ["line1\n", "line2\n"] → "\n"
    - ["no_eol"] → "\n"（默认）
    """
    for ln in lines:
        if ln.endswith("\r\n"):
            return "\r\n"
        if ln.endswith("\n"):
            return "\n"
        if ln.endswith("\r"):
            return "\r"
    return "\n"


def _normalized_lines(raw: str, eol: str, keep_final_eol: bool) -> list[str]:
    """
    将原始文本规范化为统一行尾符的行列表

    【核心功能】
    - 统一行尾符：将所有行转换为指定的 eol
    - 保留语义：非最后一行始终以 eol 结尾
    - 灵活控制：通过 keep_final_eol 控制最后一行的行尾符

    【参数说明】
    - raw: str - 原始文本
    - eol: str - 目标行尾符（如 "\r\n"、"\n"）
    - keep_final_eol: bool - 是否强制最后一行必须有行尾符

    【处理逻辑】
    ┌─────────────────────────────────────────────────────────────┐
    │ 1. 使用 split_preserve_endings 分割为行                      │
    ├─────────────────────────────────────────────────────────────┤
    │ 2. 遍历每行：                                                │
    │    ├─ 移除原始行尾符（_strip_eol）                            │
    │    ├─ 如果不是最后一行：添加目标 eol                           │
    │    └─ 如果是最后一行：                                        │
    │        ├─ keep_final_eol=True → 添加目标 eol                  │
    │        ├─ 原始有 eol → 添加目标 eol                           │
    │        └─ 其他 → 不添加行尾符                                 │
    └─────────────────────────────────────────────────────────────┘

    【返回值】
    - list[str]: 规范化后的行列表

    【示例】
    - raw="line1\r\nline2", eol="\n", keep_final_eol=True
      → ["line1\n", "line2\n"]
    - raw="hello\nworld", eol="\r\n", keep_final_eol=False
      → ["hello\r\n", "world"]
    """
    parts = split_preserve_endings(raw)
    if not parts:
        return []
    out: list[str] = []
    for i, ln in enumerate(parts):
        body = _strip_eol(ln)
        is_last = i == len(parts) - 1
        if is_last:
            # 最后一行：根据配置决定是否添加行尾符
            if keep_final_eol or _has_eol(ln):
                out.append(body + eol)
            else:
                out.append(body)
        else:
            # 非最后一行：始终添加行尾符
            out.append(body + eol)
    return out


def _read_text_safe(path: Path, max_bytes: int = _MAX_BYTES) -> str:
    """
    安全读取文本文件

    【安全机制】
    - 大小限制：超过 max_bytes 的文件会抛出 ValueError
    - 编码处理：使用 UTF-8 解码，遇到非法字符用 replace 替换

    【参数说明】
    - path: Path - 文件路径
    - max_bytes: int - 最大读取字节数（默认 2MB）

    【返回值】
    - str: 文件内容

    【异常处理】
    - ValueError: 文件超过大小限制时抛出
    """
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"file too large to edit: {len(data)} bytes (limit {max_bytes})")
    # errors="replace" 确保即使文件包含非法 UTF-8 字符也能读取
    return data.decode("utf-8", errors="replace")


@dataclass
class _EditorWriteToolMixin:
    metadata: ClassVar[dict[str, str]] = {"category": "write"}

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:  # pragma: no cover
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════════════════
# view_file
# ═══════════════════════════════════════════════════════════════════════════════


class ViewFileParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    start_line: int = Field(default=0, ge=0)
    end_line: int = Field(default=0, ge=0)
    page: int = Field(default=0, ge=0)
    page_size: int = Field(default=80, ge=1, le=500)
    show_line_numbers: bool = True
    show_total_lines: bool = True


class ViewFileTool(BaseTool):
    params_model = ViewFileParams
    name = "view_file"
    description = (
        "Read a text file and print it with 1-based line numbers. "
        "This is a superset of read_file designed for editing workflows: "
        "use ``start_line`` / ``end_line`` (both 1-based, inclusive) to view "
        "a specific range, or ``page`` + ``page_size`` for paged browsing. "
        "Total lines and the requested range are always reported in a header. "
        "Line numbers in the output are 1-based and zero-padded so that they can "
        "be copy-pasted directly into edit_by_lines / insert_at_line / delete_lines. "
        "Path must be relative to the current working directory."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the text file."},
            "start_line": {
                "type": "integer",
                "default": 0,
                "description": "1-based inclusive start. 0 = from beginning.",
            },
            "end_line": {
                "type": "integer",
                "default": 0,
                "description": "1-based inclusive end. 0 = to the end.",
            },
            "page": {
                "type": "integer",
                "default": 0,
                "description": (
                    "0-based page index. If >= 1 the viewer returns page N "
                    "(page_size lines each). 0 = ignore paging, respect range instead."
                ),
            },
            "page_size": {
                "type": "integer",
                "default": 80,
                "description": "Lines per page (1..500).",
            },
            "show_line_numbers": {"type": "boolean", "default": True},
            "show_total_lines": {"type": "boolean", "default": True},
        },
        "required": ["path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = ViewFileParams.model_validate(params)
        path = _validate_rel_path(p.path, "read")
        if not path.exists():
            return ToolResult(
                content=f"view_file: file not found: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )
        if path.is_dir():
            return ToolResult(
                content=f"view_file: path is a directory: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )
        try:
            text = _read_text_safe(path)
        except ValueError as exc:
            return ToolResult(
                content=f"view_file: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        except OSError as exc:
            return ToolResult(
                content=f"view_file failed for {p.path}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )

        lines = split_preserve_endings(text)
        total = len(lines)

        if p.page >= 1:
            # Pagination active: overrides explicit range.
            page_sz = p.page_size
            s = (p.page - 1) * page_sz + 1  # 1-based inclusive start
            e = s + page_sz - 1
            if s > total:
                header = (
                    f"view_file: {p.path} total_lines={total} "
                    f"page={p.page} page_size={page_sz} (empty)\n"
                )
                return ToolResult(content=header)
            e = min(e, total)
        else:
            s = p.start_line if p.start_line >= 1 else 1
            e = p.end_line if p.end_line >= 1 else total
            if s > total or e < 1:
                header = (
                    f"view_file: {p.path} total_lines={total}\n"
                    f"(requested range {p.start_line}-{p.end_line} empty or invalid)\n"
                )
                return ToolResult(content=header)
            if s > e:
                s, e = e, s
            s = max(s, 1)
            e = min(e, total)

        rendered: list[str] = []
        header_bits = [f"view_file: {p.path}"]
        if p.show_total_lines:
            header_bits.append(f"total_lines={total}")
        header_bits.append(f"showing={s}-{e}")
        if p.page >= 1:
            header_bits.append(f"page={p.page}/{1 + (total - 1) // p.page_size}")
        rendered.append(" ".join(header_bits))

        pad = max(3, len(str(total)))
        for idx in range(s, e + 1):
            body = _strip_eol(lines[idx - 1])
            if p.show_line_numbers:
                rendered.append(f"{idx:>{pad}} | {body}")
            else:
                rendered.append(body)
        return ToolResult(content="\n".join(rendered))


# ═══════════════════════════════════════════════════════════════════════════════
# edit_by_lines  (core cursor-edit primitive)
# ═══════════════════════════════════════════════════════════════════════════════


class EditByLinesParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    start_line: int = Field(gt=0)
    end_line: int = Field(gt=0)
    replacement: str
    backup: bool = True


class EditByLinesTool(BaseTool, _EditorWriteToolMixin):
    params_model = EditByLinesParams
    name = "edit_by_lines"
    description = (
        "Replace the inclusive 1-based line range [start_line..end_line] with the "
        "provided ``replacement`` text. "
        "This is the 'select lines N..M then type replacement' editor primitive. "
        "Line numbers can be copied directly from view_file output. "
        "A backup of the original file is written to .iwan/backups/<ts>__<path>.bak "
        "before any modification unless backup=False. "
        "Single-line edits are done by setting start_line == end_line. "
        "An empty replacement deletes the range (see also delete_lines)."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {
                "type": "integer",
                "description": "1-based inclusive start of the range being replaced.",
            },
            "end_line": {
                "type": "integer",
                "description": "1-based inclusive end of the range being replaced.",
            },
            "replacement": {
                "type": "string",
                "description": (
                    "New text placed between the kept lines before start_line and "
                    "after end_line. Pass '' to simply delete the range."
                ),
            },
            "backup": {"type": "boolean", "default": True},
        },
        "required": ["path", "start_line", "end_line", "replacement"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        p = EditByLinesParams.model_validate(params)
        target = _validate_rel_path(p.path, "write")
        out = [str(target)]
        if p.backup and target.exists():
            out.append(str(_backup_destination(Path.cwd(), target)))
        return out

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = EditByLinesParams.model_validate(params)
        path = _validate_rel_path(p.path, "write")
        if not path.exists():
            return ToolResult(
                content=f"edit_by_lines: file not found: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )
        try:
            text = _read_text_safe(path)
        except (OSError, ValueError) as exc:
            return ToolResult(
                content=f"edit_by_lines: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        lines = split_preserve_endings(text)
        total = len(lines)
        if p.start_line > total or p.end_line > total:
            return ToolResult(
                content=(
                    f"edit_by_lines: line range out of bounds: "
                    f"{p.start_line}-{p.end_line} total_lines={total}"
                ),
                is_error=True,
                error_type="runtime_error",
            )
        if p.start_line > p.end_line:
            return ToolResult(
                content=(
                    f"edit_by_lines: start_line={p.start_line} > end_line={p.end_line}"
                ),
                is_error=True,
                error_type="schema_error",
            )

        s0 = p.start_line - 1  # inclusive 0-based
        e0 = p.end_line - 1    # inclusive 0-based

        file_eol = _detect_eol(lines)
        last_had_eol = _has_eol(lines[-1]) if lines else False

        # 3 cases for replacement EOL normalization:
        #   - The last line of the replaced BLOCK: inherit EOL convention if
        #     replacement itself has any newline, but match *file's* EOL.
        #   - If e0 is last line → replacement is last segment → respect original
        #     file terminal EOL contract.
        is_removing_last_line = e0 == total - 1
        repl = _normalized_lines(
            p.replacement,
            eol=file_eol,
            keep_final_eol=(not is_removing_last_line) or last_had_eol,
        )

        backup_path: Path | None = None
        if p.backup:
            try:
                backup_path = make_backup(Path.cwd(), path)
            except (OSError, FileNotFoundError) as exc:
                return ToolResult(
                    content=f"edit_by_lines: backup failed for {p.path}: {exc}",
                    is_error=True,
                    error_type="runtime_error",
                )

        new_lines = lines[:s0] + repl + lines[e0 + 1 :]

        # Guarantee the final line EOL behavior of the whole file still matches
        # the 'last_had_eol' contract when it didn't change... unless the user
        # is replacing the terminal section with empty content (in which case we
        # keep whatever the natural concatenation produced).
        if new_lines and new_lines[-1]:
            # If original ended without EOL and we're not appending a final newline
            # via the replacement itself, strip terminal EOL if present.
            if (
                not is_removing_last_line
                and not last_had_eol
                and _has_eol(new_lines[-1])
            ):
                new_lines[-1] = _strip_eol(new_lines[-1])
            if is_removing_last_line and not last_had_eol:
                # Replacement's last line may have EOL from normalization; strip
                # if original file ended without one.
                if _has_eol(new_lines[-1]):
                    new_lines[-1] = _strip_eol(new_lines[-1])

        new_text = join_preserve_endings(new_lines)
        try:
            # Write raw bytes to avoid OS newline translation (e.g. Windows
            # would double '\n' → '\r\r\n' on top of our already-CRLF data).
            path.write_bytes(new_text.encode("utf-8"))
        except OSError as exc:
            return ToolResult(
                content=f"edit_by_lines: write failed for {p.path}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        removed = e0 - s0 + 1
        inserted = len(repl)
        msg = (
            f"edit_by_lines: replaced lines {p.start_line}-{p.end_line} "
            f"(removed {removed}, inserted {inserted}) in {p.path}"
        )
        if backup_path is not None:
            msg += f"; backup={backup_path.relative_to(Path.cwd()).as_posix()}"
        return ToolResult(content=msg)


# ═══════════════════════════════════════════════════════════════════════════════
# edit_by_search  (exact-string search replace with disambiguation safety)
# ═══════════════════════════════════════════════════════════════════════════════


class EditBySearchParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    old: str
    new: str
    occurrence: int = Field(default=0, ge=0)
    force_multiple: bool = False
    backup: bool = True


class EditBySearchTool(BaseTool, _EditorWriteToolMixin):
    params_model = EditBySearchParams
    name = "edit_by_search"
    description = (
        "Replace exact literal occurrences of substring ``old`` with ``new``. "
        "Ambiguity guard: if multiple matches are found the command aborts with a "
        "list of every matching line number — **unless** ``occurrence=N`` selects "
        "the 1-based Nth match OR ``force_multiple=True`` explicitly overrides the "
        "safety. This prevents an LLM from accidentally rewriting unrelated lines. "
        "A pre-edit backup is made under .iwan/backups/ by default."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "Exact substring to locate."},
            "new": {"type": "string", "description": "Replacement substring."},
            "occurrence": {
                "type": "integer",
                "default": 0,
                "description": (
                    "0 = replace all (safe only on single match; errors on >1). "
                    ">=1 selects exactly the 1-based Nth match to replace."
                ),
            },
            "force_multiple": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When True, allow bulk replacement of multiple matches. "
                    "Has no effect if ``occurrence`` already pins a single match."
                ),
            },
            "backup": {"type": "boolean", "default": True},
        },
        "required": ["path", "old", "new"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        p = EditBySearchParams.model_validate(params)
        target = _validate_rel_path(p.path, "write")
        out = [str(target)]
        if p.backup and target.exists():
            out.append(str(_backup_destination(Path.cwd(), target)))
        return out

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = EditBySearchParams.model_validate(params)
        path = _validate_rel_path(p.path, "write")
        if not path.exists():
            return ToolResult(
                content=f"edit_by_search: file not found: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )
        try:
            text = _read_text_safe(path)
        except (OSError, ValueError) as exc:
            return ToolResult(
                content=f"edit_by_search: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        if not p.old:
            return ToolResult(
                content="edit_by_search: 'old' must be non-empty",
                is_error=True,
                error_type="schema_error",
            )
        matches: list[tuple[int, int, int]] = []  # (occurrence 1-based, char offset, line 1-based)
        cursor = 0
        while True:
            found = text.find(p.old, cursor)
            if found == -1:
                break
            occ = len(matches) + 1
            lno = 1 + text.count("\n", 0, found)
            matches.append((occ, found, lno))
            cursor = found + len(p.old)
        if not matches:
            return ToolResult(
                content=f"edit_by_search: no occurrence found in {p.path}",
                is_error=True,
                error_type="runtime_error",
            )
        # Ambiguity handling
        if len(matches) > 1 and p.occurrence == 0 and not p.force_multiple:
            locs = " ".join(f"L{lno}" for _o, _s, lno in matches)
            return ToolResult(
                content=(
                    f"edit_by_search: ambiguous — {len(matches)} matches in "
                    f"{p.path} at lines [{locs}]. Pin occurrence=N (1-based) or "
                    "pass force_multiple=True to rewrite all matches."
                ),
                is_error=True,
                error_type="runtime_error",
            )
        if p.occurrence > len(matches):
            return ToolResult(
                content=(
                    f"edit_by_search: occurrence={p.occurrence} out of range "
                    f"({len(matches)} matches found)"
                ),
                is_error=True,
                error_type="runtime_error",
            )

        # Build result
        if p.occurrence == 0:
            new_text = text.replace(p.old, p.new)
            replaced = len(matches)
        else:
            _occ, spos, _lno = matches[p.occurrence - 1]
            new_text = text[:spos] + p.new + text[spos + len(p.old):]
            replaced = 1

        backup_path: Path | None = None
        if p.backup:
            try:
                backup_path = make_backup(Path.cwd(), path)
            except (OSError, FileNotFoundError) as exc:
                return ToolResult(
                    content=f"edit_by_search: backup failed {p.path}: {exc}",
                    is_error=True,
                    error_type="runtime_error",
                )
        try:
            path.write_bytes(new_text.encode("utf-8"))
        except OSError as exc:
            return ToolResult(
                content=f"edit_by_search: write failed {p.path}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        msg = f"edit_by_search: replaced {replaced} occurrence(s) in {p.path}"
        if backup_path is not None:
            msg += f"; backup={backup_path.relative_to(Path.cwd()).as_posix()}"
        return ToolResult(content=msg)


# ═══════════════════════════════════════════════════════════════════════════════
# insert_at_line
# ═══════════════════════════════════════════════════════════════════════════════


class InsertAtLineParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    line: int = Field(gt=0)
    text: str
    position: Literal["before", "after"] = "before"
    backup: bool = True


class InsertAtLineTool(BaseTool, _EditorWriteToolMixin):
    params_model = InsertAtLineParams
    name = "insert_at_line"
    description = (
        "Insert text before or after a 1-based line number without deleting any "
        "content. Equivalent to placing the cursor at the specified line and "
        "pressing Enter then pasting. "
        "position='before' pushes the target line down; position='after' inserts "
        "between the line and the line that originally followed. "
        "A pre-edit backup is made to .iwan/backups/ by default."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "line": {
                "type": "integer",
                "description": "1-based line number that anchors the insertion.",
            },
            "text": {"type": "string", "description": "Text to insert."},
            "position": {
                "type": "string",
                "enum": ["before", "after"],
                "default": "before",
            },
            "backup": {"type": "boolean", "default": True},
        },
        "required": ["path", "line", "text"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        p = InsertAtLineParams.model_validate(params)
        target = _validate_rel_path(p.path, "write")
        out = [str(target)]
        if p.backup and target.exists():
            out.append(str(_backup_destination(Path.cwd(), target)))
        return out

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = InsertAtLineParams.model_validate(params)
        path = _validate_rel_path(p.path, "write")
        if not path.exists():
            return ToolResult(
                content=f"insert_at_line: file not found: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )
        try:
            text = _read_text_safe(path)
        except (OSError, ValueError) as exc:
            return ToolResult(
                content=f"insert_at_line: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        lines = split_preserve_endings(text)
        total = len(lines)
        if p.line > total:
            return ToolResult(
                content=(
                    f"insert_at_line: line={p.line} > total_lines={total}"
                ),
                is_error=True,
                error_type="runtime_error",
            )

        file_eol = _detect_eol(lines)
        # Inserted block should *always* terminate with a newline in the file
        # interior (neither before nor after is ever 'the terminal line' in the
        # final concatenation — only the true last line of file determines final EOL).
        repl_eol = True
        if (p.position == "before" and p.line == total) or (
            p.position == "after" and p.line == total
        ):
            # Terminal block: inherit original file's terminal EOL contract.
            repl_eol = _has_eol(lines[-1]) if lines else True
        normalized = _normalized_lines(p.text, eol=file_eol, keep_final_eol=repl_eol)

        backup_path: Path | None = None
        if p.backup:
            try:
                backup_path = make_backup(Path.cwd(), path)
            except (OSError, FileNotFoundError) as exc:
                return ToolResult(
                    content=f"insert_at_line: backup failed {p.path}: {exc}",
                    is_error=True,
                    error_type="runtime_error",
                )

        if p.position == "before":
            idx = p.line - 1
        else:  # after
            idx = p.line

        # Edge: when inserting right after a line that has no trailing EOL
        # (typically the terminal line of a no-EOL file), the preceding segment
        # must be glued to the inserted block with *one* EOL separator —
        # otherwise "b" + "mid" would concatenate to "bmid".
        if idx > 0 and idx <= len(lines) and not _has_eol(lines[idx - 1]):
            lines = list(lines)
            lines[idx - 1] = lines[idx - 1] + file_eol

        new_lines = lines[:idx] + normalized + lines[idx:]

        # Preserve file's trailing-newline contract for the final terminal line.
        if lines and not _has_eol(lines[-1]) and new_lines and _has_eol(new_lines[-1]):
            new_lines[-1] = _strip_eol(new_lines[-1])

        new_text = join_preserve_endings(new_lines)
        try:
            path.write_bytes(new_text.encode("utf-8"))
        except OSError as exc:
            return ToolResult(
                content=f"insert_at_line: write failed {p.path}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        msg = (
            f"insert_at_line: inserted {len(normalized)} lines "
            f"position={p.position} line={p.line} in {p.path}"
        )
        if backup_path is not None:
            msg += f"; backup={backup_path.relative_to(Path.cwd()).as_posix()}"
        return ToolResult(content=msg)


# ═══════════════════════════════════════════════════════════════════════════════
# delete_lines  (thin convenience wrapper around edit_by_lines)
# ═══════════════════════════════════════════════════════════════════════════════


class DeleteLinesParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    start_line: int = Field(gt=0)
    end_line: int = Field(gt=0)
    backup: bool = True


class DeleteLinesTool(BaseTool, _EditorWriteToolMixin):
    params_model = DeleteLinesParams
    name = "delete_lines"
    description = (
        "Delete the inclusive 1-based line range [start_line..end_line]. "
        "Convenience wrapper over edit_by_lines with an empty replacement. "
        "Default backup to .iwan/backups/."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "description": "1-based inclusive start."},
            "end_line": {"type": "integer", "description": "1-based inclusive end."},
            "backup": {"type": "boolean", "default": True},
        },
        "required": ["path", "start_line", "end_line"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        p = DeleteLinesParams.model_validate(params)
        target = _validate_rel_path(p.path, "delete")
        out = [str(target)]
        if p.backup and target.exists():
            out.append(str(_backup_destination(Path.cwd(), target)))
        return out

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = DeleteLinesParams.model_validate(params)
        # Validate path early (raises PermissionError on traversal matching convention)
        _validate_rel_path(p.path, "delete")
        edit_result = await EditByLinesTool().invoke(
            {
                "path": p.path,
                "start_line": p.start_line,
                "end_line": p.end_line,
                "replacement": "",
                "backup": p.backup,
            }
        )
        if edit_result.is_error:
            return edit_result
        return ToolResult(
            content=edit_result.content.replace(
                "edit_by_lines: replaced", "delete_lines: deleted", 1
            )
        )
