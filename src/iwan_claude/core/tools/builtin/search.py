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
    if ".." in Path(root_str).parts:
        raise PermissionError(f"path traversal not allowed in root: {root_str}")
    return Path(root_str)


def _simplify_pattern_basename(pat: str) -> str | None:
    """If pattern looks like **/NAME/**, return NAME glob for name-level matching."""
    p = pat.replace("\\", "/")
    # strip leading **/
    if p.startswith("**/"):
        p = p[3:]
    # strip trailing /**
    if p.endswith("/**"):
        p = p[:-3]
    # if nothing remains or it still contains a slash, not a simple basename glob
    if "/" not in p and p:
        return p
    return None


def _matches_any_glob(name: str, patterns: list[str]) -> bool:
    for pat in patterns:
        pat_norm = pat.replace("\\", "/")
        if fnmatch.fnmatch(name, pat_norm):
            return True
        # Patterns like **/.git/** also match the basename directly.
        basename_pat = _simplify_pattern_basename(pat_norm)
        if basename_pat is not None and fnmatch.fnmatch(name, basename_pat):
            return True
    return False


def _matches_any_path_glob(rel_path: str, patterns: list[str]) -> bool:
    # Path.match supports ** recursive glob; fnmatch does not. Normalize to
    # forward slashes because Path.match semantics is POSIX-like on Windows too.
    norm = rel_path.replace("\\", "/")
    for pat in patterns:
        pat_norm = pat.replace("\\", "/")
        # Special-case "**/*": also accept single-segment paths (e.g. "src")
        # because Path.match semantics with "**/*" requires at least one slash.
        try:
            if Path(norm).match(pat_norm):
                return True
        except ValueError:
            pass
        # Fallback 1: treat **/* as match-everything
        if pat_norm == "**/*" and norm:
            return True
        # Fallback 2: if pattern is **/X/** and basename matches X directly
        basename_pat = _simplify_pattern_basename(pat_norm)
        if basename_pat is not None and fnmatch.fnmatch(norm, basename_pat):
            return True
        # Fallback 3: pure fnmatch for patterns without **
        if "**" not in pat_norm and fnmatch.fnmatch(norm, pat_norm):
            return True
    return False


def _should_ignore(rel_path: str, name: str, exclude: list[str]) -> bool:
    if _matches_any_glob(name, exclude):
        return True
    if _matches_any_path_glob(rel_path, exclude):
        return True
    return False


# ---------------------------------------------------------------------------
# 1. find_files: by name glob + optional content pattern (regex) + depth limit
# ---------------------------------------------------------------------------


class FindFilesParams(BaseModel):
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
        p = FindFilesParams.model_validate(params)
        root = _validate_rel_root(p.root)
        if not root.exists() or not root.is_dir():
            return ToolResult(
                content=f"find root does not exist or is not a directory: {p.root}",
                is_error=True,
                error_type="runtime_error",
            )

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
        try:
            content_re: re.Pattern[str] | None = None
            if p.content_pattern:
                flags = 0 if p.content_case == "sensitive" else re.IGNORECASE
                content_re = re.compile(p.content_pattern, flags)
            name_re_flags = 0 if p.name_pattern_case == "sensitive" else re.IGNORECASE
            name_re: re.Pattern[str] | None = None
            if p.name_pattern:
                name_re = re.compile(fnmatch.translate(p.name_pattern), name_re_flags)
        except re.error as exc:
            return ToolResult(
                content=f"invalid regex pattern: {exc}",
                is_error=True,
                error_type="schema_error",
            )

        @dataclass
        class Match:
            path: str
            type: str
            size: int | None
            content_snippets: list[str]

        matches: list[Match] = []
        root_resolved = root.resolve()
        max_depth = max(0, p.max_depth)

        def relpath(full: Path) -> str:
            try:
                return str(full.relative_to(root_resolved)) or "."
            except ValueError:
                return str(full)

        try:
            for dirpath, dirnames, filenames in os.walk(root_resolved):
                # Resolve to normalize 8.3 short names on Windows so that
                # relative_to(root_resolved) does not fail due to path alias
                # differences between TEMP paths returned by os.walk and Path.resolve.
                current_dir = Path(dirpath).resolve()
                try:
                    depth = len(current_dir.relative_to(root_resolved).parts)
                except ValueError:
                    depth = 0
                if depth >= max_depth:
                    dirnames[:] = []
                    continue

                excluded_dirs: list[str] = []
                for d in list(dirnames):
                    rel_d = relpath((current_dir / d).resolve())
                    if _should_ignore(rel_d, d, p.exclude):
                        excluded_dirs.append(d)
                for d in excluded_dirs:
                    dirnames.remove(d)

                items_to_check: list[tuple[str, bool]] = []  # (name, is_file)
                if p.file_type in ("any", "dir"):
                    for d in dirnames:
                        items_to_check.append((d, False))
                if p.file_type in ("any", "file"):
                    for f in filenames:
                        items_to_check.append((f, True))

                for name, is_file in items_to_check:
                    # Resolve files (to normalize 8.3 short names on Windows);
                    # dirs are already children of an already-resolved current_dir.
                    full_path_unresolved = current_dir / name
                    full_path = full_path_unresolved.resolve() if is_file else full_path_unresolved
                    rel = relpath(full_path)
                    if _should_ignore(rel, name, p.exclude):
                        continue
                    if p.include and not _matches_any_path_glob(rel, p.include):
                        continue

                    name_ok = True
                    if name_re is not None:
                        name_ok = bool(name_re.match(name))
                    if not name_ok:
                        continue

                    snippets: list[str] = []
                    size: int | None = None
                    try:
                        if is_file:
                            st = full_path.stat()
                            size = st.st_size
                            if content_re is not None:
                                snippets = _search_content(full_path, content_re, limit=3)
                                if not snippets:
                                    continue
                    except (OSError, PermissionError):
                        pass

                    matches.append(Match(
                        path=rel,
                        type="file" if is_file else "dir",
                        size=size,
                        content_snippets=snippets,
                    ))
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

        if not matches:
            return ToolResult(content="find_files: no matches")

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
        p = GrepSearchParams.model_validate(params)
        root = _validate_rel_root(p.root)
        if not root.exists() or not root.is_dir():
            return ToolResult(
                content=f"grep root does not exist or is not a directory: {p.root}",
                is_error=True,
                error_type="runtime_error",
            )

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
        try:
            if p.fixed_string:
                matcher: _Matcher = _FixedMatcher(p.pattern, case_sensitive=p.case_sensitive)
            else:
                flags = 0 if p.case_sensitive else re.IGNORECASE
                matcher = _ReMatcher(re.compile(p.pattern, flags))
        except re.error as exc:
            return ToolResult(
                content=f"invalid grep regex: {exc}",
                is_error=True,
                error_type="schema_error",
            )

        root_resolved = root.resolve()

        def rel(full: Path) -> str:
            try:
                return str(full.relative_to(root_resolved)) or "."
            except ValueError:
                return str(full)

        results: list[str] = []
        total_matches = 0

        try:
            for dirpath, dirnames, filenames in os.walk(root_resolved):
                # Resolve to normalize 8.3 short names on Windows (TEMP dir aliases)
                current = Path(dirpath).resolve()
                pruned_dirs: list[str] = []
                for d in list(dirnames):
                    rel_d = rel(current / d)
                    if _should_ignore(rel_d, d, p.exclude) or not _any_glob_include(rel_d, p.include):
                        pruned_dirs.append(d)
                for d in pruned_dirs:
                    dirnames.remove(d)

                for fname in filenames:
                    full = (current / fname).resolve()
                    rel_p = rel(full)
                    if _should_ignore(rel_p, fname, p.exclude):
                        continue
                    if not _any_glob_include(rel_p, p.include):
                        continue
                    try:
                        st = full.stat()
                    except OSError:
                        continue
                    if st.st_size > p.max_file_bytes:
                        continue
                    try:
                        with full.open("r", encoding="utf-8", errors="replace") as fh:
                            for line_no, line in enumerate(fh, start=1):
                                if matcher.search(line):
                                    total_matches += 1
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

        if not results:
            return ToolResult(content="grep_search: no matches")
        header = f"grep_search: {total_matches} matches (max={p.max_matches}) pattern='{p.pattern}' under '{p.root}'"
        return ToolResult(content=header + "\n---\n" + "\n".join(results))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _Matcher:
    def search(self, line: str) -> bool:  # pragma: no cover - trivial
        raise NotImplementedError


class _ReMatcher(_Matcher):
    def __init__(self, pattern: re.Pattern[str]) -> None:
        self._re = pattern

    def search(self, line: str) -> bool:
        return bool(self._re.search(line))


class _FixedMatcher(_Matcher):
    def __init__(self, needle: str, case_sensitive: bool) -> None:
        self._needle = needle if case_sensitive else needle.lower()
        self._case_sensitive = case_sensitive

    def search(self, line: str) -> bool:
        target = line if self._case_sensitive else line.lower()
        return self._needle in target


def _search_content(path: Path, pattern: re.Pattern[str], limit: int = 3) -> list[str]:
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
    # "**/*" matches everything
    if not include:
        return True
    return any(_matches_any_path_glob(rel_path, [pat]) for pat in include)


def _human(num: int | None) -> str:
    if num is None:
        return "?"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < step:
            return f"{num:.0f}{unit}"
        num //= int(step)
    return f"{num}TB"
