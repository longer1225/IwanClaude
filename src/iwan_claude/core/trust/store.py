"""
项目信任存储模块 - Layer 0 信任门（S9 Part A，设计见 docs/plans/S9_security_rollback_plan.md §3）

【学习要点】
1. fail-closed 持久化：trust.toml 读不了 / 值不认识 = 当作没决定（ask），
   绝不把"解析失败"猜成 allow——安全配置的默认值必须是更严的那一侧
2. 键归一化：Windows 文件系统大小写不敏感，`D:\\Repo` 与 `d:/repo` 必须是同一个键，
   否则用户改个盘符大小写就能绕过 deny 记录（与 permissions/rules.py 同法）
3. 祖先继承是双刃剑：trust 了 `~/code` 等于 trust 其下全部仓库，
   所以提供 inherit=False 退到"仅精确匹配"；继承方向只向下（父决定覆盖子目录，反之不成立）
4. 手动可编辑：文件格式与 policy.toml 一致的极简 TOML，用户可用编辑器撤销信任

【文件格式】
```toml
# ~/.iwan/trust.toml
[trust."d:/repo/foo"]
decision = "allow"
updated_at = "2026-09-22T10:00:00+00:00"
```
"""
from __future__ import annotations

import logging
import os
import tomllib
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

# 持久化只接受这三个决定；allow_once/deny_once 是会话级状态，不落盘
_PERSISTABLE = ("allow", "deny", "ask")


# 目录键归一化：resolve 到绝对路径后统一分隔符，Windows 额外转小写（文件系统语义）
def normalize_dir_key(cwd: str | Path) -> str:
    p = Path(cwd).expanduser().resolve()
    s = str(p).replace("\\", "/").rstrip("/")
    if os.name == "nt":
        s = s.lower()
    return s or "/"


class TrustStore:
    """
    信任决定存储 - 查询/记录"允许 iwan 在某目录工作吗"的持久化答案

    【设计说明】
    - lookup() 先精确匹配，未命中按 inherit 向上找最近祖先的 allow/deny；
      全都没有 → "ask"（未决定；由调用方决定弹窗或维持现状）
    - set_decision("ask") 语义是"撤销持久决定"，删条目回到未决定态
    - 每次操作即时全量重写文件（条目量级是"用户开过的项目数"，重写成本可忽略，
      换来的是崩溃不产生半写状态）
    """

    # 绑定 trust.toml 路径并设定是否启用祖先目录继承
    def __init__(self, path: Path, inherit: bool = True) -> None:
        self._path = Path(path).expanduser()
        self._inherit = inherit

    # 加载全表；文件缺失/损坏/值非法一律返回空 dict（fail-closed 的"没决定"态）
    def _load(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        try:
            data = tomllib.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            # 解析失败不能升级为崩溃：信任层坏了要退到 ask，而不是拖垮 daemon
            log.warning("trust: failed to parse %s, treating all dirs as ask", self._path)
            return {}
        raw = data.get("trust")
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, str]] = {}
        for key, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            decision = entry.get("decision")
            if decision in ("allow", "deny"):
                out[str(key)] = {"decision": str(decision),
                                 "updated_at": str(entry.get("updated_at", ""))}
            elif decision is not None:
                # 认识不了的值：丢弃该条（= ask），并留痕便于用户自查手滑
                log.warning("trust: invalid decision %r for %r, treated as ask", decision, key)
        return out

    # 全量重写 trust.toml
    def _save(self, entries: dict[str, dict[str, str]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# ~/.iwan/trust.toml",
            "# 由 iwan-core 自动管理，手动编辑生效但值必须是 allow/deny",
            "",
        ]
        for key in sorted(entries):
            e = entries[key]
            lines.append(f'[trust."{key}"]')
            lines.append(f'decision = "{e["decision"]}"')
            lines.append(f'updated_at = "{e.get("updated_at", "")}"')
            lines.append("")
        self._path.write_text("\n".join(lines), encoding="utf-8")

    # 查询目录的信任决定："allow" | "deny" | "ask"（未决定）
    def lookup(self, cwd: str | Path) -> str:
        entries = self._load()
        if not entries:
            return "ask"
        key = normalize_dir_key(cwd)
        hit = entries.get(key)
        if hit is not None:
            return hit["decision"]
        if not self._inherit:
            return "ask"
        # 祖先继承：从直接父级一路向上，最近的决定赢（trust 了外层目录 = 覆盖其下全部）
        parts = key.split("/")
        for i in range(len(parts) - 1, 0, -1):
            anc = "/".join(parts[:i]) or "/"
            h = entries.get(anc)
            if h is not None:
                return h["decision"]
        return "ask"

    # 记录持久决定；decision="ask" 表示撤销该目录的决定回到未决态
    def set_decision(self, cwd: str | Path, decision: str) -> None:
        if decision not in _PERSISTABLE:
            raise ValueError(f"decision must be one of {_PERSISTABLE}, got {decision!r}")
        entries = self._load()
        key = normalize_dir_key(cwd)
        if decision == "ask":
            entries.pop(key, None)
        else:
            entries[key] = {
                "decision": decision,
                "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        self._save(entries)

    # 撤销某目录的持久信任；返回是否确实删掉了条目
    def revoke(self, cwd: str | Path) -> bool:
        entries = self._load()
        removed = entries.pop(normalize_dir_key(cwd), None) is not None
        if removed:
            self._save(entries)
        return removed

    # 列出全部持久条目（归一化目录键 → 决定），供 CLI/TUI 展示
    def entries(self) -> dict[str, str]:
        return {k: v["decision"] for k, v in self._load().items()}
