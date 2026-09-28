# 功能：验证 pr.review 在真实 daemon 上的注册与"校验/本地拒错先于网络"两道闸
# 设计：刻意不打真实 GitHub、不真起 LLM 评审（同 speech 集成不加载 whisper 模型
# 的纪律）——cwd 指到 pytest 临时目录（必非 git 仓库），collect_context 在第一步
# 就失败，取数编排短路返回 ok=False，全程零网络零 token；这条同时证明
# "pr.review" 已注册（收到 result 而非 -32601）。缺字段/错类型两发走 pydantic
# 判别层报 JSON-RPC error——参数形状错与业务拒绝分属两条通道，各测一发。
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


# 功能：非 git 目录评审回 ok=False+非空错误+空会话号；坏参数形状走 JSON-RPC error 通道
# 设计：ok=False 时 session_id 必须是空串（GUI 靠它区分"会话已开"与"没开"），
# error 只断非空不锁文案——git stderr 随语言环境变，锁全文会碎；两条非法参数
# （缺 pr_number / pr_number 非整数）都应在进 handler 前被判别联合拦下
async def test_pr_review_rejection_channels(
    running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "p1", "pr.review",
                       {"cwd": str(tmp_path), "pr_number": 1})
        assert "result" in r, f"pr.review 未注册或异常：{r}"
        assert r["result"]["ok"] is False
        assert r["result"]["error"] != ""
        assert r["result"]["session_id"] == ""

        r = await _rpc(reader, writer, "p2", "pr.review", {"cwd": str(tmp_path)})
        assert "error" in r and r["error"]["code"] != 0

        r = await _rpc(reader, writer, "p3", "pr.review",
                       {"cwd": str(tmp_path), "pr_number": "abc"})
        assert "error" in r and r["error"]["code"] != 0
    finally:
        writer.close()
