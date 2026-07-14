"""
第二批替换：修复遗漏的类名、MCP 名、文档、注释
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def replace_in_file(filepath: Path, replacements: list[tuple[str, str]]) -> bool:
    try:
        text = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return False
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        filepath.write_text(text, encoding="utf-8")
        return True
    return False


def collect_files() -> list[Path]:
    exclude_dirs = {
        ".git", "__pycache__", ".venv", "venv", "node_modules",
        ".pytest_cache", ".mypy_cache", "dist", "build",
    }
    include_suffixes = {
        ".py", ".toml", ".md", ".yaml", ".yml", ".json", ".env",
        ".txt", ".example",
    }
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in include_suffixes:
                files.append(p)
    return files


def main() -> int:
    # 第二批替换（顺序重要）
    replacements: list[tuple[str, str]] = [
        # 类名
        ("IwanConfig", "IwanConfig"),
        ("IwanTuiApp", "IwanTuiApp"),
        # MCP 客户端信息
        ("iwan-claude", "iwan-claude"),
        # 剩余的独立命令名
        ("`iwan`（CLI）", "`iwan`（CLI）"),
        ("`iwan` CLI", "`iwan` CLI"),
        ("iwan (CLI)", "iwan (CLI)"),
        ("iwan chat 命令", "iwan chat 命令"),
        ("iwan run --goal", "iwan run --goal"),
        ("iwan trace 子命令", "iwan trace 子命令"),
        ("执行 iwan chat", "执行 iwan chat"),
        ("执行 iwan run", "执行 iwan run"),
        ("test_dotenv_before_toml_iwan_config", "test_dotenv_before_toml_iwan_config"),
        ("tmp_path / \"kama.toml\"", "tmp_path / \"iwan.toml\""),
        # RUNBOOK.md 里的 Linux 命令说明
        ("taskkill /F /PID <pid>  (Windows)", "taskkill /F /PID <pid>  (Windows)"),
    ]

    files = collect_files()
    changed = 0
    for f in files:
        if replace_in_file(f, replacements):
            changed += 1
            print(f"  [OK]   {f.relative_to(ROOT)}")

    print(f"\n第二批替换完成：改动 {changed} 个文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
