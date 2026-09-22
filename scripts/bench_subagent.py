"""subagent 子系统本地性能基准（mock provider，测框架自身开销而非 LLM 延迟）

用法：uv run python scripts/bench_subagent.py

测量范围声明：LLM 用假 provider（可控 sleep / 立即返回），所以这里量到的是
注册表、事件桥、并发闸、spawn 生命周期这些"我们写的代码"的成本；
真实部署里每次子 Agent 的墙钟大头是 provider 往返，基准数字用于回归对比。
"""
from __future__ import annotations

import asyncio
import math
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# Windows 控制台默认 GBK，强制 stdout 走 UTF-8 否则中文/µ 直接炸
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from iwan_claude.core.bus.events import SubagentStartedEvent  # noqa: E402
from iwan_claude.core.context import ExecutionContext  # noqa: E402
from iwan_claude.core.events.bus import EventBus  # noqa: E402
from iwan_claude.core.llm.types import LlmResponse, UsageStats  # noqa: E402
from iwan_claude.core.subagent.registry import BackgroundTaskRegistry  # noqa: E402
from iwan_claude.core.subagent.tool import SpawnAgentsTool, SpawnAgentTool  # noqa: E402


# 构造立即返回 end_turn 的假 provider
def _instant_provider(text: str = "ok") -> Any:
    class _P:
        async def chat(self, *a: Any, **k: Any) -> LlmResponse:
            return LlmResponse(
                stop_reason="end_turn", tool_calls=[], text=text,
                usage=UsageStats(0, 0, 0, 0, 0.0),
            )
    return _P()


# 构造每次 chat 固定 sleep 时长的假 provider（模拟 LLM 延迟、用于并发扩展性测量）
def _sleepy_provider(sleep_s: float, text: str = "ok") -> Any:
    class _P:
        async def chat(self, *a: Any, **k: Any) -> LlmResponse:
            await asyncio.sleep(sleep_s)
            return LlmResponse(
                stop_reason="end_turn", tool_calls=[], text=text,
                usage=UsageStats(0, 0, 0, 0, 0.0),
            )
    return _P()


# 通用计时器：跑 fn 并返回毫秒耗时
async def _time_ms(fn: Callable[[], Awaitable[Any]]) -> float:
    t0 = time.perf_counter()
    await fn()
    return (time.perf_counter() - t0) * 1000.0


# 基准 1：注册表在 1k / 5k 规模下的 register / batch_status / cancel_all / prune 成本
async def bench_registry_scale() -> list[str]:
    lines = ["[1] BackgroundTaskRegistry 规模成本"]
    for n in (1000, 5000):
        reg = BackgroundTaskRegistry(ttl_after_done_sec=3600)

        async def _register_all(n: int = n, reg: BackgroundTaskRegistry = reg) -> None:
            for i in range(n):
                ctx = ExecutionContext(run_id=f"r{i}", goal="g", max_steps=1)
                t = asyncio.ensure_future(asyncio.sleep(0))  # 秒完成任务，让 prune/cancel 有真实可扫的表
                reg.register(f"r{i}", t, ctx)

        ms_reg = await _time_ms(_register_all)

        # cancel_all：任务尚未落 done 的"活表"取消成本（真实使用场景）
        ms_cancel = await _time_ms(lambda: _call(lambda: reg.cancel_all()))
        await asyncio.sleep(0.05)  # 让全部 sleep(0) 任务真正落到 done/cancelled

        # batch_status：单批 100 个成员查询一次
        batch_ids = [f"r{i}" for i in range(100)]
        reg.register_batch("big", batch_ids)
        ms_batch = await _time_ms(lambda: _call(lambda: reg.batch_status("big")))

        # prune：全部完成时刻拨到过去 + ttl=1 → 全表扫描删除
        cutoff = datetime.now(UTC) - timedelta(seconds=10)
        for meta in reg._task_meta.values():
            meta["finished_at"] = cutoff
            if "cancelled_at" in meta:
                meta["cancelled_at"] = cutoff
        ms_prune = await _time_ms(lambda: _call(lambda: reg.prune(ttl_override_sec=1)))

        leftover = len(reg._tasks)
        lines.append(
            f"  n={n:>5}  register={ms_reg:8.1f}ms ({ms_reg / n * 1000:6.2f}µs/条)  "
            f"batch_status(100)={ms_batch:6.2f}ms  cancel_all={ms_cancel:7.1f}ms  "
            f"prune-all={ms_prune:7.1f}ms  prune 后残留={leftover}"
        )
    return lines


# 同步调用包装（_time_ms 要求协程）
def _call(fn: Callable[[], Any]) -> Any:
    async def _c() -> Any:
        return fn()
    return _c()


# 基准 2：批量并发扩展性——12 个任务、每任务 LLM 延迟 60ms，测不同闸上限的墙钟
async def bench_concurrency_scaling() -> list[str]:
    lines: list[str] = []
    lines.append("[2] SpawnAgentsTool 并发扩展性（12 任务 × 60ms 假延迟，wait=true）")
    with TemporaryDirectory() as td:
        tmp = Path(td)
        n_tasks, sleep_s = 12, 0.06
        for cap in (1, 3, 6, 12):
            bus = EventBus()
            reg = BackgroundTaskRegistry()
            tool = SpawnAgentsTool(
                provider=_sleepy_provider(sleep_s),
                parent_bus=bus,
                parent_run_id="bench",
                permission_manager=None,
                max_steps=3,
                task_registry=reg,
                runs_dir=tmp,
                session_id="bench",
                llm_model_name="",
            )
            tasks = [{"description": f"t{i}", "prompt": "p"} for i in range(n_tasks)]
            wall_s = (await _time_ms(lambda: tool.invoke({
                "tasks": tasks, "max_concurrency": cap, "wait": True,
            }))) / 1000.0
            theory = math.ceil(n_tasks / cap) * sleep_s
            lines.append(
                f"  cap={cap:>2}  墙钟={wall_s * 1000:7.0f}ms  理论下限={theory * 1000:5.0f}ms  "
                f"效率={theory / wall_s * 100:5.1f}%  吞吐={n_tasks / wall_s:6.1f} 子Agent/秒"
            )
            await reg.shutdown()
    return lines


# 基准 3：事件桥（child_bus._bridge → parent_bus）的每事件转发开销
async def bench_event_bridge() -> list[str]:
    lines = ["[3] 事件总线与子→父桥接（每 1000 事件毫秒数）"]
    n = 10000
    parent = EventBus()
    sink: list[Any] = []

    async def _collect(e: Any) -> None:
        sink.append(e)

    parent.subscribe(_collect)
    evt = SubagentStartedEvent(run_id="x", parent_run_id="p", description="d", ts="")
    ms_direct = await _time_ms(lambda: _publish_n(parent, evt, n))

    parent2 = EventBus()
    sink2: list[Any] = []

    async def _collect2(e: Any) -> None:
        sink2.append(e)

    parent2.subscribe(_collect2)
    child = EventBus()

    async def _bridge(e: Any) -> None:
        await parent2.publish(e)

    child.subscribe(_bridge)
    ms_bridged = await _time_ms(lambda: _publish_n(child, evt, n))

    overhead = (ms_bridged - ms_direct) / (n / 1000)
    lines.append(
        f"  直发 parent={ms_direct:7.1f}ms/{n}  经子→父桥={ms_bridged:7.1f}ms/{n}  "
        f"桥接净增≈{overhead:.3f}ms/千事件"
    )
    return lines


# 批量发布 n 个同一事件
async def _publish_n(bus: EventBus, evt: Any, n: int) -> None:
    for _ in range(n):
        await bus.publish(evt)


# 基准 4：单次 spawn 的框架开销——后台启动路径与前台完整跑一 loop 路径
async def bench_spawn_overhead() -> list[str]:
    lines: list[str] = []
    lines.append("[4] spawn 生命周期开销（provider 立即返回，测框架纯成本）")
    with TemporaryDirectory() as td:
        tmp = Path(td)
        reg = BackgroundTaskRegistry()
        tool = SpawnAgentTool(
            provider=_instant_provider(),
            parent_bus=EventBus(),
            parent_run_id="bench",
            permission_manager=None,
            max_steps=3,
            task_registry=reg,
            runs_dir=tmp,
            session_id="bench",
            llm_model_name="",
        )
        n_bg = 200

        async def _spawn_bg() -> None:
            rids = []
            for i in range(n_bg):
                rid, err = await tool.spawn_background(description=f"d{i}", prompt="p")
                assert rid is not None and err is None
                rids.append(rid)
            await asyncio.gather(*[reg.get(r)[0] for r in rids])  # type: ignore[index]

        ms_bg = await _time_ms(_spawn_bg)
        lines.append(f"  后台 spawn+跑完: {ms_bg:7.1f}ms / {n_bg} 次 → {ms_bg / n_bg * 1000:6.0f}µs/次")

        n_fg = 100

        async def _spawn_fg() -> None:
            for _ in range(n_fg):
                r = await tool.invoke({"description": "d", "prompt": "p"})
                assert not r.is_error

        ms_fg = await _time_ms(_spawn_fg)
        lines.append(f"  前台完整 run  : {ms_fg:7.1f}ms / {n_fg} 次 → {ms_fg / n_fg * 1000:6.0f}µs/次（含 EventWriter 写盘）")

        # 基准 5：稳态翻搅——500 短后台任务反复 prune 后注册表/磁盘不泄漏
        async def _churn() -> None:
            rids = []
            for i in range(500):
                rid, _ = await tool.spawn_background(description=f"c{i}", prompt="p")
                if rid:
                    rids.append(rid)
            await asyncio.gather(*[reg.get(r)[0] for r in rids if reg.get(r)])  # type: ignore[index]

        ms_churn = await _time_ms(_churn)
        cutoff = datetime.now(UTC) - timedelta(seconds=2)
        for meta in reg._task_meta.values():
            meta["finished_at"] = cutoff
        ms_prune = await _time_ms(lambda: _call(lambda: reg.prune(ttl_override_sec=1)))
        dirs_left = sum(1 for p in tmp.iterdir() if p.is_dir())
        lines.append(
            f"[5] 稳态翻搅: 500 后台任务跑完={ms_churn:6.0f}ms  prune={ms_prune:5.1f}ms  "
            f"prune 后注册表={len(reg._tasks)} 条 / 磁盘残留目录={dirs_left} 个"
        )
    return lines


# 主入口：顺序跑全部基准并打印汇总
async def main() -> None:
    print("=" * 78)
    print("subagent 子系统性能基准  (Windows asyncio 计时精度 ~15ms，小值看趋势)")
    print("=" * 78)
    for block in (
        await bench_registry_scale(),
        await bench_concurrency_scaling(),
        await bench_event_bridge(),
        await bench_spawn_overhead(),
    ):
        for line in block:
            print(line)
        print("-" * 78)


if __name__ == "__main__":
    asyncio.run(main())
