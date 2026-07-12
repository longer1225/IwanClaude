# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：用于异步编程
import asyncio
# 导入 json：用于序列化
import json
# 导入 Any：类型提示
from typing import Any

# 导入 Markdown：用于渲染 Markdown 格式文本
from rich.markdown import Markdown
# 导入 events：Textual 的事件系统
from textual import events
# 导入 App 和 ComposeResult：Textual 应用基类和组件组合
from textual.app import App, ComposeResult
# 导入 Binding：键盘绑定
from textual.binding import Binding
# 导入 VerticalScroll：垂直滚动容器
from textual.containers import VerticalScroll
# 导入 NoMatches：查询不到组件时的异常
from textual.css.query import NoMatches
# 导入 Message：自定义消息
from textual.message import Message
# 导入 Widget：所有组件的基类
from textual.widget import Widget
# 导入 Label、Static、TextArea：Textual 内置组件
from textual.widgets import Label, Static, TextArea

# 导入 KamaConfig：配置类
from kama_claude.core.config import KamaConfig
# 导入 SocketClient 和 IpcError：IPC 客户端和错误类型
from kama_claude.core.transport.socket_client import IpcError, SocketClient


# 字符串预览函数：如果字符串超过 n 个字符，截断并添加省略号
def _preview(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s


# 将工具参数转换为 JSON 字符串（带缩进，保留中文）
def _params_str(params: dict[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False, indent=2)


# 从工具参数中提取适合摘要展示的关键字段
# 不同工具关注的参数不同：read_file 关注 path，bash 关注 command
def _param_summary(tool_name: str, params: dict[str, Any], max_len: int = 72) -> str:
    # 每个工具的关键字段映射
    keys_by_tool = {
        "read_file": ("path",),
        "write_file": ("path",),
        "list_dir": ("path", "max_depth"),
        "bash": ("command",),
        "note_save": ("content",),
    }
    # 获取当前工具的关键字段
    keys = keys_by_tool.get(tool_name, ())
    # 构建摘要字符串
    parts = [f"{key}={params[key]!r}" for key in keys if key in params]
    # 如果没有关键字段，取前两个参数
    if not parts:
        parts = [f"{key}={value!r}" for key, value in list(params.items())[:2]]
    # 截断过长的摘要
    return _preview(", ".join(parts), max_len)


# LLMStreamBlock 类：在同一个 Static 组件中累积 LLM 流式输出
# 为什么用同一个组件？因为每个 token 创建一个组件会导致性能问题
class LLMStreamBlock(Static):
    """在同一个 Static widget 中累积 LLM 流式 token。"""

    # 默认 CSS 样式：内边距 0 2，文字颜色为默认文本色
    DEFAULT_CSS = "LLMStreamBlock { padding: 0 2; color: $text; }"

    # 初始化：创建空文本块
    def __init__(self) -> None:
        super().__init__("")  # 初始内容为空
        self._text = ""        # 内部缓存的文本
        self._finalized = False  # 是否已渲染为 Markdown

    # 追加一个 token 到文本块
    def append_token(self, token: str) -> None:
        # 如果已经 finalize，不再追加
        if self._finalized:
            return
        # 追加 token 到内部缓存
        self._text += token
        # 更新组件显示（原地替换，性能好）
        self.update(self._text)

    # 将累积的文本渲染为 Markdown
    # 为什么需要？因为流式输出时是纯文本，结束后需要渲染为 Markdown（粗体、代码块等）
    def finalize_markdown(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        # 如果有文本，渲染为 Markdown
        if self._text.strip():
            self.update(Markdown(self._text, code_theme="monokai"))


# ToolCallBlock 类：可折叠的工具调用块
# 折叠时显示摘要，点击后展开完整的参数和输出
class ToolCallBlock(Widget):
    """可折叠的工具调用块：折叠时显示摘要，点击后展开完整 params 和 output。"""

    # 默认 CSS 样式：
    # - detail 子组件默认隐藏（display: none）
    # - expanded 类添加后，detail 显示（display: block）
    DEFAULT_CSS = """
    ToolCallBlock { height: auto; padding: 0 2; color: $text-muted; }
    ToolCallBlock > .summary { color: $text-muted; }
    ToolCallBlock > .detail { display: none; padding: 0 2 0 4; color: $text-muted; }
    ToolCallBlock.expanded > .detail { display: block; }
    """

    # 初始化：保存工具调用信息
    def __init__(self, tool_name: str, params: dict[str, Any]) -> None:
        super().__init__()
        self._tool_name = tool_name      # 工具名称
        self._params = params            # 工具参数
        self._params_full = _params_str(params)  # 参数的完整 JSON
        self._output = ""                # 工具输出
        self._elapsed_ms = 0            # 执行耗时（毫秒）
        self._is_error = False           # 是否出错
        self._finished = False           # 是否已完成

    # 组合组件：创建摘要和详情两个子组件
    def compose(self) -> ComposeResult:
        # 摘要行（始终显示）
        yield Static(self._summary(), classes="summary")
        # 详情行（默认隐藏，点击后显示）
        yield Static("", classes="detail")

    # 生成摘要行文本
    def _summary(self) -> str:
        # note_save 工具特殊处理：显示 "remembered"
        if self._tool_name == "note_save" and self._finished and not self._is_error:
            return f"  [green]remembered[/green]  [dim]{self._elapsed_ms}ms[/dim]"

        # 生成参数摘要
        params_pre = _param_summary(self._tool_name, self._params)
        # 基础摘要：工具名称 + 参数
        line = f"  [dim]tool[/dim] [bold]{self._tool_name}[/bold]"
        if params_pre:
            line += f"  [dim]{params_pre}[/dim]"
        # 如果工具调用完成，添加状态和耗时
        if self._finished:
            color = "red" if self._is_error else "green"
            status = "failed" if self._is_error else "done"
            hint = "  [dim](click to expand)[/dim]" if self._output else ""
            line += f"  [{color}]{status}[/{color}]  [dim]{self._elapsed_ms}ms[/dim]{hint}"
        return line

    # 工具调用完成时更新结果
    def set_result(self, output: str, elapsed_ms: int, *, is_error: bool = False) -> None:
        self._output = output       # 工具输出
        self._elapsed_ms = elapsed_ms  # 耗时
        self._is_error = is_error   # 是否出错
        self._finished = True       # 标记完成
        # 如果组件已挂载，更新摘要显示
        if self.children:
            self.query_one(".summary", Static).update(self._summary())

    # 点击事件处理：切换展开/折叠状态
    def on_click(self) -> None:
        # 如果工具调用还没完成，不允许点击
        if not self._finished:
            return
        # 如果已经展开，折叠
        if "expanded" in self.classes:
            self.remove_class("expanded")
        else:
            # 如果未展开，更新详情内容并展开
            detail = self.query_one(".detail", Static)
            detail.update(
                f"[dim]params[/dim]\n{self._params_full}\n\n"
                f"[dim]output[/dim]\n{self._output}\n\n"
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"
            )
            self.add_class("expanded")


# ChatTextArea 类：自定义的聊天输入框
# 支持 Enter 提交，Cmd/Shift/Alt+Enter 换行
class ChatTextArea(TextArea):
    """支持 Enter 提交、Cmd/Shift/Alt+Enter 换行的多行聊天输入框。"""

    # 默认 CSS 样式：自动高度、圆角边框、背景色等
    DEFAULT_CSS = """
    ChatTextArea {
        height: auto;
        min-height: 3;
        max-height: 12;
        border: round $surface-lighten-2;
        background: $background;
        padding: 0 1;
        margin: 1 2;
        scrollbar-size-vertical: 1;
    }
    ChatTextArea:focus {
        border: round $accent;
        background: $background;
    }
    """

    # 自定义消息类：当用户提交时发出此消息，供宿主 App 监听
    class Submitted(Message):
        def __init__(self, area: ChatTextArea) -> None:
            self.text_area = area  # 输入框组件
            self.value = area.text  # 输入内容
            super().__init__()

    # 键盘事件处理：自定义 Enter 行为
    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        # Enter：提交消息
        if key == "enter":
            event.stop()           # 阻止事件冒泡
            event.prevent_default()  # 阻止默认行为（换行）
            if self.text.strip():   # 如果有内容
                self.post_message(self.Submitted(self))  # 发布提交消息
            return
        # Cmd/Shift/Alt+Enter：插入换行
        if key in ("alt+enter", "shift+enter", "ctrl+j", "super+enter"):
            event.stop()
            event.prevent_default()
            if not self.read_only:
                self.insert("\n")
            return
        # 其他按键：交给父类处理
        await super()._on_key(event)


# KamaTuiApp 类：KamaClaude 的终端用户界面
# 提供实时聊天功能，支持流式输出、工具调用展示等
class KamaTuiApp(App[None]):
    """KamaClaude TUI：终端滚屏风格，实时展示 agent 执行过程。"""

    # 应用标题（显示在终端顶部）
    TITLE = "KamaClaude"
    # 键盘绑定：Ctrl+Q 退出
    BINDINGS = [
        Binding("ctrl+q", "quit", "quit"),
    ]
    # CSS 样式定义
    CSS = """
    Screen { background: $background; }
    #header {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    #log-view {
        height: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }
    Static.user-turn { color: $text; padding: 1 2 0 2; }
    Static.run-header { color: $text-muted; padding: 1 2 0 2; }
    Static.step-divider { color: $text-muted; padding: 0 2; }
    Static.run-ok { color: green; padding: 0 2 1 2; }
    Static.run-err { color: red; padding: 0 2 1 2; }
    Static.usage { padding: 0 2; }
    Static.log-line { padding: 0 2; }
    """

    # 初始化：设置连接参数和内部状态
    def __init__(self, host: str, port: int, replay_run_id: str | None = None) -> None:
        super().__init__()
        self._host = host                 # daemon 主机地址
        self._port = port                 # daemon 端口
        self._replay_run_id = replay_run_id  # 可选的回放 run ID
        self._client: SocketClient | None = None  # IPC 客户端
        self._current_llm: LLMStreamBlock | None = None  # 当前 LLM 流式块
        self._pending_tool_blocks: dict[str, ToolCallBlock] = {}  # 等待完成的工具调用块
        self._session_id: str | None = None  # 当前会话 ID
        self._busy = False                 # agent 是否正在工作

    # 组合组件：创建界面布局
    def compose(self) -> ComposeResult:
        # 顶部状态栏
        yield Label("[bold]KamaClaude[/bold]  [dim]connecting...[/dim]", id="header")
        # 日志视图（滚动区域）
        yield VerticalScroll(id="log-view")
        # 聊天输入框
        yield ChatTextArea(id="prompt", show_line_numbers=False)

    # 组件挂载完成后执行
    def on_mount(self) -> None:
        # 启动 socket 循环工作线程
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")
        # 获取输入框，设置为禁用状态（连接完成后启用）
        prompt = self.query_one("#prompt", ChatTextArea)
        prompt.disabled = True
        prompt.border_title = "connecting..."

    # 退出操作：关闭 session 并退出应用
    async def action_quit(self) -> None:
        # 如果有活跃的 session，尝试关闭
        if self._client is not None and self._session_id is not None:
            try:
                await self._client.send_command("session.close", {"session_id": self._session_id})
            except (IpcError, RuntimeError, OSError):
                # 关闭失败不阻塞退出，显示警告
                self._append(Static("[yellow]warning: failed to close session[/yellow]"))
        # 退出应用
        self.exit()

    # 处理聊天输入框的提交事件
    async def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        # 获取提交内容
        content = event.value.strip()
        # 空内容不处理
        if not content:
            return
        # 如果未连接、没有 session 或 agent 正忙，显示提示
        if self._client is None or self._session_id is None or self._busy:
            self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return
        # 标记 agent 正忙
        self._busy = True
        # 清空输入框并禁用
        prompt = event.text_area
        prompt.text = ""
        prompt.disabled = True
        prompt.border_title = "agent is working..."
        # 在日志视图中显示用户输入
        self._append(Static(f"[bold]>[/bold] {content}", classes="user-turn"))
        # 更新头部状态为 running
        self._update_header("running")
        try:
            # 发送消息到 daemon 的 session
            await self._client.send_command(
                "session.send_message",
                {"session_id": self._session_id, "content": content},
            )
        except IpcError as e:
            # 发送失败，恢复状态
            self._busy = False
            prompt.disabled = False
            prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
            self._update_header("ready")
            self._append(Static(f"[red]send error: {e}[/red]", classes="log-line"))

    # 向日志视图追加组件并滚动到底部
    def _append(self, widget: Widget) -> None:
        log_view = self.query_one("#log-view", VerticalScroll)
        log_view.mount(widget)           # 挂载组件
        log_view.scroll_end(animate=False)  # 滚动到底部

    # 结束当前 LLM 流式块
    def _break_llm(self) -> None:
        if self._current_llm is not None:
            self._current_llm.finalize_markdown()  # 渲染为 Markdown
        self._current_llm = None  # 重置引用（下一个 token 将创建新块）

    # 安全获取输入框（组件未挂载时返回 None）
    def _prompt(self) -> ChatTextArea | None:
        try:
            return self.query_one("#prompt", ChatTextArea)
        except NoMatches:
            return None

    # 更新顶部状态栏
    def _update_header(self, state: str) -> None:
        try:
            header = self.query_one("#header", Label)
        except NoMatches:
            return
        # 显示 session ID（如果有）
        session = f"  [dim]{self._session_id}[/dim]" if self._session_id else ""
        # 根据状态选择颜色
        color = {
            "ready": "green",       # 就绪（可以输入）
            "running": "yellow",    # 运行中（agent 正在工作）
            "disconnected": "red",  # 断开连接
            "connecting": "dim",    # 连接中
        }.get(state, "dim")
        # 更新头部内容
        header.update(
            f"[bold]KamaClaude[/bold]  [dim]{self._host}:{self._port}[/dim]"
            f"{session}  [{color}]{state}[/{color}]"
        )

    # Socket 循环：管理连接、订阅事件、断线重连
    async def _socket_loop(self) -> None:
        header = self.query_one("#header", Label)

        # 无限循环：断线后自动重连
        while True:
            # 创建新的 SocketClient
            client = SocketClient(self._host, self._port)
            self._client = None
            try:
                # 连接到 daemon
                await client.connect()
            except (ConnectionRefusedError, OSError):
                # 连接失败，更新状态，2 秒后重试
                self._update_header("disconnected")
                await asyncio.sleep(2)
                continue

            # 连接成功
            self._client = client
            self._update_header("connecting")
            # 启动事件循环任务
            loop_task = asyncio.create_task(client.run_event_loop())

            # 定义事件回调
            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event)

            # 注册事件回调
            client.on_event(on_event)

            try:
                # 订阅事件：接收 session、run、tool、llm.token、llm.usage、log 相关事件
                params: dict[str, Any] = {
                    "topics": [
                        "session.*",
                        "run.*",
                        "step.*",
                        "tool.*",
                        "llm.token",
                        "llm.usage",
                        "log.*",
                    ],
                    "scope": "global",
                }
                # 如果有回放 run ID，添加到订阅参数
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                # 发送订阅命令
                await client.send_command("event.subscribe", params)
                # 创建 chat session
                created = await client.send_command("session.create", {"mode": "chat"})
                self._session_id = str(created["session_id"])
                # 启用输入框
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = False
                    prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    prompt.focus()
                # 更新状态为 ready
                self._update_header("ready")
                # 等待事件循环结束
                await loop_task
            except IpcError as e:
                # IPC 错误
                header.update(f"[bold]KamaClaude[/bold]  [red]subscribe error: {e}[/red]")
            finally:
                # 清理资源
                if not loop_task.done():
                    loop_task.cancel()
                self._client = None
                self._session_id = None
                # 禁用输入框
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = True
                    prompt.border_title = "disconnected, retrying..."
                # 结束当前 LLM 块
                self._break_llm()
                # 关闭连接
                await client.close()

            # 等待 2 秒后重新连接
            self._update_header("disconnected")
            await asyncio.sleep(2)

    # 根据事件类型路由到对应渲染逻辑
    def _handle_event(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")

        # llm.token：LLM 流式输出的单个 token
        if t == "llm.token":
            token = event.get("token", "")
            # 如果没有当前 LLM 块，创建新的
            if self._current_llm is None:
                llm_block = LLMStreamBlock()
                self._append(llm_block)
                self._current_llm = llm_block
            # 追加 token
            self._current_llm.append_token(token)
            return

        # 非 token 事件：结束当前 LLM 块（渲染为 Markdown）
        self._break_llm()

        # session.waiting_for_input：session 等待用户输入（chat 模式下 agent 完成回复）
        if t == "session.waiting_for_input":
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                prompt.focus()
            self._update_header("ready")

        # session.closed：session 已关闭
        elif t == "session.closed":
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.border_title = "session closed"
            self._update_header("disconnected")

        # run.started：run 开始
        elif t == "run.started":
            run_id = event.get("run_id", "")
            goal = event.get("goal", "")
            self._append(Static(
                f"[dim]run[/dim]  [cyan]{run_id}[/cyan]  [dim]{_preview(goal, 96)}[/dim]",
                classes="run-header",
            ))

        # step.started：步骤开始
        elif t == "step.started":
            step = event.get("step", "")
            self._append(Static(
                f"[dim]step {step}[/dim]",
                classes="step-divider",
            ))

        # tool.call_started：工具调用开始
        elif t == "tool.call_started":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            params = event.get("params") or {}
            tc_block = ToolCallBlock(tool_name, params)
            self._pending_tool_blocks[tool_use_id] = tc_block
            self._append(tc_block)

        # tool.call_finished：工具调用完成
        elif t == "tool.call_finished":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            output = str(event.get("output") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(output, elapsed_ms)

        # tool.call_failed：工具调用失败
        elif t == "tool.call_failed":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            error_msg = str(event.get("error_message") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(error_msg, elapsed_ms, is_error=True)

        # run.finished：run 完成
        elif t == "run.finished":
            status = event.get("status", "")
            steps = event.get("steps", 0)
            reason = event.get("reason") or ""
            if status == "success":
                self._append(Static(
                    f"[bold green]✓ completed[/bold green]  [dim]{steps} steps[/dim]",
                    classes="run-ok",
                ))
            else:
                detail = f"  [dim]{reason}[/dim]" if reason else ""
                self._append(Static(
                    f"[bold red]✗ failed[/bold red]{detail}  [dim]{steps} steps[/dim]",
                    classes="run-err",
                ))

        # llm.usage：LLM token 使用统计
        elif t == "llm.usage":
            self._append(Static(
                f"[dim]  tokens  "
                f"in={event.get('input_tokens')} "
                f"out={event.get('output_tokens')} "
                f"cache={event.get('cache_read_input_tokens')}[/dim]",
                classes="usage",
            ))

        # log.line：日志行
        elif t == "log.line":
            level = event.get("level", "INFO")
            color = "bold red" if level == "ERROR" else ("yellow" if level == "WARNING" else "dim")
            self._append(Static(
                f"[{color}]{level}[/{color}]  "
                f"[dim]{event.get('source', '')}[/dim]  {event.get('message', '')}",
                classes="log-line",
            ))


# TUI 入口函数：读取配置并启动应用
def run(config: KamaConfig, replay_run_id: str | None = None) -> None:
    app = KamaTuiApp(config.host, config.port, replay_run_id=replay_run_id)
    app.run()
