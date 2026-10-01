"""
插件注册表：扫描两级来源（内置/已安装）、读写启停账本、汇总运行素材

【学习要点】
1. 账本只存"用户选择"（name → enabled），不存插件内容——插件目录是事实源，
   账本丢失的后果只是"全部回到默认关"，可重入且无需迁移逻辑。
2. 内置插件默认 enabled=false：静默激活一个带 PreToolUse hook 的包违背
   fail-closed 最小惊扰原则——守卫必须被人格化地点头过一次。
   而 install 即视为同意（用户主动下载 URL），装完直接置 true。
3. 坏清单不殃及池：单个插件解析失败只让它自己变 error 行，
   其余插件与内置技能链照常——daemon 的可用性不该押在第三方文件质量上。
4. 贡献只在"启用态"计算：撞名（与用户 MCP / 插件之间）是启停联动的动态事实，
   禁用插件不占名字空间；每次 set_enabled/load 都从用户保留名整表重算。
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from iwan_claude.core.config import McpServerConfig
from iwan_claude.core.hooks.spec import HookSpec
from iwan_claude.core.plugins.contributor import (
    ContributionError,
    PluginContribution,
    build_contribution,
)
from iwan_claude.core.plugins.manifest import (
    ManifestError,
    PluginManifest,
    find_manifest_file,
    load_manifest,
)

logger = logging.getLogger(__name__)

# 内置插件目录：repo 根的 plugins/（parents 层级只在源码树里成立；
# 打包安装环境该路径不存在，load() 的 is_dir 守护会自然跳过，app 层如需可显式传参）
_DEFAULT_BUILTIN_DIR = Path(__file__).resolve().parents[4] / "plugins"


@dataclass
class LoadedPlugin:
    """一个插件在注册表内的完整状态（含失败态——error 插件也要现身，GUI 才能解释为什么装不上）"""
    dir_name: str                # 目录名：清单坏掉时它仍是这行的唯一身份
    manifest: PluginManifest | None
    source: str                  # "builtin" | "installed"
    enabled: bool
    contrib: PluginContribution | None
    status: str                  # "ok" | "partial" | "error"
    error: str = ""


class PluginRegistry:
    """插件的发现、启停与素材汇总；纯数据对象，副作用（hooks/MCP/skills 汇入）由装配层执行"""

    def __init__(
        self,
        *,
        install_dir: Path | None = None,
        builtin_dir: Path | None = None,
        ledger_path: Path | None = None,
    ) -> None:
        self._install_dir = install_dir or Path("~/.iwan/plugins").expanduser()
        self._builtin_dir = builtin_dir if builtin_dir is not None else _DEFAULT_BUILTIN_DIR
        self._ledger_path = ledger_path or Path("~/.iwan/plugins.toml").expanduser()
        self._plugins: list[LoadedPlugin] = []
        # 用户配置里已占用的 MCP server 名：每次重算贡献时作为保留名底座
        self._reserved_user: set[str] = set()

    # 全量扫描两级来源 + 重算贡献；reserved_mcp_names 传入用户配置的 MCP server 名
    def load(self, reserved_mcp_names: set[str] | None = None) -> None:
        self._reserved_user = set(reserved_mcp_names or ())
        ledger = self._read_ledger()
        found: list[LoadedPlugin] = []
        for source, directory in (
            ("builtin", self._builtin_dir), ("installed", self._install_dir),
        ):
            if directory is None or not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                if child.is_dir():
                    found.append(self._load_one(child, source, ledger))
        # 同名去重：已安装的顶掉内置（用户主动装同名包 = 明确的替换意图）；
        # 身份键用清单 name，坏清单退到目录名——两类都坏且同名的极端情况后者覆盖前者，无所谓
        by_key: dict[str, LoadedPlugin] = {}
        for p in found:
            key = p.manifest.name if p.manifest else p.dir_name
            if key in by_key and p.source == "builtin":
                continue
            by_key[key] = p
        self._plugins = list(by_key.values())
        self._recompute()

    # 加载单个插件目录：清单坏了只标 error，不抛异常（隔离纪律见模块头注释 3）
    def _load_one(self, plugin_dir: Path, source: str, ledger: dict[str, bool]) -> LoadedPlugin:
        try:
            manifest = load_manifest(plugin_dir)
        except ManifestError as exc:
            return LoadedPlugin(
                dir_name=plugin_dir.name, manifest=None, source=source,
                enabled=False, contrib=None, status="error", error=str(exc),
            )
        return LoadedPlugin(
            dir_name=plugin_dir.name, manifest=manifest, source=source,
            # 默认关：fail-closed 最小惊扰（账本没点头的插件一律不贡献）
            enabled=ledger.get(manifest.name, False),
            contrib=None, status="ok",
        )

    # 从"用户保留名"底座出发，按插件名序为每个启用插件计算贡献；撞名→跳过该项并标 partial
    def _recompute(self) -> None:
        reserved = set(self._reserved_user)
        for p in sorted(self._plugins, key=self._sort_key):
            if p.status == "error" or not p.enabled or p.manifest is None:
                p.contrib = None
                continue
            try:
                p.contrib = build_contribution(p.manifest, reserved)
                p.error = ""
                p.status = "partial" if p.contrib.notes else "ok"
            except (ContributionError, ValueError) as exc:
                # 贡献坏了=该插件整体跳过（半套守卫比没有守卫更危险），但只坏它自己
                p.contrib = None
                p.status = "error"
                p.error = f"{p.manifest.name}: {exc}"

    @staticmethod
    def _sort_key(p: LoadedPlugin) -> str:
        return p.manifest.name if p.manifest else p.dir_name

    # ---- 素材汇总（装配层消费）----

    # 全部"已启用"插件的 hook 规格
    def hook_specs(self) -> list[HookSpec]:
        return [h for p in self._plugins if p.contrib for h in p.contrib.hooks]

    # 全部"已启用"插件的 MCP server 配置
    def mcp_configs(self) -> list[McpServerConfig]:
        return [m for p in self._plugins if p.contrib for m in p.contrib.mcp]

    # 全部"已启用"插件的技能搜索目录（顺序=插件名序，先到先得）
    def skill_dirs(self) -> list[Path]:
        return [d for p in self._plugins if p.contrib for d in p.contrib.skill_dirs]

    # GUI/RPC 消费的行表（字段与 plugin.list 协议模型一一对应）
    def rows(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in self._plugins:
            m = p.manifest
            c = p.contrib
            out.append({
                "name": m.name if m else p.dir_name,
                "version": m.version if m else "",
                "enabled": p.enabled,
                "source": p.source,
                "skills": sorted(c.skills) if c else [],
                "hooks": [" ".join(h.argv) for h in c.hooks] if c else [],
                "mcp": [s.name for s in c.mcp] if c else [],
                "status": p.status,
                "error": p.error,
            })
        return out

    # 查单个插件（handler 层按清单 name 校验存在性）
    def get(self, name: str) -> LoadedPlugin | None:
        for p in self._plugins:
            if p.manifest is not None and p.manifest.name == name:
                return p
        return None

    # ---- 启停与账本 ----

    # 改启停位：写账本 + 整表重算贡献（禁用插件腾出的 MCP 名要让给后来者）；返回 (ok, error)
    def set_enabled(self, name: str, enabled: bool) -> tuple[bool, str]:
        p = self.get(name)
        if p is None:
            return False, f"插件不存在: {name}"
        if p.status == "error" and enabled:
            return False, f"插件 {name} 状态异常，无法启用: {p.error}"
        p.enabled = enabled
        self._recompute()
        self._write_ledger()
        return True, ""

    # 读账本（扁平 [enabled] 表：name → bool）；文件缺失/损坏一律按"无记录"处理
    def _read_ledger(self) -> dict[str, bool]:
        if not self._ledger_path.is_file():
            return {}
        try:
            data = tomllib.loads(self._ledger_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
            logger.warning("plugins ledger unreadable (%s), treating as empty", exc)
            return {}
        enabled = data.get("enabled")
        if not isinstance(enabled, dict):
            return {}
        return {k: v for k, v in enabled.items() if isinstance(v, bool)}

    # 原子写账本：先写 .tmp 再改名——断电不留半个 TOML（与 ScheduleStore 同款纪律）
    def _write_ledger_values(self, values: dict[str, bool]) -> None:
        lines = ["[enabled]\n"]
        for name in sorted(values):
            lines.append(f"{name} = {str(values[name]).lower()}\n")
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._ledger_path.with_name(self._ledger_path.name + ".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, self._ledger_path)

    # 以当前内存态为视图落账（set_enabled 路径）
    def _write_ledger(self) -> None:
        self._write_ledger_values({
            p.manifest.name: p.enabled for p in self._plugins if p.manifest
        })

    # ---- 安装（URL → ~/.iwan/plugins/）----

    # 从 URL 安装插件：下载 ZIP → 防穿越解压到暂存 → 定位校验清单 → 原子落位 → 记账本并启用
    # 返回 (插件名, 用户可读消息, 成功位)——名字单独回传给调用方广播事件用，不让它解析消息文本
    async def install(self, url: str) -> tuple[str, str, bool]:
        try:
            content = await self._download_zip(_to_archive_url(url.strip()))
            with tempfile.TemporaryDirectory(prefix="iwan_plugin_") as staging:
                stage_root = Path(staging)
                _safe_extract_zip(content, stage_root)
                pkg = _find_package_root(stage_root)
                if pkg is None:
                    return "", "ZIP 内未找到 plugin.json（.claude-plugin/ 或根级）", False
                manifest = load_manifest(pkg)
                if self._is_builtin_name(manifest.name):
                    return "", f"内置插件名不可占用: {manifest.name}", False
                self._install_dir.mkdir(parents=True, exist_ok=True)
                target = self._install_dir / manifest.name
                if target.exists():
                    shutil.rmtree(target)  # 重装语义：新包整目录覆盖旧包
                shutil.move(str(pkg), str(target))
        except (ManifestError, ContributionError, ValueError, zipfile.BadZipFile) as exc:
            return "", f"插件安装失败: {exc}", False
        except (httpx.HTTPError, OSError) as exc:
            return "", f"插件安装失败: {type(exc).__name__}: {exc}", False
        # 安装即同意：先把启用意图写进账本，再让 load 按同一事实源重扫
        self._write_ledger_values(self._read_ledger() | {manifest.name: True})
        self.load(self._reserved_user)
        row = next((r for r in self.rows() if r["name"] == manifest.name), None)
        if row and row["status"] == "error":
            return manifest.name, f"已安装 {manifest.name}，但贡献解析异常：{row['error']}", False
        return manifest.name, f"已安装插件 {manifest.name}（已启用）", True

    # 内置目录里是否已有同名插件（内置不可卸、也不可被安装版顶掉身份）
    def _is_builtin_name(self, name: str) -> bool:
        return self._builtin_dir is not None and (self._builtin_dir / name).is_dir()

    # 下载 ZIP 字节（30s 总超时/10s 连接超时，与技能安装同档）
    async def _download_zip(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content


# GitHub 仓库/分支 URL → archive ZIP 地址（与技能安装同规则；非 GitHub URL 原样返回）
def _to_archive_url(url: str) -> str:
    url = url.rstrip("/")
    if url.startswith("github.com"):
        url = "https://" + url
    elif url.startswith("git@github.com:"):
        url = url.replace("git@github.com:", "https://github.com/")
    if "github.com" in url and "/tree/" in url:
        parts = url.split("/tree/")
        return f"{parts[0]}/archive/refs/heads/{parts[1].split('/')[0]}.zip"
    if "github.com" in url and not url.endswith(".zip"):
        return f"{url}/archive/refs/heads/main.zip"
    return url


# 防 zip-slip 解压：拒绝绝对路径与含 .. 的成员（恶意包经典逃逸路径，解压期是唯一拦截点）
def _safe_extract_zip(content: bytes, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if dest_resolved not in target.parents and target != dest_resolved:
                raise ValueError(f"ZIP 含非法路径成员: {member.filename}")
        zf.extractall(dest)


# GitHub archive 会把整包套在一层 repo-name/ 目录里：清单在暂存根或第一层子目录都算数
def _find_package_root(staging: Path) -> Path | None:
    if find_manifest_file(staging) is not None:
        return staging
    for child in sorted(staging.iterdir()):
        if child.is_dir() and find_manifest_file(child) is not None:
            return child
    return None
