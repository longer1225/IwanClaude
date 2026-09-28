"""
工作流持久化（W1）— DAG 定义表 + 运行记账，两份 JSON

【学习要点】
1. 定义与运行分文件（workflows.json / workflow_runs.json）：定义是小而稳的
   用户资产，运行是只增的流水账——生命周期完全不同，混一个文件里裁剪运行
   历史时得重写出整个定义表，分文件让各自的全量原子写都只重写自己的小对象。
2. upsert 校验走"先自查文案、后存量裁决"两段式：字段级问题（空名/重名/缺
   依赖）自己检查，为的是给出精确到节点的中文文案；环检测不重新发明——
   存量 Workflow.validate()（Kahn 拓扑排序）就是权威，直接复用零改动。
   校验不过抛 ValueError，handler 薄壳转成 ok=False，坏图绝不落盘。
3. run 行的 nodes 在 create_run 时就按定义全量预置 pending 骨架：
   "get 时按定义补齐"要求读路径再查定义行，可定义可能先被删（v1 删除不级联
   运行历史）——预置让 run 行自包含，历史页永远能完整渲染，不依赖定义存活。
4. 落盘手法与 scheduled.json 同址同规格（tmp+os.replace 原子写、坏文件
   .corrupt-<ts> 改名隔离、resolve_sessions_root().parent 自动跟随
   IWAN_SESSIONS_DIR 进测试沙箱）——同一套断电安全论证不重复写第二遍。
"""
from __future__ import annotations

import json
import logging
import os
import time as _time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from iwan_claude.core.config import resolve_sessions_root
from iwan_claude.core.workflow.graph import Workflow
from iwan_claude.core.workflow.task import Task

log = logging.getLogger(__name__)

# 单工作流节点上限：编排是人手画的，30 个已经要滚动条了；上限防呆不防恶意
MAX_TASKS = 30
# 运行历史保留条数：只裁终态行，进行中的永远不裁（裁剪发生在 finish_run 后）
RUNS_KEEP = 100
# 节点产出快照 / 失败摘要的截断宽度（全文在子 run 目录，这里是行内预览）
OUTPUT_KEEP = 400
DETAIL_KEEP = 200


# datetime → 秒级 ISO 串（与 schedule.py 同口径，两模块的行可直接互相比时间）
def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# json 往返深拷贝一行：run 行嵌套 nodes 字典，浅拷贝会内外串账
def _copy(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(row))
    return out


class WorkflowStore:
    """
    工作流定义 + 运行记录的 JSON 存储

    管理 ~/.iwan/workflows.json（定义表）与 ~/.iwan/workflow_runs.json（运行流水）。
    全部方法同步、单线程假设（daemon 事件循环内串行调用），无锁。
    """

    def __init__(self, path: Path | None = None, runs_path: Path | None = None) -> None:
        # 两路径都可注入：单测指向 tmp_path，生产落 ~/.iwan/
        self._path = path if path is not None else resolve_sessions_root().parent / "workflows.json"
        self._runs_path = (
            runs_path
            if runs_path is not None
            else resolve_sessions_root().parent / "workflow_runs.json"
        )
        self._defs: list[dict[str, Any]] = []
        self._runs: list[dict[str, Any]] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # 载入与原子落盘
    # ------------------------------------------------------------------

    # 惰性载入两份文件（幂等；任一坏文件改名让路，另一份不受牵连）
    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._defs = self._read_rows(self._path, "workflows")
        self._runs = self._read_rows(self._runs_path, "workflow_runs")

    # 读一个 JSON 表文件；坏文件改名隔离后按空表处理
    def _read_rows(self, p: Path, key: str) -> list[dict[str, Any]]:
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            bad = p.with_name(f"{p.stem}.corrupt-{stamp}{p.suffix}")
            log.exception("workflow: %s 文件损坏，改名保留后以空表启动：%s", key, p)
            try:
                os.replace(p, bad)
            except OSError:
                pass
            return []
        rows = data.get(key) if isinstance(data, dict) else None
        return [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]

    # 全量原子写一个表文件（tmp + replace，同 schedule 手法）
    def _write_rows(self, p: Path, key: str, rows: list[dict[str, Any]]) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({key: rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._replace(tmp, p)

    # 原子替换并带 Windows 瞬时占用重试；三次仍失败直接抛（宁可报错不留半截）
    @staticmethod
    def _replace(src: Path, dst: Path) -> None:
        # 【学习要点】Windows 的 replace 目标文件若被杀软/搜索索引器瞬时打开会
        # 返回 WinError 5——高频循环写盘（如运行历史裁剪）实测能撞上。重试是
        # 唯一正确姿势：换 tmp 文件名没用（占的是目标），放弃写盘更不行（丢账）。
        # 微延时上限 0.15s，对事件循环只阻塞一瞬，换来持久化的确定性。
        for attempt in (0, 1, 2):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                _time.sleep(0.05 * (attempt + 1))

    # ------------------------------------------------------------------
    # 定义表 CRUD
    # ------------------------------------------------------------------

    # 全部定义行（浅拷贝；layers 现算附上，省得 GUI 各自再推一遍）
    # 名字带 _defs 而非裸 list：类体内叫 list 会遮蔽内建类型，
    # 让后文所有 list[...] 注解被 mypy 解析成这个方法（schedule 无此坑
    # 只因它 list() 之后的签名恰好没再用到 list 类型）
    def list_defs(self) -> list[dict[str, Any]]:
        self.load()
        out: list[dict[str, Any]] = []
        for d in self._defs:
            row = self._def_view(d)
            out.append(row)
        return out

    # 按 id 找定义行（含现算 layers）；不存在返回 None
    def get(self, wf_id: str) -> dict[str, Any] | None:
        self.load()
        for d in self._defs:
            if d.get("id") == wf_id:
                return self._def_view(d)
        return None

    # 定义行 → 对外视图：浅拷贝 + layers 字段（layers 是派生量，绝不写回存储）
    def _def_view(self, d: dict[str, Any]) -> dict[str, Any]:
        row = dict(d)
        row["layers"] = self._layers_of_tasks(d.get("tasks", []))
        return row

    # 把任务字典表构建成存量 Workflow 并返回分层计划；任何构建异常按"无分层"返回空
    def _layers_of_tasks(self, tasks: list[dict[str, Any]]) -> list[list[str]]:
        try:
            wf = self._build(tasks)
        except ValueError:
            return []
        return wf.get_execution_layers()

    # 新建（wf_id 空）或整图覆盖更新（wf_id 非空）；校验不过抛 ValueError（中文文案）
    def upsert(
        self, wf_id: str, name: str, description: str, tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.load()
        clean = self._validate(name, tasks)
        now = _iso(datetime.now())
        if wf_id:
            row = next((d for d in self._defs if d.get("id") == wf_id), None)
            if row is None:
                raise ValueError(f"工作流不存在：{wf_id}")
            row["name"] = clean["name"]
            row["description"] = description.strip()
            row["tasks"] = clean["tasks"]
            row["updated_at"] = now
        else:
            row = {
                "id": uuid.uuid4().hex[:12],
                "name": clean["name"],
                "description": description.strip(),
                "tasks": clean["tasks"],
                "created_at": now,
                "updated_at": now,
                "last_run_id": "",
                "last_status": "",
            }
            self._defs.append(row)
        self._write_rows(self._path, "workflows", self._defs)
        return self._def_view(row)

    # 删除定义；返回是否删掉（运行历史保留——流水账独立于定义资产，v1 不级联）
    def delete(self, wf_id: str) -> bool:
        self.load()
        before = len(self._defs)
        self._defs = [d for d in self._defs if d.get("id") != wf_id]
        if len(self._defs) != before:
            self._write_rows(self._path, "workflows", self._defs)
            return True
        return False

    # 校验并规整任务表，返回 {"name": 兜底后的名, "tasks": 去空白后的三字段表}；非法抛 ValueError
    def _validate(self, name: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        if not tasks:
            raise ValueError("工作流至少需要一个节点")
        if len(tasks) > MAX_TASKS:
            raise ValueError(f"节点数超限：{len(tasks)} > {MAX_TASKS}")
        seen: set[str] = set()
        cleaned: list[dict[str, Any]] = []
        for i, t in enumerate(tasks):
            tname = str(t.get("name") or "").strip()
            prompt = str(t.get("prompt") or "").strip()
            deps = [str(d).strip() for d in (t.get("depends_on") or []) if str(d).strip()]
            if not tname:
                raise ValueError(f"第 {i + 1} 个节点缺少名称")
            if tname in seen:
                raise ValueError(f"节点名重复：{tname}")
            if not prompt:
                raise ValueError(f"节点「{tname}」缺少指令（prompt 为空）")
            if len(set(deps)) != len(deps):
                raise ValueError(f"节点「{tname}」的依赖列表里有重复项")
            seen.add(tname)
            cleaned.append({"name": tname, "prompt": prompt, "depends_on": deps})
        for t in cleaned:
            for dep in t["depends_on"]:
                if dep not in seen:
                    raise ValueError(f"节点「{t['name']}」依赖了不存在的节点「{dep}」")
        # 字段级自查通过后，环检测交存量 Kahn 拓扑排序裁决（validate=False 只剩环一种可能）
        wf = self._build(cleaned)
        if not wf.validate():
            raise ValueError("工作流存在循环依赖，请检查节点依赖方向")
        return {
            "name": name.strip() or f"工作流-{datetime.now().strftime('%m%d%H%M')}",
            "tasks": cleaned,
        }

    # 任务字典表 → 存量 Workflow 图（add_task 对重名抛异常，上游已自查，这里只做构建）
    def _build(self, tasks: list[dict[str, Any]]) -> Workflow:
        wf = Workflow()
        for t in tasks:
            wf.add_task(Task(
                name=str(t.get("name") or ""),
                description=str(t.get("prompt") or ""),
                depends_on=[str(d) for d in (t.get("depends_on") or [])],
            ))
        return wf

    # 把一次运行的终局快照反范式写回定义行（列表页 chip 免扫运行表）
    def touch_last_run(self, wf_id: str, run_id: str, status: str) -> None:
        self.load()
        for d in self._defs:
            if d.get("id") == wf_id:
                d["last_run_id"] = run_id
                d["last_status"] = status
                self._write_rows(self._path, "workflows", self._defs)
                return

    # ------------------------------------------------------------------
    # 运行记账
    # ------------------------------------------------------------------

    # 按定义行创建一条 run 记录（nodes 预置全量 pending 骨架）并落盘
    def create_run(self, wf_row: dict[str, Any]) -> dict[str, Any]:
        self.load()
        row: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "workflow_id": wf_row["id"],
            "workflow_name": wf_row.get("name", ""),
            "session_id": "",
            "status": "running",
            "started_at": _iso(datetime.now()),
            "finished_at": "",
            "error": "",
            "nodes": {
                str(t.get("name")): {
                    "status": "pending", "child_run_id": "", "detail": "",
                    "output": "", "started_at": "", "finished_at": "",
                }
                for t in wf_row.get("tasks", [])
            },
        }
        self._runs.append(row)
        self._write_rows(self._runs_path, "workflow_runs", self._runs)
        return _copy(row)

    # 找 run 行（深拷贝返回，调用方改不脏内存表）
    def get_run(self, run_id: str) -> dict[str, Any] | None:
        self.load()
        for r in self._runs:
            if r.get("id") == run_id:
                return _copy(r)
        return None

    # 运行历史（新→旧）；wf_id 空=全部混排，limit 钳位 1..100
    def list_runs(self, wf_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        self.load()
        rows = [r for r in self._runs if not wf_id or r.get("workflow_id") == wf_id]
        rows = sorted(rows, key=lambda r: str(r.get("started_at") or ""), reverse=True)
        cap = max(1, min(int(limit), 100))
        return [_copy(r) for r in rows[:cap]]

    # 更新单节点记账（status/child_run_id/detail/output/时刻；output/detail 截断）
    def record_node(
        self, run_id: str, node: str, fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        self.load()
        for r in self._runs:
            if r.get("id") != run_id:
                continue
            nd = r["nodes"].get(node)
            if nd is None:
                return None
            for k in ("status", "child_run_id", "detail", "output", "started_at", "finished_at"):
                if k in fields:
                    nd[k] = str(fields[k] if fields[k] is not None else "")
            nd["output"] = nd["output"][:OUTPUT_KEEP]
            nd["detail"] = nd["detail"][:DETAIL_KEEP]
            self._write_rows(self._runs_path, "workflow_runs", self._runs)
            return _copy(r)
        return None

    # 给 run 挂上懒建的 one_shot 会话 id
    def attach_session(self, run_id: str, session_id: str) -> None:
        self.load()
        for r in self._runs:
            if r.get("id") == run_id:
                r["session_id"] = session_id
                self._write_rows(self._runs_path, "workflow_runs", self._runs)
                return

    # 收尾 run（终态 + 时刻 + 错误摘要），随后按 RUNS_KEEP 裁终态旧行
    def finish_run(self, run_id: str, status: str, error: str = "") -> dict[str, Any] | None:
        self.load()
        done: dict[str, Any] | None = None
        for r in self._runs:
            if r.get("id") == run_id:
                r["status"] = status
                r["finished_at"] = _iso(datetime.now())
                r["error"] = error[:DETAIL_KEEP]
                done = _copy(r)
                break
        if done is None:
            return None
        # 裁剪规则：终态行按 started_at 升序保最新 RUNS_KEEP 条；running 一律保留
        terminal = [r for r in self._runs if r.get("status") != "running"]
        keep = sorted(terminal, key=lambda r: str(r.get("started_at") or ""), reverse=True)
        cut = {id(r) for r in keep[RUNS_KEEP:]}
        if cut:
            self._runs = [r for r in self._runs if id(r) not in cut]
        self._write_rows(self._runs_path, "workflow_runs", self._runs)
        return done

    # 开机把上次 daemon 死亡时残留的 running 行标成 interrupted；返回处理条数
    def mark_orphans_interrupted(self) -> int:
        self.load()
        n = 0
        for r in self._runs:
            if r.get("status") == "running":
                r["status"] = "interrupted"
                r["finished_at"] = _iso(datetime.now())
                r["error"] = "daemon 重启，运行中断"
                n += 1
        if n:
            self._write_rows(self._runs_path, "workflow_runs", self._runs)
            log.info("workflow: %d 条中断运行已标记 interrupted", n)
        return n
