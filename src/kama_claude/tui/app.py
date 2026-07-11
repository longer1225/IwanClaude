# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：用于异步网络通信和重试逻辑
import asyncio
# 导入 json：用于序列化参数
import json
# 导入 Any：类型注解
from typing import Any

# 导入 Textual 框架的核心组件
# Textual 是一个 Python 终端 UI 框架，类似于 Web 的 React/Vue，但运行在终端里
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Label, Static

# 导入配置和 Socket 客户端
from kama_claude.core.config import KamaConfig
from kama_claude.core.transport.socket_client import IpcError, SocketClient


# 字符串预览函数：如果字符串超过 n 个字符，截断并添加省略号
# 什么是预览？就是显示长文本的前一部分，让用户知道内容但不占用太多空间
def _preview(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s


# 将参数字典转换为 JSON 字符串（用于显示）
def _params_str(params: dict[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False)


# LLMStreamBlock：流式显示 LLM 输出的组件
# 什么是流式？就是 LLM 不是一次性返回所有内容，而是一个 token 一个 token 地返回
# 这个组件负责累积 token 并实时更新显示
class LLMStreamBlock(Static):
    """在同一个 Static widget 中累积 LLM 流式 token。"""

    # CSS 样式：padding 0 2 表示左右各 2 个空格，color 使用主题文本颜色
    DEFAULT_CSS = "LLMStreamBlock { padding: 0 2; color: $text; }"

    # 初始化为空文本块
    def __init__(self) -> None:
        # 调用父类构造函数，初始文本为空
        super().__init__("")
        # 内部缓存文本（用于累积 token）
        self._text = ""

    # 追加一个 token 并刷新显示
    def append_token(self, token: str) -> None:
        # 累积 token 到内部文本
        self._text += token
        # 更新 widget 显示（刷新终端界面）
        self.update(self._text)


# ToolCallBlock：可折叠的工具调用组件
# 什么是可折叠？就是默认只显示摘要，点击后展开显示完整内容
# 这样可以避免终端界面过于拥挤
class ToolCallBlock(Widget):
    """可折叠的工具调用块：折叠时显示摘要，点击后展开完整 params 和 output。"""

    # CSS 样式定义：
    # height: auto 表示高度自适应内容
    # .detail 默认隐藏，添加 expanded 类后显示
    DEFAULT_CSS = """
    ToolCallBlock { height: auto; padding: 0 0; }
    ToolCallBlock > .detail { display: none; padding: 0 4; color: $text-muted; }
    ToolCallBlock.expanded > .detail { display: block; }
    """

    # 初始化工具调用信息
    def __init__(self, tool_name: str, params: dict[str, Any]) -> None:
        # 调用父类构造函数
        super().__init__()
        # 工具名称（如 "bash"、"read_file"）
        self._tool_name = tool_name
        # 工具参数（原始字典）
        self._params = params
        # 工具参数的 JSON 字符串（用于显示）
        self._params_full = _params_str(params)
        # 工具输出结果（初始为空）
        self._output = ""
        # 工具执行耗时（毫秒）
        self._elapsed_ms = 0
        # 是否出错
        self._is_error = False
        # 是否执行完成
        self._finished = False

    # 构建组件的子元素（Textual 的声明式 UI）
    def compose(self) -> ComposeResult:
        # 生成摘要行（始终显示）
        yield Static(self._summary(), classes="summary")
        # 生成详情行（默认隐藏）
        yield Static("", classes="detail")

    # 生成摘要行文本（用于折叠状态显示）
    def _summary(self) -> str:
        # 参数预览（最多 60 个字符）
        params_pre = _preview(self._params_full, 60)
        # 图标：✎ 表示工具调用
        icon = "[bold yellow]✎[/bold yellow]"
        # 摘要行格式：图标 + 工具名 + 参数预览
        line = f"  {icon} [bold]{self._tool_name}[/bold]  [dim]{params_pre}[/dim]"
        
        # 如果工具调用已完成
        if self._finished:
            # 输出预览（最多 50 个字符）
            out_pre = _preview(self._output, 50)
            # 颜色：错误用红色，正常用暗色
            color = "red" if self._is_error else "dim"
            # 提示：如果输出超过 50 字符，提示可以点击展开
            hint = "  [dim]▸ click to expand[/dim]" if len(self._output) > 50 else ""
            # 添加输出预览和耗时
            line += (
                f"\n  [dim]↳[/dim] [{color}]{out_pre}[/{color}]"
                f"  [dim]{self._elapsed_ms}ms[/dim]{hint}"
            )
        return line

    # 工具调用完成时更新结果并刷新摘要
    def set_result(self, output: str, elapsed_ms: int, *, is_error: bool = False) -> None:
        # 更新输出结果
        self._output = output
        # 更新耗时
        self._elapsed_ms = elapsed_ms
        # 更新是否出错
        self._is_error = is_error
        # 标记为完成
        self._finished = True
        # 如果 widget 已经挂载到界面上，更新摘要显示
        if self.children:
            self.query_one(".summary", Static).update(self._summary())

    # 点击时切换展开/折叠状态
    def on_click(self) -> None:
        # 如果还没完成，不允许展开
        if not self._finished:
            return
        
        # 如果已经展开，移除 expanded 类（折叠）
        if "expanded" in self.classes:
            self.remove_class("expanded")
        # 如果未展开，添加 expanded 类（展开）
        else:
            # 获取详情元素
            detail = self.query_one(".detail", Static)
            # 更新详情内容：完整的 params、output 和耗时
            detail.update(
                f"[dim]params:[/dim]\n    {self._params_full}\n"
                f"[dim]output:[/dim]\n    {self._output}\n"
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"
            )
            # 添加 expanded 类（触发 CSS 显示详情）
            self.add_class("expanded")


# KamaTuiApp：主应用类，继承自 Textual 的 App
# 这是整个 TUI 的入口，负责管理界面布局和事件处理
class KamaTuiApp(App[None]):
    """KamaClaude TUI：终端滚屏风格，实时展示 agent 执行过程。"""

    # 应用标题（显示在终端标题栏）
    TITLE = "KamaClaude"
    
    # 键盘绑定：按 q 键退出
    BINDINGS = [
        Binding("q", "quit", "quit"),
    ]
    
    # 全局 CSS 样式
    CSS = """
    Screen { background: $background; }  /* 屏幕背景色 */
    #header {                            /* 顶部状态栏 */
        height: 1;                       /* 高度为 1 行 */
        background: $primary;            /* 使用主题主色 */
        color: $text;                    /* 使用主题文本色 */
        padding: 0 1;                    /* 左右各 1 空格 */
    }
    #log-view {                          /* 日志视图容器 */
        height: 1fr;                     /* 占满剩余空间 */
    }
    Static.run-header { color: cyan; padding: 1 2 0 2; }       /* 运行开始标题 */
    Static.step-divider { color: $text-muted; padding: 0 2; }   /* 步骤分隔线 */
    Static.run-ok { color: green; padding: 0 2 1 2; }           /* 运行成功 */
    Static.run-err { color: red; padding: 0 2 1 2; }            /* 运行失败 */
    Static.usage { padding: 0 2; }                              /* token 使用统计 */
    Static.log-line { padding: 0 2; }                           /* 日志行 */
    """

    # 初始化连接参数和 TUI 内部状态
    def __init__(self, host: str, port: int, replay_run_id: str | None = None) -> None:
        # 调用父类构造函数
        super().__init__()
        # Core daemon 的主机地址
        self._host = host
        # Core daemon 的端口
        self._port = port
        # 回放的 run_id（可选，用于回放历史记录）
        self._replay_run_id = replay_run_id
        # Socket 客户端（初始为 None，连接后赋值）
        self._client: SocketClient | None = None
        # 当前正在显示的 LLM 流式块（用于累积 token）
        self._current_llm: LLMStreamBlock | None = None
        # 待完成的工具调用块（key 是 tool_use_id，value 是 ToolCallBlock）
        self._pending_tool_blocks: dict[str, ToolCallBlock] = {}

    # 构建界面布局（Textual 的声明式 UI）
    def compose(self) -> ComposeResult:
        # 顶部状态栏：显示应用名称和连接状态
        yield Label("[bold]KamaClaude[/bold]  [dim]connecting...[/dim]", id="header")
        # 日志视图：垂直滚动容器，显示所有事件
        yield VerticalScroll(id="log-view")

    # 应用挂载（启动）时执行：启动 Socket 连接循环
    def on_mount(self) -> None:
        # run_worker：在后台执行异步任务
        # exclusive=True：同一时间只允许一个同名 worker 运行
        # name="socket"：给 worker 命名，便于管理
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")

    # 向日志视图追加一个 widget 并滚动到底部
    def _append(self, widget: Widget) -> None:
        # 获取日志视图容器
        log_view = self.query_one("#log-view", VerticalScroll)
        # 挂载 widget 到容器中
        log_view.mount(widget)
        # 滚动到底部（无动画）
        log_view.scroll_end(animate=False)

    # 结束当前 LLM 流式块（下一个 token 将开启新块）
    def _break_llm(self) -> None:
        self._current_llm = None

    # 管理 SocketClient 生命周期：连接、订阅事件、断线重连
    # 这是 TUI 的核心网络逻辑
    async def _socket_loop(self) -> None:
        # 获取顶部状态栏
        header = self.query_one("#header", Label)

        # 无限循环：负责断线重连
        while True:
            # 创建新的 SocketClient
            client = SocketClient(self._host, self._port)
            # 清空当前客户端引用
            self._client = None
            
            try:
                # 尝试连接到 Core daemon
                await client.connect()
            except (ConnectionRefusedError, OSError):
                # 连接失败：更新状态栏提示，等待 2 秒后重试
                header.update("[bold]KamaClaude[/bold]  [red]not connected — retrying...[/red]")
                await asyncio.sleep(2)
                continue

            # 连接成功：保存客户端引用，更新状态栏
            self._client = client
            header.update(
                f"[bold]KamaClaude[/bold]  [dim]{self._host}:{self._port}[/dim]"
            )
            
            # 启动客户端事件循环（监听服务器推送的事件）
            loop_task = asyncio.create_task(client.run_event_loop())

            # 定义事件处理回调：收到事件时调用 _handle_event
            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event)

            # 注册事件回调
            client.on_event(on_event)

            try:
                # 构建订阅参数：订阅所有感兴趣的事件类型
                params: dict[str, Any] = {
                    "topics": ["run.*", "step.*", "tool.*", "llm.token", "llm.usage", "log.*"],
                    "scope": "global",
                }
                # 如果指定了回放的 run_id，添加回放参数
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                
                # 发送订阅命令
                await client.send_command("event.subscribe", params)
                
                # 等待事件循环结束（阻塞直到断开连接）
                await loop_task
            except IpcError as e:
                # 订阅出错：更新状态栏提示
                header.update(f"[bold]KamaClaude[/bold]  [red]subscribe error: {e}[/red]")
            finally:
                # 清理资源：取消事件循环任务
                if not loop_task.done():
                    loop_task.cancel()
                # 清空客户端引用
                self._client = None
                # 结束当前 LLM 流式块
                self._break_llm()
                # 关闭客户端连接
                await client.close()

            # 断开连接：更新状态栏提示，等待 2 秒后重试
            header.update("[bold]KamaClaude[/bold]  [dim]disconnected — retrying...[/dim]")
            await asyncio.sleep(2)

    # 根据事件 type 路由到对应渲染逻辑
    # 这是 TUI 的事件处理核心，负责将事件转换为 UI 显示
    def _handle_event(self, event: dict[str, Any]) -> None:
        # 获取事件类型
        t = event.get("type", "")

        # LLM 流式 token：累积显示
        if t == "llm.token":
            token = event.get("token", "")
            # 如果没有当前 LLM 块，创建一个新的
            if self._current_llm is None:
                llm_block = LLMStreamBlock()
                self._append(llm_block)
                self._current_llm = llm_block
            # 追加 token
            self._current_llm.append_token(token)
            return

        # 非 LLM 事件：结束当前 LLM 块（下一个 token 将开启新块）
        self._break_llm()

        # 运行开始事件
        if t == "run.started":
            run_id = event.get("run_id", "")
            goal = event.get("goal", "")
            self._append(Static(
                f"[bold cyan]▶ run[/bold cyan]  [dim]{run_id}[/dim]\n"
                f"  [dim]goal:[/dim] {goal}",
                classes="run-header",
            ))

        # 步骤开始事件
        elif t == "step.started":
            step = event.get("step", "")
            self._append(Static(
                f"[dim]── step {step} {'─' * 48}[/dim]",
                classes="step-divider",
            ))

        # 工具调用开始事件
        elif t == "tool.call_started":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            params = event.get("params") or {}
            # 创建工具调用块并保存到待完成字典
            tc_block = ToolCallBlock(tool_name, params)
            self._pending_tool_blocks[tool_use_id] = tc_block
            self._append(tc_block)

        # 工具调用完成事件
        elif t == "tool.call_finished":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            output = str(event.get("output") or "")
            # 如果存在对应的待完成块，更新结果
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(output, elapsed_ms)

        # 工具调用失败事件
        elif t == "tool.call_failed":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            error_msg = str(event.get("error_message") or "")
            # 如果存在对应的待完成块，更新结果（标记为错误）
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(error_msg, elapsed_ms, is_error=True)

        # 运行结束事件
        elif t == "run.finished":
            status = event.get("status", "")
            steps = event.get("steps", 0)
            reason = event.get("reason") or ""
            # 根据状态显示不同颜色
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

        # LLM token 使用统计事件
        elif t == "llm.usage":
            self._append(Static(
                f"[dim]  tokens  "
                f"in={event.get('input_tokens')} "
                f"out={event.get('output_tokens')} "
                f"cache={event.get('cache_read_input_tokens')}[/dim]",
                classes="usage",
            ))

        # 日志行事件
        elif t == "log.line":
            level = event.get("level", "INFO")
            # 根据日志级别显示不同颜色
            color = "bold red" if level == "ERROR" else ("yellow" if level == "WARNING" else "dim")
            self._append(Static(
                f"[{color}]{level}[/{color}]  "
                f"[dim]{event.get('source', '')}[/dim]  {event.get('message', '')}",
                classes="log-line",
            ))


# TUI 入口：读取配置并启动 KamaTuiApp
def run(config: KamaConfig, replay_run_id: str | None = None) -> None:
    # 创建应用实例
    app = KamaTuiApp(config.host, config.port, replay_run_id=replay_run_id)
    # 启动应用（进入事件循环）
    app.run()
