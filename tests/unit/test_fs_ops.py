from __future__ import annotations

from pathlib import Path

import pytest

from iwan_claude.core.tools.builtin.fs_ops import (
    CopyFileTool,
    DeleteFileTool,
    FileExistsTool,
    FileStatTool,
    MkdirTool,
    RenameFileTool,
)


# ── delete_file ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_file_removes_file(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("x")
    result = await DeleteFileTool().invoke({"path": str(target)})
    assert not result.is_error
    assert not target.exists()
    assert "deleted file" in result.content


@pytest.mark.asyncio
async def test_delete_file_missing_is_error(tmp_path: Path) -> None:
    result = await DeleteFileTool().invoke({"path": str(tmp_path / "nope.txt")})
    assert result.is_error
    assert result.error_type == "runtime_error"
    assert "does not exist" in result.content


@pytest.mark.asyncio
async def test_delete_file_non_empty_dir_requires_recursive(tmp_path: Path) -> None:
    d = tmp_path / "sub"
    d.mkdir()
    (d / "f.txt").write_text("x")
    result = await DeleteFileTool().invoke({"path": str(d), "recursive": False})
    assert result.is_error
    assert "directory not empty" in result.content
    assert d.exists()

    result2 = await DeleteFileTool().invoke({"path": str(d), "recursive": True})
    assert not result2.is_error
    assert not d.exists()


@pytest.mark.asyncio
async def test_delete_file_rejects_traversal() -> None:
    with pytest.raises(PermissionError):
        await DeleteFileTool().invoke({"path": "../secret"})


# ── rename_file ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rename_file_moves_file(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("hello")
    result = await RenameFileTool().invoke({"src": str(src), "dst": str(dst)})
    assert not result.is_error
    assert not src.exists()
    assert dst.read_text() == "hello"


@pytest.mark.asyncio
async def test_rename_file_no_overwrite_by_default(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("A")
    dst.write_text("B")
    result = await RenameFileTool().invoke({"src": str(src), "dst": str(dst)})
    assert result.is_error
    assert "already exists" in result.content
    assert src.exists()
    assert dst.read_text() == "B"


@pytest.mark.asyncio
async def test_rename_file_overwrite_flag(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("A")
    dst.write_text("B")
    result = await RenameFileTool().invoke(
        {"src": str(src), "dst": str(dst), "overwrite": True}
    )
    assert not result.is_error
    assert not src.exists()
    assert dst.read_text() == "A"


@pytest.mark.asyncio
async def test_rename_file_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        await RenameFileTool().invoke({"src": "../x", "dst": str(tmp_path / "y")})
    with pytest.raises(PermissionError):
        await RenameFileTool().invoke({"src": str(tmp_path / "y"), "dst": "../x"})


# ── copy_file ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_copy_file_copies_file(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("hello")
    result = await CopyFileTool().invoke({"src": str(src), "dst": str(dst)})
    assert not result.is_error
    assert src.read_text() == "hello"
    assert dst.read_text() == "hello"


@pytest.mark.asyncio
async def test_copy_file_recursive_dir(tmp_path: Path) -> None:
    src_dir = tmp_path / "src_dir"
    src_dir.mkdir()
    (src_dir / "f.txt").write_text("x")
    (src_dir / "sub").mkdir()
    (src_dir / "sub" / "g.txt").write_text("y")
    dst_dir = tmp_path / "dst_dir"

    result_nonrec = await CopyFileTool().invoke(
        {"src": str(src_dir), "dst": str(dst_dir), "recursive": False}
    )
    assert result_nonrec.is_error
    assert "directory" in result_nonrec.content

    result = await CopyFileTool().invoke(
        {"src": str(src_dir), "dst": str(dst_dir), "recursive": True}
    )
    assert not result.is_error
    assert (dst_dir / "f.txt").read_text() == "x"
    assert (dst_dir / "sub" / "g.txt").read_text() == "y"


@pytest.mark.asyncio
async def test_copy_file_missing_src(tmp_path: Path) -> None:
    result = await CopyFileTool().invoke(
        {"src": str(tmp_path / "nope"), "dst": str(tmp_path / "out")}
    )
    assert result.is_error
    assert "src does not exist" in result.content


# ── mkdir ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mkdir_creates_dir(tmp_path: Path) -> None:
    target = tmp_path / "newdir"
    result = await MkdirTool().invoke({"path": str(target)})
    assert not result.is_error
    assert target.is_dir()


@pytest.mark.asyncio
async def test_mkdir_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c"
    result = await MkdirTool().invoke({"path": str(target), "parents": True})
    assert not result.is_error
    assert target.is_dir()


@pytest.mark.asyncio
async def test_mkdir_exist_ok_false(tmp_path: Path) -> None:
    target = tmp_path / "d"
    target.mkdir()
    result = await MkdirTool().invoke(
        {"path": str(target), "exist_ok": False, "parents": True}
    )
    assert result.is_error
    assert "already exists" in result.content


@pytest.mark.asyncio
async def test_mkdir_rejects_traversal() -> None:
    with pytest.raises(PermissionError):
        await MkdirTool().invoke({"path": "../hack"})


# ── file_stat ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_stat_on_file(tmp_path: Path) -> None:
    f = tmp_path / "s.txt"
    f.write_text("line1\nline2\nline3\n")
    result = await FileStatTool().invoke({"path": str(f)})
    assert not result.is_error
    content = result.content
    assert "type: file" in content
    assert "extension: .txt" in content
    assert "line_count: 3" in content
    assert "size_bytes:" in content


@pytest.mark.asyncio
async def test_file_stat_on_dir(tmp_path: Path) -> None:
    d = tmp_path / "sd"
    d.mkdir()
    (d / "a").write_text("x")
    (d / "b").write_text("y")
    result = await FileStatTool().invoke({"path": str(d)})
    assert not result.is_error
    assert "type: directory" in result.content
    assert "child_count: 2" in result.content


@pytest.mark.asyncio
async def test_file_stat_missing(tmp_path: Path) -> None:
    result = await FileStatTool().invoke({"path": str(tmp_path / "nope")})
    assert result.is_error
    assert "does not exist" in result.content


# ── file_exists ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_exists_true_and_false(tmp_path: Path) -> None:
    yes = tmp_path / "yes.txt"
    yes.write_text("x")
    r1 = await FileExistsTool().invoke({"path": str(yes)})
    assert "exists=True" in r1.content
    assert "(file)" in r1.content

    r2 = await FileExistsTool().invoke({"path": str(tmp_path / "no.txt")})
    assert "exists=False" in r2.content

    d = tmp_path / "dir"
    d.mkdir()
    r3 = await FileExistsTool().invoke({"path": str(d)})
    assert "exists=True" in r3.content
    assert "(directory)" in r3.content


@pytest.mark.asyncio
async def test_file_exists_rejects_traversal() -> None:
    with pytest.raises(PermissionError):
        await FileExistsTool().invoke({"path": "../x"})
