"""
插件清单解析：plugin.json → PluginManifest（字段宽容 + 组件自动发现）

【学习要点】
1. 双清单位置：先找 .claude-plugin/plugin.json（官方格式，社区包直接可装），
   回退根级 plugin.json（iwan 原生形态）。两路都无 = 不是插件目录。
2. 未知字段静默忽略——与 skill frontmatter 同纪律。官方格式在持续演进
   （commands/agents 等新组件字段），严格白名单会让今天的合法包明天装不上；
   清单不是安全边界，真正执行的东西（hooks 命令、mcp command）各自有校验闸。
3. 唯一必填是 name（kebab-case），其余字段缺省有默认值——对齐官方最小清单。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# kebab-case：小写字母数字、连字符分隔（首尾不得是连字符）
_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# 官方清单路径 → iwan 原生回退路径（发现顺序即优先级）
_OFFICIAL_MANIFEST = Path(".claude-plugin") / "plugin.json"
_NATIVE_MANIFEST = Path("plugin.json")


class ManifestError(ValueError):
    """清单缺失/非法：调用方（registry）据此把整个插件标 error 跳过，不带病贡献"""


@dataclass(frozen=True)
class PluginManifest:
    """单个插件的静态描述：身份字段 + 五类组件的发现结果"""
    name: str
    version: str
    description: str
    author: str
    root: Path
    skills_dirs: list[Path]
    hooks_file: Path | None
    mcp_file: Path | None


# 按官方优先、原生回退的顺序定位清单文件；找不到返回 None（由 load_manifest 定性为错误）
def find_manifest_file(plugin_dir: Path) -> Path | None:
    for cand in (plugin_dir / _OFFICIAL_MANIFEST, plugin_dir / _NATIVE_MANIFEST):
        if cand.is_file():
            return cand
    return None


# 读并校验清单，附带组件目录发现；任何非法形态抛 ManifestError
def load_manifest(plugin_dir: Path) -> PluginManifest:
    mfile = find_manifest_file(plugin_dir)
    if mfile is None:
        raise ManifestError(f"{plugin_dir}: 缺少 plugin.json（.claude-plugin/ 或根级）")
    try:
        raw = json.loads(mfile.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{mfile}: JSON 非法: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{mfile}: 清单顶层必须是对象")
    name = raw.get("name")
    if not isinstance(name, str) or not _KEBAB_RE.match(name):
        raise ManifestError(f"{mfile}: name 必须是非空 kebab-case 字符串，got {name!r}")
    for field in ("version", "description"):
        val = raw.get(field, "")
        if not isinstance(val, str):
            raise ManifestError(f"{mfile}: {field} 必须是字符串")
    # author 官方允许对象形态 {name,email,...}：本期只认扁平字符串，对象形态按缺省处理
    author_val = raw.get("author")
    author = author_val if isinstance(author_val, str) else ""
    return PluginManifest(
        name=name,
        version=str(raw.get("version", "")),
        description=str(raw.get("description", "")),
        author=author,
        root=plugin_dir,
        skills_dirs=discover_skill_dirs(plugin_dir),
        hooks_file=_first_dir_file(plugin_dir / "hooks"),
        mcp_file=_first_existing([plugin_dir / ".mcp.json", plugin_dir / "mcp.json"]),
    )


# 组件发现·skills：返回"容器目录"（skills/）而非各技能子目录——
# SkillLoader 的检索形态是 容器/{name}/SKILL.md 与 容器/{name}.md，
# 把子目录直接喂进去会退化成 skills/<name>/<name>/SKILL.md 的错双层
def discover_skill_dirs(plugin_dir: Path) -> list[Path]:
    skills_root = plugin_dir / "skills"
    if skills_root.is_dir() and any(skills_root.iterdir()):
        return [skills_root]
    return []


# hooks 组件发现：只认 hooks/hooks.json 这一约定位置
def _first_dir_file(d: Path) -> Path | None:
    cand = d / "hooks.json"
    return cand if cand.is_file() else None


# 按顺序返回第一个存在的文件路径，全部不存在返回 None
def _first_existing(cands: list[Path]) -> Path | None:
    for c in cands:
        if c.is_file():
            return c
    return None
