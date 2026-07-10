# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：Python 的异步 I/O 库，用于网络通信和并发编程
# 什么是异步 I/O？简单说就是"发起操作后不等结果，继续做其他事，结果来了再处理"
import asyncio
# 导入 json：用于序列化/反序列化网络传输的数据
import json
# 导入 sys：用于访问系统级功能（如 stderr、exit）
import sys
# 导入 time：用于计算运行时间
import time
# 导入 Any：类型注解，表示任意类型
from typing import Any

# 导入配置类：包含 Core daemon 的主机和端口信息
from kama_claude.core.config import KamaConfig
# 导入 SocketClient：专门用于与 Core daemon 进行网络通信的客户端
# IpcError：IPC 通信过程中可能发生的错误
from kama_claude.core.transport.socket_client import IpcError, SocketClient


# StdoutPrinter 类：负责将从 Core daemon 收到的事件格式化后打印到终端
# 它是一个"事件消费者"，订阅事件流并输出到 stdout
class StdoutPrinter:
    # 初始化方法：设置状态变量
    def __init__(self) -> None:
        # _inline：标记当前是否正在行内输出（LLM Token 流式输出时为 True）
        # 什么是行内输出？就是使用 print(text, end="") 不换行输出，像打字机一样
        self._inline = False
        # _run_start：记录 run 开始的时间，用于计算总耗时
        self._run_start: float = 0.0

    # 如果当前正在行内输出（比如 LLM 正在逐字输出），先补一个换行符
    # 为什么需要这个？因为 LLM Token 是不换行输出的，如果下一个事件不是 Token，
    # 需要先换行再输出，否则格式会乱
    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # 事件处理方法：根据事件类型分发并格式化输出
    # 这是一个典型的"事件处理器"模式，通过事件的 type 字段判断如何处理
    async def handle(self, event: dict[str, Any]) -> None:
        # 获取事件类型
        t = event.get("type", "")

        # ========== run.started：运行开始 ==========
        if t == "run.started":
            # 记录开始时间
            self._run_start = time.monotonic()
            # 打印运行 ID
            print(f"[run] {event.get('run_id', '')}")

        # ========== step.started：步骤开始 ==========
        elif t == "step.started":
            # 确保换行（如果之前在输出 Token）
            self._ensure_newline()
            # 打印步骤信息
            print(f"[step {event.get('step')}] planning...")

        # ========== llm.token：LLM 输出的单个 Token ==========
        elif t == "llm.token":
            # 不换行输出 Token，flush=True 确保立即显示（否则可能缓存）
            print(event.get("token", ""), end="", flush=True)
            # 标记正在行内输出
            self._inline = True

        # ========== tool.call_started：工具调用开始 ==========
        elif t == "tool.call_started":
            # 确保换行
            self._ensure_newline()
            # 将工具参数转为 JSON 字符串，ensure_ascii=False 支持中文
            params_str = json.dumps(event.get("params", {}), ensure_ascii=False)
            # 打印工具名称和参数
            print(f"[tool] {event.get('tool_name', '')} {params_str}")

        # ========== tool.call_finished：工具调用完成 ==========
        elif t == "tool.call_finished":
            # 打印工具名称、成功标记和耗时
            print(f"[tool] {event.get('tool_name', '')} ✓  {event.get('elapsed_ms')}ms")

        # ========== tool.call_failed：工具调用失败 ==========
        elif t == "tool.call_failed":
            # 打印到 stderr（错误输出流），方便区分正常输出和错误
            print(
                f"[tool] {event.get('tool_name', '')} ✗  {event.get('error_message', '')}",
                file=sys.stderr,
            )

        # ========== step.finished：步骤完成 ==========
        elif t == "step.finished":
            self._ensure_newline()
            print(f"[step {event.get('step')}] done")

        # ========== run.finished：运行完成 ==========
        elif t == "run.finished":
            self._ensure_newline()
            # 计算总耗时
            elapsed = time.monotonic() - self._run_start
            # 打印最终状态、步骤数和耗时
            print(f"[run] {event.get('status', '')}  {event.get('steps')} steps  {elapsed:.1f}s")


# 异步核心函数：负责与 Core daemon 建立网络连接、订阅事件、发送命令、等待完成
# 这是 s2 架构的关键：CLI 不再直接创建 AgentRunner，而是通过网络请求 Core daemon
async def _run_async(goal: str, config: KamaConfig) -> int:
    # ====================== 第一步：创建 Socket 客户端 ======================
    # SocketClient 是一个封装了 TCP 连接的客户端，负责与 Core daemon 通信
    # config.host 和 config.port 是 Core daemon 监听的地址和端口
    client = SocketClient(config.host, config.port)
    
    # ====================== 第二步：建立网络连接 ======================
    # 尝试连接到 Core daemon
    # 什么是 TCP 连接？就像打电话，需要先拨通（三次握手），才能通话
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        # 如果连接被拒绝，说明 Core daemon 没有运行
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1  # 返回错误码 1

    # ====================== 第三步：准备事件处理 ======================
    # 创建 StdoutPrinter 实例，用于打印事件
    printer = StdoutPrinter()
    # 创建 asyncio.Event：一个异步事件标志，用于等待 run.finished 事件
    # 什么是 asyncio.Event？就像一个信号灯，set() 点亮，wait() 等待点亮
    finished = asyncio.Event()
    # 退出码：0 表示成功，1 表示失败
    exit_code = 0

    # ====================== 第四步：定义事件回调函数 ======================
    # on_event 是一个回调函数，当收到 Core daemon 的事件时被调用
    # 它是"事件驱动"的核心：收到什么事件，就做什么处理
    async def on_event(event: dict[str, Any]) -> None:
        # nonlocal：引用外部函数的变量（exit_code）
        nonlocal exit_code
        # 将事件交给 StdoutPrinter 处理（打印到终端）
        await printer.handle(event)
        # 如果收到 run.finished 事件，说明运行结束
        if event.get("type") == "run.finished":
            # 如果状态不是 success，设置退出码为 1
            if event.get("status") != "success":
                exit_code = 1
            # 点亮信号灯，告诉等待的代码"运行结束了"
            finished.set()

    # ====================== 第五步：注册事件回调并启动事件循环 ======================
    # 将 on_event 注册到客户端，客户端收到事件时会调用这个函数
    client.on_event(on_event)
    # 创建一个后台任务：运行客户端的事件循环
    # 什么是 asyncio.create_task？就是把一个协程放到后台运行，不阻塞当前代码
    # 事件循环的作用：持续监听网络连接，接收并处理 Core daemon 发来的事件
    loop_task = asyncio.create_task(client.run_event_loop())

    # ====================== 第六步：发送命令给 Core daemon ======================
    try:
        # 命令 1：订阅事件
        # 告诉 Core daemon："我要订阅这些事件类型"
        # topics：订阅的事件模式，支持通配符（run.* 表示所有 run 相关事件）
        # scope: "global" 表示全局订阅，接收所有运行的事件
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["run.*", "step.*", "tool.*", "llm.token", "llm.usage"],
                "scope": "global",
            },
        )
        
        # 命令 2：启动 Agent 运行
        # 告诉 Core daemon："帮我运行一个 Agent，目标是 xxx"
        # 这是真正触发 Agent 执行的命令
        await client.send_command("agent.run", {"goal": goal})
    
    # 如果发送命令过程中出错（比如网络断开）
    except IpcError as e:
        print(f"error: {e}", file=sys.stderr)
        # 取消事件循环任务
        loop_task.cancel()
        # 关闭连接
        await client.close()
        return 1

    # ====================== 第七步：等待运行结束 ======================
    # 等待 finished 事件被点亮（即收到 run.finished 事件）
    # 这是一个阻塞点，但因为是 async，不会阻塞整个事件循环
    await finished.wait()

    # ====================== 第八步：清理资源 ======================
    # 取消事件循环任务
    loop_task.cancel()
    try:
        # 等待任务被取消（捕获 CancelledError）
        await loop_task
    except asyncio.CancelledError:
        pass

    # 关闭网络连接
    await client.close()
    
    # 返回退出码
    return exit_code


# CLI 命令入口：kama run --goal "..."
# 这是一个同步函数，通过 asyncio.run() 桥接到异步的 _run_async
def cmd_run(goal: str, config: KamaConfig) -> None:
    try:
        # asyncio.run()：启动一个新的事件循环，运行 _run_async
        # 什么是事件循环？就像一个调度器，负责管理所有异步任务的执行
        exit_code = asyncio.run(_run_async(goal, config))
    except KeyboardInterrupt:
        # 用户按 Ctrl+C 退出，返回 130（标准的中断退出码）
        sys.exit(130)
    # 根据退出码退出程序
    sys.exit(exit_code)
