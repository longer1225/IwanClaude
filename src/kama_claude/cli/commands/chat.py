# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：用于异步编程
import asyncio
# 导入 sys：用于系统退出
import sys
# 导入 Any：类型提示，表示任意类型
from typing import Any

# 导入 KamaConfig：配置类
from kama_claude.core.config import KamaConfig
# 导入 SocketClient 和 IpcError：IPC 客户端和错误类型
from kama_claude.core.transport.socket_client import IpcError, SocketClient


# ChatPrinter 类：负责在终端中打印 chat 模式的流式输出
# 什么是流式输出？就是 LLM 逐 token 返回时，实时显示在终端上
class ChatPrinter:
    # 初始化：_inline 标记当前是否在 LLM 流式输出的同一行（尚未换行）
    def __init__(self) -> None:
        self._inline = False

    # 确保换行：如果当前正在 LLM 流式输出（_inline=True），先打印一个换行符
    # 为什么需要？因为工具调用、状态提示等需要在新行显示
    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # 事件处理：根据事件类型打印不同内容
    async def handle(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")
        # llm.token：LLM 流式输出的单个 token，直接追加到当前行
        if t == "llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True
        # tool.call_started：工具调用开始，打印工具名称
        elif t == "tool.call_started":
            self._ensure_newline()
            print(f"[tool] {event.get('tool_name', '')}")
        # session.waiting_for_input：session 等待用户输入，打印提示
        elif t == "session.waiting_for_input":
            self._ensure_newline()
            print("[waiting for input]")
        # session.closed：session 关闭，打印提示
        elif t == "session.closed":
            self._ensure_newline()
            print("session closed.")


# 在 asyncio 的线程池中读取 stdin（标准输入）
# 为什么用线程池？因为 input() 是同步阻塞操作，直接在事件循环中调用会卡住
# run_in_executor 把同步操作放到线程池执行，不阻塞事件循环
async def _readline(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# chat 模式的异步核心逻辑
# 解决的问题：之前的 run 命令是一次性的（一个 goal 对应一个 run）
# chat 模式支持多轮对话：用户可以连续发送多条消息，共享同一会话历史
async def _chat_async(config: KamaConfig) -> int:
    # 创建 SocketClient：连接到 Core daemon
    client = SocketClient(config.host, config.port)
    try:
        # 连接到 daemon
        await client.connect()
    except (ConnectionRefusedError, OSError):
        # 连接失败：daemon 可能没启动
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1

    # 创建 ChatPrinter：处理流式输出
    printer = ChatPrinter()
    # 注册事件回调：收到事件时调用 printer.handle
    client.on_event(printer.handle)
    # 启动事件循环任务：持续监听 daemon 发来的事件
    loop_task = asyncio.create_task(client.run_event_loop())

    try:
        # 订阅事件：接收 session、run、tool、llm.token 相关事件
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["session.*", "run.*", "tool.*", "llm.token"],
                "scope": "global",
            },
        )
        # 创建 chat session：发送 session.create 命令
        # mode="chat" 表示这是一个持续的聊天会话（不是一次性任务）
        created = await client.send_command("session.create", {"mode": "chat"})
        session_id = str(created["session_id"])
        print(f"[session: {session_id}]")

        # 主循环：持续读取用户输入并发送
        while True:
            try:
                # 读取用户输入（使用线程池避免阻塞）
                line = await _readline("> ")
            except (EOFError, KeyboardInterrupt):
                # 用户按 Ctrl+D（EOF）或 Ctrl+C，退出循环
                break
            content = line.strip()
            if not content:
                # 空输入跳过
                continue
            # 发送消息到 session：调用 session.send_message 命令
            # 所有消息都发送到同一个 session，共享对话历史
            await client.send_command(
                "session.send_message",
                {"session_id": session_id, "content": content},
            )

        # 循环结束，关闭 session
        await client.send_command("session.close", {"session_id": session_id})
    except IpcError as e:
        # IPC 通信错误
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        # 清理资源：取消事件循环任务、关闭连接
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await client.close()
    return 0


# chat 命令的入口函数
def cmd_chat(config: KamaConfig) -> None:
    try:
        exit_code = asyncio.run(_chat_async(config))
    except KeyboardInterrupt:
        # 用户按 Ctrl+C，退出码 130（标准中断退出码）
        sys.exit(130)
    sys.exit(exit_code)
