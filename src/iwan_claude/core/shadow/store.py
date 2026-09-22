"""
影子快照存储 - 文件写工具执行前的"写前状态"捕获与记账（S9 Part C）

【学习要点】
1. 账本与内容分离：objects/<sha256> 只存"内容"（按内容寻址天然去重），
   file_changes.json 只存"发生过什么"（path→blob 的时序账）——回滚 = 按账
   倒放内容，任何一侧损坏都不产生"半条记录"
2. prepare/commit 两段式：prepare 在工具执行前抓快照但不落账，commit 只在
   工具成功后把快照追加进账本。写失败的工具调用在账本上不留痕——
   "快照了但没改"与"没发生"在恢复语义上等价，账本不该记录幽灵
3. was_new 墓碑：写前文件不存在时 blob=None + was_new=True，回滚 = 删除；
   没有这个标记，新建文件将永远无法撤销（无内容可还原）
4. captured=False 是显式降级（超限/读不了的二进制），账本留痕但回滚跳过——
   静默漏拍比"拍不了并说出来"危险得多
5. ContextVar 会话隔离沿袭 sandbox.py：ShadowStore 按 run 建，
   挂在任务上下文里被 invoke_tool 读取，零签名改动

【目录结构】
<sessions_root>/<sid>/shadow/objects/<sha256>   内容块（会话级共享，跨 run 去重）
<sessions_root>/<sid>/runs/<run_id>/file_changes.json   变更账本（run 级，追加式 JSON 数组）
无 session 的一次性 run：两者都退到 <run_dir>/shadow/ 下。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 工具生成的 .iwan/backups/*.bak 不是用户文件，影子层不收（否则账本被备份套备份）
_BACKUP_MARK = ".iwan/backups/"

# 当前激活的 ShadowStore（按 asyncio 任务上下文隔离；None = 本 run 不拍快照）
_ACTIVE_SHADOW: ContextVar[ShadowStore | None] = ContextVar(
    "iwan_active_shadow", default=None,
)


# 登记本 run 的影子存储并返回 reset 用的 token（调用方负责 finally 里 reset）
def set_active_shadow(store: ShadowStore | None) -> Token[ShadowStore | None]:
    return _ACTIVE_SHADOW.set(store)


# 读取当前任务上下文中激活的影子存储（invoke_tool 的唯一读取点）
def get_active_shadow() -> ShadowStore | None:
    return _ACTIVE_SHADOW.get()


# 还原 ContextVar 到 set 之前的状态
def reset_active_shadow(token: Token[ShadowStore | None]) -> None:
    _ACTIVE_SHADOW.reset(token)


class ShadowStore:
    """
    单 run 影子快照存储：prepare 抓写前状态 → 工具执行 → commit 落账

    【设计说明】
    - 路径语义与工具一致：工具用 Path(p) 相对进程 cwd 写文件，
      这里也按 cwd 解析 abspath，账本同时保留用户原样 path 便于人读
    - Windows 去重键用 abspath 小写 posix（文件系统大小写不敏感）
    """

    # 绑定对象目录（会话级共享→跨 run 内容去重）与账本路径（run 级）、单文件捕获上限
    def __init__(
        self,
        objects_dir: Path,
        ledger_path: Path,
        max_file_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self._objects = Path(objects_dir)
        self._ledger_path = Path(ledger_path)
        self._max_file_bytes = max_file_bytes

    # 抓一批路径的写前状态；返回 pending 列表（不碰账本，等 commit）
    def prepare(self, paths: list[str]) -> list[dict[str, Any]]:
        """
        对每个受影响路径记录"写之前它是什么"：blob(was-new=None) 或降级标记

        同一路径去重（一次 multi_edit 会报多条相同 path）；
        .iwan/backups 下的备份产物直接过滤。
        """
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for p in paths:
            try:
                abspath = Path(p).expanduser().resolve()
            except OSError:
                continue
            posix = abspath.as_posix().lower()
            if _BACKUP_MARK in f"/{posix}/" :
                continue
            if posix in seen:
                continue
            seen.add(posix)
            entry: dict[str, Any] = {
                "path": str(p), "abspath": abspath.as_posix(),
                "ts": ts, "tool": "",
            }
            self._capture(abspath, entry)
            out.append(entry)
        return out

    # 把单个路径的当前内容拍进对象存储，结果写回 entry（原地更新）
    def _capture(self, abspath: Path, entry: dict[str, Any]) -> None:
        if not abspath.exists():
            # 墓碑：写前不存在 → 回滚 = 删除
            entry.update(blob=None, was_new=True, captured=True)
            return
        entry["was_new"] = False
        try:
            size = abspath.stat().st_size
            if size > self._max_file_bytes:
                entry.update(blob=None, captured=False, reason="too_large")
                return
            data = abspath.read_bytes()
        except OSError as exc:
            entry.update(blob=None, captured=False, reason=f"read_error: {exc}")
            return
        sha = hashlib.sha256(data).hexdigest()
        obj = self._objects / sha
        if not obj.exists():
            # 内容寻址：同名对象已存在即内容相同，天然去重不用锁
            self._objects.mkdir(parents=True, exist_ok=True)
            tmp = obj.with_suffix(f".tmp-{os.getpid()}")
            tmp.write_bytes(data)
            tmp.replace(obj)
        entry.update(blob=sha, captured=True)

    # 工具执行成功后把 pending 落账（追加进 file_changes.json，原子重写）
    def commit(self, pending: list[dict[str, Any]], tool_name: str = "") -> None:
        if not pending:
            return
        for e in pending:
            e["tool"] = tool_name
            # after 指纹：记账瞬间的实际内容哈希，是"外部改动"的对照基准
            # （C2 冲突检测 = 现在的盘上哈希 != 当时的 after）
            e["after"] = self._hash_now(Path(e["abspath"]))
        ledger = self._load_ledger()
        ledger.extend(pending)
        self._write_ledger(ledger)
        log.debug(
            "shadow: committed %d entr(y|ies) tool=%s", len(pending), tool_name,
        )

    # 盘上当前内容的哈希："gone"=不存在，None=读不了/超限（对照基准缺失）
    def _hash_now(self, abspath: Path) -> str | None:
        try:
            if not abspath.exists():
                return "gone"
            if abspath.stat().st_size > self._max_file_bytes:
                return None
            return hashlib.sha256(abspath.read_bytes()).hexdigest()
        except OSError:
            return None

    # 读取变更账本（文件缺失/损坏返回空账——降级为"无可回滚"而非崩溃）
    def _load_ledger(self) -> list[dict[str, Any]]:
        if not self._ledger_path.exists():
            return []
        try:
            data = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("shadow: ledger %s unreadable, starting empty", self._ledger_path)
            return []
        return data if isinstance(data, list) else []

    # 原子写账本：先写 tmp 再 replace，崩溃不会产生半截 JSON
    def _write_ledger(self, ledger: list[dict[str, Any]]) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._ledger_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8",
        )
        tmp.replace(self._ledger_path)

    # 返回本 run 的完整变更账（C2 的 files list / restore 共用）
    def ledger(self) -> list[dict[str, Any]]:
        return self._load_ledger()

    # 账本文件路径（C2 协议层按 run 目录找账本时引用）
    @property
    def ledger_path(self) -> Path:
        return self._ledger_path

    # 按 blob 哈希取回写前内容字节（无对象返回 None）
    def read_blob(self, sha: str) -> bytes | None:
        obj = self._objects / sha
        if not obj.exists():
            return None
        return obj.read_bytes()

    # 账本按 abspath 归并，返回每个文件"最近一次"变更前状态 + 当前外部改动冲突标记
    def changes_view(self) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for e in self._load_ledger():
            ap = e.get("abspath")
            if ap:
                latest[ap] = e  # 后写覆盖前写 → 留下的是最近一次 before
        out: list[dict[str, Any]] = []
        for e in latest.values():
            ap = Path(e["abspath"])
            conflict = False
            if e.get("captured") and not e.get("was_new"):
                # 冲突 = 盘上现在的内容 != 我们当初写完的样子（外部改过）
                now = self._hash_now(ap)
                conflict = e.get("after") is not None and now != e.get("after")
            out.append({
                "path": e["path"], "tool": e.get("tool", ""), "ts": e.get("ts", ""),
                "was_new": bool(e.get("was_new")), "captured": bool(e.get("captured")),
                "conflict": conflict,
            })
        return out

    # 回滚选中文件到最近一次变更前；先写反向快照（还原本身可撤销），冲突默认跳过
    def restore(self, paths: list[str], *, force: bool = False) -> list[dict[str, Any]]:
        """
        逐文件还原，返回每个文件的结果 {path, status: restored|skipped|failed, detail}

        【两段式与捕获对称】先把"当前内容"作为一次正常变更前状态 prepare+commit
        （工具名记 "restore"，形成反向快照链：还原错了还能再还原回来），
        再把 before blob 写回盘。冲突（外部改过）且未 force → 跳过不动盘。
        """
        ledger = self._load_ledger()
        latest: dict[str, dict[str, Any]] = {}
        for e in ledger:
            ap = e.get("abspath")
            if ap:
                latest[ap] = e
        # paths=["*"] 或空 = 全部；否则按原始 path 精确挑选
        if not paths or paths == ["*"]:
            targets = list(latest.values())
        else:
            want = set(paths)
            targets = [e for e in latest.values() if e.get("path") in want]
        results: list[dict[str, Any]] = []
        reverse_pending: list[dict[str, Any]] = []
        applies: list[tuple[dict[str, Any], Path]] = []
        for e in targets:
            ap = Path(e["abspath"])
            res = {"path": e["path"], "status": "", "detail": ""}
            results.append(res)
            if not e.get("captured"):
                # 当初就没拍全（超限/读失败）：无从还原，明说不猜
                res.update(status="failed", detail=f"not captured ({e.get('reason', 'unknown')})")
                continue
            if e.get("after") is not None and self._hash_now(ap) != e["after"] and not force:
                res.update(status="skipped", detail="externally modified (use force)")
                continue
            # 反向快照：捕获"还原前的当前内容"，稍后随 commit 落账
            rev: dict[str, Any] = {
                "path": e["path"], "abspath": e["abspath"],
                "ts": datetime.now(UTC).isoformat(timespec="seconds"), "tool": "",
            }
            self._capture(ap, rev)
            reverse_pending.append(rev)
            applies.append((e, ap))
            res.update(status="restored")
        # 先逐个回写 before，反向快照的账在盘真正变化之后再落（见函数末尾）
        for e, ap in applies:
            try:
                if e.get("was_new"):
                    # 写前不存在 → 还原 = 删除
                    if ap.exists():
                        ap.unlink()
                else:
                    data = self.read_blob(str(e.get("blob")))
                    if data is None:
                        # 对象被清理/丢失：没内容可回写，状态必须改回 failed，
                        # 否则账面上"已还原"而盘上未动——静默谎报比失败更糟
                        for r in results:
                            if r["path"] == e["path"]:
                                r.update(status="failed", detail="snapshot object missing")
                        continue
                    ap.parent.mkdir(parents=True, exist_ok=True)
                    ap.write_bytes(data)
            except OSError as exc:
                for r in results:
                    if r["path"] == e["path"]:
                        r.update(status="failed", detail=str(exc))
        # 【时序要点】after 指纹必须在盘真正变化之后才盖：若在回写前 commit
        # 反向快照，它记的会是"还原前"的内容，下一次 restore 把刚还原的
        # 文件误判为外部冲突而跳过——撤销链第二环当场断裂
        if reverse_pending:
            for rev in reverse_pending:
                rev["tool"] = "restore"
            for rev, (_, ap) in zip(reverse_pending, applies):
                rev["after"] = self._hash_now(ap)
            ledger.extend(reverse_pending)
            self._write_ledger(ledger)
        return results
