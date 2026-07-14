from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

from iwan_claude.core.config import IwanConfig

_PID_FILE = Path.home() / ".iwan" / "iwan-core.pid"

# 平台兼容性判断
IS_WINDOWS = sys.platform == "win32"


# 尝试连接 daemon，成功则正常返回，失败则抛出 ConnectionRefusedError/OSError
async def _ping_check(config: IwanConfig) -> None:
    _r, w = await asyncio.open_connection(config.host, config.port)
    w.close()
    await w.wait_closed()


# 跨平台：检查指定 PID 的进程是否存在
def _pid_alive(pid: int) -> bool:
    if IS_WINDOWS:
        # Windows：用 tasklist 或 ctypes，这里用最简单的方法：tasklist 过滤
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        # Unix：os.kill(pid, 0) 不发信号，只做"进程存在"检查
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


# 读取 PID 文件并确认进程存活，进程已消失则删除文件并返回 None
def _running_pid() -> int | None:
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
        if _pid_alive(pid):
            return pid
        # 进程不存在，删除脏 PID 文件
        _PID_FILE.unlink(missing_ok=True)
        return None
    except (ValueError, PermissionError):
        _PID_FILE.unlink(missing_ok=True)
        return None


# 跨平台：终止指定 PID 的进程
def _kill_pid(pid: int) -> None:
    if IS_WINDOWS:
        # Windows：用 taskkill /F /PID 强制结束
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=5,
        )
    else:
        # Unix：发 SIGTERM 优雅终止
        os.kill(pid, signal.SIGTERM)


# 打印 daemon 当前状态（running / not running）
def cmd_core_status(config: IwanConfig) -> None:
    try:
        asyncio.run(_ping_check(config))
        print(f"running  ({config.host}:{config.port})")
    except (ConnectionRefusedError, OSError):
        print("not running")


# 在后台启动 daemon，若已在运行则提示并退出
def cmd_core_start(config: IwanConfig) -> None:
    try:
        asyncio.run(_ping_check(config))
        print(f"already running  ({config.host}:{config.port})")
        return
    except (ConnectionRefusedError, OSError):
        pass

    popen_kwargs: dict = dict(
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if IS_WINDOWS:
        # Windows：使用 CREATE_NEW_PROCESS_GROUP 创建独立进程组
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Unix：start_new_session 创建新会话，daemon 独立于父终端
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        [sys.executable, "-m", "iwan_claude.core"],
        **popen_kwargs,
    )
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(proc.pid))
    print(f"started  pid={proc.pid}  ({config.host}:{config.port})")


# 终止 daemon 进程，若未运行则提示
def cmd_core_stop(config: IwanConfig) -> None:
    pid = _running_pid()
    if pid is None:
        print("not running")
        return
    _kill_pid(pid)
    _PID_FILE.unlink(missing_ok=True)
    print(f"stopped  pid={pid}")
