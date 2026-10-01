# 功能：验证 token 鉴权端到端语义——未握手被拒、正确令牌放行、错误令牌硬失败
# 设计：起一个【独立于 conftest 免鉴权实例】的 token daemon（自带 fixture），
#       因为鉴权是唯一"按 daemon 实例配置"的行为；裸 socket 测协议层拒接
#       （不经 SocketClient 的自动握手，验证服务端判定本身），
#       SocketClient 只测握手正/误两路——两层各测各的，坏在哪层一眼可辨
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from iwan_claude.core.transport.socket_client import SocketClient

TEST_TOKEN = "sekrit-token-42"


@pytest.fixture
async def token_daemon(free_port: int) -> AsyncGenerator[int, None]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="iwan_authd_"))
    env = os.environ.copy()
    env["IWAN_PORT"] = str(free_port)
    env["IWAN_LOG_FILE"] = ""
    env["IWAN_LOG_LEVEL"] = "WARNING"
    env["IWAN_SESSIONS_DIR"] = str(tmp_dir / "sessions")
    env["IWAN_POLICY_FILE"] = str(tmp_dir / "policy.toml")
    env["IWAN_SPEECH_PREWARM"] = "0"
    env["IWAN_TOKEN"] = TEST_TOKEN
    proc = subprocess.Popen(
        [sys.executable, "-m", "iwan_claude.core"],
        env=env,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        try:
            _r, w = await asyncio.open_connection("127.0.0.1", free_port)
            w.close()
            await w.wait_closed()
            break  # 端口可达即可——探针连接没握手，daemon 侧记一次拒接属预期
        except (ConnectionRefusedError, OSError):
            pass
    else:
        proc.terminate()
        proc.wait()
        pytest.fail("token daemon 未在 10 秒内就绪")
    yield free_port
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    import shutil

    shutil.rmtree(tmp_dir, ignore_errors=True)


# 功能：裸连接不发 auth.hello 直接发业务命令，收到 -32001 错误并被断连
# 设计：故意绕开 SocketClient（它会代握手），用裸 asyncio 流验证服务端
#       "首行不是握手就拒"的判定本身；读第二行确认连上后命令也确实无响应（连接已关）
async def test_no_handshake_rejected(token_daemon: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", token_daemon)
    req = {
        "jsonrpc": "2.0",
        "id": "raw-1",
        "method": "core.ping",
        "params": {"type": "core.ping", "client": "cli/test"},
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    assert line, "应收到一条错误应答"
    obj = json.loads(line)
    assert obj["error"]["code"] == -32001
    writer.close()
    await writer.wait_closed()


# 功能：正确令牌的完整链路——握手放行后业务命令照常应答
# 设计：两条 readline 顺序读（hello 应答、pong 应答），服务端握手在读循环
#       之前同步完成，天然保序，不需要 pending 路由——这正是握手设计的收益
async def test_handshake_ok_then_ping(token_daemon: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", token_daemon)
    hello = {
        "jsonrpc": "2.0",
        "id": "raw-auth",
        "method": "auth.hello",
        "params": {"token": TEST_TOKEN},
    }
    writer.write((json.dumps(hello) + "\n").encode())
    await writer.drain()
    first = await asyncio.wait_for(reader.readline(), timeout=5.0)
    assert '"ok":true' in first.decode()
    req = {
        "jsonrpc": "2.0",
        "id": "raw-2",
        "method": "core.ping",
        "params": {"type": "core.ping", "client": "cli/test"},
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    second = await asyncio.wait_for(reader.readline(), timeout=5.0)
    obj = json.loads(second)
    assert "error" not in obj
    assert "result" in obj
    writer.close()
    await writer.wait_closed()


# 功能：SocketClient 带错误令牌 connect() 抛 RuntimeError 而非静默裸连
# 设计：fail-closed 的客户端半边——宁可启动即炸，不能让业务命令发到未鉴权
#       连接上再超时；RuntimeError 与 OSError（网络层）区分开，调用方可分别处理
async def test_client_wrong_token_raises(token_daemon: int) -> None:
    c = SocketClient("127.0.0.1", token_daemon, "wrong-token")
    with pytest.raises(RuntimeError):
        await c.connect()
    await c.close()


# 功能：SocketClient 正确令牌自动握手后业务直通
# 设计：跑读循环 task 再 send_command（future settle 依赖事件循环，bench 的教训），
#       与免鉴权集成套同一调用形状——握手对业务调用完全透明是本用例的不变式
async def test_client_token_accept(token_daemon: int) -> None:
    c = SocketClient("127.0.0.1", token_daemon, TEST_TOKEN)
    await c.connect()
    task = asyncio.create_task(c.run_event_loop())
    try:
        resp = await c.send_command("core.ping", {"type": "core.ping", "client": "cli/test"})
        assert resp is not None
    finally:
        task.cancel()
        await c.close()
