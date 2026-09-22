"""
daemon_guard 单元测试

测试懒启动 daemon 守护模块的行为：
- ensure_daemon 在 daemon 已就绪时直接返回 True
- ensure_daemon 在 daemon 没跑时自动起一个
- PID 文件锁防止多终端同时起
- 死 PID 文件会被清理接管
- spawn 失败时正确清理
- 超时时返回 False

测试策略：
- 用 connect_fn 注入 mock 连接检测，避免真实 socket
- 用 spawn_fn 注入 mock spawn，避免真起子进程
- 用 tmp_path 隔离 PID 文件和日志文件
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from iwan_claude.cli import daemon_guard
from iwan_claude.cli.daemon_guard import (
    _acquire_pid_lock,
    _can_connect,
    _clear_pid_file,
    _is_pid_alive,
    _read_pid_file,
    _wait_for_ready,
    _write_pid_file,
    ensure_daemon,
)
from iwan_claude.core.config import IwanConfig

# 跳过条件：非 Windows 时也跑，但要分平台断言
IS_WINDOWS = sys.platform.startswith("win")


def _make_config() -> IwanConfig:
    """构造测试用 IwanConfig，用冷门端口避免碰撞"""
    return IwanConfig(host="127.0.0.1", port=19999)


# ===== 底层函数测试 =====


class TestCanConnect:
    """测试 _can_connect TCP 探测"""

    def test_returns_false_when_no_listener(self) -> None:
        # 没人监听的端口应该连不上
        # 用一个不太可能被占的端口
        assert _can_connect("127.0.0.1", 59998, timeout=0.1) is False

    def test_returns_true_when_listener_exists(self) -> None:
        # 起一个 socket listener，应该能连
        import socket

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))  # 让 OS 选端口
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            assert _can_connect("127.0.0.1", port, timeout=0.5) is True
        finally:
            srv.close()


class TestPidFileIO:
    """测试 PID 文件读写"""

    def test_write_and_read(self, tmp_path: Path) -> None:
        f = tmp_path / "test.pid"
        _write_pid_file(f, 12345)
        assert _read_pid_file(f) == 12345

    def test_read_missing_file_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "missing.pid"
        assert _read_pid_file(f) is None

    def test_read_empty_file_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.pid"
        f.write_text("")
        assert _read_pid_file(f) is None

    def test_read_garbage_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "garbage.pid"
        f.write_text("not_a_number")
        assert _read_pid_file(f) is None

    def test_clear_missing_file_is_noop(self, tmp_path: Path) -> None:
        f = tmp_path / "missing.pid"
        _clear_pid_file(f)  # 不应抛异常
        assert not f.exists()

    def test_clear_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.pid"
        f.write_text("123")
        _clear_pid_file(f)
        assert not f.exists()


class TestPidLock:
    """测试 PID 文件锁"""

    def test_acquire_lock_first_time(self, tmp_path: Path) -> None:
        f = tmp_path / "lock.pid"
        assert _acquire_pid_lock(f) is True
        assert f.exists()

    def test_acquire_lock_fails_second_time(self, tmp_path: Path) -> None:
        f = tmp_path / "lock.pid"
        _acquire_pid_lock(f)
        # 第二次抢锁应该失败（文件已存在）
        assert _acquire_pid_lock(f) is False


class TestIsPidAlive:
    """测试 PID 存活检测"""

    def test_current_pid_is_alive(self) -> None:
        # 当前进程一定活着
        assert _is_pid_alive(os.getpid()) is True

    def test_invalid_pid_returns_false(self) -> None:
        assert _is_pid_alive(0) is False
        assert _is_pid_alive(-1) is False

    def test_dead_pid_returns_false(self) -> None:
        # PID 1 在 Unix 上是 init，活着；Windows 上基本不存在
        # 用一个明显不存在的超大 PID
        if IS_WINDOWS:
            # Windows 下 PID 0xFFFFFFFF 基本不存在
            assert _is_pid_alive(0xFFFFFFFE) is False
        else:
            # Unix: PID 0xFFFFFFFF 超出范围
            assert _is_pid_alive(2**30) is False


class TestWaitForReady:
    """测试 socket 就绪等待"""

    def test_returns_true_when_immediately_connectable(self) -> None:
        # 用 connect_fn 注入，避免真实 socket
        def always_true(_h: str, _p: int) -> bool:
            return True

        # 直接调 _can_connect 模拟
        with patch("iwan_claude.cli.daemon_guard._can_connect", return_value=True):
            assert _wait_for_ready("127.0.0.1", 12345, timeout=1.0) is True

    def test_returns_false_on_timeout(self) -> None:
        with patch("iwan_claude.cli.daemon_guard._can_connect", return_value=False):
            # 用很短的 timeout 加快测试
            assert _wait_for_ready("127.0.0.1", 12345, timeout=0.3) is False


# ===== ensure_daemon 集成测试 =====


class TestEnsureDaemon:
    """测试 ensure_daemon 主流程"""

    def test_returns_true_when_already_connected(self, tmp_path: Path) -> None:
        """场景 1：daemon 已就绪，直接返回 True"""
        pid_file = tmp_path / "daemon.pid"
        log_file = tmp_path / "daemon.log"

        def always_connect(_h: str, _p: int) -> bool:
            return True

        result = ensure_daemon(
            _make_config(),
            pid_file=pid_file,
            log_file=log_file,
            connect_fn=always_connect,
        )
        assert result is True
        # 不应该写 PID 文件（没起 daemon）
        assert not pid_file.exists()

    def test_spawns_when_not_connected(self, tmp_path: Path, monkeypatch) -> None:
        """场景 2：daemon 没跑，自动起一个"""
        pid_file = tmp_path / "daemon.pid"
        log_file = tmp_path / "daemon.log"
        call_count = [0]
        spawn_count = [0]

        def fake_connect(_h: str, _p: int) -> bool:
            call_count[0] += 1
            # 第 1 次快速路径 → False
            # spawn 后 _wait_for_ready 调用 → True
            return call_count[0] >= 2

        def fake_spawn(_cfg, _pf: Path, _lf: Path) -> int | None:
            spawn_count[0] += 1
            return os.getpid()

        monkeypatch.setattr(daemon_guard, "_can_connect", fake_connect)

        result = ensure_daemon(
            _make_config(),
            pid_file=pid_file,
            log_file=log_file,
            spawn_fn=fake_spawn,
            timeout=2.0,
        )
        assert result is True
        assert spawn_count[0] == 1
        assert pid_file.read_text().strip() == str(os.getpid())

    def test_returns_false_when_spawn_fails(self, tmp_path: Path, monkeypatch) -> None:
        """场景 3：spawn 失败，返回 False 并清理"""
        pid_file = tmp_path / "daemon.pid"
        log_file = tmp_path / "daemon.log"

        monkeypatch.setattr(daemon_guard, "_can_connect", lambda _h, _p: False)

        def failing_spawn(_cfg, _pf: Path, _lf: Path) -> int | None:
            return None

        result = ensure_daemon(
            _make_config(),
            pid_file=pid_file,
            log_file=log_file,
            spawn_fn=failing_spawn,
            timeout=1.0,
        )
        assert result is False
        # PID 文件应该被清理
        assert not pid_file.exists()

    def test_returns_false_on_timeout(self, tmp_path: Path, monkeypatch) -> None:
        """场景 4：spawn 后 daemon 没绑上 socket，超时"""
        pid_file = tmp_path / "daemon.pid"
        log_file = tmp_path / "daemon.log"

        monkeypatch.setattr(daemon_guard, "_can_connect", lambda _h, _p: False)

        def fake_spawn(_cfg, _pf: Path, _lf: Path) -> int | None:
            return 999999  # 一个不存在的 PID

        result = ensure_daemon(
            _make_config(),
            pid_file=pid_file,
            log_file=log_file,
            spawn_fn=fake_spawn,
            timeout=0.3,  # 短超时
        )
        assert result is False
        # 超时后清理 PID 文件
        assert not pid_file.exists()

    def test_recovers_from_dead_pid_file(self, tmp_path: Path, monkeypatch) -> None:
        """场景 5：PID 文件存在但 PID 已死，清理接管"""
        pid_file = tmp_path / "daemon.pid"
        log_file = tmp_path / "daemon.log"

        # 写一个明显已死的 PID
        pid_file.write_text("99999999")
        assert not _is_pid_alive(99999999)  # 确认死

        spawn_count = [0]
        call_count = [0]

        def fake_connect(_h: str, _p: int) -> bool:
            call_count[0] += 1
            # 第 1 次快速路径 → False
            # spawn 后 → True
            return call_count[0] >= 2

        def fake_spawn(_cfg, _pf: Path, _lf: Path) -> int | None:
            spawn_count[0] += 1
            return os.getpid()

        monkeypatch.setattr(daemon_guard, "_can_connect", fake_connect)

        result = ensure_daemon(
            _make_config(),
            pid_file=pid_file,
            log_file=log_file,
            spawn_fn=fake_spawn,
            timeout=2.0,
        )
        assert result is True
        assert spawn_count[0] == 1  # 起了一次 daemon

    def test_waits_when_other_holding_lock(self, tmp_path: Path, monkeypatch) -> None:
        """场景 6：别人正在起 daemon（PID 文件存在且 PID 活着），等就绪"""
        pid_file = tmp_path / "daemon.pid"
        log_file = tmp_path / "daemon.log"

        # 写当前进程 PID（活着），模拟别人正在起
        pid_file.write_text(str(os.getpid()))

        spawn_count = [0]
        call_count = [0]

        def fake_connect(_h: str, _p: int) -> bool:
            call_count[0] += 1
            # 第 1 次快速路径 → False
            # 第 2 次起 _wait_for_ready → True（别人绑好了）
            return call_count[0] >= 2

        def should_not_spawn(_cfg, _pf: Path, _lf: Path) -> int | None:
            raise AssertionError("不应该 spawn，PID 活着应该等")

        monkeypatch.setattr(daemon_guard, "_can_connect", fake_connect)

        result = ensure_daemon(
            _make_config(),
            pid_file=pid_file,
            log_file=log_file,
            spawn_fn=should_not_spawn,
            timeout=2.0,
        )
        assert result is True

    def test_uses_real_pidfile_when_no_arg(self, tmp_path: Path, monkeypatch) -> None:
        """场景 7：不传 pid_file 时用默认路径"""
        # 用 monkeypatch 改默认路径到 tmp_path，避免污染用户 home
        fake_default = tmp_path / "daemon.pid"
        monkeypatch.setattr(daemon_guard, "_DEFAULT_PID_FILE", str(fake_default))
        monkeypatch.setattr(
            daemon_guard, "_DEFAULT_DAEMON_LOG", str(tmp_path / "daemon.log")
        )
        monkeypatch.setattr(daemon_guard, "_can_connect", lambda _h, _p: True)

        result = ensure_daemon(_make_config())
        assert result is True
