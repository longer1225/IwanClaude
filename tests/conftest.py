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
def daemon_env() -> dict[str, str]:
    # 用例级 fixture：返回要追加进 daemon 子进程的环境变量（默认空）
    return {}


@pytest.fixture
async def running_daemon(
    free_port: int, daemon_env: dict[str, str]
) -> AsyncGenerator[subprocess.Popen[bytes], None]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="iwan_daemon_"))
    env = os.environ.copy()
    env["IWAN_PORT"] = str(free_port)
    env["IWAN_LOG_FILE"] = ""
    # 测试 daemon 恒为免鉴权：用户 shell 里残留的 IWAN_TOKEN 不得泄进测试实例
    env["IWAN_TOKEN"] = ""
    # 同理隔离 GitHub 令牌：测试 daemon 的 cwd 是仓库根，会自动加载 .env 里的真实
    # IWAN_GITHUB_TOKEN——一旦带上它，连"非 git/匿名"类负向用例都可能因真拉到 PR 而翻红。
    # 配置层 IWAN_GITHUB_TOKEN 用 `is not None` 判定，故显式设空即可盖过 .env 的值。
    env["IWAN_GITHUB_TOKEN"] = ""
    env["IWAN_LOG_LEVEL"] = "WARNING"
    env["IWAN_SESSIONS_DIR"] = str(tmp_dir / "sessions")
    env["IWAN_POLICY_FILE"] = str(tmp_dir / "policy.toml")
    # 信任文件同样关进 tmp：否则 daemon 会读用户真实 ~/.iwan/trust.toml，
    # 目录被标成 deny/allow 时信任相关用例会随机翻红（也与"测试不碰用户数据"冲突）
    env["IWAN_TRUST_FILE"] = str(tmp_dir / "trust.toml")
    # 插件安装目录与账本一起改道临时目录：集成测试的 set_enabled 绝不写用户 ~/.iwan
    env["IWAN_PLUGINS_DIR"] = str(tmp_dir / "plugins")
    # 关语音预热：测试 daemon 不拖百兆模型进内存、不依赖模型缓存存在
    env["IWAN_SPEECH_PREWARM"] = "0"
    # LLM：daemon 启动期就会构造 provider（会话摘要用），缺 key 直接 SystemExit
    # ——不注入假 key 的话，整套集成测试都会死在 "Daemon did not start"。
    # 没有真实 key 的环境用假 key + 死端口 base_url：daemon 能起，万一有用例触发
    # run 也会立刻 llm_error（不联网、不烧 token）；有真 key 的环境保持原样。
    if not (env.get("DEEPSEEK_API_KEY") or env.get("ANTHROPIC_API_KEY")):
        env["DEEPSEEK_API_KEY"] = "test-key-not-validated"
        env["IWAN_LLM_BASE_URL"] = "http://127.0.0.1:9"
    # 用例级覆盖（例如信任闸门用例要把信任超时压短）
    env.update(daemon_env)

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
