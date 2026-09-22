"""
subagent 子系统 2026-09 评审修复回归测试

对应 P0–P2 修复清单：注册表生命周期（run_dir 清理/批次收缩/重复注册取消/
shutdown 收尸）、程序化 spawn_background、前后台异常对称、批量并发闸
（排队不计超时）、取消契约分辨。每条测试钉死一个曾出过问题的具体行为，
防止后续重构把它改回去。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from iwan_claude.core.bus.events import SubagentFinishedEvent, SubagentStartedEvent
from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.types import LlmResponse, UsageStats
from iwan_claude.core.subagent.registry import BackgroundTaskRegistry
from iwan_claude.core.subagent.tool import (
    AgentResultTool,
    SpawnAgentsTool,
    SpawnAgentTool,
    _ChildHandle,
)


# 构造一次性成功返回的假 provider
def _text_provider(result_text: str = "child done") -> Any:
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LlmResponse(
            stop_reason="end_turn",
            tool_calls=[],
            text=result_text,
            usage=UsageStats(0, 0, 0, 0, 0.0),
        )
    )
    return provider


class _ProviderStub:
    """最小 LLM provider 桩：只有 async chat 属性，不用 mock 以免吞掉并发计数闭包"""
    def __init__(self) -> None:
        self.chat: Any = None


# 构造 sleep 指定时长的假 provider，可选 tracker 记录同时在 chat 中的任务峰值
def _slow_provider(sleep_s: float, result_text: str = "slow done", tracker: Any = None) -> Any:
    async def _chat(*args: Any, **kwargs: Any) -> LlmResponse:
        if tracker is not None:
            tracker["active"] += 1
            tracker["max"] = max(tracker["max"], tracker["active"])
        await asyncio.sleep(sleep_s)
        if tracker is not None:
            tracker["active"] -= 1
        return LlmResponse(
            stop_reason="end_turn",
            tool_calls=[],
            text=result_text,
            usage=UsageStats(0, 0, 0, 0, 0.0),
        )

    provider = _ProviderStub()
    provider.chat = _chat
    return provider


# 构造 SpawnAgentTool 及其共享注册表/事件总线
def _make_spawn_tool(
    tmp_path: Path,
    provider: Any = None,
    *,
    registry: BackgroundTaskRegistry | None = None,
    depth: int = 0,
) -> tuple[SpawnAgentTool, BackgroundTaskRegistry, EventBus]:
    bus = EventBus()
    reg = registry or BackgroundTaskRegistry()
    tool = SpawnAgentTool(
        provider=provider or _text_provider(),
        parent_bus=bus,
        parent_run_id="parent-review-fixes",
        permission_manager=None,
        max_steps=5,
        task_registry=reg,
        runs_dir=tmp_path,
        session_id="sess-review",
        depth=depth,
    )
    return tool, reg, bus


# 构造 SpawnAgentsTool（批量）及其共享注册表/事件总线
def _make_spawn_agents_tool(
    tmp_path: Path,
    provider: Any,
    *,
    registry: BackgroundTaskRegistry | None = None,
    depth: int = 0,
) -> tuple[SpawnAgentsTool, BackgroundTaskRegistry, EventBus]:
    bus = EventBus()
    reg = registry or BackgroundTaskRegistry()
    tool = SpawnAgentsTool(
        provider=provider,
        parent_bus=bus,
        parent_run_id="parent-review-fixes",
        permission_manager=None,
        max_steps=5,
        task_registry=reg,
        runs_dir=tmp_path,
        session_id="sess-review",
        llm_model_name="",
        depth=depth,
    )
    return tool, reg, bus


# 订阅总线并把全部事件收集进列表（测试断言事件用）
async def _collect_events(bus: EventBus) -> list[Any]:
    collected: list[Any] = []

    async def _sub(event: Any) -> None:
        collected.append(event)

    bus.subscribe(_sub)
    return collected


# 造一个已完成（done 且无异常）的 asyncio.Task，供纯注册表测试注册用
async def _done_task(ctx: ExecutionContext) -> asyncio.Task[None]:
    async def _noop() -> None:
        return None

    t = asyncio.create_task(_noop())
    await t
    return t


# ═══════════════════ 注册表生命周期 ═══════════════════


# 功能：register 撞 run_id 且旧任务还在跑时，必须先 cancel 旧任务再覆盖注册
# 设计：用 Event 挂住旧任务制造"运行中"，重复注册后断旧 task cancelled、新 task 在位；
#       这是 P0 修复——旧实现静默覆盖会留下没人能取消的孤儿 LLM 循环
@pytest.mark.asyncio
async def test_register_duplicate_cancels_live_old_task() -> None:
    reg = BackgroundTaskRegistry()
    gate = asyncio.Event()

    async def _hold() -> None:
        await gate.wait()

    old = asyncio.create_task(_hold())
    ctx_old = ExecutionContext(run_id="dup-1", goal="old", max_steps=1)
    reg.register("dup-1", old, ctx_old)

    new = asyncio.create_task(_hold())
    ctx_new = ExecutionContext(run_id="dup-1", goal="new", max_steps=1)
    reg.register("dup-1", new, ctx_new)

    await asyncio.sleep(0)
    assert old.cancelled()
    assert not new.done()
    got = reg.get("dup-1")
    assert got is not None and got[0] is new
    new.cancel()
    await asyncio.gather(old, new, return_exceptions=True)


# 功能：prune 清走过期任务时，必须删除其 run_dir 磁盘目录并把随之变空的批次整体删掉
# 设计：run_dir 用真实 tmp_path 子目录（touch 一个 marker 文件），created_at 拨到过去触发 TTL；
#       断 removed==1 + 目录消失 + batch_status 变 None——旧实现三个字典只清一个，
#       批次永远增长且摘要里冒 "unknown" 僵尸行
@pytest.mark.asyncio
async def test_prune_removes_run_dir_and_empty_batch(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    reg = BackgroundTaskRegistry(ttl_after_done_sec=1)
    run_dir = tmp_path / "run-expired"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text("{}", encoding="utf-8")

    ctx = ExecutionContext(run_id="p-1", goal="g", max_steps=1)
    t = await _done_task(ctx)
    reg.register("p-1", t, ctx, batch_id="b-1", run_dir=str(run_dir))
    reg.register_batch("b-1", ["p-1"])
    old = datetime.now(UTC) - timedelta(seconds=10)
    reg._task_meta["p-1"]["finished_at"] = old

    assert reg.prune() == 1
    assert not run_dir.exists()
    assert reg.batch_status("b-1") is None
    assert "b-1" not in reg._batches and "b-1" not in reg._batch_meta


# 功能：prune 只清掉批次里过期的成员，批次记录收缩后仍保留存活任务
# 设计：同批次放一个过期完成任务 + 一个新鲜运行任务，断批次存活列表收缩为后者
#       而不是整体删除——防止清理逻辑矫枉过正误伤还在跑的任务
@pytest.mark.asyncio
async def test_prune_shrinks_partial_batch_keeps_alive() -> None:
    from datetime import UTC, datetime, timedelta

    reg = BackgroundTaskRegistry(ttl_after_done_sec=1)
    ctx_old = ExecutionContext(run_id="a-old", goal="g", max_steps=1)
    t_old = await _done_task(ctx_old)
    reg.register("a-old", t_old, ctx_old, batch_id="b-x")

    keep = asyncio.create_task(asyncio.sleep(30))
    ctx_keep = ExecutionContext(run_id="a-keep", goal="g", max_steps=1)
    reg.register("a-keep", keep, ctx_keep, batch_id="b-x")
    reg.register_batch("b-x", ["a-old", "a-keep"])
    reg._task_meta["a-old"]["finished_at"] = datetime.now(UTC) - timedelta(seconds=10)

    assert reg.prune() == 1
    assert reg._batches["b-x"] == ["a-keep"]
    keep.cancel()
    await asyncio.gather(keep, return_exceptions=True)


# 功能：registry.shutdown 必须先取消全部任务，且返回时任务已全部退出（可 await 收尸）
# 设计：任务内部用 CancelledError 分支置位退出标志；shutdown 返回后立刻断
#       flag 已 True、ctx.status=="cancelled"、返回计数正确——旧实现只 cancel 不 await，
#       daemon 退出时表现为 "Task was destroyed but it is pending"
@pytest.mark.asyncio
async def test_shutdown_cancels_and_awaits_pending() -> None:
    reg = BackgroundTaskRegistry()
    exited: list[bool] = []

    async def _body() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            exited.append(True)
            raise

    t = asyncio.create_task(_body())
    ctx = ExecutionContext(run_id="sd-1", goal="g", max_steps=1)
    reg.register("sd-1", t, ctx)
    t2 = asyncio.create_task(asyncio.sleep(30))
    ctx2 = ExecutionContext(run_id="sd-2", goal="g", max_steps=1)
    reg.register("sd-2", t2, ctx2)

    await asyncio.sleep(0)  # 让两个任务真正进入 await，否则取消在协程体开始前生效、分支不进
    n = await reg.shutdown()
    assert n == 2
    assert exited == [True]
    assert t.done() and t2.done()
    assert ctx.status == "cancelled"


# 功能：task_ids_in_batch 只返回仍在本注册表中的批次成员 run_id
# 设计：先注册再手动从 _tasks 摘掉一个（模拟 prune 后的元数据残留），
#       断查询结果排除已摘除项——它替代了批量工具以前直接翻 _tasks 私袋的越界代码
@pytest.mark.asyncio
async def test_task_ids_in_batch_only_live() -> None:
    reg = BackgroundTaskRegistry()
    for rid in ("t1", "t2"):
        ctx = ExecutionContext(run_id=rid, goal="g", max_steps=1)
        t = await _done_task(ctx)
        reg.register(rid, t, ctx, batch_id="b-live")
    reg._tasks.pop("t2")
    ids = reg.task_ids_in_batch("b-live")
    assert ids == ["t1"]


# 功能：meta() 返回的是拷贝，改它不影响注册表内部状态
# 设计：agent_result 展示层拿它取 created_at/description；若返回内部字典引用，
#       展示层的任何"顺手改一下"都会污染生命周期数据
@pytest.mark.asyncio
async def test_meta_returns_copy() -> None:
    reg = BackgroundTaskRegistry()
    ctx = ExecutionContext(run_id="m1", goal="g", max_steps=1)
    t = await _done_task(ctx)
    reg.register("m1", t, ctx, description="keep me")
    m = reg.meta("m1")
    assert m is not None
    m["description"] = "tampered"
    assert reg.meta("m1")["description"] == "keep me"
    assert reg.meta("nope") is None


# ═══════════════════ SpawnAgentTool ═══════════════════


# 功能：runs_dir 缺省不再回退 cwd，构造期必须 ValueError 响亮失败
# 设计：daemon 的 cwd 是服务目录，旧默认值把子 Agent events.jsonl 埋进没人看的路径；
#       pytest.raises 直接构造一次即钉死，不需要跑完整循环
def test_runs_dir_none_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit runs_dir"):
        SpawnAgentTool(
            provider=_text_provider(),
            parent_bus=EventBus(),
            parent_run_id="p",
            permission_manager=None,
            max_steps=5,
            runs_dir=None,
        )


# 功能：spawn_background 以 (run_id, None) / (None, 错误文案) 结构化返回，不再靠调用方 parse 文案
# 设计：成功路断 run_id 非空且注册表在位；失败路用 depth=2 触发嵌套上限，断 err 非空且 rid 为 None
#       ——旧批量工具从 ToolResult 文案 split("run_id=") 提取，文案一改就静默丢任务
@pytest.mark.asyncio
async def test_spawn_background_structured_returns(tmp_path: Path) -> None:
    tool, reg, _ = _make_spawn_tool(tmp_path)
    rid, err = await tool.spawn_background(description="d", prompt="p")
    assert err is None and rid is not None
    assert reg.get(rid) is not None

    deep, _, _ = _make_spawn_tool(tmp_path, depth=2)
    rid2, err2 = await deep.spawn_background(description="d", prompt="p")
    assert rid2 is None and err2 is not None and "nesting limit" in err2


# 功能：显式指定但查无此 profile 的 subagent_type 必须报错，不得静默降级成默认角色
# 设计：loader.load 对未知名返回 None 而非 raise，旧代码 except 分支是死路；
#       直接调 spawn_background 传不存在类型，断 (None, 错误)——角色承诺落空属 fail-closed 违例
@pytest.mark.asyncio
async def test_unknown_subagent_type_fails_loud(tmp_path: Path) -> None:
    tool, reg, _ = _make_spawn_tool(tmp_path)
    rid, err = await tool.spawn_background(description="d", prompt="p", subagent_type="ghost_role_x")
    assert rid is None
    assert err is not None and "unknown subagent_type" in err
    assert reg.all() == []


# 功能：前台模式子循环裸异常必须被兜住——status=failed、is_error 结果、照常发 Finished 事件
# 设计：monkeypatch _prepare_child 塞入只负责抛 RuntimeError 的桩 loop，
#       绕开 AgentLoop 自身会吞异常的干扰，专测 invoke 前台新增的 except Exception 分支；
#       Finished 事件用 bus 订阅收集，断 status=="failed"（旧实现异常直接炸穿父循环）
@pytest.mark.asyncio
async def test_foreground_exception_becomes_error_result(tmp_path: Path) -> None:
    tool, _, bus = _make_spawn_tool(tmp_path)
    events = await _collect_events(bus)

    class _BoomLoop:
        async def run(self, ctx: ExecutionContext) -> None:
            raise RuntimeError("kaboom")

    ctx = ExecutionContext(run_id="fg-err", goal="g", max_steps=1)
    handle = _ChildHandle(
        run_id="fg-err",
        loop=_BoomLoop(),  # type: ignore[arg-type]
        context=ctx,
        bus=EventBus(),
        run_path=tmp_path / "fg-err",
        timeout=5.0,
        description="boom",
    )
    handle.run_path.mkdir(parents=True, exist_ok=True)

    async def _fake_prepare(*a: Any, **k: Any) -> tuple[_ChildHandle | None, Any]:
        return handle, None

    tool._prepare_child = _fake_prepare  # type: ignore[method-assign]

    result = await tool.invoke({"description": "d", "prompt": "p"})
    assert result.is_error
    assert "kaboom" in result.content
    assert ctx.status == "failed" and "exception" in ctx.reason
    finished = [e for e in events if isinstance(e, SubagentFinishedEvent)]
    assert finished and finished[-1].status == "failed"


# 功能：批量后台模式下并发闸排队时间不计入子任务超时
# 设计：sem=1、每任务 sleep 0.08、timeout 0.15——第三个任务从创建到开跑排队 ~0.16s，
#       若超时像旧实现那样包住排队阶段它必被误杀；新实现全部 success 即证计时从获闸开始
@pytest.mark.asyncio
async def test_gate_queue_wait_not_counted_in_timeout(tmp_path: Path) -> None:
    tool, reg, _ = _make_spawn_tool(tmp_path, _slow_provider(0.08, "ok"))
    sem = asyncio.Semaphore(1)
    rids = []
    for i in range(3):
        rid, err = await tool.spawn_background(
            description=f"q{i}", prompt="p", timeout_sec=0.15, gate=sem
        )
        assert err is None and rid is not None
        rids.append(rid)
    tasks = [reg.get(r)[0] for r in rids]  # type: ignore[index]
    await asyncio.gather(*tasks)
    statuses = [reg.get(r)[1].status for r in rids]  # type: ignore[index]
    assert statuses == ["success"] * 3, "排队时间被计入超时：任务被误杀"


# 功能：两个独立 SpawnAgentTool 实例共享同一注册表时，互相可见对方的后台任务
# 设计：对应 P0-1 的根因——AgentRunner 每次 prompt run 重建，旧版每 runner 一个注册表
#       导致 run_id 跨 run 必死。这里用两个 tool 实例模拟两次 run，
#       B 的 AgentResultTool 能取到 A 注册的任务结果即证所有权上移有效
@pytest.mark.asyncio
async def test_shared_registry_visible_across_tool_instances(tmp_path: Path) -> None:
    shared = BackgroundTaskRegistry()
    tool_a, _, _ = _make_spawn_tool(tmp_path, _text_provider("from-A"), registry=shared)
    tool_b, _, _ = _make_spawn_tool(tmp_path, _text_provider("from-B"), registry=shared)

    rid, err = await tool_a.spawn_background(description="a", prompt="p")
    assert err is None and rid is not None
    entry = shared.get(rid)
    assert entry is not None
    await entry[0]

    res = await AgentResultTool(shared).invoke({"run_id": rid})
    assert not res.is_error
    assert "from-A" in res.content
    assert shared.meta(rid)["description"] == "a"


# ═══════════════════ SpawnAgentsTool ═══════════════════


# 功能：wait=true 时任一任务启动失败，其余未启动任务必须被跳过（拉闸），错误聚合进返回
# 设计：tasks=[坏 profile, 正常]、max_concurrency=1——坏任务先跑并拉闸，正常任务获闸时
#       看到 flag 直接 skip；断结果 is_error、含 "unknown subagent_type"，且 parent bus
#       收到 0 个 Started 事件（跳过发生在 prepare 前，不产生半启动任务）
@pytest.mark.asyncio
async def test_wait_batch_start_failure_skips_remaining(tmp_path: Path) -> None:
    tool, _, bus = _make_spawn_agents_tool(tmp_path, _text_provider())
    events = await _collect_events(bus)
    result = await tool.invoke({
        "tasks": [
            {"description": "bad", "prompt": "p", "subagent_type": "no_such_profile_xyz"},
            {"description": "good", "prompt": "p"},
        ],
        "max_concurrency": 1,
        "wait": True,
    })
    assert result.is_error
    assert "failed to start" in result.content and "succeeded=0/2" in result.content
    assert "unknown subagent_type" in result.content
    assert [e for e in events if isinstance(e, SubagentStartedEvent)] == []


# 功能：批量等待中子任务被外部 cancel 属正常结局，父协程吞掉它继续交付该 rid
# 设计：wait=true 挂住一个慢任务，从 Started 事件拿到 rid 后 registry.cancel——
#       _run_one 的 CancelledError 分辨分支（t.cancelled() 且自身未被取消）应放行，
#       批次以 cancelled 统计正常收尾；若有人把吞取消改成一律上抛，此测试先红
@pytest.mark.asyncio
async def test_child_cancel_while_batch_awaiting_is_tolerated(tmp_path: Path) -> None:
    tool, reg, bus = _make_spawn_agents_tool(tmp_path, _slow_provider(1.0))
    started: list[str] = []

    async def _watch(event: Any) -> None:
        if isinstance(event, SubagentStartedEvent):
            started.append(event.run_id)

    bus.subscribe(_watch)
    inv = asyncio.create_task(tool.invoke({
        "tasks": [{"description": "s", "prompt": "p"}],
        "max_concurrency": 1,
        "wait": True,
    }))
    while not started:
        await asyncio.sleep(0.01)
    assert reg.cancel(started[0], reason="user")
    result = await inv
    assert "cancelled=1" in result.content


# 功能：取消冲着批量等待协程本身来时，CancelledError 必须原样上抛（不违反 asyncio 取消契约）
# 设计：同上一测试的场景，但 cancel 的是 invoke 外层任务——_run_one 里
#       cur.cancelling()>0 分辨出"这次取消不是子任务的"，必须 raise；
#       断外层收到 CancelledError，结尾 shutdown 收尸子任务避免悬挂告警
@pytest.mark.asyncio
async def test_external_cancel_propagates_out_of_batch_wait(tmp_path: Path) -> None:
    tool, reg, bus = _make_spawn_agents_tool(tmp_path, _slow_provider(30))
    started: list[str] = []

    async def _watch(event: Any) -> None:
        if isinstance(event, SubagentStartedEvent):
            started.append(event.run_id)

    bus.subscribe(_watch)
    inv = asyncio.create_task(tool.invoke({
        "tasks": [{"description": "s", "prompt": "p"}],
        "max_concurrency": 1,
        "wait": True,
    }))
    while not started:
        await asyncio.sleep(0.01)
    inv.cancel()
    with pytest.raises(asyncio.CancelledError):
        await inv
    await reg.shutdown()


# 功能：wait=false 批量后台任务受 max_concurrency 约束，排队不并发起飞
# 设计：sem=1 + 每任务 sleep 0.05 + tracker 记录同时进入 chat 的最大任务数；
#       旧实现后台批不设闸、description 宣称防 429 实则全量起飞，max_active 会到 3；
#       新实现必须恒为 1
@pytest.mark.asyncio
async def test_background_batch_respects_concurrency_cap(tmp_path: Path) -> None:
    tracker = {"active": 0, "max": 0}
    tool, reg, _ = _make_spawn_agents_tool(tmp_path, _slow_provider(0.05, "x", tracker))
    result = await tool.invoke({
        "tasks": [{"description": f"t{i}", "prompt": "p"} for i in range(3)],
        "max_concurrency": 1,
        "wait": False,
    })
    assert not result.is_error
    batch_id = result.content.split("batch_id=")[1].split(".")[0]
    tasks = [reg.get(r)[0] for r in reg.task_ids_in_batch(batch_id)]  # type: ignore[index]
    await asyncio.gather(*tasks)
    assert tracker["max"] == 1
    assert len(reg.task_ids_in_batch(batch_id)) == 3
