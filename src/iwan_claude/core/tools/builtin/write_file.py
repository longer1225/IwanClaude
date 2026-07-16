from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from iwan_claude.core.sandbox import check_file_size, check_total_quota, validate_path
from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
_WRITE_MODES = ("overwrite", "append", "fail_if_exists")


def _validate_rel_path(path_str: str, operation: str = "write") -> Path:
    if ".." in Path(path_str).parts:
        raise PermissionError(f"path traversal not allowed: {path_str}")
    validate_path(path_str, operation)
    return Path(path_str)


@dataclass
class _WriteToolMixin:
    metadata: ClassVar[dict[str, str]] = {"category": "write"}

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:  # pragma: no cover
        raise NotImplementedError


class WriteFileParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    content: str
    mode: Literal["overwrite", "append", "fail_if_exists"] = "overwrite"
    backup: bool = True


class WriteFileTool(BaseTool, _WriteToolMixin):
    params_model = WriteFileParams
    name = "write_file"
    description = (
        "Write text content to a file, creating it (and any parent directories) if it "
        "does not exist. Supports three write modes: "
        "'overwrite' (default) replaces existing contents, "
        "'append' appends to the end of the file, "
        "'fail_if_exists' refuses to modify an existing file. "
        "When backup=True (default), existing files are copied to a '.bak.<timestamp>' "
        "sibling before any destructive write, enabling rollback. "
        "Path must be relative to the current working directory. "
        "Content size is limited to 1 MB per call."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            },
            "content": {
                "type": "string",
                "description": "Text content to write or append.",
            },
            "mode": {
                "type": "string",
                "enum": list(_WRITE_MODES),
                "default": "overwrite",
                "description": (
                    "How to treat an existing destination: "
                    "'overwrite' replaces it, "
                    "'append' concatenates, "
                    "'fail_if_exists' aborts."
                ),
            },
            "backup": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true and the file already exists, copy it to "
                    "<file>.bak.<UTC-ts> before writing."
                ),
            },
        },
        "required": ["path", "content"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        p = WriteFileParams.model_validate(params)
        target = _validate_rel_path(p.path, "write")
        affected: list[str] = [str(target)]
        if target.exists() and p.backup and p.mode != "append":
            backup_path = _backup_destination(target)
            affected.append(str(backup_path))
        return affected

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = WriteFileParams.model_validate(params)
        # PermissionError is intentionally raised directly (not wrapped as ToolResult)
        # so that the invocation layer (see test_write_file_rejects_traversal) can
        # uniformly detect path traversal attempts and surface them as an event.
        path = _validate_rel_path(p.path, "write")

        encoded = p.content.encode("utf-8")
        if len(encoded) > _MAX_BYTES:
            return ToolResult(
                content=f"content too large: {len(encoded)} bytes (limit 1 MB)",
                is_error=True,
                error_type="runtime_error",
            )

        try:
            check_file_size(encoded)
            check_total_quota(len(encoded))
        except ValueError as exc:
            return ToolResult(
                content=str(exc),
                is_error=True,
                error_type="runtime_error",
            )

        exists = path.exists()
        if exists and p.mode == "fail_if_exists":
            return ToolResult(
                content=f"refuse to overwrite existing file (mode=fail_if_exists): {p.path}",
                is_error=True,
                error_type="runtime_error",
            )

        backup_path: Path | None = None
        if exists and p.backup and p.mode != "append":
            try:
                backup_path = _backup_destination(path)
                shutil.copy2(path, backup_path)
            except OSError as exc:
                return ToolResult(
                    content=f"failed to create backup for {p.path}: {exc}",
                    is_error=True,
                    error_type="runtime_error",
                )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if p.mode == "append":
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(p.content)
            else:
                path.write_text(p.content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                content=f"write failed for {p.path}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )

        msg_bits = [f"wrote {len(encoded)} bytes to {p.path} (mode={p.mode})"]
        if backup_path is not None:
            msg_bits.append(f"backup={backup_path.name}")
        return ToolResult(content="; ".join(msg_bits))


def _backup_destination(path: Path) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f"{path.name}.bak.{ts}")
