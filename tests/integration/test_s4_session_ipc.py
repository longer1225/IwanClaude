from __future__ import annotations

import asyncio
import json
import subprocess


# 发送一条 JSON-RPC 请求并返回响应对象
async def _send_recv(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    method: str,
    params: dict,
    req_id: str = "1",
) -> dict:
    req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    return json.loads(line)


# 功能：验证 daemon 暴露 session.create、session.get_history、session.close 三个 S4 IPC 命令
# 设计：不触发 session.send_message，避免真实 LLM 依赖；只验证 CoreApp handler 注册、协议序列化和 session 状态持久化
async def test_session_create_history_close_over_ipc(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)

    created = await _send_recv(
        reader,
        writer,
        "session.create",
        {"mode": "chat", "title": "ipc test"},
        req_id="create",
    )
    assert "result" in created, created
    session_id = created["result"]["session_id"]
    assert created["result"]["status"] == "active"

    history = await _send_recv(
        reader,
        writer,
        "session.get_history",
        {"session_id": session_id},
        req_id="history",
    )
    assert history["result"]["messages"] == []

    closed = await _send_recv(
        reader,
        writer,
        "session.close",
        {"session_id": session_id},
        req_id="close",
    )
    assert closed["result"]["status"] == "closed"

    writer.close()
    await writer.wait_closed()


# 功能：验证 daemon 暴露 session.set_auto_mode 命令，且 session.create 返回当前 auto_mode
# 设计：创建会话后读取 auto_mode，切换为 read_only 和 on，断言每次返回的模式正确；不触发真实 LLM
async def test_session_auto_mode_over_ipc(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)

    created = await _send_recv(
        reader,
        writer,
        "session.create",
        {"mode": "chat", "title": "auto mode test"},
        req_id="create",
    )
    assert "result" in created, created
    session_id = created["result"]["session_id"]
    assert created["result"].get("auto_mode", "off") == "off"

    set_ro = await _send_recv(
        reader,
        writer,
        "session.set_auto_mode",
        {"session_id": session_id, "mode": "read_only"},
        req_id="auto_ro",
    )
    assert set_ro["result"]["mode"] == "read_only"

    set_on = await _send_recv(
        reader,
        writer,
        "session.set_auto_mode",
        {"session_id": session_id, "mode": "on"},
        req_id="auto_on",
    )
    assert set_on["result"]["mode"] == "on"

    invalid = await _send_recv(
        reader,
        writer,
        "session.set_auto_mode",
        {"session_id": session_id, "mode": "fast"},
        req_id="auto_invalid",
    )
    assert "error" in invalid

    writer.close()
    await writer.wait_closed()


# 功能：验证 daemon 暴露 session.set_effort_level 命令，且 session.create 返回当前 effort_level
# 设计：创建会话后读取 effort_level，切换为 high 和 max，断言每次返回的等级正确；非法值应返回 error
async def test_session_effort_level_over_ipc(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)

    created = await _send_recv(
        reader,
        writer,
        "session.create",
        {"mode": "chat", "title": "effort test"},
        req_id="create",
    )
    assert "result" in created, created
    session_id = created["result"]["session_id"]
    assert created["result"].get("effort_level", "medium") == "medium"

    set_high = await _send_recv(
        reader,
        writer,
        "session.set_effort_level",
        {"session_id": session_id, "level": "high"},
        req_id="effort_high",
    )
    assert set_high["result"]["level"] == "high"

    set_max = await _send_recv(
        reader,
        writer,
        "session.set_effort_level",
        {"session_id": session_id, "level": "max"},
        req_id="effort_max",
    )
    assert set_max["result"]["level"] == "max"

    invalid = await _send_recv(
        reader,
        writer,
        "session.set_effort_level",
        {"session_id": session_id, "level": "turbo"},
        req_id="effort_invalid",
    )
    assert "error" in invalid

    writer.close()
    await writer.wait_closed()


# 功能：验证 daemon 暴露 session.set_model 命令，且 session.create 返回当前 model_preset
# 设计：创建会话后读取 model_preset，切换为 fast 和 powerful，断言每次返回的预设正确；非法值应返回 error
async def test_session_model_preset_over_ipc(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)

    created = await _send_recv(
        reader,
        writer,
        "session.create",
        {"mode": "chat", "title": "model test"},
        req_id="create",
    )
    assert "result" in created, created
    session_id = created["result"]["session_id"]
    assert created["result"].get("model_preset", "balanced") == "balanced"

    set_fast = await _send_recv(
        reader,
        writer,
        "session.set_model",
        {"session_id": session_id, "preset": "fast"},
        req_id="model_fast",
    )
    assert set_fast["result"]["preset"] == "fast"

    set_powerful = await _send_recv(
        reader,
        writer,
        "session.set_model",
        {"session_id": session_id, "preset": "powerful"},
        req_id="model_powerful",
    )
    assert set_powerful["result"]["preset"] == "powerful"

    invalid = await _send_recv(
        reader,
        writer,
        "session.set_model",
        {"session_id": session_id, "preset": "turbo"},
        req_id="model_invalid",
    )
    assert "error" in invalid

    writer.close()
    await writer.wait_closed()
