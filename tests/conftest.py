from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest


@pytest.fixture
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return port  # socket released; daemon can bind to this port


@pytest.fixture
async def running_daemon(free_port: int) -> AsyncGenerator[subprocess.Popen[bytes], None]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="iwan_daemon_"))
    env = os.environ.copy()
    env["IWAN_PORT"] = str(free_port)
    env["IWAN_LOG_FILE"] = ""
    env["IWAN_LOG_LEVEL"] = "WARNING"
    env["IWAN_SESSIONS_DIR"] = str(tmp_dir / "sessions")
    env["IWAN_POLICY_FILE"] = str(tmp_dir / "policy.toml")

    # 启动 daemon 子进程，将 stderr 重定向到临时日志以便排查启动失败
    stderr_path = tmp_dir / "daemon_stderr.log"
    proc = subprocess.Popen(
        [sys.executable, "-m", "iwan_claude.core"],
        env=env,
        stderr=open(stderr_path, "w", encoding="utf-8"),
    )

    # 首次冷启动需要编译字节码并加载持久化策略，超时放宽到 10 秒
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
            writer.close()
            await writer.wait_closed()
            break
        except (ConnectionRefusedError, OSError):
            pass
    else:
        proc.terminate()
        proc.wait()
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="ignore") if stderr_path.exists() else ""
        pytest.fail(f"Daemon did not start within 10 seconds\nstderr:\n{stderr_text}")

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
