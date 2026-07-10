# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：Python 的异步 I/O 库，用于网络通信
import asyncio
# 导入 json：用于序列化/反序列化数据
import json
# 导入 Any：类型注解，表示任意类型
from typing import Any

# 导入 textual 库：用于创建终端图形界面（TUI）
# App：TUI 应用的基类
# ComposeResult：用于构建 UI 的返回类型
from textual.app import App, ComposeResult
# Binding：键盘绑定装饰器，用于定义快捷键
from textual.binding import Binding
# Label：文本标签组件
# RichLog：可滚动的日志组件，支持富文本和语法高亮
from textual.widgets import Label, RichLog

# 导入 SocketClient：TCP 客户端，用于与 Core daemon 通信
# IpcError：IPC 通信错误类型
from kama_claude.core.transport.socket_client import IpcError, SocketClient


# KamaTuiApp 类：KamaClaude 的终端图形界面应用
# 什么是 TUI？就是终端里的图形界面，像一个小应用程序
# 相比 CLI，TUI 有更好的视觉效果和交互体验
class KamaTuiApp(App[None]):
    """KamaClaude 终端 UI：实时显示 daemon 事件流，支持断线自动重连。"""

    # 应用标题（显示在终端顶部）
    TITLE = "KamaClaude TUI"
    # 键盘绑定：按 'q' 键退出应用
    BINDINGS = [Binding("q", "quit", "Quit")]
    # CSS 样式：定义界面布局和外观
    CSS = """
    Screen { layout: vertical; }          /* 屏幕布局：垂直排列 */
    #status {                             /* 状态栏样式 */
        height: 1;                        /* 高度：1 行 */
        background: $primary;             /* 背景色：主题色 */
        color: $text;                     /* 文字色：主题文字色 */
        padding: 0 1;                     /* 左右内边距：1 */
    }
    #log { height: 1fr; }                 /* 日志区域：占满剩余空间 */
    """

    # 初始化方法：保存连接参数和 token 缓冲区
    # 传参：
    #   host - Core daemon 的主机地址
    #   port - Core daemon 的端口
    #   replay_run_id - 可选，要回放的 run ID
    def __init__(self, host: str, port: int, replay_run_id: str | None = None) -> None:
        # 调用父类初始化
        super().__init__()
        # 保存主机地址
        self._host = host
        # 保存端口
        self._port = port
        # 保存要回放的 run ID（可选）
        self._replay_run_id = replay_run_id
        # Token 缓冲区：用于累积 LLM 的流式输出
        # 为什么需要缓冲区？因为 LLM 是逐 token 输出的，
        # 如果每个 token 都单独写入日志，会有很多行，
        # 所以先累积起来，遇到其他事件时一起写入
        self._token_buf = ""

    # 构建 UI：返回组件列表
    # 什么是 ComposeResult？就是一个生成器，yield 出要显示的组件
    def compose(self) -> ComposeResult:
        # 状态栏：显示连接状态
        yield Label("● connecting...", id="status")
        # 日志区域：显示事件流，支持高亮和标记
        yield RichLog(id="log", highlight=True, markup=True)

    # 挂载后启动 socket 连接 worker
    # 什么是 on_mount？就是组件被添加到屏幕上后触发的事件
    # 什么是 worker？就是在后台运行的异步任务，不阻塞 UI
    def on_mount(self) -> None:
        # run_worker：启动一个后台工作线程
        # exclusive=True：同一时间只运行一个同名 worker（防止重复连接）
        # name="socket"：给 worker 命名，方便管理
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")

    # 管理 SocketClient 生命周期：连接、订阅、接收事件、断线重连
    # 这是 TUI 的核心方法，负责与 Core daemon 通信
    async def _socket_loop(self) -> None:
        # 获取日志组件（通过 ID 查询）
        log = self.query_one("#log", RichLog)
        # 获取状态栏组件
        status = self.query_one("#status", Label)

        # 无限循环：持续尝试连接，断开后自动重连
        while True:
            # 创建新的 SocketClient
            client = SocketClient(self._host, self._port)
            try:
                # 尝试连接到 Core daemon
                await client.connect()
            except (ConnectionRefusedError, OSError):
                # 连接失败，更新状态栏，等待 2 秒后重试
                status.update("● not connected — retrying in 2s")
                await asyncio.sleep(2)
                continue

            # 连接成功，更新状态栏
            status.update(f"● connected  {self._host}:{self._port}")
            
            # 启动事件循环任务：持续读取服务器消息
            loop_task = asyncio.create_task(client.run_event_loop())

            # 定义事件回调函数：收到事件时调用
            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event, log)

            # 注册事件回调
            client.on_event(on_event)

            try:
                # 准备订阅参数
                params: dict[str, Any] = {
                    # 订阅的事件类型：运行、步骤、工具、LLM、日志
                    "topics": [
                        "run.*", "step.*", "tool.*",
                        "llm.token", "llm.usage", "log.*",
                    ],
                    # 订阅范围：全局（接收所有事件）
                    "scope": "global",
                }
                # 如果指定了要回放的 run ID，添加到参数中
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                
                # 发送订阅命令
                await client.send_command("event.subscribe", params)
                
                # 等待事件循环结束（通常是连接断开）
                await loop_task
            except IpcError as e:
                # 订阅失败，更新状态栏
                status.update(f"● subscribe error — {e}")
            finally:
                # 清理资源：取消事件循环任务
                if not loop_task.done():
                    loop_task.cancel()
                # 刷新剩余的 token 缓冲区
                self._flush_tokens(log)
                # 关闭连接
                await client.close()

            # 更新状态栏，等待 2 秒后重新连接
            status.update("● disconnected — retrying in 2s")
            await asyncio.sleep(2)

    # 将 llm.token 累积缓冲区写入日志并清空
    def _flush_tokens(self, log: RichLog) -> None:
        # 如果缓冲区有内容，写入日志
        if self._token_buf:
            log.write(self._token_buf)
            # 清空缓冲区
            self._token_buf = ""

    # 根据事件 type 字段格式化并写入 RichLog，llm.token 累积后整体写入
    # 这是事件处理的核心方法，负责将不同类型的事件显示在界面上
    def _handle_event(self, event: dict[str, Any], log: RichLog) -> None:
        # 获取事件类型
        t = event.get("type", "")

        # ====================== llm.token：LLM 流式输出 ======================
        # 如果是 LLM 的 token 事件，累积到缓冲区，不立即写入
        # 为什么？因为 token 是逐字输出的，累积起来再写入更流畅
        if t == "llm.token":
            self._token_buf += event.get("token", "")
            return

        # 如果不是 token 事件，先刷新缓冲区（把之前累积的 token 写入）
        self._flush_tokens(log)

        # ====================== run.started：运行开始 ======================
        if t == "run.started":
            log.write(
                f"[bold blue]▶ run[/bold blue]  {event.get('run_id', '')}  "
                f"{event.get('goal', '')}"
            )
        
        # ====================== step.started：步骤开始 ======================
        elif t == "step.started":
            log.write(f"[bold]  step {event.get('step')}[/bold]  planning...")
        
        # ====================== tool.call_started：工具调用开始 ======================
        elif t == "tool.call_started":
            params_str = json.dumps(event.get("params", {}), ensure_ascii=False)
            log.write(f"[green]  tool[/green]  {event.get('tool_name', '')}  {params_str}")
        
        # ====================== tool.call_finished：工具调用完成 ======================
        elif t == "tool.call_finished":
            log.write(
                f"[green]  tool[/green]  {event.get('tool_name', '')} "
                f"✓  {event.get('elapsed_ms')}ms"
            )
        
        # ====================== tool.call_failed：工具调用失败 ======================
        elif t == "tool.call_failed":
            log.write(
                f"[red]  tool[/red]  {event.get('tool_name', '')} "
                f"✗  {event.get('error_message', '')}"
            )
        
        # ====================== step.finished：步骤完成 ======================
        elif t == "step.finished":
            log.write(f"  step {event.get('step')}  done")
        
        # ====================== run.finished：运行完成 ======================
        elif t == "run.finished":
            s = event.get("status", "")
            # 根据状态选择颜色：success 绿色，其他红色
            color = "green" if s == "success" else "red"
            log.write(f"[{color}]■ run[/{color}]  {s}  {event.get('steps')} steps")
        
        # ====================== llm.usage：LLM 使用量 ======================
        elif t == "llm.usage":
            log.write(
                f"[dim]  usage[/dim]  in={event.get('input_tokens')} "
                f"out={event.get('output_tokens')} "
                f"cache_read={event.get('cache_read_input_tokens')}"
            )
        
        # ====================== log.line：日志行 ======================
        elif t == "log.line":
            level = event.get("level", "INFO")
            log.write(
                f"[dim]{level}[/dim]  {event.get('source', '')}  {event.get('message', '')}"
            )
