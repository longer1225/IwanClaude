"""
daemon_guard - 懒启动 daemon 守护模块

【作用】
让用户感觉"敲一下命令就能用"，不需要先 `iwan core start`。
首次需要 daemon 时自动后台起一个，后续复用。

【核心 API】
- ensure_daemon(config): 确保 daemon 在跑，没跑就起一个

【设计要点】
1. 先试连 socket，连得上直接返回 True（最快路径）
2. 连不上 → 看 PID 文件，PID 活着说明别人正在起，等他绑好 socket
3. PID 不活或文件不存在 → atomic 创建文件抢锁 → 抢到才起 daemon
4. 起 daemon 后台进程（detach + 日志重定向）
5. 轮询 socket 直到就绪或超时
6. 成功后 PID 文件保留作为状态记录；daemon 退出后下次自动清理接管

【PID 文件】
- 位置: ~/.iwan/daemon.pid
- 格式: 纯文本，单行 PID
- 作用: 多终端同时启动时的协调点 + daemon 状态记录

【跨平台】
- Windows: DETACHED_PROCESS 后台运行；ctypes + kernel32 探测进程
- Unix: start_new_session 后台运行；os.kill(pid, 0) 探测进程
"""
from __future__ import annotations

# logging: 日志记录
# os: 操作系统接口（os.open / os.kill / O_EXCL）
# socket: TCP 连接探测
# subprocess: 起 daemon 进程
# sys: sys.platform 平台判断 / sys.executable Python 解释器路径
# time: 轮询间隔
# pathlib: PID/日志文件路径
# typing: 类型提示
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from iwan_claude.core.config import IwanConfig

log = logging.getLogger(__name__)

# 默认 PID 文件路径（用户 home 下的 .iwan 目录）
_DEFAULT_PID_FILE = "~/.iwan/daemon.pid"

# 默认 daemon 日志路径
_DEFAULT_DAEMON_LOG = "~/.iwan/logs/daemon.log"

# 轮询间隔（秒）
_POLL_INTERVAL_S = 0.1

# 单次 TCP 探测超时（秒）
_CONNECT_TIMEOUT_S = 0.3


def _is_windows() -> bool:
    """判断是否 Windows 平台"""
    return sys.platform.startswith("win")


def _is_pid_alive(pid: int) -> bool:
    """
    检查指定 PID 的进程是否还活着

    Windows: OpenProcess + GetExitCodeProcess，STILL_ACTIVE(259) 表示活着
    Unix: os.kill(pid, 0) 不真杀只探测，没抛 ProcessLookupError 就是活的

    参数：
        pid: 进程 ID
    返回：
        True: 进程还在跑
        False: 进程已退出或无权限查询
    """
    if pid <= 0:
        return False

    if _is_windows():
        # Windows 用 kernel32 探测
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259

            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                ):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        # Unix: os.kill(pid, 0) 不真杀，只探测
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # 进程存在但没权限 → 还是活的
            return True
        return True


def _can_connect(host: str, port: int, timeout: float = _CONNECT_TIMEOUT_S) -> bool:
    """
    尝试 TCP 连接 daemon socket

    参数：
        host: 主机
        port: 端口
        timeout: 连接超时
    返回：
        True: 能连上（daemon 在跑且绑了 socket）
        False: 连不上
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_pid_file(pid_file: Path) -> int | None:
    """
    读 PID 文件

    返回：
        int: 文件里的 PID
        None: 文件不存在 / 为空 / 内容不是数字
    """
    try:
        text = pid_file.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def _write_pid_file(pid_file: Path, pid: int) -> None:
    """写 PID 到文件"""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid), encoding="utf-8")


def _clear_pid_file(pid_file: Path) -> None:
    """清理 PID 文件（不存在则忽略）"""
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def _acquire_pid_lock(pid_file: Path) -> bool:
    """
    尝试抢 PID 锁（原子创建文件）

    返回 True 表示抢到锁，False 表示别人已经持有

    多终端同时启动时，只有一个能成功创建文件（O_CREAT | O_EXCL）
    """
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        # O_CREAT | O_EXCL: 文件不存在才创建，已存在则失败
        fd = os.open(
            str(pid_file),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o644,
        )
        os.close(fd)
        return True
    except FileExistsError:
        # 文件已存在 → 抢锁失败
        return False
    except OSError:
        return False


def _spawn_daemon(
    config: IwanConfig, pid_file: Path, log_file: Path
) -> int | None:
    """
    后台 spawn 一个 iwan-core daemon 进程

    Windows: CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    Unix: start_new_session=True

    参数：
        config: 配置（暂未使用，预留扩展）
        pid_file: PID 文件路径（预留，本函数不写）
        log_file: 日志输出路径

    返回：
        int: 子进程 PID
        None: 启动失败
    """
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # 用 sys.executable + 模块方式启动
    # 避免 PATH 里没有 iwan-core 包装器的情况
    cmd = [sys.executable, "-m", "iwan_claude.core"]

    # 日志重定向（追加模式，行缓冲）
    try:
        log_fp = open(log_file, "a", encoding="utf-8", buffering=1)
    except OSError as exc:
        log.error("daemon_guard: open log file failed: %s", exc)
        return None

    try:
        if _is_windows():
            # Windows: DETACHED_PROCESS 让子进程不继承父控制台
            # CREATE_NEW_PROCESS_GROUP 让 Ctrl+C 不传播到子进程
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                cmd,
                stdout=log_fp,
                stderr=log_fp,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                close_fds=True,
            )
        else:
            # Unix: start_new_session 创建新进程组
            proc = subprocess.Popen(
                cmd,
                stdout=log_fp,
                stderr=log_fp,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        log.error("daemon_guard: spawn failed: %s", exc)
        log_fp.close()
        return None

    # 不 wait，让 daemon 自己跑
    # 注意：log_fp 不能在这里 close，子进程继承 fd 继续写
    # 子进程退出后 OS 自动回收 fd
    return proc.pid


def _wait_for_ready(
    host: str,
    port: int,
    timeout: float = 5.0,
    poll_interval: float = _POLL_INTERVAL_S,
) -> bool:
    """
    轮询 socket 直到就绪或超时

    参数：
        host: 主机
        port: 端口
        timeout: 总超时秒数
        poll_interval: 轮询间隔秒数
    返回：
        True: 就绪
        False: 超时
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _can_connect(host, port):
            return True
        time.sleep(poll_interval)
    return False


def ensure_daemon(
    config: IwanConfig,
    *,
    timeout: float = 5.0,
    pid_file: str | Path | None = None,
    log_file: str | Path | None = None,
    spawn_fn: Callable[[IwanConfig, Path, Path], int | None] | None = None,
    connect_fn: Callable[[str, int], bool] | None = None,
) -> bool:
    """
    确保 daemon 在跑，没跑就起一个

    流程：
    1. 试连 socket，连得上直接返回 True（最快路径）
    2. 连不上 → 看 PID 文件
       - PID 活着 → 别人正在起，等他绑好 socket
       - PID 不活/文件不存在 → 清理后抢锁自己起
    3. 抢锁失败 → 别人抢到了，等他
    4. 抢锁成功 → spawn daemon → 写 PID → 等就绪
    5. 成功后 PID 文件保留作为状态记录

    参数：
        config: IwanConfig 配置（host + port）
        timeout: 等 socket 就绪的超时秒数（默认 5.0）
        pid_file: PID 文件路径（默认 ~/.iwan/daemon.pid）
        log_file: daemon 日志路径（默认 ~/.iwan/logs/daemon.log）
        spawn_fn: 自定义 spawn 函数（测试注入用）
        connect_fn: 自定义连接检测函数（测试注入用）

    返回：
        True: daemon 已就绪可用
        False: 启动失败或超时
    """
    pid_path = (
        Path(pid_file).expanduser() if pid_file else Path(_DEFAULT_PID_FILE).expanduser()
    )
    log_path = (
        Path(log_file).expanduser() if log_file else Path(_DEFAULT_DAEMON_LOG).expanduser()
    )

    # 自定义连接检测（测试用）
    can_connect = connect_fn or (lambda h, p: _can_connect(h, p))

    # ===== 1. 快速路径：daemon 已经在跑 =====
    if can_connect(config.host, config.port):
        return True

    # ===== 2. 看 PID 文件 =====
    existing_pid = _read_pid_file(pid_path)
    if existing_pid is not None and _is_pid_alive(existing_pid):
        # 别人正在起 daemon（还没绑 socket），等他
        log.debug(
            "daemon_guard: pid %d alive, waiting for socket", existing_pid
        )
        return _wait_for_ready(config.host, config.port, timeout=timeout)

    # ===== 3. PID 不活或文件不存在，清理后抢锁 =====
    _clear_pid_file(pid_path)

    if not _acquire_pid_lock(pid_path):
        # 抢锁失败，别人抢到了，等他
        log.debug("daemon_guard: pid lock held by other, waiting")
        return _wait_for_ready(config.host, config.port, timeout=timeout)

    # ===== 4. 抢到锁，spawn daemon =====
    try:
        # 立即写入当前 CLI 进程 PID 作为 sentinel
        # 防止别的终端在 spawn 完成前读到空文件误删锁
        # spawn 成功后会被真实 daemon PID 覆盖
        _write_pid_file(pid_path, os.getpid())

        spawn = spawn_fn or _spawn_daemon
        pid = spawn(config, pid_path, log_path)
        if pid is None:
            log.error("daemon_guard: spawn failed, see %s", log_path)
            _clear_pid_file(pid_path)
            return False

        # 写真实 daemon PID 覆盖 sentinel
        _write_pid_file(pid_path, pid)

        # ===== 5. 等 socket 就绪 =====
        ready = _wait_for_ready(config.host, config.port, timeout=timeout)
        if not ready:
            log.error(
                "daemon_guard: daemon not ready after %.1fs, see %s",
                timeout,
                log_path,
            )
            _clear_pid_file(pid_path)
            return False

        # 成功：PID 文件保留作为状态记录（下次启动可读）
        log.info("daemon_guard: daemon ready, pid=%d", pid)
        return True
    except Exception as exc:
        log.error("daemon_guard: ensure failed: %s", exc)
        _clear_pid_file(pid_path)
        return False
