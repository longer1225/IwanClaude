"""
Chat 命令模块 - 命令行交互式聊天客户端

【学习要点】
1. 异步事件驱动：使用 asyncio 和事件订阅模式实现实时消息推送
2. Socket 客户端：通过 TCP Socket 与核心服务通信
3. 流式输出：处理 LLM 的 token 级流式响应，实现打字机效果
4. 权限审批：在聊天过程中处理工具调用的权限请求
5. Checkpoint 管理：支持列出和恢复 LangGraph 的检查点

【核心流程】
1. 连接核心服务
2. 订阅事件（session、run、tool、llm.token、permission）
3. 创建 chat session
4. 循环读取用户输入并发送消息
5. 实时处理服务端推送的事件
6. 处理权限审批请求
7. 支持 /checkpoint 命令管理检查点
"""
from __future__ import annotations

# asyncio：异步 I/O 框架
# sys：系统相关操作，如退出程序、输出到 stderr
# typing：类型提示
import asyncio
import sys
from typing import Any

# 导入配置和客户端
from iwan_claude.core.config import IwanConfig
from iwan_claude.core.transport.socket_client import (
    IpcError,       # IPC 通信错误
    SocketClient,   # Socket 客户端
)

# 权限决策映射：用户输入的简短命令 -> 权限决策类型
# y: allow_once（允许一次）
# a: always_allow（始终允许）
# n: deny_once（拒绝一次）
# d: always_deny（始终拒绝）
_DECISION_MAP: dict[str, str] = {
    "y": "allow_once",
    "a": "always_allow",
    "n": "deny_once",
    "d": "always_deny",
}


class ChatPrinter:
    """
    聊天输出处理器 - 负责处理和打印来自服务端的各种事件
    
    核心功能：
    1. 处理 LLM 的流式 token 输出（打字机效果）
    2. 打印工具调用信息
    3. 显示权限审批请求
    4. 管理输出格式（换行、缩进等）
    """
    
    # 初始化 chat 模式的流式输出状态和待审批权限请求
    def __init__(self) -> None:
        """初始化聊天输出处理器"""
        # _inline：标记当前输出是否在同一行（用于流式输出的换行控制）
        # 当 LLM 正在输出 token 时，_inline 为 True，表示还没有换行
        self._inline = False
        
        # pending_permission_id：待审批的权限请求 ID
        # 如果有值，说明正在等待用户审批工具调用权限
        self.pending_permission_id: str | None = None

    # 若当前 LLM token 尚未换行，则补一个换行
    def _ensure_newline(self) -> None:
        """确保输出换行，避免新内容与上一行混在一起"""
        if self._inline:
            print()
            self._inline = False

    # 按事件类型打印 chat 输出、等待提示和权限审批请求
    async def handle(self, event: dict[str, Any]) -> None:
        """
        处理来自服务端的事件
        
        参数：
            event: 事件字典，包含 type 字段标识事件类型
            
        支持的事件类型：
            llm.token: LLM 的流式输出 token
            tool.call_started: 工具调用开始
            permission.requested: 权限请求
            session.waiting_for_input: 会话等待用户输入
            session.closed: 会话关闭
        """
        # 获取事件类型
        t = event.get("type", "")
        
        # ===== LLM 流式输出 =====
        # 当收到 llm.token 事件时，直接打印 token，不换行
        # 使用 flush=True 确保立即输出，实现打字机效果
        if t == "llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True  # 标记当前在同一行
        
        # ===== 工具调用开始 =====
        # 打印工具名称，用于告知用户正在执行哪个工具
        elif t == "tool.call_started":
            self._ensure_newline()  # 先换行，确保工具信息单独一行
            print(f"[tool] {event.get('tool_name', '')}")
        
        # ===== 权限请求 =====
        # 当 Agent 要调用工具时，需要用户审批
        elif t == "permission.requested":
            self._ensure_newline()
            tool_name = str(event.get("tool_name", ""))       # 工具名称
            param_preview = str(event.get("param_preview", ""))  # 参数预览
            tool_use_id = str(event.get("tool_use_id", ""))   # 工具调用 ID
            print(f"[permission] {tool_name}  {param_preview}")
            print("  y=allow once  a=always allow  n=deny once  d=always deny")
            self.pending_permission_id = tool_use_id  # 记录待审批的权限 ID
        
        # ===== 会话等待输入 =====
        # LLM 已经完成响应，等待用户输入
        elif t == "session.waiting_for_input":
            self._ensure_newline()
            self.pending_permission_id = None  # 清除待审批的权限请求
            print("[waiting for input]")
        
        # ===== 会话关闭 =====
        elif t == "session.closed":
            self._ensure_newline()
            print("session closed.")


# 在线程池中读取 stdin，避免阻塞 socket event loop
async def _readline(prompt: str) -> str:
    """
    异步读取用户输入
    
    为什么要用线程池？
    - input() 是同步阻塞函数，如果直接在事件循环中调用，会阻塞整个事件循环
    - 使用 run_in_executor() 将 input() 放在线程池中执行，不会阻塞事件循环
    - 这样可以同时处理网络事件（如 LLM 流式输出）和用户输入
    
    参数：
        prompt: 输入提示字符（如 "> "）
    
    返回：
        用户输入的字符串
    """
    # 获取当前运行的事件循环
    loop = asyncio.get_running_loop()
    # 在默认线程池中执行 input() 函数
    return await loop.run_in_executor(None, input, prompt)


# 异步核心：创建 chat session，循环读取用户输入并发送到 daemon；权限请求时优先处理审批
async def _chat_async(config: IwanConfig) -> int:
    """
    异步聊天核心逻辑
    
    工作流程：
    1. 创建 Socket 客户端并连接核心服务
    2. 注册事件处理器（ChatPrinter）
    3. 订阅所需的事件类型
    4. 创建 chat session
    5. 循环读取用户输入并处理
    6. 处理权限审批请求
    7. 支持 /checkpoint 命令
    
    参数：
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
    printer = ChatPrinter()
    # 注册事件处理器：当收到事件时，调用 printer.handle 处理
    client.on_event(printer.handle)
    # 启动事件循环任务：持续监听服务端发送的事件
    loop_task = asyncio.create_task(client.run_event_loop())

    try:
        # ===== 订阅事件 =====
        # 告诉服务端，我们关心哪些类型的事件
        # topics 是事件模式列表，支持通配符 *
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["session.*", "run.*", "tool.*", "llm.token", "permission.*"],
                "scope": "global",  # 全局订阅，接收所有会话的事件
            },
        )
        
        # ===== 创建聊天会话 =====
        # 请求服务端创建一个 chat 模式的会话
        created = await client.send_command("session.create", {"mode": "chat"})
        session_id = str(created["session_id"])
        print(f"[session: {session_id}]")

        # ===== 主循环：读取用户输入 =====
        while True:
            try:
                # 异步读取用户输入，提示符为 "> "
                line = await _readline("> ")
            except (EOFError, KeyboardInterrupt):
                # 用户按 Ctrl+D（EOF）或 Ctrl+C，退出循环
                break
            
            # 去除首尾空白
            content = line.strip()
            # 空输入跳过
            if not content:
                continue

            # ===== 权限审批优先处理 =====
            # 如果有待审批的权限请求，用户输入被解释为权限决策
            if printer.pending_permission_id:
                # 根据用户输入查找对应的决策类型
                decision = _DECISION_MAP.get(content.lower())
                if decision is None:
                    # 无效输入，提示用户正确的选项
                    print("  enter y (allow once), a (always allow), "
                          "n (deny once), d (always deny)")
                    continue
                
                # 获取待审批的工具调用 ID
                tool_use_id = printer.pending_permission_id
                # 清除待审批状态
                printer.pending_permission_id = None
                
                # 发送权限决策到服务端
                await client.send_command(
                    "permission.respond",
                    {"tool_use_id": tool_use_id, "decision": decision},
                )
                continue

            # ===== Checkpoint 命令处理 =====
            # 支持 /checkpoint list 和 /checkpoint restore <index_or_id>
            if content.startswith("/checkpoint"):
                parts = content.split()
                
                # 列出所有 checkpoints
                if len(parts) >= 2 and parts[1] == "list":
                    result = await client.send_command(
                        "session.checkpoint.list",
                        {"session_id": session_id},
                    )
                    checkpoints = result.get("checkpoints", [])
                    thread_id = result.get("thread_id", "")
                    print(f"\nCheckpoints for session {thread_id}:")
                    
                    if not checkpoints:
                        print("  (no checkpoints found - make sure engine=langgraph and checkpoint_backend!=none)")
                    else:
                        # 倒序显示，最新的在前面
                        for i, cp in enumerate(reversed(checkpoints)):
                            ts = cp.get("timestamp", "")
                            summary = cp.get("summary", "")
                            node = cp.get("node", "")
                            print(f"  [{i}] step={cp['step']}  {ts}  {summary}")
                            print(f"     id: {cp['checkpoint_id']}")
                    print()
                    continue
                
                # 恢复到指定 checkpoint
                elif len(parts) >= 3 and parts[1] == "restore":
                    idx_or_id = parts[2]
                    # 先获取 checkpoint 列表
                    list_result = await client.send_command(
                        "session.checkpoint.list",
                        {"session_id": session_id},
                    )
                    checkpoints = list_result.get("checkpoints", [])
                    
                    if not checkpoints:
                        print("  error: no checkpoints available")
                        continue

                    # 判断用户输入的是索引还是 ID
                    if idx_or_id.isdigit():
                        # 索引模式：从后往前数
                        idx = int(idx_or_id)
                        if idx < 0 or idx >= len(checkpoints):
                            print(f"  error: index {idx} out of range (0-{len(checkpoints)-1})")
                            continue
                        cp = checkpoints[len(checkpoints) - 1 - idx]
                        checkpoint_id = cp["checkpoint_id"]
                    else:
                        # ID 模式：直接使用用户输入的 ID
                        checkpoint_id = idx_or_id

                    # 请求恢复 checkpoint
                    result = await client.send_command(
                        "session.checkpoint.restore",
                        {"session_id": session_id, "checkpoint_id": checkpoint_id},
                    )
                    
                    if result.get("success"):
                        print(f"\n  restored to step {result['step']}: {result['message']}")
                    else:
                        print(f"\n  restore failed: {result['message']}")
                    print()
                    continue
                
                # 命令格式错误
                else:
                    print("  usage: /checkpoint list | /checkpoint restore <index_or_id>")
                    continue

            # ===== 发送聊天消息 =====
            # 如果不是特殊命令，将用户输入作为聊天消息发送
            await client.send_command(
                "session.send_message",
                {"session_id": session_id, "content": content},
            )

        # 退出循环后，关闭会话
        await client.send_command("session.close", {"session_id": session_id})
    
    except IpcError as e:
        # IPC 通信错误
        print(f"error: {e}", file=sys.stderr)
        return 1
    
    finally:
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
    
    return 0


# 执行 iwan chat 命令
def cmd_chat(config: IwanConfig) -> None:
    """
    Chat 命令的同步入口
    
    使用方式：iwan chat
    
    参数：
        config: IwanConfig 配置对象
    
    退出码：
        0: 正常退出
        1: 连接失败或通信错误
        130: 用户按 Ctrl+C 中断
    """
    try:
        # 运行异步聊天函数
        exit_code = asyncio.run(_chat_async(config))
    except KeyboardInterrupt:
        # 用户按 Ctrl+C，退出码 130 表示被中断
        sys.exit(130)
    # 根据异步函数的返回值退出
    sys.exit(exit_code)
