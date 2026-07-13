# 导入 Python 3.7+ 的类型注解特性
from __future__ import annotations

# 导入 asyncio（异步 I/O 框架）
import asyncio

# 导入 json（用于序列化工具参数）
import json

# 导入 logging（用于记录日志）
import logging

# 导入 Any（表示任意类型）
from typing import Any

# 创建日志记录器（属于当前模块）
log = logging.getLogger(__name__)

# 导入 Markdown（用于渲染 AI 回复为 Markdown）
from rich.markdown import Markdown

# 导入 textual 的事件系统
from textual import events

# 导入 textual 的 App 和 ComposeResult（应用基类和组件组合）
from textual.app import App, ComposeResult

# 导入 VerticalScroll（垂直滚动容器）
from textual.containers import VerticalScroll

# 导入 NoMatches（组件查询异常）
from textual.css.query import NoMatches

# 导入 Message（textual 的消息系统）
from textual.message import Message

# 导入 Widget（所有组件的基类）
from textual.widget import Widget

# 导入 Binding（键盘快捷键绑定）
from textual.binding import Binding

# 导入 Label、Static、TextArea（textual 内置组件）
from textual.widgets import Label, Static, TextArea

# 导入 KamaConfig（配置对象）
from kama_claude.core.config import KamaConfig

# 导入 IpcError（IPC 通信异常）和 SocketClient（TCP 客户端）
from kama_claude.core.transport.socket_client import IpcError, SocketClient


# 字符串截断函数：如果字符串长度超过 n，截断并添加省略号
# 参数 s: 输入字符串
# 参数 n: 最大长度
# 返回值: 截断后的字符串
def _preview(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s


# 将工具参数字典转换为格式化的 JSON 字符串
# 参数 params: 工具调用参数字典
# 返回值: 格式化的 JSON 字符串（带缩进，保留中文）
def _params_str(params: dict[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False, indent=2)


# 从工具参数中提取最适合摘要展示的关键字段
# 参数 tool_name: 工具名
# 参数 params: 工具调用参数字典
# 参数 max_len: 最大长度（默认 72）
# 返回值: 参数摘要字符串（如 "path='README.md'"）
def _param_summary(tool_name: str, params: dict[str, Any], max_len: int = 72) -> str:
    # 每个工具对应的关键字段映射
    keys_by_tool = {
        "read_file": ("path",),      # 读文件显示路径
        "write_file": ("path",),     # 写文件显示路径
        "list_dir": ("path", "max_depth"),  # 列目录显示路径和深度
        "bash": ("command",),        # bash 显示命令
        "note_save": ("content",),   # 保存笔记显示内容
    }
    
    # 获取该工具对应的关键字段列表
    keys = keys_by_tool.get(tool_name, ())
    
    # 构建参数字符串列表（如 ["path='README.md'"]）
    parts = [f"{key}={params[key]!r}" for key in keys if key in params]
    
    # 如果没有关键字段，取前两个参数
    if not parts:
        parts = [f"{key}={value!r}" for key, value in list(params.items())[:2]]
    
    # 拼接并截断到最大长度
    return _preview(", ".join(parts), max_len)


# LLM 流式输出块组件
# 功能：在同一个 Static widget 中累积 LLM 流式返回的 token，实现实时打字机效果
# 完成后将文本渲染为 Markdown
class LLMStreamBlock(Static):
    """在同一个 Static widget 中累积 LLM 流式 token。"""

    # 默认 CSS 样式：内边距 0 2，使用默认文本颜色
    DEFAULT_CSS = "LLMStreamBlock { padding: 0 2; color: $text; }"

    # 初始化流式块（空文本）
    def __init__(self) -> None:
        # 调用父类构造函数，初始文本为空
        super().__init__("")
        
        # 累积的文本内容（所有 token 拼接后的完整文本）
        self._text = ""
        
        # 是否已完成（完成后不再接受新 token）
        self._finalized = False

    # 追加一个 token 并刷新显示
    # 参数 token: LLM 返回的单个 token（如 "这"、"是"、"一"）
    # 返回值: 无
    def append_token(self, token: str) -> None:
        # 如果已完成，跳过
        if self._finalized:
            return
        
        # 将 token 追加到累积文本
        self._text += token
        
        # 更新 widget 显示（实现实时打字机效果）
        self.update(self._text)

    # 将累积文本渲染为 Markdown（流式输出结束后调用）
    # 返回值: 无
    def finalize_markdown(self) -> None:
        # 如果已完成，跳过
        if self._finalized:
            return
        
        # 标记为已完成
        self._finalized = True
        
        # 如果有文本内容，渲染为 Markdown
        if self._text.strip():
            # 使用 monokai 代码主题渲染代码块
            self.update(Markdown(self._text, code_theme="monokai"))


# 工具调用块组件（可折叠）
# 功能：显示工具调用的摘要，点击后展开显示完整参数和输出
# 状态：进行中（显示工具名和参数）→ 完成（显示状态和耗时）
class ToolCallBlock(Widget):
    """可折叠的工具调用块：折叠时显示摘要，点击后展开完整 params 和 output。"""

    # 默认 CSS 样式：
    # - 高度自适应，内边距 0 2，使用暗淡文本颜色
    # - .summary 类：暗淡文本颜色
    # - .detail 类：默认隐藏，展开时显示
    # - .expanded 类：展开状态，显示 .detail
    DEFAULT_CSS = """
    ToolCallBlock { height: auto; padding: 0 2; color: $text-muted; }
    ToolCallBlock > .summary { color: $text-muted; }
    ToolCallBlock > .detail { display: none; padding: 0 2 0 4; color: $text-muted; }
    ToolCallBlock.expanded > .detail { display: block; }
    """

    # 初始化工具调用信息
    # 参数 tool_name: 工具名（如 "read_file"）
    # 参数 params: 工具调用参数字典（如 {"path": "README.md"}）
    def __init__(self, tool_name: str, params: dict[str, Any]) -> None:
        super().__init__()
        
        # 工具名
        self._tool_name = tool_name
        
        # 工具调用参数（原始字典）
        self._params = params
        
        # 参数的完整 JSON 字符串（用于展开时显示）
        self._params_full = _params_str(params)
        
        # 工具执行结果输出
        self._output = ""
        
        # 执行耗时（毫秒）
        self._elapsed_ms = 0
        
        # 是否为错误状态
        self._is_error = False
        
        # 是否执行完成
        self._finished = False

    # 组合子组件（textual 的组件组合机制）
    def compose(self) -> ComposeResult:
        # 摘要行（始终显示）
        yield Static(self._summary(), classes="summary")
        
        # 详情行（默认隐藏，点击展开后显示）
        yield Static("", classes="detail")

    # 生成摘要行文本
    # 返回值: 摘要字符串（如 "tool read_file path='README.md' done 500ms"）
    def _summary(self) -> str:
        # 特殊处理 note_save：成功完成时显示 "remembered"
        if self._tool_name == "note_save" and self._finished and not self._is_error:
            return f"  [green]remembered[/green]  [dim]{self._elapsed_ms}ms[/dim]"

        # 生成参数摘要
        params_pre = _param_summary(self._tool_name, self._params)
        
        # 构建基本摘要行："tool <工具名> <参数>"
        line = f"  [dim]tool[/dim] [bold]{self._tool_name}[/bold]"
        if params_pre:
            line += f"  [dim]{params_pre}[/dim]"
        
        # 如果已完成，添加状态和耗时
        if self._finished:
            # 错误显示红色，成功显示绿色
            color = "red" if self._is_error else "green"
            status = "failed" if self._is_error else "done"
            
            # 如果有输出，添加展开提示
            hint = "  [dim](click to expand)[/dim]" if self._output else ""
            
            # 添加状态和耗时
            line += f"  [{color}]{status}[/{color}]  [dim]{self._elapsed_ms}ms[/dim]{hint}"
        
        return line

    # 工具调用完成时更新结果并刷新摘要
    # 参数 output: 工具执行结果
    # 参数 elapsed_ms: 执行耗时（毫秒）
    # 参数 is_error: 是否为错误（默认 False）
    # 返回值: 无
    def set_result(self, output: str, elapsed_ms: int, *, is_error: bool = False) -> None:
        # 更新输出
        self._output = output
        
        # 更新耗时
        self._elapsed_ms = elapsed_ms
        
        # 更新错误状态
        self._is_error = is_error
        
        # 标记为已完成
        self._finished = True
        
        # 如果组件已挂载（有子组件），更新摘要显示
        if self.children:
            self.query_one(".summary", Static).update(self._summary())

    # 点击时切换展开/折叠状态
    def on_click(self) -> None:
        # 如果未完成，不响应点击
        if not self._finished:
            return
        
        # 如果已经展开，折叠
        if "expanded" in self.classes:
            self.remove_class("expanded")
        else:
            # 如果未展开，展开并更新详情内容
            detail = self.query_one(".detail", Static)
            detail.update(
                f"[dim]params[/dim]\n{self._params_full}\n\n"  # 完整参数
                f"[dim]output[/dim]\n{self._output}\n\n"      # 输出结果
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"   # 耗时
            )
            # 添加 expanded 类（触发 CSS 显示详情）
            self.add_class("expanded")


# 权限选择控件（内联）
# 功能：在日志流中显示权限审批选项，用户通过键盘选择是否允许工具调用
# 特点：不需要 ModalScreen，直接挂载在界面中，键盘焦点自动转移
class PermissionSelect(Static):
    """内联权限选择控件：挂载在日志流中，键盘焦点无需 ModalScreen。"""

    # 允许获取焦点（键盘操作必需）
    can_focus = True

    # 默认 CSS 样式：高度自适应，内边距 0 2，底部边距 1
    DEFAULT_CSS = """
    PermissionSelect {
        height: auto;
        padding: 0 2;
        margin-bottom: 1;
    }
    """

    # 权限选项列表：(决策值, 显示标签, 快捷键提示)
    _CHOICES: tuple[tuple[str, str, str], ...] = (
        ("allow_once",   "Allow once",   "y / 1"),   # 允许一次（不保存到缓存）
        ("always_allow", "Always allow", "a / 2"),   # 始终允许（保存到缓存）
        ("deny_once",    "Deny",         "n / 3"),   # 拒绝一次（不保存到缓存）
        ("always_deny",  "Always deny",  "d / 4"),   # 始终拒绝（保存到缓存）
    )
    
    # 快捷键到决策值的映射
    _KEY_MAP: dict[str, str] = {
        "y": "allow_once",  "1": "allow_once",   # y 或 1 → 允许一次
        "a": "always_allow","2": "always_allow",  # a 或 2 → 始终允许
        "n": "deny_once",   "3": "deny_once",    # n 或 3 → 拒绝一次
        "d": "always_deny", "4": "always_deny",  # d 或 4 → 始终拒绝
    }

    # 用户作出权限决策时发布的消息（textual 的消息系统）
    # 宿主 App 通过监听此消息来处理权限审批响应
    class Decided(Message):
        # 初始化决策消息
        # 参数 widget: PermissionSelect 控件引用
        # 参数 tool_use_id: 工具调用 ID（用于 IPC 回复）
        # 参数 decision: 用户的决策（allow_once/always_allow/deny_once/always_deny）
        def __init__(self, widget: "PermissionSelect", tool_use_id: str, decision: str) -> None:
            self.widget = widget           # 控件引用（用于清理）
            self.tool_use_id = tool_use_id # 工具调用 ID（用于匹配 Core 的待审批请求）
            self.decision = decision       # 决策值
            super().__init__()

    # 初始化权限选择控件
    # 参数 tool_use_id: 工具调用 ID（用于 IPC 回复时匹配）
    def __init__(self, tool_use_id: str) -> None:
        super().__init__("")
        
        # 工具调用 ID（用于 IPC 回复）
        self._tool_use_id = tool_use_id
        
        # 当前光标位置（选中的选项索引）
        self._cursor = 0

    # 组件挂载时的回调（textual 生命周期）
    def on_mount(self) -> None:
        # 渲染 UI
        self.update(self._render_ui())
        
        # 获取焦点（让用户可以直接使用键盘操作）
        self.focus()
        
        # 记录调试日志
        log.debug(
            "PermissionSelect.on_mount  can_focus=%s  focused_after=%r",
            self.can_focus,
            self.app.focused,
        )
        
        # 在下一帧检查焦点是否真正转移（调试用）
        self.app.call_after_refresh(self._log_deferred_focus)

    # 在下一帧记录焦点状态（调试用）
    def _log_deferred_focus(self) -> None:
        log.debug(
            "PermissionSelect.deferred_focus  app.focused=%r  has_focus=%s  focusable=%s",
            self.app.focused,
            self.has_focus,
            self.focusable,
        )

    # 焦点到达时的回调（调试用）
    def on_focus(self, event: events.Focus) -> None:
        log.debug("PermissionSelect.on_focus  has_focus=%s  app.focused=%r", self.has_focus, self.app.focused)

    # 焦点离开时的回调（调试用）
    def on_blur(self, event: events.Blur) -> None:
        log.debug("PermissionSelect.on_blur  app.focused=%r", self.app.focused)

    # 生成带光标高亮的选项列表文本
    # 返回值: 选项列表的富文本字符串
    def _render_ui(self) -> str:
        # 存储每行文本
        lines: list[str] = []
        
        # 遍历所有选项
        for i, (_, label, key_hint) in enumerate(self._CHOICES):
            # 如果是当前光标位置，高亮显示
            if i == self._cursor:
                lines.append(f"  [bold cyan]❯ {label}[/bold cyan]  [dim]{key_hint}[/dim]")
            else:
                lines.append(f"    {label}  [dim]{key_hint}[/dim]")
        
        # 添加操作提示
        lines.append("[dim]  ↑↓ navigate   enter confirm[/dim]")
        
        # 拼接所有行
        return "\n".join(lines)

    # 键盘事件处理：方向键导航、快捷键直接选择、Enter 确认
    def on_key(self, event: events.Key) -> None:
        log.debug("PermissionSelect.on_key  key=%r  char=%r", event.key, event.character)
        
        key = event.key
        
        # 上方向键或 k 键：向上移动光标
        if key in ("up", "k"):
            event.stop()  # 阻止事件冒泡
            self._cursor = (self._cursor - 1) % len(self._CHOICES)
            self.update(self._render_ui())
        
        # 下方向键或 j 键：向下移动光标
        elif key in ("down", "j"):
            event.stop()  # 阻止事件冒泡
            self._cursor = (self._cursor + 1) % len(self._CHOICES)
            self.update(self._render_ui())
        
        # Enter 键：确认当前光标位置的选项
        elif key == "enter":
            event.stop()  # 阻止事件冒泡
            self._pick(self._CHOICES[self._cursor][0])
        
        # 其他键：检查是否为快捷键
        else:
            decision = self._KEY_MAP.get(key)
            if decision is not None:
                event.stop()  # 阻止事件冒泡
                self._pick(decision)

    # 用户选择后发布决策消息
    # 参数 decision: 用户的决策（allow_once/always_allow/deny_once/always_deny）
    # 返回值: 无
    # 注意：这里只发布消息，不直接发送 IPC 响应
    # 宿主 App 监听 Decided 消息后，负责发送 IPC 响应和清理控件
    def _pick(self, decision: str) -> None:
        log.debug("PermissionSelect._pick  decision=%s", decision)
        
        # 发布 Decided 消息（宿主 App 会监听此消息）
        self.post_message(self.Decided(self, self._tool_use_id, decision))


# 权限审批摘要块组件
# 功能：在日志流中显示权限审批的摘要状态
# 状态：待审批（显示工具名和参数）→ 已解决（显示审批结果）
class PermissionBlock(Static):
    """日志里的权限审批摘要"""

    # 决策值到显示标签的映射
    _LABEL_MAP: dict[str, str] = {
        "allow_once":   "allowed (once)",   # 允许一次
        "always_allow": "always allowed",   # 始终允许
        "deny_once":    "denied",           # 拒绝一次
        "always_deny":  "always denied",    # 始终拒绝
        "timeout":      "⏱ timed out",      # 超时
    }
    # 暴露为类属性（供外部使用）
    LABEL_MAP = _LABEL_MAP

    # 权限决策已解决时发布的消息（textual 的消息系统）
    class Resolved(Message):
        def __init__(self, block: PermissionBlock, decision: str) -> None:
            self.block = block       # PermissionBlock 控件引用
            self.decision = decision # 决策值
            super().__init__()

    # 初始化权限审批块
    # 参数 tool_use_id: 工具调用 ID
    # 参数 tool_name: 工具名（如 "bash"）
    # 参数 param_preview: 参数预览（如 "command='rm -rf /'"）
    def __init__(self, tool_use_id: str, tool_name: str, param_preview: str) -> None:
        # 工具调用 ID（用于匹配）
        self._tool_use_id = tool_use_id
        
        # 工具名
        self._tool_name = tool_name
        
        # 参数预览
        self._param_preview = param_preview
        
        # 是否已解决
        self._resolved = False
        
        # 调用父类构造函数，初始显示待审批文本
        super().__init__(self._pending_text(), classes="log-line")

    # 生成待审批状态的文本
    # 返回值: 待审批状态的富文本字符串
    def _pending_text(self) -> str:
        # 如果有参数预览，添加到文本中
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        
        # 返回格式："? permission  <工具名>  <参数预览>"
        return f"[bold red]? permission[/bold red]  [bold]{self._tool_name}[/bold]{preview}"

    # 将审批块收缩为单行摘要（审批完成后调用）
    # 参数 decision: 用户的决策（allow_once/always_allow/deny_once/always_deny/timeout）
    # 返回值: 无
    def _resolve(self, decision: str) -> None:
        # 如果已解决，跳过
        if self._resolved:
            return
        
        # 标记为已解决
        self._resolved = True
        
        # 判断是否允许（allow_once 和 always_allow 都允许）
        allowed = decision in ("allow_once", "always_allow")
        
        # 根据是否允许选择图标（✓ 或 ✗）
        icon = "[bold green]✓[/bold green]" if allowed else "[bold red]✗[/bold red]"
        
        # 获取决策的显示标签
        label = self._LABEL_MAP.get(decision, decision)
        
        # 如果有参数预览，添加到文本中
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        
        # 更新显示文本
        # 格式："✓ permission  <工具名>  <参数预览>  <标签>"
        self.update(
            f"{icon} permission  [bold]{self._tool_name}[/bold]{preview}  [dim]{label}[/dim]"
        )
        
        # 发布 Resolved 消息（宿主 App 可监听此消息）
        self.post_message(self.Resolved(self, decision))


# 聊天输入框组件（多行）
# 功能：支持 Enter 提交消息，Cmd/Shift/Alt+Enter 插入换行
class ChatTextArea(TextArea):
    """支持 Enter 提交、Cmd/Shift/Alt+Enter 换行的多行聊天输入框。"""

    # 默认 CSS 样式：
    # - 高度自适应，最小高度 3，最大高度 12
    # - 圆角边框，背景色使用主题背景
    # - 内边距 0 1，外边距 1 2
    # - 聚焦时边框颜色变为强调色
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

    # 用户提交消息时发布的消息（textual 的消息系统）
    # 宿主 App 通过监听此消息来处理用户输入
    class Submitted(Message):
        def __init__(self, area: ChatTextArea) -> None:
            self.text_area = area  # 输入框控件引用
            self.value = area.text  # 输入的文本内容
            super().__init__()

    # 键盘事件处理：Enter 提交，组合键换行，其余交回父类
    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        
        # Enter 键：提交消息
        if key == "enter":
            event.stop()           # 阻止事件冒泡
            event.prevent_default() # 阻止默认行为（不插入换行）
            
            # 如果文本不为空，发布 Submitted 消息
            if self.text.strip():
                self.post_message(self.Submitted(self))
            return
        
        # Cmd/Shift/Alt+Enter 或 Ctrl+J：插入换行
        if key in ("alt+enter", "shift+enter", "ctrl+j", "super+enter"):
            event.stop()           # 阻止事件冒泡
            event.prevent_default() # 阻止默认行为
            
            # 如果不是只读模式，插入换行
            if not self.read_only:
                self.insert("\n")
            return
        
        # 其他键：交给父类 TextArea 处理
        await super()._on_key(event)


# KamaClaude TUI 主应用类
# 功能：终端滚屏风格的聊天界面，实时展示 agent 执行过程
# 组件：头部状态栏 + 日志滚动视图 + 聊天输入框
class KamaTuiApp(App[None]):
    """KamaClaude TUI：终端滚屏风格，实时展示 agent 执行过程。"""

    # 应用标题（显示在终端标题栏）
    TITLE = "KamaClaude"
    
    # 键盘快捷键绑定：Ctrl+Q 退出
    BINDINGS = [
        Binding("ctrl+q", "quit", "quit"),
    ]
    
    # 应用 CSS 样式：
    # - Screen：背景色使用主题背景
    # - #header：高度 1，背景色使用主题表面色，内边距 0 1
    # - #log-view：高度占满剩余空间，显示滚动条
    # - .user-turn：用户消息样式
    # - .run-header：run 头部样式
    # - .step-divider：步骤分隔线样式
    # - .run-ok/run-err：run 完成/失败样式
    # - .usage：token 使用量样式
    # - .log-line：日志行样式
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

    # 初始化连接参数和 TUI 内部状态
    # 参数 host: Core daemon 的主机地址（默认 localhost）
    # 参数 port: Core daemon 的端口（默认 10389）
    # 参数 replay_run_id: 可选的回放 run ID（用于回放历史事件）
    def __init__(self, host: str, port: int, replay_run_id: str | None = None) -> None:
        super().__init__()
        
        # Core daemon 的主机地址
        self._host = host
        
        # Core daemon 的端口
        self._port = port
        
        # 可选的回放 run ID（用于调试，回放指定 run 的历史事件）
        self._replay_run_id = replay_run_id
        
        # SocketClient 实例（用于与 Core daemon 通信）
        self._client: SocketClient | None = None
        
        # 当前正在显示的 LLM 流式块（用于累积 token）
        self._current_llm: LLMStreamBlock | None = None
        
        # 待完成的工具调用块映射：tool_use_id → ToolCallBlock
        # 用于工具调用完成时更新结果
        self._pending_tool_blocks: dict[str, ToolCallBlock] = {}
        
        # 待解决的权限审批块映射：tool_use_id → PermissionBlock
        # 用于权限审批完成时更新状态
        self._pending_permission_blocks: dict[str, PermissionBlock] = {}
        
        # 当前会话 ID（连接成功后由 Core 返回）
        self._session_id: str | None = None
        
        # agent 是否正在工作（防止重复发送消息）
        self._busy = False

    # 组合应用组件（textual 的组件组合机制）
    def compose(self) -> ComposeResult:
        # 头部状态栏（显示应用名和连接状态）
        yield Label("[bold]KamaClaude[/bold]  [dim]connecting...[/dim]", id="header")
        
        # 日志滚动视图（显示聊天记录、工具调用、权限审批等）
        yield VerticalScroll(id="log-view")
        
        # 聊天输入框（多行，Enter 提交）
        yield ChatTextArea(id="prompt", show_line_numbers=False)

    # 应用挂载时的回调（textual 生命周期）
    def on_mount(self) -> None:
        # 启动 socket 连接工作线程（exclusive=True 表示同一时间只有一个实例）
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")
        
        # 获取输入框并禁用（连接完成后启用）
        prompt = self.query_one("#prompt", ChatTextArea)
        prompt.disabled = True
        prompt.border_title = "connecting..."

    # 全局键盘事件处理（兜底）
# 当 PermissionSelect 失去焦点后，作为兜底处理权限快捷键
# 确保用户即使焦点不在权限选择控件上，也能通过键盘操作权限审批
    def on_key(self, event: events.Key) -> None:
        log.debug("App.on_key  key=%r  focused=%r", event.key, self.focused)
        
        # 如果没有待审批的权限请求，跳过
        if not self._pending_permission_blocks:
            return
        
        try:
            # 获取权限选择控件
            select = self.query_one(PermissionSelect)
            
            # 如果权限选择控件有焦点，让它自行处理（事件不会冒泡到这里）
            if select.has_focus:
                return
            
            # 获取按键
            key = event.key
            
            # 检查是否为权限快捷键
            decision = PermissionSelect._KEY_MAP.get(key)
            if decision:
                event.stop()
                select._pick(decision)
            
            # 上方向键或 k 键：向上移动光标
            elif key in ("up", "k"):
                event.stop()
                select._cursor = (select._cursor - 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            
            # 下方向键或 j 键：向下移动光标
            elif key in ("down", "j"):
                event.stop()
                select._cursor = (select._cursor + 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            
            # Enter 键：确认当前光标位置的选项
            elif key == "enter":
                event.stop()
                select._pick(PermissionSelect._CHOICES[select._cursor][0])
        
        # 如果发生异常（如控件不存在），忽略
        except Exception:
            pass

    # 退出应用时的回调（绑定到 Ctrl+Q）
    # 退出前尽力关闭当前 session（发送 session.close 命令）
    # 失败也不阻塞 TUI 退出
    async def action_quit(self) -> None:
        # 如果已连接且有 session_id，发送关闭命令
        if self._client is not None and self._session_id is not None:
            try:
                await self._client.send_command("session.close", {"session_id": self._session_id})
            except (IpcError, RuntimeError, OSError):
                # 关闭失败，显示警告
                self._append(Static("[yellow]warning: failed to close session[/yellow]"))
        
        # 退出应用
        self.exit()

    # 处理聊天输入框提交事件（用户按 Enter 发送消息）
    # 使用 worker 发送消息，避免 await 阻塞 App 消息泵
    async def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        # 获取用户输入内容并去除首尾空白
        content = event.value.strip()
        
        # 如果内容为空，跳过
        if not content:
            return
        
        # 检查条件：必须已连接、有 session_id、agent 不忙
        if self._client is None or self._session_id is None or self._busy:
            self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return
        
        # 标记 agent 正忙（防止重复发送）
        self._busy = True
        
        # 获取输入框并清空
        prompt = event.text_area
        prompt.text = ""
        
        # 禁用输入框（agent 运行期间不接受输入）
        prompt.disabled = True
        prompt.read_only = False
        prompt.border_title = "agent is working..."
        
        # 在日志视图中显示用户消息
        self._append(Static(f"[bold]>[/bold] {content}", classes="user-turn"))
        
        # 更新头部状态为 running
        self._update_header("running")
        
        # 在 worker 中发送消息（避免阻塞 App 消息泵）
        # exclusive=False 表示允许多个 send_message worker 同时运行（虽然实际被 _busy 限制）
        self.run_worker(self._do_send_message(content), name="send_message", exclusive=False)

    # 在 worker 中执行 IPC 发送
    # 使 App 消息泵在 agent 运行期间仍能处理键盘/焦点等消息
    async def _do_send_message(self, content: str) -> None:
        # 如果客户端未连接，返回
        if self._client is None:
            return
        
        try:
            # 发送 session.send_message 命令到 Core daemon
            await self._client.send_command(
                "session.send_message",
                {"session_id": self._session_id, "content": content},
            )
            
            # 注意：这里不等待 run 完成（run 完成通过事件通知）
            # 发送命令后立即返回，App 消息泵继续运行
            
        except (IpcError, RuntimeError, OSError) as e:
            # 发送失败，恢复状态
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.read_only = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
            
            # 更新头部状态为 ready
            self._update_header("ready")
            
            # 显示错误消息
            self._append(Static(f"[red]send error: {e}[/red]", classes="log-line"))

    # 处理权限选择控件的用户决策（监听 PermissionSelect.Decided 消息）
    # 流程：发送 IPC 响应 → 更新权限审批块 → 恢复输入框
    async def on_permission_select_decided(self, msg: PermissionSelect.Decided) -> None:
        # 获取工具调用 ID 和用户决策
        tool_use_id = msg.tool_use_id
        decision = msg.decision
        
        # 记录日志
        log.info("permission decided tool_use_id=%s decision=%s", tool_use_id, decision)
        
        try:
            # 移除权限选择控件（已完成选择）
            msg.widget.remove()
            
            # 获取对应的权限审批块并更新状态
            perm_block = self._pending_permission_blocks.pop(tool_use_id, None)
            if perm_block is not None:
                perm_block._resolve(decision)
            
            # 如果客户端已连接，发送权限响应到 Core daemon
            if self._client is not None:
                try:
                    await self._client.send_command(
                        "permission.respond",
                        {"tool_use_id": tool_use_id, "decision": decision},
                    )
                except (IpcError, RuntimeError, OSError):
                    # 发送失败不影响 UI 更新
                    pass
            
            # 如果没有其他待审批请求，恢复输入框
            if not self._pending_permission_blocks:
                p = self._prompt()
                if p is not None:
                    p.disabled = False
                    p.read_only = False
                    p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    p.focus()
        
        except Exception:
            # 记录异常但不崩溃
            log.exception("on_permission_select_decided failed tool_use_id=%s", tool_use_id)

    # 向日志视图追加一个 widget 并滚动到底部
    def _append(self, widget: Widget) -> None:
        # 获取日志滚动视图控件
        log_view = self.query_one("#log-view", VerticalScroll)
        
        # 挂载新 widget（追加到视图末尾）
        log_view.mount(widget)
        
        # 滚动到底部（不带动画，立即显示最新内容）
        log_view.scroll_end(animate=False)

    # 结束当前 LLM 流式块（下一个 token 将开启新块）
    # 调用时机：收到非 llm.token 事件时（如工具调用、权限请求等）
    def _break_llm(self) -> None:
        # 如果有当前 LLM 块，完成 markdown 渲染
        if self._current_llm is not None:
            self._current_llm.finalize_markdown()
        
        # 重置当前 LLM 块为 None（下一个 token 将创建新块）
        self._current_llm = None

    # 将权限选择控件挂载到 Screen 顶层（#prompt 之前）
    # 原因：避免与 VerticalScroll 争抢焦点，确保用户能正常操作权限选择
    def _mount_permission_select(self, select: PermissionSelect) -> None:
        self.mount(select, before="#prompt")

    # 安全获取输入框控件（封装异常处理）
    # 返回值: ChatTextArea 实例或 None
    def _prompt(self) -> ChatTextArea | None:
        try:
            return self.query_one("#prompt", ChatTextArea)
        except Exception:
            return None

    # 根据连接和运行状态刷新顶部标题栏
    # 参数 state: 状态字符串（ready/running/disconnected/connecting）
    def _update_header(self, state: str) -> None:
        try:
            # 获取头部控件
            header = self.query_one("#header", Label)
        except NoMatches:
            # 如果控件不存在（如测试中），返回
            return
        
        # 如果有 session_id，添加到标题中（暗淡显示）
        session = f"  [dim]{self._session_id}[/dim]" if self._session_id else ""
        
        # 根据状态选择颜色
        color = {
            "ready": "green",      # 就绪（绿色）
            "running": "yellow",   # 运行中（黄色）
            "disconnected": "red", # 断开连接（红色）
            "connecting": "dim",   # 连接中（暗淡）
        }.get(state, "dim")
        
        # 更新头部文本
        # 格式："KamaClaude  host:port  session_id  state"
        header.update(
            f"[bold]KamaClaude[/bold]  [dim]{self._host}:{self._port}[/dim]"
            f"{session}  [{color}]{state}[/{color}]"
        )

    # 管理 SocketClient 生命周期：连接、订阅事件、断线重连
    # 这是 TUI 与 Core daemon 通信的核心方法
    async def _socket_loop(self) -> None:
        # 获取头部控件（用于显示连接状态）
        header = self.query_one("#header", Label)

        # 无限循环：断线后自动重连
        while True:
            # 创建新的 SocketClient 实例
            client = SocketClient(self._host, self._port)
            
            # 将 _client 置为 None（表示未连接状态）
            self._client = None
            
            try:
                # 连接到 Core daemon
                await client.connect()
            except (ConnectionRefusedError, OSError):
                # 连接被拒绝或发生网络错误
                log.warning("connection refused %s:%s, retrying", self._host, self._port)
                self._update_header("disconnected")
                
                # 等待 2 秒后重试
                await asyncio.sleep(2)
                continue

            # 连接成功
            log.info("connected to %s:%s", self._host, self._port)
            self._client = client
            self._update_header("connecting")
            
            # 启动 SocketClient 的事件循环（后台运行，监听来自 Core 的事件）
            loop_task = asyncio.create_task(client.run_event_loop())

            # 定义事件回调函数：当收到 Core 发来的事件时调用
            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event)

            # 注册事件回调（SocketClient 收到事件后会调用此函数）
            client.on_event(on_event)

            try:
                # 为 loop_task 添加完成回调（用于记录异常）
                loop_task.add_done_callback(
                    lambda t: log.error("loop_task failed: %s", t.exception())
                    if not t.cancelled() and t.exception() is not None
                    else None
                )
                
                # 准备事件订阅参数：订阅所有相关主题
                params: dict[str, Any] = {
                    "topics": [
                        "session.*",    # 会话相关事件
                        "run.*",        # run 相关事件
                        "step.*",       # 步骤相关事件
                        "tool.*",       # 工具调用相关事件
                        "llm.token",    # LLM token 事件
                        "llm.usage",    # LLM token 使用量事件
                        "log.*",        # 日志事件
                        "permission.*", # 权限相关事件
                    ],
                    "scope": "global",  # 全局范围（接收所有事件）
                }
                
                # 如果是回放模式，添加回放参数
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                
                # 发送事件订阅命令到 Core
                await client.send_command("event.subscribe", params)
                
                # 创建聊天会话（发送 session.create 命令）
                created = await client.send_command("session.create", {"mode": "chat"})
                
                # 保存 session_id（后续发送消息需要）
                self._session_id = str(created["session_id"])
                log.info("session created session_id=%s", self._session_id)
                
                # 启用输入框（连接完成，可以开始聊天）
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = False
                    prompt.read_only = False
                    prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    prompt.focus()
                
                # 更新头部状态为 ready
                self._update_header("ready")
                
                # 等待 loop_task 结束（正常情况下不会结束，除非断线）
                await loop_task
                
            except IpcError as e:
                # IPC 错误（如订阅失败）
                header.update(f"[bold]KamaClaude[/bold]  [red]subscribe error: {e}[/red]")
                
            finally:
                # 清理资源
                # 如果 loop_task 还没结束，取消它
                if not loop_task.done():
                    loop_task.cancel()
                
                # 重置状态
                self._client = None
                self._session_id = None
                
                # 禁用输入框
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = True
                    prompt.read_only = False
                    prompt.border_title = "disconnected, retrying..."
                
                # 结束当前 LLM 块
                self._break_llm()
                
                # 关闭客户端连接
                await client.close()

            # 更新头部状态为 disconnected
            self._update_header("disconnected")
            
            # 等待 2 秒后重试连接
            await asyncio.sleep(2)

    # 根据事件 type 路由到对应渲染逻辑
    # 捕获异常防止 socket loop 因单个事件崩溃
    def _handle_event(self, event: dict[str, Any]) -> None:
        try:
            # 调用实际的事件处理逻辑
            self._handle_event_inner(event)
        except Exception:
            # 记录异常但不崩溃（防止单个坏事件影响整个 TUI）
            log.exception("_handle_event crashed  event_type=%s", event.get("type", "?"))

    # 实际的事件路由逻辑（根据事件类型分发到不同处理）
    def _handle_event_inner(self, event: dict[str, Any]) -> None:
        # 获取事件类型
        t = event.get("type", "")

        # ==================== LLM token 事件 ====================
        # 处理 LLM 流式输出的单个 token
        if t == "llm.token":
            # 获取 token 内容
            token = event.get("token", "")
            
            # 如果没有当前 LLM 块，创建一个新的
            if self._current_llm is None:
                llm_block = LLMStreamBlock()
                self._append(llm_block)
                self._current_llm = llm_block
            
            # 将 token 追加到当前 LLM 块
            self._current_llm.append_token(token)
            return

        # 非 token 事件：结束当前 LLM 块（下一个 token 将创建新块）
        self._break_llm()

        # ==================== 会话事件 ====================
        # session.waiting_for_input：agent 等待用户输入（run 完成）
        if t == "session.waiting_for_input":
            # 标记 agent 不忙
            self._busy = False
            
            # 启用输入框
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.read_only = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                prompt.focus()
            
            # 更新头部状态为 ready
            self._update_header("ready")

        # session.closed：会话已关闭
        elif t == "session.closed":
            # 标记 agent 不忙
            self._busy = False
            
            # 禁用输入框
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.read_only = False
                prompt.border_title = "session closed"
            
            # 更新头部状态为 disconnected
            self._update_header("disconnected")

        # ==================== Run 事件 ====================
        # run.started：run 开始
        elif t == "run.started":
            run_id = event.get("run_id", "")
            goal = event.get("goal", "")
            
            # 追加 run 头部信息
            self._append(Static(
                f"[dim]run[/dim]  [cyan]{run_id}[/cyan]  [dim]{_preview(goal, 96)}[/dim]",
                classes="run-header",
            ))

        # step.started：步骤开始
        elif t == "step.started":
            step = event.get("step", "")
            
            # 追加步骤分隔线
            self._append(Static(
                f"[dim]step {step}[/dim]",
                classes="step-divider",
            ))

        # ==================== 工具调用事件 ====================
        # tool.call_started：工具调用开始
        elif t == "tool.call_started":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            params = event.get("params") or {}
            
            # 创建工具调用块
            tc_block = ToolCallBlock(tool_name, params)
            
            # 保存到待完成映射（用于后续更新结果）
            self._pending_tool_blocks[tool_use_id] = tc_block
            
            # 追加到日志视图
            self._append(tc_block)

        # tool.call_finished：工具调用完成
        elif t == "tool.call_finished":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            output = str(event.get("output") or "")
            
            # 如果有对应的工具调用块，更新结果
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(output, elapsed_ms)

        # tool.call_failed：工具调用失败
        elif t == "tool.call_failed":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            error_msg = str(event.get("error_message") or "")
            
            # 如果有对应的工具调用块，更新错误结果
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(error_msg, elapsed_ms, is_error=True)

        # ==================== Run 结束事件 ====================
        # run.finished：run 结束
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

        # ==================== LLM 使用量事件 ====================
        # llm.usage：LLM token 使用量
        elif t == "llm.usage":
            self._append(Static(
                f"[dim]  tokens  "
                f"in={event.get('input_tokens')} "       # 输入 token 数
                f"out={event.get('output_tokens')} "     # 输出 token 数
                f"cache={event.get('cache_read_input_tokens')}[/dim]",  # 缓存读取 token 数
                classes="usage",
            ))

        # ==================== 权限事件 ====================
        # permission.requested：权限请求（需要用户审批）
        elif t == "permission.requested":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            param_preview = str(event.get("param_preview", ""))
            
            try:
                _focused_repr = repr(self.focused)
            except Exception:
                _focused_repr = "?"
            
            # 记录日志
            log.info(
                "permission.requested tool=%s id=%s  app.focused=%s",
                tool_name, tool_use_id, _focused_repr,
            )
            
            # 创建权限审批块
            perm_block = PermissionBlock(tool_use_id, tool_name, param_preview)
            
            # 保存到待解决映射
            self._pending_permission_blocks[tool_use_id] = perm_block
            
            # 禁用输入框（等待权限审批）
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.border_title = "permission required"
            
            # 追加权限审批块到日志视图
            self._append(perm_block)
            
            # 创建权限选择控件并挂载到 Screen 顶层
            select = PermissionSelect(tool_use_id)
            self._mount_permission_select(select)
            
            log.debug("PermissionSelect mounted before #prompt  pending=%d", len(self._pending_permission_blocks))

        # permission.denied：权限被拒绝（超时或断连等非用户交互触发）
        # 用户主动 deny 已由 on_permission_select_decided 处理
        elif t == "permission.denied":
            tool_use_id = str(event.get("tool_use_id", ""))
            decision = str(event.get("decision", "denied"))
            
            # 如果有对应的权限审批块，更新状态
            if tool_use_id in self._pending_permission_blocks:
                perm_block = self._pending_permission_blocks.pop(tool_use_id, None)
                if perm_block is not None:
                    perm_block._resolve(decision)
                
                # 移除权限选择控件
                try:
                    select = self.query_one(PermissionSelect)
                    select.remove()
                except Exception:
                    pass
                
                # 如果没有其他待审批请求，恢复输入框
                if not self._pending_permission_blocks:
                    p = self._prompt()
                    if p is not None:
                        p.disabled = False
                        p.read_only = False
                        p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                        p.focus()

        # ==================== 日志事件 ====================
        # log.line：日志行
        elif t == "log.line":
            level = event.get("level", "INFO")
            
            # 根据日志级别选择颜色
            color = "bold red" if level == "ERROR" else ("yellow" if level == "WARNING" else "dim")
            
            # 追加日志行到视图
            self._append(Static(
                f"[{color}]{level}[/{color}]  "
                f"[dim]{event.get('source', '')}[/dim]  {event.get('message', '')}",
                classes="log-line",
            ))


# TUI 入口函数：读取配置并启动 KamaTuiApp
def run(config: KamaConfig, replay_run_id: str | None = None) -> None:
    # 创建 KamaTuiApp 实例
    app = KamaTuiApp(config.host, config.port, replay_run_id=replay_run_id)
    
    # 启动 TUI 应用（阻塞直到应用退出）
    app.run()
