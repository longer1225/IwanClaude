from __future__ import annotations

from pathlib import Path

import pytest

from iwan_claude.core.tools.builtin.editor import (
    DeleteLinesTool,
    EditByLinesTool,
    EditBySearchTool,
    InsertAtLineTool,
    ViewFileTool,
    join_preserve_endings,
    make_backup,
    split_preserve_endings,
)


# ── Helpers: line preservation roundtrip ──────────────────────────────────────


def test_split_join_roundtrip_crlf_and_lf() -> None:
    text_crlf = "A\r\nB\r\nC\r\n"
    assert join_preserve_endings(split_preserve_endings(text_crlf)) == text_crlf
    text_lf = "A\nB\nC\n"
    assert join_preserve_endings(split_preserve_endings(text_lf)) == text_lf
    text_noeol = "A\nB\nC"
    assert join_preserve_endings(split_preserve_endings(text_noeol)) == text_noeol


def test_split_join_empty() -> None:
    assert split_preserve_endings("") == []
    assert join_preserve_endings([]) == ""


# ── make_backup ───────────────────────────────────────────────────────────────


def test_make_backup_places_file_under_iwan_backups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "main.py"
    src.write_text("print('hello')\n")
    bak = make_backup(tmp_path, Path("main.py"))
    assert bak.exists()
    assert bak.read_text() == "print('hello')\n"
    assert ".iwan/backups" in str(bak.relative_to(tmp_path)).replace("\\", "/")
    assert bak.name.endswith("__main.py.bak")


# ═══════════════════════════════════════════════════════════════════════════════
# view_file
# ═══════════════════════════════════════════════════════════════════════════════


def _write_sample(tmp_path: Path, trailing_eol: bool = True) -> Path:
    lines = [f"line {i}: content" for i in range(1, 13)]
    text = "\n".join(lines)
    if trailing_eol:
        text += "\n"
    f = tmp_path / "sample.txt"
    f.write_text(text)
    return f


@pytest.mark.asyncio
async def test_view_file_default_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _write_sample(tmp_path)
    result = await ViewFileTool().invoke({"path": "sample.txt"})
    assert not result.is_error
    assert "total_lines=12" in result.content
    assert "showing=1-12" in result.content
    for i in range(1, 13):
        assert f"line {i}: content" in result.content
    # line numbers present, zero-padded (min 3 digits for 12 total → pad=2)
    assert "  1 | line 1: content" in result.content or "1 | line 1: content" in result.content


@pytest.mark.asyncio
async def test_view_file_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _write_sample(tmp_path)
    result = await ViewFileTool().invoke(
        {"path": "sample.txt", "start_line": 5, "end_line": 7}
    )
    assert not result.is_error
    assert "showing=5-7" in result.content
    assert "line 5:" in result.content
    assert "line 6:" in result.content
    assert "line 7:" in result.content
    assert "line 4:" not in result.content
    assert "line 8:" not in result.content


@pytest.mark.asyncio
async def test_view_file_pagination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _write_sample(tmp_path)  # 12 lines
    r1 = await ViewFileTool().invoke(
        {"path": "sample.txt", "page": 1, "page_size": 5}
    )
    assert not r1.is_error
    assert "showing=1-5" in r1.content
    assert "page=1/3" in r1.content
    r2 = await ViewFileTool().invoke(
        {"path": "sample.txt", "page": 3, "page_size": 5}
    )
    assert "showing=11-12" in r2.content


@pytest.mark.asyncio
async def test_view_file_hide_line_numbers_and_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _write_sample(tmp_path)
    result = await ViewFileTool().invoke(
        {
            "path": "sample.txt",
            "start_line": 1,
            "end_line": 1,
            "show_line_numbers": False,
            "show_total_lines": False,
        }
    )
    assert "total_lines" not in result.content
    assert " | " not in result.content
    assert "line 1: content" in result.content


@pytest.mark.asyncio
async def test_view_file_missing_and_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = await ViewFileTool().invoke({"path": "does_not_exist.txt"})
    assert r.is_error
    (tmp_path / "subdir").mkdir()
    rd = await ViewFileTool().invoke({"path": "subdir"})
    assert rd.is_error


@pytest.mark.asyncio
async def test_view_file_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PermissionError):
        await ViewFileTool().invoke({"path": "../x"})


# ═══════════════════════════════════════════════════════════════════════════════
# edit_by_lines  (core cursor edit)
# ═══════════════════════════════════════════════════════════════════════════════


_ORIG = "import sys\n\ndef main():\n    x = 1\n    y = 2\n    print(x + y)\n\n\nmain()\n"


def _prog(tmp_path: Path) -> Path:
    f = tmp_path / "prog.py"
    f.write_text(_ORIG)
    return f


@pytest.mark.asyncio
async def test_edit_by_lines_replace_middle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _prog(tmp_path)
    result = await EditByLinesTool().invoke(
        {
            "path": "prog.py",
            "start_line": 4,
            "end_line": 5,
            "replacement": "    s = 'hello'\n    print(s)\n",
        }
    )
    assert not result.is_error
    assert "removed 2, inserted 2" in result.content
    # verify output content
    updated = f.read_text()
    assert "s = 'hello'" in updated
    assert "print(s)" in updated
    assert "x = 1" not in updated
    assert "y = 2" not in updated
    # EOL preserved LF and final EOL
    assert updated.endswith("main()\n")
    # backup written under .iwan/backups
    backups = list((tmp_path / ".iwan" / "backups").glob("*__prog.py.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == _ORIG


@pytest.mark.asyncio
async def test_edit_by_lines_delete_range_via_empty_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _prog(tmp_path)
    # Remove lines 6-7 (blank + blank)
    result = await EditByLinesTool().invoke(
        {"path": "prog.py", "start_line": 7, "end_line": 7, "replacement": ""}
    )
    assert not result.is_error
    updated = f.read_text()
    # line 7 originally '\n' before main() → removed; main() follows immediately
    assert "main()" in updated
    assert updated.count("\n") < _ORIG.count("\n")


@pytest.mark.asyncio
async def test_edit_by_lines_single_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _prog(tmp_path)
    await EditByLinesTool().invoke(
        {
            "path": "prog.py",
            "start_line": 1,
            "end_line": 1,
            "replacement": "import os  # replaced\nimport sys\n",
        }
    )
    updated = f.read_text()
    assert updated.startswith("import os  # replaced\nimport sys\n")
    assert updated.splitlines()[1] == "import sys" or "import sys" in updated


@pytest.mark.asyncio
async def test_edit_by_lines_bounds_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _prog(tmp_path)  # 8 lines
    r1 = await EditByLinesTool().invoke(
        {"path": "prog.py", "start_line": 1, "end_line": 99, "replacement": ""}
    )
    assert r1.is_error
    assert "out of bounds" in r1.content

    r2 = await EditByLinesTool().invoke(
        {"path": "prog.py", "start_line": 5, "end_line": 3, "replacement": ""}
    )
    assert r2.is_error
    assert "start_line" in r2.content

    # file not found
    r3 = await EditByLinesTool().invoke(
        {"path": "nope.txt", "start_line": 1, "end_line": 1, "replacement": ""}
    )
    assert r3.is_error


@pytest.mark.asyncio
async def test_edit_by_lines_no_backup_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _prog(tmp_path)
    await EditByLinesTool().invoke(
        {"path": "prog.py", "start_line": 1, "end_line": 1, "replacement": "# nothing\n", "backup": False}
    )
    backups = list((tmp_path / ".iwan" / "backups").glob("*.bak"))
    assert backups == []


@pytest.mark.asyncio
async def test_edit_by_lines_crlf_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "crlf.txt"
    f.write_bytes(b"A\r\nB\r\nC\r\nD\r\n")
    await EditByLinesTool().invoke(
        {
            "path": "crlf.txt",
            "start_line": 2,
            "end_line": 3,
            "replacement": "X\nY\n",
        }
    )
    raw = f.read_bytes()
    assert raw == b"A\r\nX\r\nY\r\nD\r\n"


# ═══════════════════════════════════════════════════════════════════════════════
# edit_by_search
# ═══════════════════════════════════════════════════════════════════════════════


_CFG = """server_name = alpha
server_port = 8000
debug = true
server_url = http://alpha:8000/
log_level = info
log_file = /tmp/server.log
"""


def _cfg(tmp_path: Path) -> Path:
    f = tmp_path / "cfg.txt"
    f.write_text(_CFG)
    return f


@pytest.mark.asyncio
async def test_edit_by_search_single_occurrence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _cfg(tmp_path)
    result = await EditBySearchTool().invoke(
        {"path": "cfg.txt", "old": "8000", "new": "9090", "occurrence": 1}
    )
    # But 8000 appears twice! → ambiguous if occurrence=0 → test separate
    # occurrence=1: only replaces the first occurrence
    assert not result.is_error
    text = f.read_text()
    assert "server_port = 9090" in text
    assert "http://alpha:8000" in text  # 2nd occurrence preserved


@pytest.mark.asyncio
async def test_edit_by_search_ambiguous_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _cfg(tmp_path)
    result = await EditBySearchTool().invoke(
        {"path": "cfg.txt", "old": "server", "new": "app"}
    )
    assert result.is_error
    assert "ambiguous" in result.content
    assert "L1 L4" in result.content or "occurrence=N" in result.content


@pytest.mark.asyncio
async def test_edit_by_search_force_multiple(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _cfg(tmp_path)
    result = await EditBySearchTool().invoke(
        {"path": "cfg.txt", "old": "server", "new": "app", "force_multiple": True}
    )
    assert not result.is_error
    text = f.read_text()
    assert text.count("app") == _CFG.count("server")
    assert text.startswith("app_name =")
    assert "app_url = http" in text


@pytest.mark.asyncio
async def test_edit_by_search_no_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _cfg(tmp_path)
    r = await EditBySearchTool().invoke(
        {"path": "cfg.txt", "old": "ZZZZZ", "new": "nothing"}
    )
    assert r.is_error
    assert "no occurrence found" in r.content


@pytest.mark.asyncio
async def test_edit_by_search_occurrence_out_of_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _cfg(tmp_path)
    r = await EditBySearchTool().invoke(
        {"path": "cfg.txt", "old": "server", "new": "x", "occurrence": 99}
    )
    assert r.is_error
    assert "out of range" in r.content


@pytest.mark.asyncio
async def test_edit_by_search_empty_old(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _cfg(tmp_path)
    r = await EditBySearchTool().invoke({"path": "cfg.txt", "old": "", "new": "x"})
    assert r.is_error
    assert "'old' must be non-empty" in r.content


# ═══════════════════════════════════════════════════════════════════════════════
# insert_at_line
# ═══════════════════════════════════════════════════════════════════════════════


_SRC_LF = "package main\n\nimport \"fmt\"\n\nfunc main() {\n\tfmt.Println(\"hi\")\n}\n"


def _src(tmp_path: Path) -> Path:
    f = tmp_path / "main.go"
    f.write_text(_SRC_LF)
    return f


@pytest.mark.asyncio
async def test_insert_before_pushes_target_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _src(tmp_path)
    result = await InsertAtLineTool().invoke(
        {
            "path": "main.go",
            "line": 5,
            "position": "before",
            "text": "const Version = \"1.0\"\nconst Build = \"abc\"\n",
        }
    )
    assert not result.is_error
    lines = f.read_text().splitlines()
    # func main() was line 5 → now line 7
    assert lines[4] == "const Version = \"1.0\""
    assert lines[5] == "const Build = \"abc\""
    assert lines[6] == "func main() {"


@pytest.mark.asyncio
async def test_insert_after_places_between(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = _src(tmp_path)
    await InsertAtLineTool().invoke(
        {
            "path": "main.go",
            "line": 2,
            "position": "after",
            "text": "import \"os\"\n",
        }
    )
    lines = f.read_text().splitlines()
    # line 1=package; line2 empty; line3 should become import "os" then import fmt
    assert lines[2] == 'import "os"'
    assert lines[3] == 'import "fmt"'


@pytest.mark.asyncio
async def test_insert_preserves_eol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "x.txt"
    f.write_bytes(b"ONE\r\nTWO\r\nTHREE\r\n")
    await InsertAtLineTool().invoke(
        {"path": "x.txt", "line": 2, "position": "before", "text": "A\nB\n"}
    )
    raw = f.read_bytes()
    assert raw == b"ONE\r\nA\r\nB\r\nTWO\r\nTHREE\r\n"


@pytest.mark.asyncio
async def test_insert_at_line_no_eol_terminal_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # file without terminal EOL
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "noeol.txt"
    f.write_text("a\nb")  # no trailing \n
    await InsertAtLineTool().invoke(
        {"path": "noeol.txt", "line": 2, "position": "after", "text": "mid\nc"}
    )
    updated = f.read_text()
    # Should remain without trailing newline on final line 'c'
    assert not updated.endswith("\n")
    assert updated.splitlines() == ["a", "b", "mid", "c"]


@pytest.mark.asyncio
async def test_insert_at_line_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PermissionError):
        await InsertAtLineTool().invoke(
            {"path": "../hack.txt", "line": 1, "position": "before", "text": "x"}
        )


# ═══════════════════════════════════════════════════════════════════════════════
# delete_lines
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delete_lines_middle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "l.txt"
    f.write_text("1\n2\n3\n4\n5\n6\n7\n")
    result = await DeleteLinesTool().invoke(
        {"path": "l.txt", "start_line": 3, "end_line": 5}
    )
    assert not result.is_error
    assert "deleted" in result.content
    lines = f.read_text().splitlines()
    assert lines == ["1", "2", "6", "7"]
    # backup produced
    assert any((tmp_path / ".iwan" / "backups").glob("*__l.txt.bak"))


@pytest.mark.asyncio
async def test_delete_lines_single(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "l.txt"
    f.write_text("a\nb\nc\n")
    await DeleteLinesTool().invoke({"path": "l.txt", "start_line": 2, "end_line": 2})
    assert f.read_text().splitlines() == ["a", "c"]


@pytest.mark.asyncio
async def test_delete_lines_no_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "l.txt"
    f.write_text("a\nb\n")
    await DeleteLinesTool().invoke(
        {"path": "l.txt", "start_line": 1, "end_line": 1, "backup": False}
    )
    backups = list((tmp_path / ".iwan" / "backups").glob("*"))
    assert not any(b.name.endswith(".bak") for b in backups)
