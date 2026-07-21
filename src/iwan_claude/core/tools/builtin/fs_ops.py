"""
文件系统操作工具 - 提供删除、重命名、复制、创建目录等操作

【学习要点】
1. 安全检查：禁止路径遍历（..），验证沙箱权限
2. 原子操作：提供 overwrite 和 recursive 参数控制操作行为
3. 元数据管理：estimate_affected_paths 方法用于预检查受影响的路径
4. Mixin 模式：使用 _WriteToolMixin 标记写入类工具

【工具分类】
- DeleteFileTool：删除文件或目录
- RenameFileTool：重命名或移动文件/目录
- CopyFileTool：复制文件或目录
- MkdirTool：创建目录
- FileStatTool：获取文件元数据
- FileExistsTool：检查文件是否存在

【安全机制】
- 路径遍历检测：禁止 ".." 出现在路径中
- 沙箱验证：validate_path 确保操作在允许的路径范围内
- 权限检查：自动处理 PermissionError 和 OSError
"""
from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from iwan_claude.core.sandbox import validate_path
from iwan_claude.core.tools.base import BaseTool, ToolResult


def _validate_rel_path(path_str: str, operation: str = "access") -> Path:
    """
    验证相对路径 - 安全检查辅助函数

    【参数说明】
    - path_str: str - 路径字符串
    - operation: str - 操作类型（access/read/write/delete/rename/copy/mkdir）

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


def _now_iso() -> str:
    """
    获取当前 UTC 时间的 ISO 格式字符串

    【用途】
    用于生成时间戳，如备份文件名中的时间戳

    【示例输出】
    "2024-01-15T10:30:45.123456+00:00"
    """
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Shared helpers for metadata / estimate_affected_paths
# ---------------------------------------------------------------------------

@dataclass
class _WriteToolMixin:
    """
    写入工具 Mixin - 为写入类工具添加元数据和接口

    【学习要点】
    1. Mixin 模式：通过多重继承为类添加功能
    2. ClassVar：类变量，不参与实例化
    3. 接口约定：定义 estimate_affected_paths 方法，由子类实现

    【用途】
    标记工具属于 "write" 类别，便于权限管理和快照管理器预检查

    【设计说明】
    将 metadata 放在具体类中作为 ClassVar，避免与 ABC BaseTool 的 MRO 冲突；
    Mixin 只声明预期的接口并提供默认的估算辅助函数。

    【子类实现要求】
    必须实现 estimate_affected_paths 方法，返回受影响的路径列表
    """

    metadata: ClassVar[dict[str, str]] = {"category": "write"}

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        """
        估算受影响的路径 - 用于权限预检查和快照管理

        【返回值】
        - list[str]: 可能被修改的路径列表
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1. delete_file
# ---------------------------------------------------------------------------


class DeleteFileParams(BaseModel):
    """
    删除文件参数模型

    【字段说明】
    - path: str - 要删除的文件或目录路径，必须是相对路径
    - force: bool - 是否跳过非空目录的安全检查（默认 False）
    - recursive: bool - 是否递归删除目录（默认 False）

    【参数组合说明】
    - 普通文件：直接删除，不需要 recursive 或 force
    - 空目录：直接删除，不需要 recursive 或 force
    - 非空目录：需要 recursive=True 才能删除
    - 强制删除：force=True 可以跳过安全提示，但仍需要用户通过权限管理器确认
    """
    model_config = ConfigDict(extra="ignore")
    path: str
    force: bool = False
    recursive: bool = False


class DeleteFileTool(BaseTool, _WriteToolMixin):
    """
    删除文件工具 - 删除指定的文件或目录

    【学习要点】
    1. 安全检查：非空目录需要 recursive=True 才能删除
    2. 权限管理：删除操作需要用户确认（通过权限管理器）
    3. 递归删除：使用 shutil.rmtree 删除目录及其所有内容
    4. 符号链接：使用 path.is_symlink() 检测符号链接

    【使用示例】
    ```python
    tool = DeleteFileTool()
    
    # 删除文件
    result = await tool.invoke({"path": "tmp.txt"})
    
    # 删除空目录
    result = await tool.invoke({"path": "empty_dir"})
    
    # 删除非空目录（递归）
    result = await tool.invoke({"path": "my_dir", "recursive": True})
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 验证路径安全性（路径遍历、沙箱范围）
    3. 检查目标是否存在
    4. 根据目标类型执行删除
       - 文件/符号链接：直接删除（path.unlink()）
       - 空目录：直接删除（path.rmdir()）
       - 非空目录：需要 recursive=True，使用 shutil.rmtree()
    5. 返回结果或错误
    """
    params_model = DeleteFileParams
    name = "delete_file"
    description = (
        "Delete a file or directory. Use `force=True` to skip safety prompts for "
        "non-empty directories (always require user approval via permission manager). "
        "Use `recursive=True` to delete directories and their contents; otherwise only "
        "empty directories and single files can be removed. Path must be relative to CWD."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to file or directory."},
            "force": {
                "type": "boolean",
                "default": False,
                "description": "If true, bypasses the 'non-empty directory' safety check.",
            },
            "recursive": {
                "type": "boolean",
                "default": False,
                "description": "If true, delete directories recursively (like rm -r).",
            },
        },
        "required": ["path"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        """
        估算受影响的路径 - 用于权限预检查

        【参数说明】
        - params: dict - 工具调用参数

        【返回值】
        - list[str]: 受影响的路径列表
          - 如果不是递归删除，只返回目标路径
          - 如果是递归删除目录，返回所有子文件和子目录的路径

        【设计用途】
        快照管理器可以在操作前预检查所有受影响的路径，便于备份和恢复
        """
        p = DeleteFileParams.model_validate(params)
        root = _validate_rel_path(p.path, "delete")

        # 如果不是递归删除，只返回目标路径
        if not p.recursive:
            return [str(root)]

        # 递归收集所有受影响的路径
        collected: list[str] = []
        if root.is_file():
            return [str(root)]
        if root.is_dir():
            for dp, _dn, fn in os.walk(root):
                # 添加所有文件
                for name in fn:
                    collected.append(str(Path(dp) / name))
                # 添加目录本身
                collected.append(dp)

        return collected or [str(root)]

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行删除操作

        【执行流程详解】
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 验证输入参数（Pydantic）                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 2. 验证路径安全性（路径遍历、沙箱范围）                        │
        ├─────────────────────────────────────────────────────────────┤
        │ 3. 检查目标是否存在                                          │
        │    └─ 不存在 → 返回错误                                       │
        ├─────────────────────────────────────────────────────────────┤
        │ 4. 根据目标类型执行删除                                       │
        │    ├─ 文件/符号链接 → path.unlink()                         │
        │    ├─ 空目录 → path.rmdir()                                 │
        │    └─ 非空目录 → shutil.rmtree()（需要 recursive=True）       │
        ├─────────────────────────────────────────────────────────────┤
        │ 5. 处理异常（权限错误、文件系统错误）                           │
        ├─────────────────────────────────────────────────────────────┤
        │ 6. 返回成功结果                                              │
        └─────────────────────────────────────────────────────────────┘
        """
        # 1. 验证输入参数
        p = DeleteFileParams.model_validate(params)

        # 2. 验证路径安全性
        path = _validate_rel_path(p.path, "delete")

        # 3. 检查目标是否存在
        if not path.exists():
            return ToolResult(
                content=f"delete target does not exist: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )

        try:
            # 4. 根据目标类型执行删除
            if path.is_file() or path.is_symlink():
                # 文件或符号链接：直接删除
                path.unlink()
                return ToolResult(content=f"deleted file: {p.path}")

            if path.is_dir():
                # 目录：检查是否为空
                if not p.recursive and any(path.iterdir()):
                    # 非空目录且没有指定递归删除
                    if not p.force:
                        return ToolResult(
                            content=(
                                f"directory not empty: {p.path}. "
                                "Set recursive=True (and force=True if needed) to delete it."
                            ),
                            is_error=True,
                            error_type="runtime_error",
                        )

                # 执行删除
                if p.recursive or p.force:
                    # 递归删除目录及其所有内容
                    shutil.rmtree(path)
                else:
                    # 删除空目录
                    path.rmdir()

                return ToolResult(content=f"deleted directory: {p.path} (recursive={p.recursive})")

            # 未知类型
            return ToolResult(
                content=f"unsupported file type for delete: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )

        except PermissionError as exc:
            # 权限错误
            return ToolResult(
                content=f"permission denied deleting {p.path}: {exc}",
                is_error=True,
                error_type="permission_denied",
            )
        except OSError as exc:
            # 文件系统错误
            return ToolResult(
                content=f"failed to delete {p.path}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )


# ---------------------------------------------------------------------------
# 2. rename_file (also used for move)
# ---------------------------------------------------------------------------


class RenameFileParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    src: str
    dst: str
    overwrite: bool = False


class RenameFileTool(BaseTool, _WriteToolMixin):
    params_model = RenameFileParams
    name = "rename_file"
    description = (
        "Rename or move a file/directory from src path to dst path. "
        "By default refuses to overwrite the destination if it already exists; "
        "set overwrite=True to replace it. Paths must be relative to CWD."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "Source relative path."},
            "dst": {"type": "string", "description": "Destination relative path."},
            "overwrite": {
                "type": "boolean",
                "default": False,
                "description": "If true and dst exists, it is replaced.",
            },
        },
        "required": ["src", "dst"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        p = RenameFileParams.model_validate(params)
        src = _validate_rel_path(p.src, "rename")
        dst = _validate_rel_path(p.dst, "rename")
        affected: list[str] = [str(src)]
        if dst.exists() and p.overwrite:
            if dst.is_dir():
                for dp, _dn, fn in os.walk(dst):
                    for name in fn:
                        affected.append(str(Path(dp) / name))
                    affected.append(dp)
            else:
                affected.append(str(dst))
        affected.append(str(dst))
        return affected

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = RenameFileParams.model_validate(params)
        src = _validate_rel_path(p.src, "rename")
        dst = _validate_rel_path(p.dst, "rename")
        if not src.exists():
            return ToolResult(
                content=f"rename src does not exist: {p.src}",
                is_error=True,
                error_type="runtime_error",
            )
        if dst.exists() and not p.overwrite:
            return ToolResult(
                content=(
                    f"destination already exists: {p.dst}. Set overwrite=True to replace."
                ),
                is_error=True,
                error_type="runtime_error",
            )
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and p.overwrite:
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            shutil.move(str(src), str(dst))
            return ToolResult(content=f"renamed/moved {p.src} → {p.dst} (overwrite={p.overwrite})")
        except OSError as exc:
            return ToolResult(
                content=f"failed to rename {p.src} → {p.dst}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )


# ---------------------------------------------------------------------------
# 3. copy_file
# ---------------------------------------------------------------------------


class CopyFileParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    src: str
    dst: str
    overwrite: bool = False
    recursive: bool = False


class CopyFileTool(BaseTool, _WriteToolMixin):
    params_model = CopyFileParams
    name = "copy_file"
    description = (
        "Copy a file or directory. By default refuses to overwrite existing destinations; "
        "set overwrite=True to replace. Use recursive=True to copy directories. "
        "Paths must be relative to CWD."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "Source relative path."},
            "dst": {"type": "string", "description": "Destination relative path."},
            "overwrite": {
                "type": "boolean",
                "default": False,
                "description": "Overwrite dst if it exists.",
            },
            "recursive": {
                "type": "boolean",
                "default": False,
                "description": "Recursively copy directories (cp -r).",
            },
        },
        "required": ["src", "dst"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        p = CopyFileParams.model_validate(params)
        _validate_rel_path(p.src, "copy")
        dst = _validate_rel_path(p.dst, "copy")
        affected: list[str] = []
        if dst.exists() and p.overwrite:
            if dst.is_dir():
                for dp, _dn, fn in os.walk(dst):
                    for name in fn:
                        affected.append(str(Path(dp) / name))
                    affected.append(dp)
            else:
                affected.append(str(dst))
        affected.append(str(dst))
        return affected

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = CopyFileParams.model_validate(params)
        src = _validate_rel_path(p.src, "copy")
        dst = _validate_rel_path(p.dst, "copy")
        if not src.exists():
            return ToolResult(
                content=f"copy src does not exist: {p.src}",
                is_error=True,
                error_type="runtime_error",
            )
        if dst.exists() and not p.overwrite:
            return ToolResult(
                content=(
                    f"destination already exists: {p.dst}. Set overwrite=True to replace."
                ),
                is_error=True,
                error_type="runtime_error",
            )
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_file():
                shutil.copy2(src, dst)
                return ToolResult(content=f"copied file: {p.src} → {p.dst}")
            if src.is_dir():
                if not p.recursive:
                    return ToolResult(
                        content=(
                            f"src is a directory: {p.src}. Set recursive=True to copy."
                        ),
                        is_error=True,
                        error_type="runtime_error",
                    )
                if dst.exists() and dst.is_dir() and p.overwrite:
                    shutil.rmtree(dst)
                shutil.copytree(src, dst, dirs_exist_ok=p.overwrite)
                return ToolResult(
                    content=f"copied directory (recursive): {p.src} → {p.dst}"
                )
            return ToolResult(
                content=f"unsupported source type for copy: {p.src}",
                is_error=True,
                error_type="runtime_error",
            )
        except OSError as exc:
            return ToolResult(
                content=f"failed to copy {p.src} → {p.dst}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )


# ---------------------------------------------------------------------------
# 4. mkdir
# ---------------------------------------------------------------------------


class MkdirParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    parents: bool = True
    exist_ok: bool = True


class MkdirTool(BaseTool, _WriteToolMixin):
    params_model = MkdirParams
    name = "mkdir"
    description = (
        "Create a directory. By default creates missing parent directories (mkdir -p) "
        "and does not error if the directory already exists. Path must be relative to CWD."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative directory path to create."},
            "parents": {
                "type": "boolean",
                "default": True,
                "description": "Create parent directories as needed (mkdir -p).",
            },
            "exist_ok": {
                "type": "boolean",
                "default": True,
                "description": "If false, error when directory already exists.",
            },
        },
        "required": ["path"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        p = MkdirParams.model_validate(params)
        root = _validate_rel_path(p.path, "mkdir")
        if not p.parents:
            return [str(root)]
        parts: list[str] = []
        for i in range(1, len(root.parts) + 1):
            parts.append(str(Path(*root.parts[:i])))
        return parts

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = MkdirParams.model_validate(params)
        path = _validate_rel_path(p.path, "mkdir")
        try:
            path.mkdir(parents=p.parents, exist_ok=p.exist_ok)
            return ToolResult(content=f"mkdir {p.path} (parents={p.parents}, exist_ok={p.exist_ok})")
        except FileExistsError:
            return ToolResult(
                content=f"directory already exists: {p.path} (exist_ok=False)",
                is_error=True,
                error_type="runtime_error",
            )
        except OSError as exc:
            return ToolResult(
                content=f"mkdir failed {p.path}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )


# ---------------------------------------------------------------------------
# 5. file_stat
# ---------------------------------------------------------------------------


class FileStatParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str


class FileStatTool(BaseTool):
    params_model = FileStatParams
    name = "file_stat"
    description = (
        "Return metadata for a file or directory: size, mtime/ctime/atime, "
        "permissions (octal), line count (for text files), extension, file/dir flag. "
        "Path must be relative to CWD."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to inspect."}
        },
        "required": ["path"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        return []

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = FileStatParams.model_validate(params)
        path = _validate_rel_path(p.path, "read")
        if not path.exists():
            return ToolResult(
                content=f"file does not exist: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )
        try:
            st = path.stat()
            info: dict[str, Any] = {
                "path": p.path,
                "exists": True,
                "type": "directory" if path.is_dir() else "file",
                "size_bytes": st.st_size,
                "size_human": _human_size(st.st_size),
                "permissions_octal": oct(stat.S_IMODE(st.st_mode)),
                "mtime_iso": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
                "ctime_iso": datetime.fromtimestamp(st.st_ctime, UTC).isoformat(),
                "atime_iso": datetime.fromtimestamp(st.st_atime, UTC).isoformat(),
                "extension": path.suffix,
                "stem": path.stem,
                "name": path.name,
            }
            if path.is_file():
                try:
                    with path.open("r", encoding="utf-8", errors="ignore") as fh:
                        info["line_count"] = sum(1 for _ in fh)
                except OSError:
                    info["line_count"] = None
            if path.is_dir():
                try:
                    info["child_count"] = len(list(path.iterdir()))
                except OSError:
                    info["child_count"] = None
            lines = [f"--- stat for {p.path} ---"]
            for k, v in info.items():
                lines.append(f"{k}: {v}")
            return ToolResult(content="\n".join(lines))
        except OSError as exc:
            return ToolResult(
                content=f"stat failed for {p.path}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )


# ---------------------------------------------------------------------------
# 6. file_exists (lightweight boolean answer)
# ---------------------------------------------------------------------------


class FileExistsParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str


class FileExistsTool(BaseTool):
    params_model = FileExistsParams
    name = "file_exists"
    description = (
        "Quick boolean check for whether a file or directory exists. "
        "Use this instead of read_file/delete_file to avoid FileNotFoundError in "
        "conditional logic. Path must be relative to CWD."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to check."}
        },
        "required": ["path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = FileExistsParams.model_validate(params)
        path = _validate_rel_path(p.path, "read")
        exists = path.exists()
        kind = ""
        if exists:
            kind = " (directory)" if path.is_dir() else " (file)"
        return ToolResult(content=f"{p.path}: exists={exists}{kind}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _human_size(num_bytes: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < step:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= step
    return f"{num_bytes:.2f} PB"
