# 功能：验证定时任务的三档 kind 数学、spec 校验、存储持久化与"补跑跳过"语义
# 设计：next_due_after/skip_stale/ScheduleStore 全部收"时间/路径"入参
# （ref datetime、path= tmp_path），所以整组测试不需要 monkeypatch 时钟或
# 真实 HOME——固定基准时刻 + 断言绝对结果，比 mock now() 更不易自欺。
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iwan_claude.core.schedule import ScheduleStore, next_due_after, validate_spec

# 基准：2026-09-23 是星期三（weekday()==2），10:00 墙钟
REF = datetime(2026, 9, 23, 10, 0, 0)


# 功能：every_minutes 档 = 基准 + N 分钟，严格向后
# 设计：N=1 与 N=1440（跨日）两点覆盖"分钟级"与"跨天加法交给 timedelta"
@pytest.mark.parametrize(
    ("spec", "want"),
    [("1", REF + timedelta(minutes=1)), ("1440", REF + timedelta(days=1))],
)
def test_every_minutes(spec: str, want: datetime) -> None:
    assert next_due_after("every_minutes", spec, REF) == want


# 功能：daily 档在今天时刻未到则今天、已过则明天，等于当下也必须明天（严格>）
# 设计：三个 ref 卡在同一目标时刻的 前/整点/后，专测 `cand <= ref` 的等号边界
def test_daily_boundary() -> None:
    target = REF.replace(hour=18, minute=30)
    assert next_due_after("daily", "18:30", REF) == target                    # 未到：今天
    assert next_due_after("daily", "18:30", target) == target.replace(day=24)  # 正好到点：算过期
    assert next_due_after("daily", "09:00", REF) == datetime(2026, 9, 24, 9, 0)  # 已过：明天 09:00


# 功能：weekly 档找"严格晚于 ref 的最近目标星期"，当天时刻未到也算本周
# 设计：REF 是周三；分别选 本周三晚于当前/下周一/上周二三个目标，
# 一次覆盖"当天顺延""向前找""绕一整周"三种相对位置
def test_weekly_targets() -> None:
    assert next_due_after("weekly", "2@18:00", REF) == datetime(2026, 9, 23, 18, 0)   # 今天周三，18:00 未到
    assert next_due_after("weekly", "2@09:00", REF) == datetime(2026, 9, 30, 9, 0)     # 今天已过点→下周三
    assert next_due_after("weekly", "0@09:30", REF) == datetime(2026, 9, 28, 9, 30)    # 下周一
    assert next_due_after("weekly", "1@08:00", REF) == datetime(2026, 9, 29, 8, 0)     # 下周二


# 功能：spec 校验接受三档合法形态、拒绝越界/畸形组合
# 设计：表驱动正误各一组，含 "25:00"（时界）、"0@@9:30"（分隔符滥用）、
# every_minutes 边界 0 与 100001——校验器的价值恰恰在边界判定
@pytest.mark.parametrize(
    ("kind", "spec", "ok"),
    [
        ("every_minutes", "5", True),
        ("every_minutes", "0", False),
        ("every_minutes", "abc", False),
        ("daily", "09:30", True),
        ("daily", "25:00", False),
        ("daily", "9:30", False),
        ("weekly", "0@09:30", True),
        ("weekly", "7@09:30", False),
        ("weekly", "1@@9:30", False),
        ("hourly", "5", False),
    ],
)
def test_validate_spec(kind: str, spec: str, ok: bool) -> None:
    assert (validate_spec(kind, spec) is None) == ok


# 功能：create→update(禁用/重启用)→delete 全生命周期正确落盘与重排期
# 设计：同一路径建两个 Store 实例，第二个只从磁盘读——"持久化"的证据必须
# 是换对象后还在，而不是同一对象自说自话；重启用后 next_due 必须被重排
def test_store_lifecycle(tmp_path: Path) -> None:
    p = tmp_path / "scheduled.json"
    store = ScheduleStore(path=p)
    row = store.create("体检", "D:/demo", "跑一遍测试", "every_minutes", "5")
    assert row["next_due"]  # 已排期
    # 换实例=从磁盘复活：内容必须等价
    reloaded = ScheduleStore(path=p)
    assert reloaded.get(row["id"]) is not None
    assert reloaded.list()[0]["prompt"] == "跑一遍测试"
    # 禁用不重排、重启用必须重排（next_due 变新）
    before = reloaded.list()[0]["next_due"]
    reloaded.update(row["id"], {"enabled": False})
    assert reloaded.list()[0]["next_due"] == before
    reloaded.update(row["id"], {"enabled": True})
    after = datetime.fromisoformat(reloaded.list()[0]["next_due"])
    assert after > datetime.now()
    assert reloaded.delete(row["id"]) is True
    assert ScheduleStore(path=p).list() == []


# 功能：损坏的 scheduled.json 被改名保留并以空表降级启动
# 设计：手写非法 JSON——load() 不许抛（daemon 启动路径就一行 load，
# 任何异常都会变成"整个 core 起不来"），同时坏文件必须还在（改名而非删除）
def test_store_corrupt_file(tmp_path: Path) -> None:
    p = tmp_path / "scheduled.json"
    p.write_text("{ not json", encoding="utf-8")
    store = ScheduleStore(path=p)
    store.load()
    assert store.list() == []
    assert p.exists() is False
    assert any(f.name.startswith("scheduled.corrupt-") for f in tmp_path.iterdir())


# 功能：skip_stale 把过期排期推到下一班且不动禁用任务（补跑跳过的落点）
# 设计：手造两条 next_due 在昨天的记录（模拟合盖一周），一条 enabled=False；
# skip_stale 后前者全部变未来、后者纹丝不动——"禁用不追排期"防复活炸跑
def test_skip_stale(tmp_path: Path) -> None:
    p = tmp_path / "scheduled.json"
    store = ScheduleStore(path=p)
    a = store.create("a", "", "x", "daily", "09:00")
    b = store.create("b", "", "y", "daily", "09:00")
    # 直接篡改内存行再写回：两条都"过期"，b 禁用
    store._tasks[0]["next_due"] = "2020-01-01T09:00:00"  # noqa: SLF001 - 测试注入过期态
    store._tasks[1]["next_due"] = "2020-01-01T09:00:00"  # noqa: SLF001
    store._tasks[1]["enabled"] = False
    store._save()
    assert store.skip_stale(now=REF) == 1  # 只有启用那条被推进
    rows = {t["id"]: t for t in store.list()}
    assert datetime.fromisoformat(rows[a["id"]]["next_due"]) > REF
    assert rows[b["id"]]["next_due"] == "2020-01-01T09:00:00"
