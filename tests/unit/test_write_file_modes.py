from __future__ import annotations

from pathlib import Path

import pytest

from iwan_claude.core.tools.builtin.write_file import WriteFileTool


# ── mode: overwrite (default) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_file_overwrite_replaces(tmp_path: Path) -> None:
    f = tmp_path / "ov.txt"
    f.write_text("OLD")
    result = await WriteFileTool().invoke(
        {"path": str(f), "content": "NEW", "mode": "overwrite"}
    )
    assert not result.is_error
    assert f.read_text() == "NEW"
    assert "mode=overwrite" in result.content


@pytest.mark.asyncio
async def test_write_file_overwrite_creates_backup_by_default(tmp_path: Path) -> None:
    f = tmp_path / "bk.txt"
    f.write_text("ORIGINAL")
    result = await WriteFileTool().invoke(
        {"path": str(f), "content": "REPLACED", "mode": "overwrite", "backup": True}
    )
    assert not result.is_error
    assert f.read_text() == "REPLACED"
    assert "backup=" in result.content

    siblings = list(tmp_path.iterdir())
    backups = [s for s in siblings if s.name.startswith("bk.txt.bak.")]
    assert len(backups) == 1
    assert backups[0].read_text() == "ORIGINAL"


@pytest.mark.asyncio
async def test_write_file_overwrite_no_backup(tmp_path: Path) -> None:
    f = tmp_path / "nb.txt"
    f.write_text("ORIGINAL")
    result = await WriteFileTool().invoke(
        {"path": str(f), "content": "REPLACED", "mode": "overwrite", "backup": False}
    )
    assert not result.is_error
    assert f.read_text() == "REPLACED"
    assert "backup=" not in result.content
    backups = [s for s in tmp_path.iterdir() if "bak" in s.name]
    assert backups == []


# ── mode: append ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_file_append_concatenates(tmp_path: Path) -> None:
    f = tmp_path / "ap.txt"
    f.write_text("FIRST\n")
    result = await WriteFileTool().invoke(
        {"path": str(f), "content": "SECOND\n", "mode": "append"}
    )
    assert not result.is_error
    assert f.read_text() == "FIRST\nSECOND\n"
    assert "mode=append" in result.content


@pytest.mark.asyncio
async def test_write_file_append_does_not_backup(tmp_path: Path) -> None:
    f = tmp_path / "ap2.txt"
    f.write_text("A")
    result = await WriteFileTool().invoke(
        {"path": str(f), "content": "B", "mode": "append", "backup": True}
    )
    assert not result.is_error
    assert "backup=" not in result.content
    backups = [s for s in tmp_path.iterdir() if "bak" in s.name]
    assert backups == []


@pytest.mark.asyncio
async def test_write_file_append_creates_new_file(tmp_path: Path) -> None:
    f = tmp_path / "new_append.txt"
    result = await WriteFileTool().invoke(
        {"path": str(f), "content": "HELLO", "mode": "append"}
    )
    assert not result.is_error
    assert f.read_text() == "HELLO"


# ── mode: fail_if_exists ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_file_fail_if_exists_aborts(tmp_path: Path) -> None:
    f = tmp_path / "protected.txt"
    f.write_text("DO NOT TOUCH")
    result = await WriteFileTool().invoke(
        {"path": str(f), "content": "REPLACEMENT", "mode": "fail_if_exists"}
    )
    assert result.is_error
    assert result.error_type == "runtime_error"
    assert "fail_if_exists" in result.content
    assert f.read_text() == "DO NOT TOUCH"


@pytest.mark.asyncio
async def test_write_file_fail_if_exists_allows_new(tmp_path: Path) -> None:
    f = tmp_path / "fresh.txt"
    result = await WriteFileTool().invoke(
        {"path": str(f), "content": "GOOD", "mode": "fail_if_exists"}
    )
    assert not result.is_error
    assert f.read_text() == "GOOD"


# ── size limit and traversal ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_file_oversized_rejected(tmp_path: Path) -> None:
    big = "x" * (2 * 1024 * 1024)  # 2 MB, exceeds 1 MB limit
    f = tmp_path / "big.txt"
    result = await WriteFileTool().invoke({"path": str(f), "content": big})
    assert result.is_error
    assert "too large" in result.content
    assert not f.exists()


@pytest.mark.asyncio
async def test_write_file_rejects_traversal_in_invoke() -> None:
    # Match the path-traversal convention of other FS tools: raise PermissionError
    # so the invocation layer catches and emits a permission event.
    with pytest.raises(PermissionError):
        await WriteFileTool().invoke({"path": "../hack.txt", "content": "x"})


@pytest.mark.asyncio
async def test_write_file_default_mode_is_overwrite(tmp_path: Path) -> None:
    f = tmp_path / "default.txt"
    f.write_text("OLD")
    result = await WriteFileTool().invoke({"path": str(f), "content": "NEW"})
    assert not result.is_error
    assert "mode=overwrite" in result.content
    assert f.read_text() == "NEW"
