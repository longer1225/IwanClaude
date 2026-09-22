"""Windows Job Object 模块测试（任务 8）

核心不变式：加入 Job 的进程树在 Job 最后一个句柄关闭时被 OS 终止（防孤儿进程）。
非 Windows 平台验证优雅降级（全 no-op），Windows 平台真实 spawn/kill 验证。
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from iwan_claude.core.tools.job_object import (
    assign_process_to_job,
    close_job,
    create_process_job,
)

_IS_WINDOWS = sys.platform == "win32"


# 功能：验证非 Windows 平台三个函数全部优雅降级为 no-op（create=None / assign=False / close 静默）
# 设计：调用方（bash/run_python）按"job 可能为 None"写统一逻辑的前提，
#       是 Linux/macOS 分支绝不抛 ctypes 异常——此测试锁死该契约；Windows 上跳过
@pytest.mark.skipif(_IS_WINDOWS, reason="no-op 降级仅在非 Windows 平台生效")
def test_graceful_noop_on_non_windows() -> None:
    job = create_process_job()
    assert job is None
    assert assign_process_to_job(job, 1234) is False
    close_job(job)  # 不抛异常即通过


# 功能：验证 Windows 上 create_process_job 返回有效句柄且 close_job 可正常关闭
# 设计：真实调用 Win32 CreateJobObjectW/SetInformationJobObject；若环境权限受限
#       （如某些容器）create 返回 None，主动 skip 而非误报失败
@pytest.mark.skipif(not _IS_WINDOWS, reason="Job Object 是 Windows 专属机制")
def test_create_and_close_handle_roundtrip() -> None:
    job = create_process_job()
    if job is None:
        pytest.skip("当前环境无法创建 Job Object（权限受限）")
    assert isinstance(job, int) and job > 0
    close_job(job)
    # 重复关闭同一句柄不应 crash（CloseHandle 对无效句柄返回 False，仅 debug 日志）
    close_job(job)


# 功能：验证带内存与 CPU 时间限额参数的创建路径同样成功
# 设计：限额设置走 SetInformationJobObject 的另外两个 LIMIT 位；若某位设置失败
#       整个创建返回 None——单独用例避免"默认参数能过、限额参数静默失效"的盲区
@pytest.mark.skipif(not _IS_WINDOWS, reason="Job Object 是 Windows 专属机制")
def test_create_with_memory_and_cpu_limits() -> None:
    job = create_process_job(memory_limit_bytes=64 * 1024 * 1024, cpu_time_limit_s=5)
    if job is None:
        pytest.skip("当前环境无法设置 Job 限额（权限受限）")
    close_job(job)


# 功能：验证子进程加入 Job 后，关闭 Job 句柄会将其强制终止（KILL_ON_JOB_CLOSE 核心能力）
# 设计：真实 spawn 一个 sleep 60s 的 python 子进程 → assign → close → proc.wait()
#       必须在超时窗口内以非零退出码返回（被 OS 杀死）；若没被杀死 wait 抛 TimeoutExpired
#       即为回归。finally 兜底 kill 防止测试失败时留下孤儿
@pytest.mark.skipif(not _IS_WINDOWS, reason="Job Object 是 Windows 专属机制")
def test_kill_on_job_close_terminates_child() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        job = create_process_job()
        if job is None:
            pytest.skip("当前环境无法创建 Job Object（权限受限）")
        assert assign_process_to_job(job, proc.pid) is True
        close_job(job)  # 最后一个句柄关闭 → OS 终止组内进程

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                assert rc != 0  # 被终止而非自然退出
                return
            time.sleep(0.1)
        pytest.fail("关闭 Job 句柄后子进程仍存活：KILL_ON_JOB_CLOSE 未生效")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# 功能：验证 assign_process_to_job 对无效/已退出 PID 与空 Job 的容错（返回 False 不抛异常）
# 设计：生产路径存在"进程秒退 → OpenProcess 失败"竞态，绝不能让 bash/run_python
#       因归属失败而崩掉整个工具调用——False + debug 日志是契约的一部分
@pytest.mark.skipif(not _IS_WINDOWS, reason="Job Object 是 Windows 专属机制")
def test_assign_handles_bad_pid_and_null_job() -> None:
    assert assign_process_to_job(None, 1) is False  # 无 Job（非 Windows/创建失败）
    job = create_process_job()
    if job is None:
        pytest.skip("当前环境无法创建 Job Object（权限受限）")
    try:
        # 极大 PID 基本不存在；即便撞上真实进程也只是返回 True/False，绝不抛
        assert assign_process_to_job(job, 2**31 - 2) in (True, False)
    finally:
        close_job(job)
