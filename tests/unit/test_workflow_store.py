"""
工作流存储层单测 — 定义校验 / 原子持久化 / 运行记账 / 孤儿清算

【学习要点】全部用例走 tmp_path 注入双路径，生产路径（~/.iwan）零触碰；
断言只钉行为契约（校验文案、落盘内容、行形态），不钉实现细节。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iwan_claude.core.workflow.store import (
    DETAIL_KEEP,
    MAX_TASKS,
    OUTPUT_KEEP,
    RUNS_KEEP,
    WorkflowStore,
)


# 造一张合法的 N 节点线性图（a0←a1←…），供各用例在基础上做单点破坏
def _chain(n: int) -> list[dict[str, Any]]:
    return [
        {"name": f"a{i}", "prompt": f"指令{i}", "depends_on": [f"a{i - 1}"] if i else []}
        for i in range(n)
    ]


# 构造指向 tmp 双文件的存储实例
def _store(tmp: Path) -> WorkflowStore:
    return WorkflowStore(tmp / "workflows.json", tmp / "workflow_runs.json")


# 功能：upsert 新建→list/get→整图更新→删除的 CRUD 闭环，layers 由服务端现算
# 设计：一次真实两段编排（A→B）全程走一遍：新建行自动生成 id/时刻，更新走
# 同 id 覆盖（created_at 不变而 tasks 换图、layers 从两列变一列）；删除后
# 再删返回 False（幂等语义）。layers 断 [A],[B] 证明派生量随图变
def test_upsert_get_update_delete_roundtrip(tmp_path: Path) -> None:
    st = _store(tmp_path)
    row = st.upsert("", "调研流水线", "备注", _chain(2))
    wf_id = str(row["id"])
    assert row["layers"] == [["a0"], ["a1"]]
    assert row["created_at"] and row["updated_at"] and row["last_status"] == ""

    assert [d["id"] for d in st.list_defs()] == [wf_id]
    assert st.get("nope") is None

    updated = st.upsert(wf_id, "改名", "", [{"name": "solo", "prompt": "x", "depends_on": []}])
    assert updated["name"] == "改名"
    assert updated["created_at"] == row["created_at"]  # 更新不许动出生时刻
    assert updated["layers"] == [["solo"]]

    assert st.delete(wf_id) is True
    assert st.delete(wf_id) is False
    assert st.list_defs() == []


# 功能：name 留空时服务端兜底生成"工作流-MMDDHHMM"，用户给了就原样保留
# 设计：兜底名是 GUI 编辑器不填标题时的唯一防线，断前缀而非全串——
# 日期部分随时钟漂移，全串断言就是明天的 flaky 源
def test_default_name_fallback(tmp_path: Path) -> None:
    st = _store(tmp_path)
    row = st.upsert("", "   ", "", _chain(1))
    assert row["name"].startswith("工作流-")
    row2 = st.upsert("", "有名字", "", _chain(1))
    assert row2["name"] == "有名字"


# 功能：六类坏图（空表/超上限/空节点名/重名/空指令/依赖不存在/成环）逐条拒绝
# 设计：每类各断"抛 ValueError 且错误文案含关键字"——文案是要原样回显给
# GUI 用户的，只断抛异常等于放任无意义报错；最后 list 为空证明没有任何
# 一张坏图漏到磁盘（校验先于落盘的纪律）
def test_invalid_graphs_rejected_nothing_persisted(tmp_path: Path) -> None:
    st = _store(tmp_path)
    cases: list[tuple[list[dict[str, Any]], str]] = [
        ([], "至少"),
        (_chain(MAX_TASKS + 1), "超限"),
        ([{"name": "  ", "prompt": "x", "depends_on": []}], "缺少名称"),
        ([{"name": "a", "prompt": "x", "depends_on": []},
          {"name": "a", "prompt": "y", "depends_on": []}], "重复"),
        ([{"name": "a", "prompt": "  ", "depends_on": []}], "缺少指令"),
        ([{"name": "a", "prompt": "x", "depends_on": ["ghost"]}], "不存在"),
        ([{"name": "a", "prompt": "x", "depends_on": ["b"]},
          {"name": "b", "prompt": "y", "depends_on": ["a"]}], "循环"),
    ]
    for tasks, keyword in cases:
        try:
            st.upsert("", "n", "", tasks)
            raise AssertionError(f"应当拒绝：{keyword}")
        except ValueError as e:
            assert keyword in str(e), f"文案缺关键字 {keyword}：{e}"
    assert st.list_defs() == []


# 功能：定义表 JSON 损坏时改名隔离并以空表继续，另一份文件不受牵连
# 设计：模拟"只有 workflows.json 被写坏"的最差现场（断电只砸中一份文件）：
# 断坏文件被挪走、原路径可重新写入、runs 文件内容原封不动——隔离半径
# 必须止于单文件，跨文件连坐会把一次小故障放大成整个功能报废
def test_corrupt_defs_isolated(tmp_path: Path) -> None:
    defs = tmp_path / "workflows.json"
    st = _store(tmp_path)
    st.upsert("", "先建一条", "", _chain(1))
    run = st.create_run(st.list_defs()[0])
    defs.write_text("{ broken json !!", encoding="utf-8")

    st2 = _store(tmp_path)
    assert st2.list_defs() == []  # 坏表按空表起
    assert any(p.name.startswith("workflows.corrupt-") for p in tmp_path.iterdir())
    st2.upsert("", "复活", "", _chain(1))  # 隔离后能继续写
    assert [d["name"] for d in st2.list_defs()] == ["复活"]
    # runs 文件未损坏：运行行还在（孤儿 running 由引擎清算流程负责，不归载入管）
    assert st2.get_run(run["id"]) is not None


# 功能：create_run 预置全 pending 骨架，record_node 逐步推进，output/detail 截断落盘
# 设计：pending 骨架在 run 诞生时就写进存储（而非 get 时按定义补齐）——
# 断"定义删除后 run 行仍含全部节点"正是这套自包含设计的价值证明；
# 截断宽度断到存储层磁盘内容（重开一个 Store 实例读文件），断内存对象
# 只能证明赋值、断文件才能证明"写穿落盘"
def test_run_accounting_lifecycle(tmp_path: Path) -> None:
    st = _store(tmp_path)
    wf = st.upsert("", "两节点", "", _chain(2))
    run = st.create_run(wf)
    assert all(n["status"] == "pending" for n in run["nodes"].values())

    st.record_node(run["id"], "a0", {"status": "running", "child_run_id": "c1", "started_at": "T"})
    st.record_node(run["id"], "a0", {
        "status": "ok", "output": "O" * (OUTPUT_KEEP + 100), "finished_at": "T2",
    })
    st.record_node(run["id"], "a1", {"status": "fail", "detail": "E" * (DETAIL_KEEP + 100)})
    assert st.record_node(run["id"], "ghost", {"status": "ok"}) is None  # 未名节点拒记

    st.delete(wf["id"])  # 定义删除不影响 run 行自包含
    fresh = _store(tmp_path)
    row = fresh.get_run(run["id"])
    assert row is not None
    assert row["status"] == "running"
    assert set(row["nodes"]) == {"a0", "a1"}
    assert row["nodes"]["a0"]["status"] == "ok"
    assert len(row["nodes"]["a0"]["output"]) == OUTPUT_KEEP
    assert row["nodes"]["a1"]["detail"] == "E" * DETAIL_KEEP


# 功能：attach_session 把懒建会话 id 写进 run 行（审批卡路由的依据字段）
# 设计：单独成测而非顺带断言——session_id 是"审批卡黑洞=节点挂死"这条
# 因果链上的关键载荷，值得一个名字直接指着它的契约
def test_attach_session(tmp_path: Path) -> None:
    st = _store(tmp_path)
    wf = st.upsert("", "w", "", _chain(1))
    run = st.create_run(wf)
    st.attach_session(run["id"], "sess-42")
    assert _store(tmp_path).get_run(run["id"])["session_id"] == "sess-42"  # type: ignore[index]


# 功能：finish_run 落终态并把 last_run/last_status 反范式同步进定义行
# 设计：touch_last_run 对已删定义静默无操作（级联删除后引擎才收尾的竞态
# 会走到这条路）——断"不抛异常"即可，列表页 chip 在定义消失后本就不存在
def test_finish_and_touch_last_run(tmp_path: Path) -> None:
    st = _store(tmp_path)
    wf = st.upsert("", "w", "", _chain(1))
    run = st.create_run(wf)
    done = st.finish_run(run["id"], "success", "")
    assert done is not None and done["status"] == "success" and done["finished_at"]
    st.touch_last_run(wf["id"], run["id"], "success")
    row = _store(tmp_path).get(wf["id"])
    assert row is not None and row["last_run_id"] == run["id"] and row["last_status"] == "success"
    st.finish_run("no-such-run", "success")
    st.touch_last_run("no-such-wf", "x", "y")  # 双双无操作不抛
    st.delete(wf["id"])
    st.touch_last_run(wf["id"], run["id"], "failed")  # 定义已删：静默


# 功能：list_runs 按 wf_id 过滤、新→旧排序、limit 钳位 1..100
# 设计：三条 run 的 started_at 靠 finish→再建循环里必然的时间推进无法保证
# 互异，直接改写内存行的时刻字段再落盘构造确定性排序；limit=0 与负数
# 都该被钳成 1 而不是返回空表（空表会让"到底没跑过还是参数错"无从分辨）
def test_list_runs_filter_sort_limit(tmp_path: Path) -> None:
    st = _store(tmp_path)
    wf1 = st.upsert("", "一", "", _chain(1))
    wf2 = st.upsert("", "二", "", _chain(1))
    r1 = st.create_run(wf1)
    r2 = st.create_run(wf1)
    r3 = st.create_run(wf2)
    # 手工排时刻：r1 最早、r2 中、r3 最新
    for rid, ts in ((r1["id"], "2026-01-01T00:00:01"), (r2["id"], "2026-01-01T00:00:02"),
                    (r3["id"], "2026-01-01T00:00:03")):
        for row in st._runs:
            if row["id"] == rid:
                row["started_at"] = ts
    st._write_rows(st._runs_path, "workflow_runs", st._runs)

    ids = [r["id"] for r in st.list_runs(wf1["id"])]
    assert ids == [r2["id"], r1["id"]]  # 过滤 + 新→旧
    assert [r["id"] for r in st.list_runs("", 100)] == [r3["id"], r2["id"], r1["id"]]
    assert len(st.list_runs("", 0)) == 1
    assert len(st.list_runs("", -5)) == 1


# 功能：mark_orphans_interrupted 只清算 running 行且落盘，无孤儿时零写盘
# 设计：模拟 daemon 崩溃重启后重建实例读旧文件——终态行不许被再次改写
# （success 被标成 interrupted 会把历史数据洗成假事故）；返回计数供启动
# 日志引用，0 时不产生文件写（无变化的写盘是纯噪音）
def test_mark_orphans_interrupted(tmp_path: Path) -> None:
    st = _store(tmp_path)
    wf = st.upsert("", "w", "", _chain(1))
    r1 = st.create_run(wf)
    r2 = st.create_run(wf)
    st.finish_run(r2["id"], "success")

    st2 = _store(tmp_path)
    assert st2.mark_orphans_interrupted() == 1
    assert st2.get_run(r1["id"])["status"] == "interrupted"  # type: ignore[index]
    assert st2.get_run(r1["id"])["error"] == "daemon 重启，运行中断"  # type: ignore[index]
    assert st2.get_run(r2["id"])["status"] == "success"  # type: ignore[index]
    assert st2.mark_orphans_interrupted() == 0  # 幂等：二次清算无事发生


# 功能：运行历史超 RUNS_KEEP 时只裁最旧终态行，进行中的行永远幸存
# 设计：裁剪切在 finish_run 后（新行自己就是候选）——断总长恰为
# RUNS_KEEP+1（那条故意留 running 的）证明"裁剪保 running"不是巧合；
# 全量重写若按创建序误杀，running 会消失，断言立刻红
def test_runs_trim_keeps_active(tmp_path: Path) -> None:
    st = _store(tmp_path)
    wf = st.upsert("", "w", "", _chain(1))
    keep_running = st.create_run(wf)
    for _ in range(RUNS_KEEP + 5):
        r = st.create_run(wf)
        st.finish_run(r["id"], "success")
    rows = st._runs
    assert keep_running["id"] in [r["id"] for r in rows]
    assert len(rows) == RUNS_KEEP + 1


# 功能：定义与运行各自原子落盘，文件内容是完整 JSON 而非半截拼接
# 设计：tmp+os.replace 手法的可观测面就是"任何时刻文件都读得动"——
# 写完立即 json.loads 原文并核对包裹键（workflows/workflow_runs），
# 键名是持久化格式契约，改它=换版本要迁移，必须被测试钉死
def test_files_shape(tmp_path: Path) -> None:
    st = _store(tmp_path)
    wf = st.upsert("", "w", "", _chain(1))
    st.create_run(wf)
    defs = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    runs = json.loads((tmp_path / "workflow_runs.json").read_text(encoding="utf-8"))
    assert list(defs) == ["workflows"] and len(defs["workflows"]) == 1
    assert list(runs) == ["workflow_runs"] and len(runs["workflow_runs"]) == 1
