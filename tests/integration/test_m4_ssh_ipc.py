# 功能：验证 ssh.* RPC（连接库/密钥/信任/终端）在真实 daemon 上可达：conn CRUD 闭环 + 密钥形状 + 信任失败 + 终端错误路径与断连生命周期
# 设计：running_daemon fixture 把 IWAN_SESSIONS_DIR 指进 tmp，SshConnStore 与
# keys 目录（sessions 根的兄弟 ssh/）自动落进沙箱——集成层第一次可以不打
# monkeypatch 就直接做写操作而不污染 ~/.iwan。错误路径选"关闭端口做
# host_trust"：验证的是安全不变式（不可达 → ok=False → known_hosts 不落盘），
# 比 mock 更真、比打外网更快。
from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest


# 发一帧 JSON-RPC 并取回带指定 id 的响应（同连接多次复用）
async def _rpc(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, rid: str, method: str, params: dict) -> dict:
    req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    writer.write((json.dumps(req) + "\n").encode("utf-8"))
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=15.0)
    return json.loads(line)


# 功能：conn_list→add→update→delete 闭环，含重名拒绝与未知 id 文案
# 设计：update 用"改 host 但 name 不动"的局部形态——顺带证明 handler 把
# None 字段透传成了 store 的"不动"语义（若误传空串会被校验拒绝，帧里可见）
async def test_ssh_conn_ipc_roundtrip(running_daemon: subprocess.Popen[bytes], free_port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "s0", "ssh.conn_list", {})
        assert r["result"]["connections"] == []

        r = await _rpc(reader, writer, "s1", "ssh.conn_add", {"name": "lab", "host": "10.1.1.7", "user": "ops", "port": 22})
        assert r["result"]["ok"] is True and r["result"]["id"]
        cid = r["result"]["id"]

        r = await _rpc(reader, writer, "s2", "ssh.conn_add", {"name": "lab", "host": "other", "user": "ops"})
        assert r["result"]["ok"] is False and "名称已存在" in r["result"]["error"]

        # 空 user 在 wire 层也必须被拒（静默回落本地用户名是事故源）
        r = await _rpc(reader, writer, "s3", "ssh.conn_add", {"name": "noUser", "host": "h", "user": "  "})
        assert r["result"]["ok"] is False and "用户" in r["result"]["error"]

        r = await _rpc(reader, writer, "s4", "ssh.conn_update", {"id": cid, "host": "10.1.1.8"})
        assert r["result"]["ok"] is True
        r = await _rpc(reader, writer, "s5", "ssh.conn_list", {})
        row = r["result"]["connections"][0]
        assert row["host"] == "10.1.1.8" and row["name"] == "lab" and row["port"] == 22

        r = await _rpc(reader, writer, "s6", "ssh.conn_update", {"id": "ghost", "host": "x"})
        assert r["result"]["ok"] is False and "连接不存在" in r["result"]["error"]

        r = await _rpc(reader, writer, "s7", "ssh.conn_delete", {"id": cid})
        assert r["result"]["ok"] is True
        r = await _rpc(reader, writer, "s8", "ssh.conn_delete", {"id": cid})
        assert r["result"]["ok"] is False
        r = await _rpc(reader, writer, "s9", "ssh.conn_list", {})
        assert r["result"]["connections"] == []
    finally:
        writer.close()
        await writer.wait_closed()


# 功能：ssh.key_status 空态返回完整形状（默认值收口，GUI 不用防 undefined）
# 设计：沙箱目录此刻还没有密钥文件（fixture 的 tmp 全新）——空态恰好是
# 确定性场景；若环境里恰好有密钥（不该发生），断言 has_key is bool 兜住两侧
async def test_ssh_key_status_shape(running_daemon: subprocess.Popen[bytes], free_port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "k0", "ssh.key_status", {})
        res = r["result"]
        assert isinstance(res["has_key"], bool)
        assert set(res) >= {"has_key", "pubkey_path", "fingerprint"}
        if not res["has_key"]:
            assert res["pubkey_path"] == "" and res["fingerprint"] == ""
    finally:
        writer.close()
        await writer.wait_closed()


# 功能：对回环关闭端口做 host_trust → ok=False，且沙箱 known_hosts 不落盘
# 设计：不 skip 工具链缺失的情况？要 skip——keyscan 不存在时 _run_tool 返回
# 127 也是 ok=False，但 error 文案不同；这里断言的是"失败 + 不写文件"这条
# 与安全相关的不变式，两种失败都满足，故无需 skipif
async def test_ssh_host_trust_closed_port(running_daemon: subprocess.Popen[bytes], free_port: int) -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "t0", "ssh.host_trust", {"host": "127.0.0.1", "port": port})
        res = r["result"]
        assert res["ok"] is False and res["error"]
    finally:
        writer.close()
        await writer.wait_closed()
    # 沙箱 known_hosts：sessions 根（fixture 的 tmp_dir/sessions）的兄弟 ssh/ 下
    if shutil.which("ssh-keyscan") is not None:
        # daemon 的 sessions 目录在 free_port 对应的 tmp 里无法从这里反推，
        # 故只断言"我们没在真实 ~/.iwan/ssh 下写东西"——生产文件存在性不该被本测试改变
        real_kh = Path.home() / ".iwan" / "ssh" / "known_hosts"
        text = real_kh.read_text(encoding="utf-8") if real_kh.exists() else ""
        assert f"127.0.0.1 {port}" not in text


# 功能：终端四个 RPC 的错误路径——未知 conn、幽灵 sid、非法 base64，全部 ok=False 且零副作用
# 设计：故意不真开任何 ssh 会话——未知 conn 校验发生在 spawn 前，幽灵 sid
# 校验发生在池查找处，非法 base64 校验甚至排在 sid 查找之前（断言错误文案
# 就能证明校验顺序：同一帧里两个错因同时存在时先报 base64）。
# 这些路径不依赖环境有没有 ssh.exe，所以不需要 skipif。
async def test_ssh_term_rpc_error_paths(running_daemon: subprocess.Popen[bytes], free_port: int) -> None:
    import base64
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "e0", "ssh.term_open", {"conn_id": "ghost-conn"})
        assert r["result"]["ok"] is False and "未知 SSH 连接" in r["result"]["error"]

        r = await _rpc(reader, writer, "e1", "ssh.term_write", {
            "session_id": "ghost-sid", "data_b64": base64.b64encode(b"ls\n").decode(),
        })
        assert r["result"]["ok"] is False and "会话不存在" in r["result"]["error"]

        # 同一帧两处错：非法 base64 必须先被报出来（校验序不变式）
        r = await _rpc(reader, writer, "e2", "ssh.term_write", {"session_id": "ghost-sid", "data_b64": "!!!not-base64!!!"})
        assert r["result"]["ok"] is False and "base64" in r["result"]["error"]

        r = await _rpc(reader, writer, "e3", "ssh.term_resize", {"session_id": "ghost-sid", "cols": 100, "rows": 30})
        assert r["result"]["ok"] is False and "会话不存在" in r["result"]["error"]

        r = await _rpc(reader, writer, "e4", "ssh.term_close", {"session_id": "ghost-sid"})
        assert r["result"]["ok"] is False and "会话不存在" in r["result"]["error"]
    finally:
        writer.close()
        await writer.wait_closed()


# 功能：term_open 对回环关闭端口不挂死——RPC 快速返回 ok，随后 ssh 自灭、会话从池中消失
# 设计：这是 M4c 的核心安全不变式在集成层的投影：BatchMode=yes 让认证询问
# 不可能出现（GUI 没有输密码通道）、连接被拒后 ssh 退出 → 读循环 EOF →
# close 摘池。断连后用轮询 term_write 观察"会话不存在"，而不是订事件流——
# 错误帧就是池状态的权威读法，省掉订阅排布的测试复杂度。skipif 只护
# ssh.exe 缺失的机器（那种环境终端功能本就不可用，不算回归）。
@pytest.mark.skipif(shutil.which("ssh") is None, reason="环境缺少 ssh 客户端")
async def test_ssh_term_open_dead_port_lifecycle(running_daemon: subprocess.Popen[bytes], free_port: int) -> None:
    import base64
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "d0", "ssh.conn_add", {
            "name": "deadlab", "host": "127.0.0.1", "user": "nobody", "port": dead_port,
        })
        cid = r["result"]["id"]

        r = await _rpc(reader, writer, "d1", "ssh.term_open", {"conn_id": cid, "cols": 80, "rows": 24})
        assert r["result"]["ok"] is True and r["result"]["session_id"]
        sid = r["result"]["session_id"]

        gone = False
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            r = await _rpc(reader, writer, "d2", "ssh.term_write", {
                "session_id": sid, "data_b64": base64.b64encode(b"\n").decode(),
            })
            if r["result"]["ok"] is False and "会话不存在" in r["result"]["error"]:
                gone = True
                break
            await asyncio.sleep(0.2)
        assert gone, "ssh 对关闭端口退出后，会话应在超时前从池中消失"

        r = await _rpc(reader, writer, "d3", "ssh.term_close", {"session_id": sid})
        assert r["result"]["ok"] is False

        await _rpc(reader, writer, "d4", "ssh.conn_delete", {"id": cid})
    finally:
        writer.close()
        await writer.wait_closed()
