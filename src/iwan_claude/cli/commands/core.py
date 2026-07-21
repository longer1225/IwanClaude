"""
核心服务管理命令模块 - 管理 daemon 的启动、停止和状态查询

【学习要点】
1. 进程管理：如何检测进程是否存在、如何终止进程
2. 跨平台兼容性：Windows 和 Unix 系统的进程管理方式不同
3. PID 文件：用于记录后台进程的 PID，实现进程状态追踪
4. 后台进程启动：使用 subprocess.Popen 创建独立于终端的进程
5. 守护进程模式：在 Unix 上使用 start_new_session，在 Windows 上使用 CREATE_NEW_PROCESS_GROUP

【核心概念】
- Daemon：在后台运行的服务进程，不依附于终端
- PID：进程标识符，每个运行中的进程都有唯一的 PID
- PID 文件：存储 PID 的文件，用于追踪后台进程
"""
from __future__ import annotations

# asyncio：用于异步网络连接测试
# os：操作系统相关功能，如进程管理
# signal：信号处理，用于 Unix 系统的进程终止
# subprocess：创建子进程，用于启动 daemon 和执行系统命令
# sys：系统相关操作，如获取 Python 解释器路径、判断操作系统
# pathlib：路径操作，用于文件和目录管理
import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

# 导入配置数据结构
from iwan_claude.core.config import IwanConfig

# PID 文件路径：用户主目录下的 .iwan/iwan-core.pid
# 这个文件用于记录 daemon 进程的 PID
_PID_FILE = Path.home() / ".iwan" / "iwan-core.pid"

# 平台兼容性判断：检查当前操作系统是否为 Windows
IS_WINDOWS = sys.platform == "win32"


# 尝试连接 daemon，成功则正常返回，失败则抛出异常
async def _ping_check(config: IwanConfig) -> None:
    """
    检查 daemon 是否正在运行
    
    通过尝试建立 TCP 连接来检测 daemon 是否存活
    如果连接成功，说明 daemon 正在运行；如果连接被拒绝，说明未运行
    
    参数：
        config: IwanConfig 配置对象，包含 host 和 port 信息
    
    异常：
        ConnectionRefusedError: 连接被拒绝（daemon 未运行）
        OSError: 其他网络错误
    """
    # 创建 TCP 连接，不发送任何数据
    _r, w = await asyncio.open_connection(config.host, config.port)
    # 立即关闭连接
    w.close()
    await w.wait_closed()


# 跨平台：检查指定 PID 的进程是否存在
def _pid_alive(pid: int) -> bool:
    """
    判断给定 PID 的进程是否仍然存活
    
    跨平台实现：
    - Windows：使用 tasklist 命令查询进程
    - Unix：使用 os.kill(pid, 0) 发送空信号（不终止进程，只检查存在性）
    
    参数：
        pid: 进程标识符
    
    返回：
        bool: 进程存在返回 True，否则返回 False
    """
    if IS_WINDOWS:
        # Windows：使用 tasklist 命令过滤指定 PID
        # /FI 是 filter 参数，PID eq {pid} 表示只显示 PID 等于指定值的进程
        # /NH 表示不显示表头（No Header）
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,  # 捕获 stdout 和 stderr
                text=True,            # 返回文本而非字节
                timeout=5,            # 5 秒超时
            )
            # 如果 PID 在输出中，说明进程存在
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        # Unix：os.kill(pid, 0) 发送信号 0，不终止进程，只做存在性检查
        # 如果进程不存在，会抛出 ProcessLookupError
        # 如果没有权限，会抛出 PermissionError
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


# 读取 PID 文件并确认进程存活，进程已消失则删除文件并返回 None
def _running_pid() -> int | None:
    """
    获取当前运行中的 daemon 进程 PID
    
    工作流程：
    1. 检查 PID 文件是否存在
    2. 读取 PID 文件内容
    3. 验证进程是否仍然存活
    4. 如果进程不存在，删除脏 PID 文件
    
    返回：
        int | None: 进程 PID，如果未运行或文件不存在返回 None
    """
    # PID 文件不存在，说明 daemon 未运行
    if not _PID_FILE.exists():
        return None
    
    try:
        # 读取 PID 文件内容并转换为整数
        pid = int(_PID_FILE.read_text().strip())
        
        # 检查进程是否存活
        if _pid_alive(pid):
            return pid
        
        # 进程已消失，但 PID 文件还在（脏文件），删除它
        _PID_FILE.unlink(missing_ok=True)
        return None
        
    except (ValueError, PermissionError):
        # 文件内容不是有效整数，或者没有权限读取
        # 删除脏 PID 文件
        _PID_FILE.unlink(missing_ok=True)
        return None


# 跨平台：终止指定 PID 的进程
def _kill_pid(pid: int) -> None:
    """
    终止指定 PID 的进程
    
    跨平台实现：
    - Windows：使用 taskkill /F /PID 强制终止进程
    - Unix：发送 SIGTERM 信号，优雅终止进程
    
    参数：
        pid: 要终止的进程 PID
    """
    if IS_WINDOWS:
        # Windows：taskkill 命令终止进程
        # /F 表示强制终止（Force）
        # /PID 指定要终止的进程 PID
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=5,
        )
    else:
        # Unix：发送 SIGTERM 信号
        # SIGTERM 是终止信号，进程可以捕获并进行清理工作
        os.kill(pid, signal.SIGTERM)


# 打印 daemon 当前状态（running / not running）
def cmd_core_status(config: IwanConfig) -> None:
    """
    显示 daemon 的运行状态
    
    使用方式：iwan core status
    
    参数：
        config: IwanConfig 配置对象
    
    输出：
        running (host:port) 或 not running
    """
    try:
        # 尝试连接 daemon
        asyncio.run(_ping_check(config))
        # 连接成功，打印运行状态
        print(f"running  ({config.host}:{config.port})")
    except (ConnectionRefusedError, OSError):
        # 连接失败，打印未运行状态
        print("not running")


# 在后台启动 daemon，若已在运行则提示并退出
def cmd_core_start(config: IwanConfig) -> None:
    """
    在后台启动 daemon 进程
    
    使用方式：iwan core start
    
    工作流程：
    1. 检查 daemon 是否已运行
    2. 如果未运行，创建后台进程
    3. 将 PID 写入 PID 文件
    4. 打印启动信息
    
    参数：
        config: IwanConfig 配置对象
    """
    try:
        # 先检查 daemon 是否已运行
        asyncio.run(_ping_check(config))
        print(f"already running  ({config.host}:{config.port})")
        return
    except (ConnectionRefusedError, OSError):
        # 未运行，继续启动流程
        pass

    # 后台进程参数
    popen_kwargs: dict = dict(
        stdout=subprocess.DEVNULL,  # 标准输出重定向到空设备（丢弃）
        stderr=subprocess.DEVNULL,  # 标准错误重定向到空设备（丢弃）
    )
    
    if IS_WINDOWS:
        # Windows：使用 CREATE_NEW_PROCESS_GROUP 创建独立进程组
        # 这样进程不会继承父进程的控制台，关闭控制台不会终止 daemon
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Unix：使用 start_new_session 创建新会话
        # 这样 daemon 进程与当前终端完全脱离，成为独立的会话领导者
        popen_kwargs["start_new_session"] = True

    # 创建子进程，运行 iwan_claude.core 模块
    # sys.executable 是当前 Python 解释器的路径
    # -m 参数表示运行模块
    proc = subprocess.Popen(
        [sys.executable, "-m", "iwan_claude.core"],
        **popen_kwargs,
    )
    
    # 确保 PID 文件的父目录存在
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 将 PID 写入文件
    _PID_FILE.write_text(str(proc.pid))
    
    # 打印启动信息
    print(f"started  pid={proc.pid}  ({config.host}:{config.port})")


# 终止 daemon 进程，若未运行则提示
def cmd_core_stop(config: IwanConfig) -> None:
    """
    停止 daemon 进程
    
    使用方式：iwan core stop
    
    工作流程：
    1. 读取 PID 文件获取进程 PID
    2. 如果未运行，打印提示并返回
    3. 终止进程
    4. 删除 PID 文件
    5. 打印停止信息
    
    参数：
        config: IwanConfig 配置对象
    """
    # 获取运行中的进程 PID
    pid = _running_pid()
    
    if pid is None:
        # 未运行，打印提示
        print("not running")
        return
    
    # 终止进程
    _kill_pid(pid)
    # 删除 PID 文件
    _PID_FILE.unlink(missing_ok=True)
    # 打印停止信息
    print(f"stopped  pid={pid}")
