"""
一次性改造脚本：IwanClaude → IwanClaude + Linux → Windows
使用方法：python scripts/rename_to_iwan.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rename_source_dir() -> None:
    """重命名 src/iwan_claude → src/iwan_claude"""
    old = ROOT / "src" / "iwan_claude"
    new = ROOT / "src" / "iwan_claude"
    if old.exists() and not new.exists():
        print(f"[DIR]  {old}  →  {new}")
        shutil.move(str(old), str(new))


def replace_in_file(filepath: Path, replacements: list[tuple[str, str]]) -> bool:
    """在单个文件中执行多组替换，返回是否有改动"""
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
    """收集需要处理的文件"""
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
            if p.suffix.lower() in include_suffixes or fn in (
                "AGENT.md", "CLAUDE.md", "README.md", "RUNBOOK.md", "WIRE_PROTOCOL.md",
            ):
                files.append(p)
    return files


def main() -> int:
    # 1. 重命名源码目录
    print("=" * 60)
    print("步骤 1/2: 重命名源码目录")
    print("=" * 60)
    rename_source_dir()

    # 2. 定义所有替换规则（顺序重要！长的先替换）
    replacements: list[tuple[str, str]] = [
        # 包名 import（最长的先替换）
        ("iwan_claude", "iwan_claude"),
        # TUI/项目名（IwanClaude → IwanClaude）
        ("IwanClaude", "IwanClaude"),
        # 环境变量前缀（IWAN_ → IWAN_）
        ("IWAN_", "IWAN_"),
        # 路径：~/.iwan → ~/.iwan
        ("~/.iwan", "~/.iwan"),
        # 路径：.iwan/ → .iwan/
        (".iwan/", ".iwan/"),
        # 路径：.iwan" → .iwan"（边界，如字符串结尾）
        (".kama\"", ".iwan\""),
        # 路径：.iwan' → .iwan'
        (".iwan'", ".iwan'"),
        # 命令名：iwan-core → iwan-core
        ("iwan-core", "iwan-core"),
        # 命令名：iwan-tui → iwan-tui
        ("iwan-tui", "iwan-tui"),
        # 命令名：kama ping → iwan ping 等（带空格的 kama → iwan）
        ("taskkill /F /PID <pid>  (Windows)", "taskkill /F /PID <pid>  (Windows)"),
        # 独立的命令名 kama → iwan（比如 "uv run iwan "）
        # 注意：这会放在最后，避免提前匹配 iwan-tui / iwan-core 已经处理过的
    ]

    # 处理文件内容
    print()
    print("=" * 60)
    print("步骤 2/2: 批量替换文件内容")
    print("=" * 60)
    files = collect_files()
    changed = 0
    for f in files:
        if replace_in_file(f, replacements):
            changed += 1
            print(f"  [OK]   {f.relative_to(ROOT)}")

    # 最后处理单独的 "kama" 命令名（避免提前匹配）
    last_replacements = [
        ("uv run iwan ", "uv run iwan "),
        ("prog=\"kama\"", "prog=\"iwan\""),
        ("prog='iwan'", "prog='iwan'"),
    ]
    for f in files:
        if replace_in_file(f, last_replacements):
            pass  # 已经算在 changed 里了

    print()
    print(f"完成！共处理 {len(files)} 个文件，改动 {changed} 个文件。")
    print()
    print("提示：")
    print("  1. 请检查 TUI ASCII banner 是否需要更新（IWANCLAUDE）")
    print("  2. 请检查 BashTool 的 Windows PowerShell 适配")
    print("  3. 请检查 pyproject.toml 的 packages 路径")
    return 0


if __name__ == "__main__":
    sys.exit(main())
