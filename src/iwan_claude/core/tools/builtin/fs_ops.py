from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from iwan_claude.core.tools.base import BaseTool, ToolResult


def _validate_rel_path(path_str: str) -> Path:
    if ".." in Path(path_str).parts:
        raise PermissionError(f"path traversal not allowed: {path_str}")
    return Path(path_str)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Shared helpers for metadata / estimate_affected_paths
# ---------------------------------------------------------------------------

@dataclass
class _WriteToolMixin:
    """Write-category tools should inherit this mixin (after BaseTool) so that
    the snapshot manager can discover affected paths pre-invocation.

    We put the attributes on the concrete classes as ClassVar to avoid MRO
    complications with the ABC BaseTool; the mixin just declares the expected
    interface and provides a default estimate helper for single-path tools.
    """

    metadata: ClassVar[dict[str, str]] = {"category": "write"}

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:  # pragma: no cover - trivial
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1. delete_file
# ---------------------------------------------------------------------------


class DeleteFileParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    force: bool = False
    recursive: bool = False


class DeleteFileTool(BaseTool, _WriteToolMixin):
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
        p = DeleteFileParams.model_validate(params)
        root = _validate_rel_path(p.path)
        if not p.recursive:
            return [str(root)]
        collected: list[str] = []
        if root.is_file():
            return [str(root)]
        if root.is_dir():
            for dp, _dn, fn in os.walk(root):
                for name in fn:
                    collected.append(str(Path(dp) / name))
                collected.append(dp)
        return collected or [str(root)]

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = DeleteFileParams.model_validate(params)
        path = _validate_rel_path(p.path)
        if not path.exists():
            return ToolResult(
                content=f"delete target does not exist: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                return ToolResult(content=f"deleted file: {p.path}")
            if path.is_dir():
                if not p.recursive and any(path.iterdir()):
                    if not p.force:
                        return ToolResult(
                            content=(
                                f"directory not empty: {p.path}. "
                                "Set recursive=True (and force=True if needed) to delete it."
                            ),
                            is_error=True,
                            error_type="runtime_error",
                        )
                if p.recursive or p.force:
                    shutil.rmtree(path)
                else:
                    path.rmdir()
                return ToolResult(content=f"deleted directory: {p.path} (recursive={p.recursive})")
            return ToolResult(
                content=f"unsupported file type for delete: {p.path}",
                is_error=True,
                error_type="runtime_error",
            )
        except PermissionError as exc:
            return ToolResult(
                content=f"permission denied deleting {p.path}: {exc}",
                is_error=True,
                error_type="permission_denied",
            )
        except OSError as exc:
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
        src = _validate_rel_path(p.src)
        dst = _validate_rel_path(p.dst)
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
        src = _validate_rel_path(p.src)
        dst = _validate_rel_path(p.dst)
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
        dst = _validate_rel_path(p.dst)
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
        src = _validate_rel_path(p.src)
        dst = _validate_rel_path(p.dst)
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
        root = _validate_rel_path(p.path)
        if not p.parents:
            return [str(root)]
        parts: list[str] = []
        for i in range(1, len(root.parts) + 1):
            parts.append(str(Path(*root.parts[:i])))
        return parts

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = MkdirParams.model_validate(params)
        path = _validate_rel_path(p.path)
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
        path = _validate_rel_path(p.path)
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
        path = _validate_rel_path(p.path)
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
