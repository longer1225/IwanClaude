"""
Run 命令模块 - 非交互式执行 Agent 任务

【学习要点】
1. 异步事件驱动：使用 asyncio.Event 等待任务完成
2. 事件订阅：订阅 run.*、step.*、tool.*、llm.token 等事件
3. 流式输出：实时显示 Agent 的执行进度
4. 退出码处理：根据任务执行结果返回相应的退出码

【核心流程】
1. 连接核心服务
2. 订阅执行相关事件
3. 发送 agent.run 命令启动任务
4. 实时处理事件并显示进度
5. 等待 run.finished 事件
6. 根据执行结果返回退出码

【事件类型】
- run.started: 任务开始
- step.started: 步骤开始
- llm.token: LLM 流式输出
- tool.call_started: 工具调用开始
- tool.call_finished: 工具调用完成
- tool.call_failed: 工具调用失败
- step.finished: 步骤完成
- run.finished: 任务完成
"""
from __future__ import annotations

# asyncio：异步 I/O 框架
# json：JSON 序列化/反序列化
# sys：系统相关操作
# time：时间相关功能
# typing：类型提示
import asyncio
import json
import sys
import time
from typing import Any

# 导入配置和客户端
from iwan_claude.core.config import IwanConfig
from iwan_claude.core.transport.socket_client import (
    IpcError,       # IPC 通信错误
    SocketClient,   # Socket 客户端
)


class StdoutPrinter:
    """
    标准输出打印机 - 负责将执行事件格式化为可读的终端输出
    
    核心功能：
    1. 处理 LLM 的流式 token 输出（打字机效果）
    2. 显示任务和步骤的开始/完成状态
    3. 显示工具调用的执行结果
    4. 计算并显示任务总耗时
    """
    
    # 接收 dict 格式的事件并将运行进度格式化打印到终端
    def __init__(self) -> None:
        """初始化标准输出打印机"""
        # _inline：标记当前输出是否在同一行（用于流式输出的换行控制）
        self._inline = False
        # _run_start：任务开始时间（用于计算总耗时）
        self._run_start: float = 0.0

    # 若当前行有未换行的 token，补一个换行符
    def _ensure_newline(self) -> None:
        """确保输出换行，避免新内容与上一行混在一起"""
        if self._inline:
            print()
            self._inline = False

    # 根据事件 type 字段分发并格式化打印到 stdout/stderr
    async def handle(self, event: dict[str, Any]) -> None:
        """
        处理执行事件并打印到终端
        
        参数：
            event: 事件字典，包含 type 字段标识事件类型
        """
        t = event.get("type", "")

        # ===== 任务开始 =====
        if t == "run.started":
            # 记录任务开始时间
            self._run_start = time.monotonic()
            print(f"[run] {event.get('run_id', '')}")

        # ===== 步骤开始 =====
        elif t == "step.started":
            self._ensure_newline()
            print(f"[step {event.get('step')}] planning...")

        # ===== LLM 流式输出 =====
        elif t == "llm.token":
            # 不换行打印 token，实现打字机效果
            print(event.get("token", ""), end="", flush=True)
            self._inline = True

        # ===== 工具调用开始 =====
        elif t == "tool.call_started":
            self._ensure_newline()
            # 将工具参数转换为 JSON 字符串
            params_str = json.dumps(event.get("params", {}), ensure_ascii=False)
            print(f"[tool] {event.get('tool_name', '')} {params_str}")

        # ===== 工具调用完成 =====
        elif t == "tool.call_finished":
            print(f"[tool] {event.get('tool_name', '')} ✓  {event.get('elapsed_ms')}ms")

        # ===== 工具调用失败 =====
        elif t == "tool.call_failed":
            # 错误信息打印到 stderr
            print(
                f"[tool] {event.get('tool_name', '')} ✗  {event.get('error_message', '')}",
                file=sys.stderr,
            )

        # ===== 步骤完成 =====
        elif t == "step.finished":
            self._ensure_newline()
            print(f"[step {event.get('step')}] done")

        # ===== 任务完成 =====
        elif t == "run.finished":
            self._ensure_newline()
            # 计算任务总耗时
            elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.get('status', '')}  {event.get('steps')} steps  {elapsed:.1f}s")


# 异步核心：连接 daemon，订阅事件，触发 run，等待 run.finished
async def _run_async(goal: str, config: IwanConfig) -> int:
    """
    异步执行 Agent 任务
    
    工作流程：
    1. 创建 Socket 客户端并连接核心服务
    2. 创建事件处理器（StdoutPrinter）
    3. 创建完成事件（asyncio.Event）
    4. 注册事件处理回调
    5. 订阅执行相关事件
    6. 发送 agent.run 命令启动任务
    7. 等待 run.finished 事件
    8. 返回退出码
    
    参数：
        goal: 任务目标（用户输入的指令）
        config: IwanConfig 配置对象
    
    返回：
        退出码：0 表示成功，1 表示失败
    """
    # 创建 Socket 客户端
    client = SocketClient(config.host, config.port)
    
    try:
        # 连接核心服务
        await client.connect()
    except (ConnectionRefusedError, OSError):
        # 连接失败，打印错误信息
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1

    # 创建输出处理器
    printer = StdoutPrinter()
    
    # 创建完成事件：用于等待任务结束
    # asyncio.Event() 是一个简单的事件标志，set() 后 wait() 会返回
    finished = asyncio.Event()
    
    # 退出码：0 表示成功，1 表示失败
    exit_code = 0

    # 事件处理回调函数
    async def on_event(event: dict[str, Any]) -> None:
        """处理来自服务端的事件"""
        # nonlocal 关键字：引用外层函数的 exit_code 变量
        nonlocal exit_code
        
        # 打印事件信息
        await printer.handle(event)
        
        # 如果收到任务完成事件
        if event.get("type") == "run.finished":
            # 如果状态不是 success，设置退出码为 1
            if event.get("status") != "success":
                exit_code = 1
            # 触发完成事件，唤醒等待的代码
            finished.set()

    # 注册事件处理器
    client.on_event(on_event)
    
    # 启动事件循环任务：持续监听服务端发送的事件
    loop_task = asyncio.create_task(client.run_event_loop())

    try:
        # ===== 订阅事件 =====
        # 订阅与执行相关的事件类型
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["run.*", "step.*", "tool.*", "llm.token", "llm.usage"],
                "scope": "global",
            },
        )
        
        # ===== 启动任务 =====
        # 发送 agent.run 命令，传入任务目标
        await client.send_command("agent.run", {"goal": goal})
        
    except IpcError as e:
        # IPC 通信错误
        print(f"error: {e}", file=sys.stderr)
        # 取消事件循环任务
        loop_task.cancel()
        # 关闭客户端连接
        await client.close()
        return 1

    # 等待任务完成事件
    await finished.wait()

    # 清理资源
    # 取消事件循环任务
    loop_task.cancel()
    try:
        # 等待任务取消完成
        await loop_task
    except asyncio.CancelledError:
        pass

    # 关闭客户端连接
    await client.close()
    
    # 返回退出码
    return exit_code


# 执行 iwan run --goal "..." 命令
def cmd_run(goal: str, config: IwanConfig) -> None:
    """
    Run 命令的同步入口
    
    使用方式：iwan run --goal "your task here"
    
    参数：
        goal: 任务目标（用户输入的指令）
        config: IwanConfig 配置对象
    
    退出码：
        0: 任务成功完成
        1: 连接失败、通信错误或任务执行失败
        130: 用户按 Ctrl+C 中断
    """
    try:
        # 运行异步任务
        exit_code = asyncio.run(_run_async(goal, config))
    except KeyboardInterrupt:
        # 用户按 Ctrl+C，退出码 130 表示被中断
        sys.exit(130)
    # 根据异步函数的返回值退出
    sys.exit(exit_code)
