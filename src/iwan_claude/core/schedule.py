"""
定时任务子系统（M2）— 三档预设调度器 + JSON 持久化

【学习要点】
1. 三档 kind 的"下次到期"全部用纯日期算术，不引 croniter：
   every_minutes:N → 基准时间 + N 分钟；daily:HH:MM → 今天/明天的墙钟点；
   weekly:W@HH:MM（0=周一）→ 同周内找下一个匹配星期。语义简单，
   简单 = 可单测，也少一个第三方依赖的供应链面。
2. 存储选 ~/.iwan/scheduled.json（会话根的兄弟文件），原子写 tmp+replace——
   和 snapshot 同一手法：断电/崩溃最坏丢最后一次未落盘变更，绝不出现半截 JSON。
3. 错过语义 = 开机补跑跳过（boot 时把所有已过期的 next_due 直接推到下一班）：
   笔记本合盖一周回来不该炸出一屏历史任务。运行中逾期只补一次——
   触发后立即推进 next_due，天然"至多一次"。
4. 调度器不 import 会话层：触发靠注入的 async fire 回调（app.py 复刻
   agent.run 的即发即返路径），本模块只管"何时该跑、跑完记账"。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from iwan_claude.core.config import resolve_sessions_root

log = logging.getLogger(__name__)

# kind/spec 合法集：validate 与展示文案共用，防止两边写歪
KINDS = ("every_minutes", "daily", "weekly")


# 校验 kind+spec 组合；返回错误文案（None = 合法）
def validate_spec(kind: str, spec: str) -> str | None:
    if kind not in KINDS:
        return f"未知调度类型 kind={kind!r}（可用：{', '.join(KINDS)}）"
    if kind == "every_minutes":
        if not spec.isdigit() or not (1 <= int(spec) <= 100000):
            return "every_minutes 的 spec 应为 1..100000 的整数分钟数"
        return None
    if kind == "daily":
        err = _validate_hhmm(spec)
        return None if err is None else f"daily 的 spec 应为 HH:MM：{err}"
    # weekly: "W@HH:MM"，W 为 0..6（0=周一，与 datetime.weekday() 对齐）
    if "@" not in spec:
        return "weekly 的 spec 应为 星期@HH:MM（如 0@09:30，0=周一）"
    wday, _, hhmm = spec.partition("@")
    if not wday.isdigit() or int(wday) > 6:
        return "weekly 的星期应为 0..6（0=周一）"
    err = _validate_hhmm(hhmm)
    return None if err is None else f"weekly 的时间部分无效：{err}"


# 校验 HH:MM 墙钟串（严格要求两位补零，与 GUI time 输入产出的形态一致）
def _validate_hhmm(spec: str) -> str | None:
    parts = spec.split(":")
    if len(parts) != 2 or not all(len(p) == 2 and p.isdigit() for p in parts):
        return "格式应为 HH:MM"
    h, m = int(parts[0]), int(parts[1])
    if h > 23 or m > 59:
        return "时/分越界"
    return None


# 解析 HH:MM 为 (时, 分)；调用方保证已过 validate
def _parse_hhmm(spec: str) -> tuple[int, int]:
    h, m = spec.split(":")
    return int(h), int(m)


# 求"严格晚于 ref"的下一班到期时间（naive 本地墙钟）；spec 非法返回 None
def next_due_after(kind: str, spec: str, ref: datetime) -> datetime | None:
    if validate_spec(kind, spec) is not None:
        return None
    if kind == "every_minutes":
        return ref + timedelta(minutes=int(spec))
    if kind == "daily":
        h, m = _parse_hhmm(spec)
        cand = ref.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= ref:
            cand += timedelta(days=1)
        return cand
    # weekly：先拆出星期与时刻两段（spec 形如 "W@HH:MM"），从今天 0 点起逐日找
    wday_s, _, hhmm = spec.partition("@")
    wday = int(wday_s)
    h, m = _parse_hhmm(hhmm)
    target = datetime(ref.year, ref.month, ref.day, h, m, tzinfo=ref.tzinfo)
    for i in range(8):
        cand = target + timedelta(days=i)
        if cand.weekday() == wday and cand > ref:
            return cand
    return None


class ScheduleStore:
    """
    任务表持久化（~/.iwan/scheduled.json）

    【学习要点】
    - 载入遇坏文件不硬失败：改名保留 .corrupt-时间戳 后从空表开始，
      调度功能降级可恢复，绝不让整个 daemon 因一个 JSON 逗号起不来。
    - 每次变更全量原子重写：任务表是个位数规模的小列表，增量化不值回票价。
    """

    def __init__(self, path: Path | None = None) -> None:
        # 路径可注入：单测指向 tmp_path，生产落 ~/.iwan/scheduled.json
        self._path = path if path is not None else resolve_sessions_root().parent / "scheduled.json"
        self._tasks: list[dict[str, Any]] = []
        self._loaded = False

    # 从磁盘载入任务表（幂等；坏文件改名让路）
    def load(self) -> None:
        self._loaded = True
        if not self._path.exists():
            self._tasks = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            bad = self._path.with_name(f"scheduled.corrupt-{stamp}.json")
            log.exception("schedule: 任务表损坏，改名保留后以空表启动：%s", self._path)
            try:
                os.replace(self._path, bad)
            except OSError:
                pass
            self._tasks = []
            return
        rows = data.get("tasks") if isinstance(data, dict) else None
        self._tasks = [t for t in (rows or []) if isinstance(t, dict) and t.get("id")]

    # 全量原子写盘（tmp + replace，同 snapshot 手法）
    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"tasks": self._tasks}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    # 全部任务行（浅拷贝，调用方改了不污染内存表）
    def list(self) -> list[dict[str, Any]]:
        if not self._loaded:
            self.load()
        return [dict(t) for t in self._tasks]

    # 按 id 找任务行；不存在返回 None
    def get(self, task_id: str) -> dict[str, Any] | None:
        if not self._loaded:
            self.load()
        for t in self._tasks:
            if t.get("id") == task_id:
                return dict(t)
        return None

    # 新建任务：id 自动生成，next_due 从当前时刻推算；kind/spec 非法抛 ValueError
    def create(
        self, name: str, cwd: str, prompt: str, kind: str, spec: str,
    ) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        err = validate_spec(kind, spec)
        if err is not None:
            raise ValueError(err)
        now = datetime.now()
        row = {
            "id": uuid.uuid4().hex[:12],
            "name": name.strip() or f"任务-{now.strftime('%m%d%H%M')}",
            "cwd": cwd,
            "prompt": prompt,
            "kind": kind,
            "spec": spec,
            "enabled": True,
            "last_run": "",
            "last_result": "",
            "next_due": _iso(now),
        }
        nd = next_due_after(kind, spec, now)
        row["next_due"] = _iso(nd) if nd else ""
        self._tasks.append(row)
        self._save()
        return dict(row)

    # 局部更新任务；kind/spec 任一变更就联动重算 next_due；未知 id 返回 None
    def update(self, task_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self._loaded:
            self.load()
        for t in self._tasks:
            if t.get("id") != task_id:
                continue
            new_kind = str(fields.get("kind") or t["kind"])
            new_spec = str(fields.get("spec") or t["spec"])
            err = validate_spec(new_kind, new_spec)
            if err is not None:
                raise ValueError(err)
            # 先记下旧节奏：下面赋完值就比不出来了
            old_kind, old_spec = t["kind"], t["spec"]
            was_enabled = bool(t.get("enabled"))
            for k in ("name", "cwd", "prompt", "kind", "spec", "enabled"):
                if k in fields and fields[k] is not None:
                    t[k] = fields[k]
            # 重新启用或节奏变了：next_due 按新节奏从现在重排（不补旧账）
            rescheduled = (
                bool(fields.get("enabled")) and not was_enabled
            ) or new_kind != old_kind or new_spec != old_spec
            if rescheduled:
                nd = next_due_after(t["kind"], t["spec"], datetime.now())
                t["next_due"] = _iso(nd) if nd else ""
            self._save()
            return dict(t)
        return None

    # 删除任务；返回是否删掉了
    def delete(self, task_id: str) -> bool:
        if not self._loaded:
            self.load()
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.get("id") != task_id]
        if len(self._tasks) != before:
            self._save()
            return True
        return False

    # 原地更新一次运行的记账字段（last_run/last_result/next_due），触发路径专用
    def record_run(self, task_id: str, last_run: str, last_result: str, next_due: str) -> None:
        for t in self._tasks:
            if t.get("id") == task_id:
                t["last_run"] = last_run
                t["last_result"] = last_result
                t["next_due"] = next_due
                self._save()
                return

    # 开机/构造时把已过期的排期整体推到下一班（补跑跳过的落点）
    def skip_stale(self, now: datetime | None = None) -> int:
        if not self._loaded:
            self.load()
        ref = now or datetime.now()
        changed = 0
        for t in self._tasks:
            if not t.get("enabled"):
                continue
            due = _parse_iso(t.get("next_due", ""))
            if due is None or due <= ref:
                nd = next_due_after(t["kind"], t["spec"], ref)
                t["next_due"] = _iso(nd) if nd else ""
                changed += 1
        if changed:
            self._save()
        return changed


# datetime → 秒级 ISO 串（存储/展示统一口径）
def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ISO 串 → naive datetime；空/坏返回 None
def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# 触发回调签名：async fire(task行) → (是否成功, 结果说明)
FireCb = Callable[["dict[str, Any]"], Awaitable["tuple[bool, str]"]]
# 记账后广播回调签名：async notified(task行, ok, run_id, detail)
FiredCb = Callable[["dict[str, Any]", bool, str, str], Awaitable[None]]


class Scheduler:
    """
    30 秒 tick 的 asyncio 调度循环

    【学习要点】
    - 不用堆每个任务的 sleep 定时器：任务表小且档期粗（分钟级），
      单循环轮询最省心，tick 间隔远小于最小调度粒度即可。
    - 触发→记账→广播的顺序固定：先跑（fire 回调即发即返），
      立刻推进 next_due——哪怕这次失败也不重排，"至多一次"由顺序保证。
    """

    # tick 间隔（秒）：30s 对分钟级粒度足够，又不至于空转耗电
    TICK_SEC = 30.0

    def __init__(self, store: ScheduleStore, fire: FireCb, on_fired: FiredCb | None = None) -> None:
        self._store = store
        self._fire = fire
        self._on_fired = on_fired
        self._task: asyncio.Task[None] | None = None

    # 启动调度循环：先跳过历史欠账，再进循环
    async def start(self) -> None:
        skipped = self._store.skip_stale()
        if skipped:
            log.info("schedule: 开机跳过 %d 条已过期排期（补跑语义=推到下一班）", skipped)
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="scheduler")

    # 停止调度循环（shutdown 用）：cancel + await，幂等
    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # 主循环：每 tick 扫一遍到期任务；单次异常只记日志不杀循环
    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.TICK_SEC)
            try:
                await self._tick()
            except Exception:
                log.exception("schedule: tick 异常（循环继续）")

    # 一次扫描：所有 enabled 且 next_due 到点的任务逐个触发记账
    async def _tick(self) -> None:
        now = datetime.now()
        for t in self._store.list():
            if not t.get("enabled"):
                continue
            due = _parse_iso(t.get("next_due", ""))
            if due is None or due > now:
                continue
            await self._fire_and_record(t, advance=True, now=now)

    # 手动"立即运行"：同一路 fire，但不动排期与记账
    async def run_now(self, task_id: str) -> dict[str, Any]:
        t = self._store.get(task_id)
        if t is None:
            return {"ok": False, "run_id": "", "error": f"任务不存在：{task_id}"}
        return await self._fire_and_record(t, advance=False, now=datetime.now())

    # 触发一条任务并按需记账/广播；返回 {ok, run_id, error}
    async def _fire_and_record(
        self, t: dict[str, Any], advance: bool, now: datetime,
    ) -> dict[str, Any]:
        try:
            ok, detail = await self._fire(t)
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
            log.exception("schedule: 任务 %s 触发异常", t.get("id"))
        run_id = ""
        # detail 约定为 "run_id|人类可读说明"，fire 回调没内容时按失败处理
        if "|" in detail:
            run_id, _, detail = detail.partition("|")
        # 【学习要点】广播在记账**之前**：客户端要靠这条 fired 把 run_id 登记进
        # 事件路由表，而 fire 回调里的 run 是异步开跑的——先记账（含磁盘写）再广播
        # 的那几毫秒间隙，定时 run 的首批 llm.token 已到客户端，run_id 无人认领
        # 被严格归属丢弃，此后整条回复永久消失，审批卡冻结"运行中"假象
        if self._on_fired is not None:
            try:
                await self._on_fired(t, ok, run_id, detail)
            except Exception:
                log.exception("schedule: 广播 schedule.fired 失败（不影响任务本身）")
        if advance:
            nd = next_due_after(t["kind"], t["spec"], now)
            self._store.record_run(
                t["id"], _iso(now), ("成功" if ok else f"失败：{detail}")[:200],
                _iso(nd) if nd else "",
            )
        return {"ok": ok, "run_id": run_id, "error": "" if ok else detail}
