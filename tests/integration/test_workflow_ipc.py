# 功能：验证工作流六个 RPC 方法在真实 daemon 上的定义回环、校验拒写与错误边界
# 设计：走原始 TCP + JSON-RPC 帧（同 schedule 集成测试纪律），测 wire 协议本身。
# fixture 已把 IWAN_SESSIONS_DIR 指到 tmp，workflows.json 落临时目录。刻意
# 不测 workflow.run 的执行路径——节点会真起 subagent 打 LLM，集成环境无密钥；
# run 只测"不存在的 id 必须 ok=False 给文案"这条不碰调度的边界。
from __future__ import annotations

import asyncio
import json
import subprocess


# 发一帧 JSON-RPC 并取回带指定 id 的响应（同连接多次复用）
async def _rpc(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, rid: str, method: str, params: dict) -> dict:
    req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    writer.write((json.dumps(req) + "\n").encode("utf-8"))
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    return json.loads(line)


# 功能：workflow.save→list→get→delete 全生命周期，layers 随图出现在响应里
# 设计：一次连接内顺序发完——list 夹逼证明存在性、get 附带空 runs 表证明
# "详情页一次拉齐"的响应形状；缺字段（tasks 里的 prompt）在 pydantic 判别
# 层就该报 JSON-RPC error 而非 ok=False（参数形状错≠业务拒绝，两条通道
# 各测一发）；删后 list 归零闭环
async def test_workflow_crud_roundtrip(
    running_daemon: subprocess.Popen[bytes], free_port: int
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "s1", "workflow.save", {
            "name": "集成两节点",
            "description": "d",
            "tasks": [
                {"name": "A", "prompt": "只回复：完成", "depends_on": []},
                {"name": "B", "prompt": "总结上游", "depends_on": ["A"]},
            ],
        })
        assert r["result"]["ok"] is True
        wid = r["result"]["id"]

        r = await _rpc(reader, writer, "l1", "workflow.list", {})
        rows = r["result"]["workflows"]
        assert any(w["id"] == wid and w["layers"] == [["A"], ["B"]] for w in rows)

        r = await _rpc(reader, writer, "g1", "workflow.get", {"id": wid})
        res = r["result"]
        assert res["ok"] is True
        assert res["workflow"]["name"] == "集成两节点"
        assert res["runs"] == []

        # 判别层错误：节点缺 prompt 字段 → JSON-RPC 参数错误，不是业务 ok=False
        r = await _rpc(reader, writer, "s2", "workflow.save", {
            "tasks": [{"name": "X", "depends_on": []}],
        })
        assert "error" in r and r["error"]["code"] != 0

        r = await _rpc(reader, writer, "g2", "workflow.get", {"id": wid})
        assert len(r["result"]["workflow"]["tasks"]) == 2  # 拒绝的坏 save 不许触碰原图

        r = await _rpc(reader, writer, "d1", "workflow.delete", {"id": wid})
        assert r["result"]["ok"] is True
        r = await _rpc(reader, writer, "d2", "workflow.delete", {"id": wid})
        assert r["result"]["ok"] is False and "不存在" in r["result"]["error"]

        r = await _rpc(reader, writer, "rs1", "workflow.runs", {"id": wid})
        assert r["result"]["runs"] == []
    finally:
        writer.close()
        await writer.wait_closed()


# 功能：成环定义必须被服务端拒绝且一张坏图都不落盘
# 设计：schedule 集成只测过 spec 非法一种拒绝，这里补 DAG 特有的环——
# 它是"客户端草图、服务端权威"决策链上最用户可感的一条：save 回 ok=False
# 带"循环"文案，随后 list 为空证明未持久化（校验先于写盘的 wire 级重验）
async def test_workflow_cycle_rejected_not_persisted(
    running_daemon: subprocess.Popen[bytes], free_port: int
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "c1", "workflow.save", {
            "name": "环",
            "tasks": [
                {"name": "A", "prompt": "a", "depends_on": ["B"]},
                {"name": "B", "prompt": "b", "depends_on": ["A"]},
            ],
        })
        assert r["result"]["ok"] is False and "循环" in r["result"]["error"]
        r = await _rpc(reader, writer, "c2", "workflow.list", {})
        assert r["result"]["workflows"] == []

        r = await _rpc(reader, writer, "c3", "workflow.run", {"id": "ghost-id"})
        assert r["result"]["ok"] is False and "不存在" in r["result"]["error"]
    finally:
        writer.close()
        await writer.wait_closed()
