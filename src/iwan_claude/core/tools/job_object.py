"""
Windows Job Object 子进程组管理 - 防止 daemon 退出/超时后留下孤儿进程

【学习要点】
1. Job Object：Windows 内核对象，把若干进程归入一个组；设置
   JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 后，Job 的最后一个句柄被关闭时
   组内所有进程（含子孙进程）一并终止。
2. daemon 场景：iwan-core 被杀时进程句柄由 OS 回收 → Job 句柄随之关闭 →
   bash / run_python 拉起的子进程树被连带杀死，不留孤儿。
3. 超时场景：命令超时后 proc.kill() 只能杀直接子进程（如 powershell.exe），
   它派生的孙进程仍存活；关闭 Job 句柄可把整棵树清掉。
4. 非 Windows 平台：所有函数优雅降级为 no-op（返回 None/False），调用方无需分支。

【使用方式】
    job = create_process_job()
    try:
        proc = await asyncio.create_subprocess_exec(...)
        assign_process_to_job(job, proc.pid)
        ...等待/超时处理...
    finally:
        close_job(job)  # 触发 KILL_ON_JOB_CLOSE 清理残余进程
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger(__name__)

# 平台判定：Job Object 是 Win32 概念，其他平台全部走 no-op 分支
_IS_WINDOWS = sys.platform == "win32"

# ===== Win32 常量 =====
# Job 关闭时终止组内所有进程（核心能力）
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
# 启用进程级内存配额限制
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
# 启用进程级 CPU 时间配额限制（单位 100ns）
_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
# SetInformationJobObject 的信息类别：扩展限制结构
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
# OpenProcess 所需权限：设置配额 + 终止（AssignProcessToJobObject 要求）
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0020

# 默认内存配额：2 GiB（run_python 建 venv / pip 安装都需要一定余量）
_DEFAULT_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
# 默认 CPU 时间配额：0 = 不限制（超时由 asyncio.wait_for 控制；此项留给后续收紧）
_DEFAULT_CPU_TIME_LIMIT_S = 0


if _IS_WINDOWS:

    # JOBOBJECT_BASIC_LIMIT_INFORMATION 的 ctypes 镜像（字段顺序/大小必须与 WinSDK 一致）
    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),   # LARGE_INTEGER：每进程 CPU 时间上限
            ("PerJobUserTimeLimit", ctypes.c_int64),       # 每 Job CPU 时间上限
            ("LimitFlags", wintypes.DWORD),                # 启用哪些限制（*_LIMIT_* 位组合）
            ("MinimumWorkingSetSize", ctypes.c_size_t),    # 工作集下限（0=不限制）
            ("MaximumWorkingSetSize", ctypes.c_size_t),    # 工作集上限
            ("ActiveProcessLimit", wintypes.DWORD),        # Job 内活跃进程数上限（0=不限）
            ("Affinity", wintypes.DWORD),                  # CPU 亲和性掩码
            ("PriorityClass", wintypes.DWORD),             # 优先级类
            ("SchedulingClass", wintypes.DWORD),           # 调度类
        ]

    # IO_COUNTERS 的 ctypes 镜像（本模块不使用，但扩展结构体内嵌它，布局必须一致）
    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    # JOBOBJECT_EXTENDED_LIMIT_INFORMATION：基本限制 + 内存配额
    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),       # 每进程内存上限（字节）
            ("JobMemoryLimit", ctypes.c_size_t),           # Job 总内存上限
            ("PeakProcessMemoryUsed", ctypes.c_size_t),    # 输出字段（读回用）
            ("PeakJobMemoryUsed", ctypes.c_size_t),        # 输出字段
        ]

    # 预取三个 kernel32 入口并声明签名（避免每次调用重复配置）
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # CreateJobObjectW：创建 Job，返回 HANDLE（NULL=失败）
    _CreateJobObjectW = _kernel32.CreateJobObjectW
    _CreateJobObjectW.restype = wintypes.HANDLE
    _CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    # SetInformationJobObject：设置 Job 限额信息
    _SetInformationJobObject = _kernel32.SetInformationJobObject
    _SetInformationJobObject.restype = wintypes.BOOL
    _SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    # OpenProcess：按 PID 取得进程句柄（需 SET_QUOTA|TERMINATE 权限）
    _OpenProcess = _kernel32.OpenProcess
    _OpenProcess.restype = wintypes.HANDLE
    _OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    # AssignProcessToJobObject：把进程加入 Job
    _AssignProcessToJobObject = _kernel32.AssignProcessToJobObject
    _AssignProcessToJobObject.restype = wintypes.BOOL
    _AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    # CloseHandle：关闭句柄（Job 的最后句柄关闭即触发终止）
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.restype = wintypes.BOOL
    _CloseHandle.argtypes = [wintypes.HANDLE]


# 创建 Job Object 并设置 KILL_ON_JOB_CLOSE / 内存 / CPU 限额；非 Windows 或失败返回 None
def create_process_job(
    memory_limit_bytes: int = _DEFAULT_MEMORY_LIMIT_BYTES,
    cpu_time_limit_s: int = _DEFAULT_CPU_TIME_LIMIT_S,
) -> int | None:
    # 非 Windows：优雅降级，调用方按无 Job 语义运行
    if not _IS_WINDOWS:
        return None
    job = _CreateJobObjectW(None, None)
    if not job:
        logger.warning("job object: CreateJobObjectW failed err=%s", ctypes.get_last_error())
        return None
    job_int = int(job)  # HANDLE → 整数句柄值，便于类型标注
    # 组装限额位图：关句柄杀进程组必开；内存/CPU 视入参而定
    limit_flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ext = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    if memory_limit_bytes > 0:
        limit_flags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        ext.ProcessMemoryLimit = ctypes.c_size_t(memory_limit_bytes)
    if cpu_time_limit_s > 0:
        limit_flags |= _JOB_OBJECT_LIMIT_PROCESS_TIME
        # Windows CPU 时间以 100 纳秒为单位
        ext.BasicLimitInformation.PerProcessUserTimeLimit = cpu_time_limit_s * 10_000_000
    ext.BasicLimitInformation.LimitFlags = limit_flags
    ok = _SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(ext),
        ctypes.sizeof(ext),
    )
    if not ok:
        err = ctypes.get_last_error()
        logger.warning("job object: SetInformationJobObject failed err=%s", err)
        _CloseHandle(job)
        return None
    return job_int


# 把已创建的子进程（按 PID）加入 Job；必须在进程退出前尽快调用
def assign_process_to_job(job: int | None, pid: int) -> bool:
    # 无 Job（非 Windows 或创建失败）→ no-op
    if job is None:
        return False
    # 按 PID 打开进程句柄；进程可能已瞬间退出（OpenProcess 失败），静默返回 False
    proc_handle = _OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not proc_handle:
        logger.debug("job object: OpenProcess(pid=%s) failed err=%s", pid, ctypes.get_last_error())
        return False
    try:
        ok = bool(_AssignProcessToJobObject(wintypes.HANDLE(job), proc_handle))
        if not ok:
            logger.warning(
                "job object: AssignProcessToJobObject(pid=%s) failed err=%s",
                pid, ctypes.get_last_error(),
            )
        return ok
    finally:
        # 进程句柄用完即关；Job 句柄仍持有，不影响归属关系
        _CloseHandle(proc_handle)


# 关闭 Job 句柄：若组内仍有存活进程，KILL_ON_JOB_CLOSE 会把整棵进程树终止
def close_job(job: int | None) -> None:
    if job is None:
        return
    if not _CloseHandle(wintypes.HANDLE(job)):
        logger.debug("job object: CloseHandle(job=%s) failed err=%s", job, ctypes.get_last_error())
