"""_schedule_fire 会话复用语义的单元测试（不建真 daemon，fake SessionManager 即可）"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from iwan_claude.core.app import CoreApp


# 搭建最小 CoreApp 壳：绕过重构造，只注入 _schedule_fire 用到的成员
def _mk_app() -> tuple[CoreApp, list[Any], list[Any]]:
    app = CoreApp.__new__(CoreApp)
    created: list[Any] = []
    sent: list[Any] = []

    class FakeSessions:
        def __init__(self) -> None:
            self.all: list[Any] = []

        async def create(self, mode: str, title: str = "", cwd: str = "") -> Any:
            s = SimpleNamespace(id=f"sess-{len(self.all)}", mode=mode, status="active", title=title)
            self.all.append(s)
            created.append(s)
            return s

        def list_sessions(self) -> list[Any]:
            return list(self.all)

        async def send_message(self, sid: str, content: str, *, run_id: str | None = None) -> None:
            sent.append((sid, content, run_id))

    app._sessions = FakeSessions()  # type: ignore[attr-defined]
    app._sched_sessions = {}  # type: ignore[attr-defined]
    app._sched_last_fire = {}  # type: ignore[attr-defined]
    app._running_runs = set()  # type: ignore[attr-defined]
    return app, created, sent


# 功能：同一任务连续两次 fire 必须复用同一个 chat 会话，消息各发一条
# 设计：fake manager 记录 create/send 调用序列，断言"只建一次 + 两次同 sid"，
#       正是旧 one_shot 实现（每次新建）与新语义的分界；不经 daemon 避免时序噪声
async def test_schedule_fire_reuses_one_session_per_task() -> None:
    app, created, sent = _mk_app()
    task = {"id": "t1", "name": "报时", "prompt": "现在几点", "cwd": ""}
    ok1, d1 = await app._schedule_fire(task)
    await asyncio.gather(*app._running_runs)
    ok2, d2 = await app._schedule_fire(task)
    await asyncio.gather(*app._running_runs)
    assert ok1 and ok2
    assert len(created) == 1
    assert created[0].mode == "chat"
    sid1, sid2 = sent[0][0], sent[1][0]
    assert sid1 == sid2 == created[0].id
    assert len(sent) == 2
    # run_id 通过 "run_id|说明" detail 透传给调度器记账
    assert d1.split("|")[0] and d2.split("|")[0] and d1 != d2


# 功能：常驻会话被用户手动关闭（status=closed）后，下一班 fire 自动新建线程续跑
# 设计：直接在 fake 对象上翻 closed 位，模拟 GUI 关会话的终态；断言"换 id 不换任务"，
#       排除复用死会话导致 send 永远失败的路径
async def test_schedule_fire_recreates_after_manual_close() -> None:
    app, created, sent = _mk_app()
    task = {"id": "t1", "name": "报时", "prompt": "p", "cwd": ""}
    await app._schedule_fire(task)
    await asyncio.gather(*app._running_runs)
    created[0].status = "closed"
    ok, _ = await app._schedule_fire(task)
    await asyncio.gather(*app._running_runs)
    assert ok
    assert len(created) == 2
    assert sent[1][0] == created[1].id != created[0].id


# 功能：daemon 重启（内存映射清空）后，fire 按"定时·"标题从持久层捞回原线程续写
# 设计：模拟重启=清空 _sched_sessions 但 fake store 保留旧会话对象；不捞回的后果
#       是每个重启周期断一次报时历史，标题是 _schedule_fire 唯一跨进程约定
async def test_schedule_fire_reclaims_thread_by_title_after_restart() -> None:
    app, created, sent = _mk_app()
    task = {"id": "t1", "name": "报时", "prompt": "p", "cwd": ""}
    await app._schedule_fire(task)
    await asyncio.gather(*app._running_runs)
    app._sched_sessions.clear()  # 模拟 daemon 重启：映射丢、磁盘会话在
    ok, _ = await app._schedule_fire(task)
    await asyncio.gather(*app._running_runs)
    assert ok
    assert len(created) == 1
    assert sent[1][0] == created[0].id


# 功能：同标题但模式非 chat（如残留 one_shot 旧线程）不许被误捞为续写目标
# 设计：守卫标题回捞的模式条件——误捞 one_shot 会让 send_message 撞上
#       closed/关闭语义，制造比重启断档更隐蔽的怪状态
async def test_schedule_fire_title_reclaim_skips_non_chat() -> None:
    app, created, sent = _mk_app()
    task = {"id": "t1", "name": "报时", "prompt": "p", "cwd": ""}
    await app._schedule_fire(task)
    await asyncio.gather(*app._running_runs)
    # 篡改唯一会话的 mode，模拟旧版 one_shot 残留同名线程
    created[0].mode = "one_shot"
    app._sched_sessions.clear()
    await app._schedule_fire(task)
    await asyncio.gather(*app._running_runs)
    assert len(created) == 2
    assert sent[1][0] == created[1].id


# 功能：上一班 run 未结束（send_message 抛 SESSION_BUSY 类错误）时 fire 仍返回触发成功，
#       但后台 task 消化异常不炸循环
# 设计：fake send 抛异常模拟 busy；断言 done 后 task 无 exception——异常被
#       _schedule_send 吞成日志是"调度循环永不因单会话忙而死"的关键不变式
async def test_schedule_send_swallows_busy_exception() -> None:
    app, created, _ = _mk_app()
    assert app._sessions is not None

    async def busy(sid: str, content: str, *, run_id: str | None = None) -> None:
        raise RuntimeError("session busy")

    app._sessions.send_message = busy  # type: ignore[method-assign]
    task = {"id": "t1", "name": "n", "prompt": "p", "cwd": ""}
    ok, _ = await app._schedule_fire(task)
    assert ok
    tasks = list(app._running_runs)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert not any(isinstance(r, Exception) for r in results)
