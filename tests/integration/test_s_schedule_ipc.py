# 功能：验证定时任务五个写读命令与 mcp.status 在真实 daemon 上的端到端回环
# 设计：走原始 TCP + JSON-RPC 帧（同 test_ping 纪律），不引 SDK——测的就是
# wire 协议本身。fixture 已把 IWAN_SESSIONS_DIR 指到 tmp，scheduled.json
# 落临时目录，不会污染用户真实排期。不测 run_now 触发执行（要碰 LLM），
# 只测"不存在的 id 必须 ok=False 且给出文案"这条安全边界。
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


# 功能：schedule.create→list→update→delete 全生命周期与校验失败路径
# 设计：一次连接内顺序发完，中途 list 可见新建行、删后不可见——"存在性"
# 用两次 list 夹逼证明；非法 spec 的 create 必须 ok=False 且不落盘（行数不变）
async def test_schedule_crud_roundtrip(
    running_daemon: subprocess.Popen[bytes], free_port: int
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "c1", "schedule.create", {
            "name": "集成测试任务", "cwd": "", "prompt": "说一句你好",
            "kind": "every_minutes", "spec": "60",
        })
        assert r["result"]["ok"] is True
        tid = r["result"]["id"]

        r = await _rpc(reader, writer, "l1", "schedule.list", {})
        rows = r["result"]["tasks"]
        assert any(t["id"] == tid and t["name"] == "集成测试任务" for t in rows)
        assert all(t["next_due"] for t in rows)

        # 非法 spec：服务端校验必须拦住，且不留半成品行
        r = await _rpc(reader, writer, "c2", "schedule.create", {
            "name": "坏任务", "cwd": "", "prompt": "x", "kind": "daily", "spec": "25:70",
        })
        assert r["result"]["ok"] is False and r["result"]["error"]
        r = await _rpc(reader, writer, "l2", "schedule.list", {})
        assert len(r["result"]["tasks"]) == 1

        r = await _rpc(reader, writer, "u1", "schedule.update", {"id": tid, "enabled": False})
        assert r["result"]["ok"] is True
        r = await _rpc(reader, writer, "l3", "schedule.list", {})
        assert r["result"]["tasks"][0]["enabled"] is False

        r = await _rpc(reader, writer, "d1", "schedule.delete", {"id": tid})
        assert r["result"]["ok"] is True
        r = await _rpc(reader, writer, "d2", "schedule.delete", {"id": tid})
        assert r["result"]["ok"] is False  # 幂等：再删报不存在

        r = await _rpc(reader, writer, "d3", "schedule.run_now", {"id": "nonexistent-id"})
        assert r["result"]["ok"] is False and "不存在" in r["result"]["error"]
    finally:
        writer.close()
        await writer.wait_closed()


# 功能：mcp.status 在默认配置（无 MCP 服务器）下回空表而非报错
# 设计：daemon fixture 不配 [mcp.servers]，这一条验证新注册的方法名可达、
# 结果形状正确——真服务器列表的验证交给单测的 status() 聚合逻辑
async def test_mcp_status_roundtrip(
    running_daemon: subprocess.Popen[bytes], free_port: int
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "m1", "mcp.status", {})
        assert "result" in r
        assert r["result"]["servers"] == []
    finally:
        writer.close()
        await writer.wait_closed()
