"""
插件贡献合流：把单个插件目录翻译成运行系统能吃的三类对象

【学习要点】
1. 逐插件隔离的校验纪律：配置层 [[hooks]] 是"全有或全无"（用户自己的文件，
   笔误当场炸启动）；插件层是"坏一个跳一个"——第三方包不该有能力炸 daemon，
   但它自己的贡献必须完整（半套 hooks 静默生效比整体禁用更危险）。
2. MCP server 名加 `{plugin}__` 前缀：工具名格式 mcp__{server}__{tool} 已经
   占用了一个命名空间维度，插件名再入一层才能防"插件 A 顶掉用户 server B"。
3. .mcp.json 内部未知键静默忽略（官方格式同样在演进），但语义硬伤
   （stdio 无 command / transport 非法）当场拒绝整个插件。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from iwan_claude.core.config import McpServerConfig
from iwan_claude.core.hooks.spec import HookSpec, _parse_one  # 共用单条校验逻辑
from iwan_claude.core.plugins.manifest import PluginManifest


class ContributionError(ValueError):
    """插件贡献文件非法：registry 捕获后把整个插件标 error 跳过"""


@dataclass
class PluginContribution:
    """一个插件被启用时应汇入运行系统的全部素材"""
    skills: list[str] = field(default_factory=list)        # 技能名（= 目录名）
    skill_dirs: list[Path] = field(default_factory=list)   # 技能搜索目录
    hooks: list[HookSpec] = field(default_factory=list)
    mcp: list[McpServerConfig] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)         # partial 原因（撞名跳过等）


# 展开命令串里的官方兼容变量（必须在 json.loads 之后做——Windows 路径的
# 反斜杠若先注入 JSON 文本会当场炸出非法转义，这是先展开方案的现形事故）
def _expand(text: str, plugin_root: Path) -> str:
    return text.replace(
        "${CLAUDE_PLUGIN_ROOT}", str(plugin_root)
    ).replace("${IWAN_PYTHON}", sys.executable)


# 解析插件 hooks/hooks.json → HookSpec 表；source 记插件名，观测与报错可溯源
def parse_plugin_hooks(hooks_file: Path, plugin_name: str, plugin_root: Path) -> list[HookSpec]:
    try:
        raw = json.loads(hooks_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContributionError(f"hooks 文件 JSON 非法: {exc}") from exc
    entries = raw.get("hooks") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ContributionError("hooks.json 必须是 {'hooks': [...]} 或裸数组")
    specs: list[HookSpec] = []
    for i, entry in enumerate(entries):
        # 变量展开在 JSON 解析之后、单条校验之前（见 _expand 注释的事故说明）
        if isinstance(entry, dict) and isinstance(entry.get("command"), str):
            entry = {**entry, "command": _expand(entry["command"], plugin_root)}
        # 复用配置层的单条校验（同一安全语义：argv 化、未知键拒绝、超时正数）
        spec = _parse_one(entry, i)
        specs.append(HookSpec(
            event=spec.event, matcher=spec.matcher,
            argv=spec.argv, timeout_s=spec.timeout_s,
            source=plugin_name,
        ))
    return specs


# 解析插件根级 .mcp.json → McpServerConfig 表（名字已加 {plugin}__ 前缀）
def parse_plugin_mcp(mcp_file: Path, plugin_name: str, plugin_root: Path) -> list[McpServerConfig]:
    try:
        raw = json.loads(mcp_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContributionError(f".mcp.json JSON 非法: {exc}") from exc
    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        raise ContributionError(".mcp.json 必须含 mcpServers 对象")
    out: list[McpServerConfig] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            raise ContributionError(f"mcpServers.{name} 必须是对象")
        transport = spec.get("transport", "stdio")
        if transport not in ("stdio", "tcp"):
            raise ContributionError(f"mcpServers.{name}.transport 必须是 stdio/tcp")
        raw_args = [
            _expand(str(a), plugin_root)
            for a in spec.get("args", [])
            if isinstance(a, (str, int, float))
        ]
        cfg = McpServerConfig(
            name=f"{plugin_name}__{name}", transport=transport,
            command=_expand(str(spec.get("command", "")), plugin_root),
            args=raw_args,
            env={str(k): _expand(str(v), plugin_root) for k, v in spec.get("env", {}).items()},
            host=_expand(str(spec.get("host", "localhost")), plugin_root),
            port=int(spec.get("port", 3000)),
            timeout_sec=float(spec.get("timeout_sec", 30.0)),
        )
        if cfg.transport == "stdio" and not cfg.command:
            raise ContributionError(f"mcpServers.{name}: stdio 传输必须有 command")
        out.append(cfg)
    return out


# 合流单插件的全部贡献（responsible 只做撞名检查，撞名 server 跳过并记 note → partial）
def build_contribution(
    manifest: PluginManifest, reserved_mcp_names: set[str],
) -> PluginContribution:
    contrib = PluginContribution()
    if manifest.hooks_file is not None:
        contrib.hooks = parse_plugin_hooks(
            manifest.hooks_file, manifest.name, manifest.root,
        )
    if manifest.mcp_file is not None:
        for cfg in parse_plugin_mcp(manifest.mcp_file, manifest.name, manifest.root):
            if cfg.name in reserved_mcp_names:
                contrib.notes.append(f"MCP server '{cfg.name}' 与已占用名撞车，已跳过")
                continue
            reserved_mcp_names.add(cfg.name)
            contrib.mcp.append(cfg)
    for d in manifest.skills_dirs:
        contrib.skill_dirs.append(d)
        # 行展示用的技能名 = 容器内容的枚举（目录名 / .md 去后缀），与 loader 检索形态同源
        contrib.skills.extend(sorted(
            [p.name for p in d.iterdir() if p.is_dir()]
            + [p.stem for p in d.glob("*.md")]
        ))
    return contrib
