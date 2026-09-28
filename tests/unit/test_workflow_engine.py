"""
工作流引擎装配层单测 — 事件时序 / 上游传递 / fail-fast / 并发闸门 / 停机收口 / 用户取消

【学习要点】launch/await_child/cancel_child/on_node/on_run_finished 五个协作者
全部换成内存 fake：引擎对 subagent、总线、LLM 零感知，这一层能测的全部是
"时序与记账"——恰好是它存在的唯一理由。cancel_child 的 fake 只记击杀名单
（Rig 的"子 Agent"本就是一串文本，没有真 task 可杀），击杀时序=名单内容。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from iwan_claude.core.workflow.engine import NodeLaunchCtx, WorkflowEngine
from iwan_claude.core.workflow.store import WorkflowStore


# 造一张线性两节点图（a0→a1）进 tmp 存储，返回 (store, 定义行)
def _mk(tmp_path: Path, tasks: list[dict[str, Any]]) -> tuple[WorkflowStore, dict[str, Any]]:
    st = WorkflowStore(tmp_path / "w.json", tmp_path / "r.json")
    row = st.upsert("", "引擎测试", "", tasks)
    return st, row


# 引擎测试台：记录事件序列与每个节点收到的 prompt，行为可逐用例覆写。
# child_run_id 统一取 "node-<节点名>"：await 覆写表按它匹配失败/特殊产出
class Rig:
    def __init__(self, st: WorkflowStore) -> None:
        self._st = st
        self.events: list[tuple[str, str]] = []
        self.finished: list[tuple[str, str, str]] = []  # (status, error, session_id)
        self.prompts: dict[str, str] = {}
        self.launch_fail: str = ""       # 非空=命中节点启动失败
        self.await_result: dict[str, tuple[bool, str]] = {}  # 节点→(ok, text) 覆写
        self.cancelled_children: list[str] = []  # cancel_child fake 的击杀名单

    # 击杀 fake：只登记不真杀；恒返 True——"杀没杀到"是注册表的事，引擎侧只关心登记过
    def cancel_child(self, child: str) -> bool:
        self.cancelled_children.append(child)
        return True

    # launch：登记 prompt，按覆写表决定启动成败
    async def launch(self, ctx: NodeLaunchCtx) -> tuple[str, str]:
        self.prompts[ctx.node] = ctx.prompt
        if self.launch_fail and ctx.node in self.launch_fail:
            return "", "provider 不可用"
        return f"node-{ctx.node}", ""

    # await_child：覆写表优先，默认成功并产出带 child id 的文本
    async def await_child(self, child: str) -> tuple[bool, str]:
        for node, res in self.await_result.items():
            if child == f"node-{node}":
                return res
        return True, f"OUT-{child}"

    async def on_node(self, tr: Any) -> None:
        self.events.append((tr.node, tr.status))

    async def on_finished(self, tr: Any) -> None:
        self.finished.append((tr.status, tr.error, tr.session_id))
        # 生产里 touch_last_run 挂在 app.py 的广播回调上（引擎不碰定义行），
        # 测试台照抄这条副作用，让"三层记账"断言在单测里也闭环
        self._st.touch_last_run(tr.workflow_id, tr.run_id, tr.status)

    # 组装引擎；max_parallel_runs 等参数透传，个别协作者可在用例里换私有字段。
    # cancel_child 恒接线：普通用例本就不触发它，反证"没点取消就没有击杀"
    def build(self, **kw: Any) -> WorkflowEngine:
        return WorkflowEngine(
            self._st, self.launch, self.await_child, self.on_node, self.on_finished,
            cancel_child=self.cancel_child, **kw,
        )


# 功能：两节点串行图全流程——事件序 running/ok×2 + finished(success)，记账同步落盘
# 设计：事件序列用整表 == 断言而非"包含"：顺序（A 终态先于 B 起跳）是分层
# 调度的核心不变式，包含式断言会放过"B 抢跑"这类真 bug；同时断 run 行终态
# 与两个节点 ok+output 快照、定义行 last_status 反范式——一次跑通三层记账
async def test_happy_path_events_and_accounting(tmp_path: Path) -> None:
    tasks = [{"name": "A", "prompt": "做A", "depends_on": []},
             {"name": "B", "prompt": "做B", "depends_on": ["A"]}]
    st, wf = _mk(tmp_path, tasks)
    rig = Rig(st)
    eng = rig.build()
    run_id, err = eng.start_run(wf["id"])
    assert err == "" and run_id
    await asyncio.sleep(0.2)
    assert rig.events == [("A", "running"), ("A", "ok"), ("B", "running"), ("B", "ok")]
    assert rig.finished and rig.finished[0][0] == "success"
    row = st.get_run(run_id)
    assert row["status"] == "success"  # type: ignore[index]
    assert row["nodes"]["A"]["output"] == "OUT-node-A"  # type: ignore[index]
    assert st.get(wf["id"])["last_status"] == "success"
    assert eng.active_run_for(wf["id"]) == ""  # 收尾后自动摘登记


# 功能：下游 prompt 真的拼进上游产出；入口节点 prompt 原样不带上游段
# 设计：compose 是工作流区别于"三个独立 subagent"的唯一语义——必须断到
# launch 收到的 ctx.prompt 内容层面；"【上游节点产出】"哨兵串按节点区分
# 有无，A（无依赖）不许出现、B（有依赖）必须出现且携带 A 的输出文本
async def test_upstream_output_composed_into_prompt(tmp_path: Path) -> None:
    tasks = [{"name": "A", "prompt": "指令A", "depends_on": []},
             {"name": "B", "prompt": "指令B", "depends_on": ["A"]}]
    st, wf = _mk(tmp_path, tasks)
    rig = Rig(st)
    eng = rig.build()
    eng.start_run(wf["id"])
    await asyncio.sleep(0.2)
    assert rig.prompts["A"] == "指令A"
    assert rig.prompts["B"].startswith("指令B")
    assert "【上游节点产出】" in rig.prompts["B"]
    assert "OUT-node-A" in rig.prompts["B"]


# 功能：每路上游产出截断到 2000 字，防子 Agent 上下文被长产出挤爆
# 设计：给 A 造 5000 字输出，断 B prompt 里含"…前 2000 字"且不含第 2001
# 字的标记符——只断 prompt 总长会被本节点指令长度干扰，标记符落点才精确
async def test_upstream_truncation(tmp_path: Path) -> None:
    tasks = [{"name": "A", "prompt": "a", "depends_on": []},
             {"name": "B", "prompt": "b", "depends_on": ["A"]}]
    st, wf = _mk(tmp_path, tasks)
    rig = Rig(st)
    rig.await_result["A"] = (True, "X" * 2000 + "MARKER" + "Y" * 3000)
    eng = rig.build()
    eng.start_run(wf["id"])
    await asyncio.sleep(0.2)
    assert "MARKER" not in rig.prompts["B"]
    assert "X" * 2000 in rig.prompts["B"]


# 功能：launch 失败 → 该节点 fail（detail 含原因）、下游节点根本不启动、run 终态 failed
# 设计：fail-fast 的三个可分观测面一次断全：事件表（A fail 且无 B running）、
# run 行（B 仍 pending——未被调度的节点灰显的数据依据）、finished 文案；
# child_run_id 为空的 fail 行也要能立住（启动即败没有深链可给）
async def test_launch_failure_fails_fast(tmp_path: Path) -> None:
    tasks = [{"name": "A", "prompt": "a", "depends_on": []},
             {"name": "B", "prompt": "b", "depends_on": ["A"]}]
    st, wf = _mk(tmp_path, tasks)
    rig = Rig(st)
    rig.launch_fail = "A"
    eng = rig.build()
    run_id, _ = eng.start_run(wf["id"])
    await asyncio.sleep(0.2)
    assert rig.events == [("A", "running"), ("A", "fail")]
    row = st.get_run(run_id)
    assert row["status"] == "failed"  # type: ignore[index]
    assert row["nodes"]["A"]["status"] == "fail"  # type: ignore[index]
    assert row["nodes"]["A"]["detail"].startswith("启动失败")  # type: ignore[index]
    assert row["nodes"]["B"]["status"] == "pending"  # type: ignore[index]
    assert rig.finished[0][0] == "failed" and "A" in rig.finished[0][1]


# 功能：子 Agent 以失败终态收尾（await 返回 not ok）→ 节点 fail、detail 用 reason 原文
# 设计：与 launch 失败分测——那是"没跑起来"，这是"跑了但砸了"；detail 走
# await 通道的文本，断精确字符串防止两路径共用文案时互相污染
async def test_child_failure_marks_node_fail(tmp_path: Path) -> None:
    tasks = [{"name": "A", "prompt": "a", "depends_on": []}]
    st, wf = _mk(tmp_path, tasks)
    rig = Rig(st)
    rig.await_result["A"] = (False, "限流 429")
    eng = rig.build()
    run_id, _ = eng.start_run(wf["id"])
    await asyncio.sleep(0.2)
    assert rig.events == [("A", "running"), ("A", "fail")]
    assert st.get_run(run_id)["nodes"]["A"]["detail"] == "限流 429"  # type: ignore[index]
    assert rig.finished[0][0] == "failed"


# 功能：同一工作流进行中重复 start_run 被拒；并行 run 数超上限被拒
# 设计：不等待第一次跑完就二次 start_run（A 挂在一个 never-resolve 的
# await 上制造确定性的"进行中"窗口）——比 sleep+竞态可靠；两个不同工作流
# 占满 max_parallel_runs=2 后第三个必须吃闭门羹，文案含"上限"
async def test_run_gates(tmp_path: Path) -> None:
    tasks_a = [{"name": "A", "prompt": "a", "depends_on": []}]
    st, wf1 = _mk(tmp_path, tasks_a)
    st2_row = st.upsert("", "w2", "", tasks_a)
    gate = asyncio.Event()

    rig = Rig(st)
    eng = rig.build()

    # 把 await_child 换成挂起：first run 卡在 running 里
    async def hang(child: str) -> tuple[bool, str]:
        await gate.wait()
        return True, "late"
    eng._await_child = hang  # type: ignore[method-assign]

    rid1, _ = eng.start_run(wf1["id"])
    assert eng.active_run_for(wf1["id"]) == rid1
    dup_id, dup_err = eng.start_run(wf1["id"])
    assert dup_id == "" and "进行中" in dup_err

    rid2, _ = eng.start_run(st2_row["id"])
    assert rid2
    rid3, err3 = eng.start_run(st.upsert("", "w3", "", tasks_a)["id"])
    assert rid3 == "" and "上限" in err3
    gate.set()
    await eng.shutdown()


# 功能：start_run 对不存在的工作流 id 返回中文错误而非抛异常
# 设计：RPC 薄壳把 (run_id, err) 直接翻译成 ok=False——异常穿透会炸成
# JSON-RPC internal error，用户看到的就不是"工作流不存在"而是未知错误
async def test_start_run_unknown_id(tmp_path: Path) -> None:
    st, _ = _mk(tmp_path, [{"name": "A", "prompt": "a", "depends_on": []}])
    rig = Rig(st)
    rid, err = rig.build().start_run("ghost")
    assert rid == "" and "不存在" in err


# 功能：shutdown 取消活跃 run——行标 interrupted、活跃表清空、二次 shutdown 幂等
# 设计：A 挂在 gate 上人为造"进行中"，shutdown 必须 ①等协程收尸（返回后
# 断言即可见账落地，不需要再 sleep）②interrupted 而非 failed（停机与跑砸
# 是两种历史，GUI 文案和排障方向完全不同）；广播在取消路径上不保证，
# finished 列表为空是可接受语义——账比通知重要。Rig 现已恒接 cancel_child：
# 停机路径必须零击杀、零 cancelled 落账——"用户取消 vs 关停"的分叉焊死在这
async def test_shutdown_marks_interrupted(tmp_path: Path) -> None:
    st, wf = _mk(tmp_path, [{"name": "A", "prompt": "a", "depends_on": []}])
    gate = asyncio.Event()
    rig = Rig(st)
    eng = rig.build()

    async def hang(child: str) -> tuple[bool, str]:
        await gate.wait()
        return True, "late"
    eng._await_child = hang  # type: ignore[method-assign]

    run_id, _ = eng.start_run(wf["id"])
    await eng.shutdown()
    assert st.get_run(run_id)["status"] == "interrupted"  # type: ignore[index]
    assert eng._active == {}
    assert rig.cancelled_children == []  # 停机不杀子：击杀是用户取消的专属语义
    assert rig.finished == []  # 且无 cancelled 广播泄漏进停机路径
    await eng.shutdown()  # 幂等：空表二次关停无事发生
    gate.set()


# 功能：同层两兄弟并行跑、都成功，事件序允许交错但兄弟必先后继必后
# 设计：A 扇出 B/C（并发度 3 容得下）——继任者 D 的事件必须全部排在
# B、C 的 ok 之后（分层屏障），兄弟之间不排序（并行语义，钉了就是假 flaky）；
# 用"集合关系"表达：D running 之前 ok 集合 == {B, C}
async def test_sibling_parallel_within_layer(tmp_path: Path) -> None:
    tasks = [
        {"name": "A", "prompt": "a", "depends_on": []},
        {"name": "B", "prompt": "b", "depends_on": ["A"]},
        {"name": "C", "prompt": "c", "depends_on": ["A"]},
        {"name": "D", "prompt": "d", "depends_on": ["B", "C"]},
    ]
    st, wf = _mk(tmp_path, tasks)
    rig = Rig(st)
    eng = rig.build()
    run_id, _ = eng.start_run(wf["id"])
    await asyncio.sleep(0.3)
    seq = rig.events
    d_idx = seq.index(("D", "running"))
    assert {("B", "ok"), ("C", "ok")} < set(seq[:d_idx])
    assert rig.finished[0][0] == "success"
    row = st.get_run(run_id)
    assert all(n["status"] == "ok" for n in row["nodes"].values())  # type: ignore[index]
    # D 的上游段同时携带 B、C 两路产出
    assert "OUT-node-B" in rig.prompts["D"] and "OUT-node-C" in rig.prompts["D"]


# 功能：广播回调抛异常不许掀翻运行主体（订阅端错误与调度解耦）
# 设计：on_node 换成必炸版：run 仍要走到 success——事件是尽力而为的旁路，
# 账本（store）才是真相；这条测的就是"旁路短路、主干道照常"
async def test_broken_listener_does_not_break_run(tmp_path: Path) -> None:
    st, wf = _mk(tmp_path, [{"name": "A", "prompt": "a", "depends_on": []}])
    rig = Rig(st)
    eng = rig.build()

    async def boom(tr: Any) -> None:
        raise RuntimeError("订阅端炸了")
    eng._on_node = boom  # type: ignore[method-assign]

    run_id, _ = eng.start_run(wf["id"])
    await asyncio.sleep(0.2)
    assert st.get_run(run_id)["status"] == "success"  # type: ignore[index]
    assert rig.finished and rig.finished[0][0] == "success"


# ==================== 用户取消（workflow.cancel 的引擎面） ====================

# 功能：单节点运行中取消——事件序 running→cancelled→finished(cancelled)，行/节点/
# 击杀名单/活跃表四方落账一致
# 设计：A 挂 never-resolve gate 造确定"进行中"窗口，cancel_run 是 awaited 坐等到账，
# 返回后零 sleep 直接断全表：①事件整表 ==（含 cancelled 补标在 finished 之前，
# 这是 store 守卫时序的产品面）②child_run_id 在取消行里保留（深链不丢）
# ③定义行 last_status 反范式同步（列表页 chip 的数据源）④击杀名单恰含 node-A
async def test_cancel_single_node_full_sequence(tmp_path: Path) -> None:
    st, wf = _mk(tmp_path, [{"name": "A", "prompt": "a", "depends_on": []}])
    gate = asyncio.Event()
    rig = Rig(st)
    eng = rig.build()

    async def hang(child: str) -> tuple[bool, str]:
        await gate.wait()
        return True, "late"
    eng._await_child = hang  # type: ignore[method-assign]

    run_id, _ = eng.start_run(wf["id"])
    await asyncio.sleep(0.1)  # 让 A 走完 running 记账 + launch 登记，再进挂起
    assert await eng.cancel_run(run_id) == ""
    assert rig.events == [("A", "running"), ("A", "cancelled")]
    assert rig.finished == [("cancelled", "用户取消", "")]
    row = st.get_run(run_id)
    assert row["status"] == "cancelled"  # type: ignore[index]
    assert row["error"] == "用户取消"  # type: ignore[index]
    assert row["nodes"]["A"]["status"] == "cancelled"  # type: ignore[index]
    assert row["nodes"]["A"]["child_run_id"] == "node-A"  # type: ignore[index]
    assert rig.cancelled_children == ["node-A"]
    assert eng._active == {} and eng.active_run_for(wf["id"]) == ""
    assert st.get(wf["id"])["last_status"] == "cancelled"
    gate.set()


# 功能：A→B 串行图取消——A 标 cancelled，B 保持 pending（"没轮到"不伪造"被取消"）
# 设计：B 是否被动过有两处独立观测：事件表里零 B 条目（不假发 N 条取消事件是
# 本决策的核心）、run 行骨架里 B 仍是 pending（GUI 灰显"待跑"的数据依据）。
# 若实现者日后"好心"把 pending 翻 cancelled，这两处断言都会红
async def test_cancel_keeps_pending_nodes_pending(tmp_path: Path) -> None:
    tasks = [{"name": "A", "prompt": "a", "depends_on": []},
             {"name": "B", "prompt": "b", "depends_on": ["A"]}]
    st, wf = _mk(tmp_path, tasks)
    gate = asyncio.Event()
    rig = Rig(st)
    eng = rig.build()

    async def hang(child: str) -> tuple[bool, str]:
        await gate.wait()
        return True, "late"
    eng._await_child = hang  # type: ignore[method-assign]

    run_id, _ = eng.start_run(wf["id"])
    await asyncio.sleep(0.1)
    assert await eng.cancel_run(run_id) == ""
    assert not any(n == "B" for n, _ in rig.events)
    row = st.get_run(run_id)
    assert row["nodes"]["A"]["status"] == "cancelled"  # type: ignore[index]
    assert row["nodes"]["B"]["status"] == "pending"  # type: ignore[index]
    assert rig.finished[0][0] == "cancelled"
    gate.set()


# 功能：同层双兄弟并行取消——两个都被击杀、都被标 cancelled，后继 D 绝不启动
# 设计：A 速通、B/C 挂 gate 造并行 running；击杀名单用集合断言（并行无先后，
# 钉顺序就是假 flaky）；"D 零事件"验证取消真的掐断了分层屏障的接力——只停
# 当前节点不停调度的实现会在这里露馅。B/C 的 running 记账先后也不断，只断集合
async def test_cancel_kills_all_running_siblings(tmp_path: Path) -> None:
    tasks = [
        {"name": "A", "prompt": "a", "depends_on": []},
        {"name": "B", "prompt": "b", "depends_on": ["A"]},
        {"name": "C", "prompt": "c", "depends_on": ["A"]},
        {"name": "D", "prompt": "d", "depends_on": ["B", "C"]},
    ]
    st, wf = _mk(tmp_path, tasks)
    gate = asyncio.Event()
    rig = Rig(st)
    eng = rig.build()

    async def hang_bc(child: str) -> tuple[bool, str]:
        if child == "node-A":
            return True, "OUT-node-A"
        await gate.wait()
        return True, "late"
    eng._await_child = hang_bc  # type: ignore[method-assign]

    run_id, _ = eng.start_run(wf["id"])
    await asyncio.sleep(0.15)  # A 速通 + B/C 完成 launch 登记后进挂起
    assert await eng.cancel_run(run_id) == ""
    assert set(rig.cancelled_children) == {"node-B", "node-C"}
    row = st.get_run(run_id)
    assert row["nodes"]["A"]["status"] == "ok"  # type: ignore[index]
    assert row["nodes"]["B"]["status"] == "cancelled"  # type: ignore[index]
    assert row["nodes"]["C"]["status"] == "cancelled"  # type: ignore[index]
    assert ("D", "running") not in rig.events
    assert rig.finished[0][0] == "cancelled"
    gate.set()


# 功能：二次取消与幽灵 run_id 均被文案拒绝且不二次记账——finished/节点事件恰好一次
# 设计：第一次 cancel_run 坐等到账后 _active 已摘，第二次必然走"无活跃"早退分支；
# 恰好一次用计数断言（len(finished)==1、cancelled 事件数==1）而非整表——本测试
# 的敌人是"双收口"（尾扫与 except 分支重复落账），整表断言把时序噪音也算进敌营
async def test_double_cancel_and_ghost_are_rejected(tmp_path: Path) -> None:
    st, wf = _mk(tmp_path, [{"name": "A", "prompt": "a", "depends_on": []}])
    gate = asyncio.Event()
    rig = Rig(st)
    eng = rig.build()

    async def hang(child: str) -> tuple[bool, str]:
        await gate.wait()
        return True, "late"
    eng._await_child = hang  # type: ignore[method-assign]

    run_id, _ = eng.start_run(wf["id"])
    await asyncio.sleep(0.1)
    assert await eng.cancel_run(run_id) == ""
    again = await eng.cancel_run(run_id)
    assert again != "" and "无需取消" in again
    ghost = await eng.cancel_run("ghost-run")
    assert ghost != "" and "无需取消" in ghost
    assert len(rig.finished) == 1
    assert [s for _, s in rig.events].count("cancelled") == 1
    gate.set()


# 功能：cancel 抢在协程首帧调度前——尾扫补标 cancelled 且 finished 广播恰好一次
# 设计：start_run 后零 sleep 直接 cancel_run：Task 从未启动，_execute 的
# try/except 一行业务码都没执行（except 分支不存在），落账只能靠 cancel_run
# 尾扫。行状态守卫让"尾扫 vs except 分支"互斥——len(finished)==1 就是对这条
# 互斥的直接钉桩；节点全 pending（连 running 都没记过）是"从未启动"的旁证
async def test_cancel_before_first_scheduling_tail_sweep(tmp_path: Path) -> None:
    st, wf = _mk(tmp_path, [{"name": "A", "prompt": "a", "depends_on": []}])
    rig = Rig(st)
    eng = rig.build()
    run_id, _ = eng.start_run(wf["id"])
    assert await eng.cancel_run(run_id) == ""
    assert rig.events == []  # 协程从未开跑：没有任何节点事件
    assert rig.finished == [("cancelled", "用户取消", "")]  # 且恰好一次
    row = st.get_run(run_id)
    assert row["status"] == "cancelled"  # type: ignore[index]
    assert row["nodes"]["A"]["status"] == "pending"  # type: ignore[index]
    assert eng._active == {}
