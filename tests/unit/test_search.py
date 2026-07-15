from __future__ import annotations

from pathlib import Path

import pytest

from iwan_claude.core.tools.builtin.search import FindFilesTool, GrepSearchTool


def _make_tree(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("def hello():\n    print('hello world')\n\nVALUE = 42\n")
    (root / "src" / "utils.py").write_text("from typing import Any\n\ndef helper(x: Any) -> Any:\n    return x\n")
    (root / "docs").mkdir()
    (root / "docs" / "README.md").write_text("# Hello\n\nThis is README for world project.\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_main.py").write_text("def test_hello():\n    assert 1 + 1 == 2\n    VALUE = 42\n")
    exclude_dir = root / ".git"
    exclude_dir.mkdir()
    (exclude_dir / "config").write_text("this should not appear")


# ── find_files ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_files_by_name_glob(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = await FindFilesTool().invoke(
        {"root": str(tmp_path), "name_pattern": "*.py"}
    )
    assert not result.is_error
    assert "main.py" in result.content
    assert "utils.py" in result.content
    assert "test_main.py" in result.content
    assert "README.md" not in result.content
    assert ".git" not in result.content


@pytest.mark.asyncio
async def test_find_files_by_content_regex(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = await FindFilesTool().invoke(
        {"root": str(tmp_path), "content_pattern": "VALUE"}
    )
    assert not result.is_error
    assert "main.py" in result.content
    assert "test_main.py" in result.content
    assert "utils.py" not in result.content
    assert "line 5" in result.content or "VALUE = 42" in result.content


@pytest.mark.asyncio
async def test_find_files_max_depth(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = await FindFilesTool().invoke(
        {"root": str(tmp_path), "name_pattern": "*.py", "max_depth": 1}
    )
    assert not result.is_error
    assert "main.py" not in result.content
    assert "test_main.py" not in result.content


@pytest.mark.asyncio
async def test_find_files_include_filter(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = await FindFilesTool().invoke(
        {"root": str(tmp_path), "name_pattern": "*.py", "include": ["tests/**"]}
    )
    assert not result.is_error
    assert "test_main.py" in result.content
    # src/main.py must not appear; distinguish from test_main.py by path prefix
    assert "src" + "\\" not in result.content and "src/" not in result.content


@pytest.mark.asyncio
async def test_find_files_no_matches(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = await FindFilesTool().invoke(
        {"root": str(tmp_path), "name_pattern": "*.rs"}
    )
    assert not result.is_error
    assert "no matches" in result.content


@pytest.mark.asyncio
async def test_find_files_invalid_root(tmp_path: Path) -> None:
    result = await FindFilesTool().invoke(
        {"root": str(tmp_path / "does_not_exist")}
    )
    assert result.is_error
    assert "does not exist" in result.content


@pytest.mark.asyncio
async def test_find_files_rejects_traversal() -> None:
    with pytest.raises(PermissionError):
        await FindFilesTool().invoke({"root": "../"})


# ── grep_search ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grep_search_finds_pattern(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = await GrepSearchTool().invoke(
        {"root": str(tmp_path), "pattern": "world"}
    )
    assert not result.is_error
    assert "main.py:2" in result.content or "main.py:" in result.content
    assert "README.md:3" in result.content or "README.md:" in result.content
    assert ".git" not in result.content


@pytest.mark.asyncio
async def test_grep_search_case_sensitive(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result_sensitive = await GrepSearchTool().invoke(
        {"root": str(tmp_path), "pattern": "VALUE", "case_sensitive": True}
    )
    assert "main.py" in result_sensitive.content
    assert "test_main.py" in result_sensitive.content

    result_insensitive = await GrepSearchTool().invoke(
        {"root": str(tmp_path), "pattern": "value", "case_sensitive": False}
    )
    assert "main.py" in result_insensitive.content


@pytest.mark.asyncio
async def test_grep_search_fixed_string(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = await GrepSearchTool().invoke(
        {"root": str(tmp_path), "pattern": "Any", "fixed_string": True}
    )
    assert not result.is_error
    assert "utils.py" in result.content


@pytest.mark.asyncio
async def test_grep_search_no_matches(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    result = await GrepSearchTool().invoke(
        {"root": str(tmp_path), "pattern": "ZZZZ_not_exist"}
    )
    assert not result.is_error
    assert "no matches" in result.content


@pytest.mark.asyncio
async def test_grep_search_include_exclude(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    # Pattern "def" only appears in python files; include docs only → no matches
    result = await GrepSearchTool().invoke(
        {
            "root": str(tmp_path),
            "pattern": "def",
            "include": ["docs/**"],
        }
    )
    assert "no matches" in result.content


@pytest.mark.asyncio
async def test_grep_search_invalid_root(tmp_path: Path) -> None:
    result = await GrepSearchTool().invoke(
        {"root": str(tmp_path / "nope"), "pattern": "x"}
    )
    assert result.is_error
    assert "does not exist" in result.content


@pytest.mark.asyncio
async def test_grep_search_rejects_traversal() -> None:
    with pytest.raises(PermissionError):
        await GrepSearchTool().invoke({"root": "../", "pattern": "x"})
