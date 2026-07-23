"""
TUI 主应用模块

该模块实现了 IwanClaude 的终端用户界面，基于 Textual 框架构建。

核心功能：
- 实时展示 Agent 执行过程（运行日志、工具调用、LLM 输出）
- 支持流式 LLM 输出，逐 token 渲染
- 内联权限审批，无需弹窗即可授权工具调用
- 斜杠命令自动补全，支持技能触发和系统命令
- 检查点管理，支持列出和恢复检查点
- 会话历史搜索（Ctrl+R）
- 上下文压缩（/compact）

设计要点：
- 使用 Textual 框架构建终端界面
- 采用事件驱动架构，通过 IPC 与 core 服务通信
- 使用 Rich Markdown 渲染 LLM 输出
- 支持快捷键绑定（Ctrl+Q 退出、F6 检查点、Ctrl+R 历史搜索）
- 响应式布局，自动适应终端大小

核心组件：
- IwanTuiApp: TUI 主应用类
- LLMStreamBlock: 流式 LLM 输出块
- ToolCallBlock: 可折叠的工具调用块
- PermissionSelect: 内联权限选择控件
- SlashCompleteWidget: 斜杠命令自动补全弹窗
- ChatTextArea: 聊天输入框（支持 Enter 提交）

使用示例：
    >>> from iwan_claude.tui.app import run
    >>> from iwan_claude.core.config import get_config
    >>> config = get_config()
    >>> run(config)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

from rich.markdown import Markdown
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, Static, TextArea

from iwan_claude.core.config import IwanConfig
from iwan_claude.core.skills.loader import SkillLoader
from iwan_claude.core.transport.socket_client import IpcError, SocketClient


def _preview(s: str, n: int) -> str:
    """
    文本预览函数

    将字符串截断到指定长度，超出部分用省略号表示。

    参数：
        s: str - 原始字符串
        n: int - 最大长度

    返回：
        str: 截断后的字符串（如果超出长度则添加省略号）

    使用示例：
        >>> _preview("hello world", 5)
        "hello…"
        >>> _preview("hi", 5)
        "hi"
    """
    return s[:n] + "…" if len(s) > n else s


def _params_str(params: dict[str, Any]) -> str:
    """
    将工具参数字典转换为格式化的 JSON 字符串

    参数：
        params: dict[str, Any] - 工具参数字典

    返回：
        str: 格式化的 JSON 字符串（缩进 2，保留中文）

    使用示例：
        >>> _params_str({"path": "/etc/passwd"})
        '{\n  "path": "/etc/passwd"\n}'
    """
    return json.dumps(params, ensure_ascii=False, indent=2)


def _param_summary(tool_name: str, params: dict[str, Any], max_len: int = 72) -> str:
    """
    从工具参数中提取最适合摘要展示的关键字段

    根据工具名称选择关键参数，生成简短的参数摘要，用于工具调用块的摘要显示。

    参数：
        tool_name: str - 工具名称
        params: dict[str, Any] - 工具参数字典
        max_len: int - 最大长度（默认 72）

    返回：
        str: 参数摘要字符串

    关键参数映射：
        - read_file: path
        - write_file: path
        - list_dir: path, max_depth
        - bash: command
        - note_save: content

    使用示例：
        >>> _param_summary("bash", {"command": "ls -la /etc"})
        'command='ls -la /etc''
        >>> _param_summary("read_file", {"path": "/etc/passwd", "encoding": "utf-8"})
        'path='/etc/passwd''
    """
    keys_by_tool = {
        "read_file": ("path",),
        "write_file": ("path",),
        "list_dir": ("path", "max_depth"),
        "bash": ("command",),
        "note_save": ("content",),
    }
    keys = keys_by_tool.get(tool_name, ())
    parts = [f"{key}={params[key]!r}" for key in keys if key in params]
    if not parts:
        parts = [f"{key}={value!r}" for key, value in list(params.items())[:2]]
    return _preview(", ".join(parts), max_len)


class LLMStreamBlock(Static):
    """
    LLM 流式输出块

    在同一个 Static widget 中累积 LLM 流式 token，支持逐 token 渲染和最终 Markdown 格式化。

    工作原理：
    1. 初始化时创建空文本块
    2. 通过 append_token() 逐 token 追加文本并更新显示
    3. 通过 finalize_markdown() 将累积的文本渲染为 Markdown

    设计要点：
    - 使用 _text 存储累积的原始文本
    - 使用 _finalized 标记是否已完成渲染
    - 完成后使用 Rich Markdown 渲染，支持代码高亮（monokai 主题）

    使用示例：
        >>> block = LLMStreamBlock()
        >>> block.append_token("Hello")
        >>> block.append_token(" World")
        >>> block.finalize_markdown()
    """

    DEFAULT_CSS = "LLMStreamBlock { padding: 0 2; color: $text; }"

    def __init__(self) -> None:
        """
        初始化 LLM 流式输出块

        属性：
            _text: 累积的原始文本，初始为空字符串
            _finalized: 是否已完成渲染，初始为 False
        """
        super().__init__("")
        self._text = ""
        self._finalized = False

    def append_token(self, token: str) -> None:
        """
        追加一个 token 并刷新显示

        将 token 追加到累积文本中，并更新 widget 显示。

        参数：
            token: str - LLM 生成的单个 token

        实现细节：
        - 如果已完成渲染（_finalized=True），则忽略后续 token
        - 使用 update() 更新 widget 显示，触发 Textual 重绘

        使用示例：
            >>> block.append_token("Hello")
            >>> block.append_token(" ")
            >>> block.append_token("World")
        """
        if self._finalized:
            return
        self._text += token
        self.update(self._text)

    def finalize_markdown(self) -> None:
        """
        将累积文本渲染为 Markdown，供流式块结束后显示

        将累积的原始文本转换为 Rich Markdown 对象，实现代码高亮等格式化效果。

        实现细节：
        - 如果已完成渲染，则直接返回
        - 设置 _finalized 为 True，防止后续 token 覆盖
        - 如果文本非空，使用 Markdown 渲染，代码主题使用 monokai
        - 如果文本为空，保持空显示

        使用示例：
            >>> block = LLMStreamBlock()
            >>> block.append_token("# Title")
            >>> block.append_token("\n\nHello **world**")
            >>> block.finalize_markdown()
        """
        if self._finalized:
            return
        self._finalized = True
        if self._text.strip():
            self.update(Markdown(self._text, code_theme="monokai"))


class ToolCallBlock(Widget):
    """
    可折叠的工具调用块

    折叠时显示工具调用摘要（工具名称、关键参数、状态），点击后展开显示完整的参数和输出。

    工作原理：
    1. 初始化时创建摘要和详情两个子 widget
    2. 工具调用完成时通过 set_result() 更新结果
    3. 点击时切换 expanded CSS 类，控制详情的显示/隐藏

    设计要点：
    - 使用 CSS 类控制展开/折叠状态
    - 摘要显示关键信息，详情显示完整参数和输出
    - note_save 工具特殊处理，显示 "remembered" 标签
    - 支持错误状态显示（红色 failed）和成功状态显示（绿色 done）

    使用示例：
        >>> block = ToolCallBlock("bash", {"command": "ls -la"})
        >>> block.set_result("file1.txt\\nfile2.txt", 123)
        >>> # 点击后展开显示完整参数和输出
    """

    DEFAULT_CSS = """
    ToolCallBlock { height: auto; padding: 0 2; color: $text-muted; }
    ToolCallBlock > .summary { color: $text-muted; }
    ToolCallBlock > .detail { display: none; padding: 0 2 0 4; color: $text-muted; }
    ToolCallBlock.expanded > .detail { display: block; }
    """

    def __init__(self, tool_name: str, params: dict[str, Any]) -> None:
        """
        初始化工具调用块

        参数：
            tool_name: str - 工具名称
            params: dict[str, Any] - 工具参数

        属性：
            _tool_name: 工具名称
            _params: 工具参数字典
            _params_full: 格式化后的完整参数 JSON 字符串
            _output: 工具输出内容，初始为空字符串
            _elapsed_ms: 执行耗时（毫秒），初始为 0
            _is_error: 是否为错误状态，初始为 False
            _finished: 是否已完成，初始为 False
        """
        super().__init__()
        self._tool_name = tool_name
        self._params = params
        self._params_full = _params_str(params)
        self._output = ""
        self._elapsed_ms = 0
        self._is_error = False
        self._finished = False

    def compose(self) -> ComposeResult:
        """
        组合子 widget

        创建摘要和详情两个子 widget：
        - .summary: 显示工具调用摘要
        - .detail: 显示完整参数和输出（默认隐藏）

        返回：
            ComposeResult: 子 widget 生成器
        """
        yield Static(self._summary(), classes="summary")
        yield Static("", classes="detail")

    def _summary(self) -> str:
        """
        生成摘要行文本

        根据工具名称、参数和状态生成摘要行，用于折叠状态下的显示。

        返回：
            str: 格式化的摘要文本

        特殊处理：
        - note_save 工具完成且无错误时显示 "remembered" 标签
        - 其他工具显示工具名称、参数摘要和状态

        显示格式：
            "tool" tool_name params_pre status elapsed_ms (click to expand)
        """
        if self._tool_name == "note_save" and self._finished and not self._is_error:
            return f"  [green]remembered[/green]  [dim]{self._elapsed_ms}ms[/dim]"

        params_pre = _param_summary(self._tool_name, self._params)
        line = f"  [dim]tool[/dim] [bold]{self._tool_name}[/bold]"
        if params_pre:
            line += f"  [dim]{params_pre}[/dim]"
        if self._finished:
            color = "red" if self._is_error else "green"
            status = "failed" if self._is_error else "done"
            hint = "  [dim](click to expand)[/dim]" if self._output else ""
            line += f"  [{color}]{status}[/{color}]  [dim]{self._elapsed_ms}ms[/dim]{hint}"
        return line

    def set_result(self, output: str, elapsed_ms: int, *, is_error: bool = False) -> None:
        """
        工具调用完成时更新结果并刷新摘要

        更新工具调用的结果、耗时和状态，并刷新摘要显示。

        参数：
            output: str - 工具输出内容
            elapsed_ms: int - 执行耗时（毫秒）
            is_error: bool - 是否为错误状态，默认为 False

        实现细节：
        - 更新内部状态（_output, _elapsed_ms, _is_error, _finished）
        - 如果 widget 已挂载（有子 widget），更新摘要显示
        - 如果 widget 未挂载，跳过 DOM 更新（稍后挂载时会自动显示最新状态）
        """
        self._output = output
        self._elapsed_ms = elapsed_ms
        self._is_error = is_error
        self._finished = True
        if self.children:
            self.query_one(".summary", Static).update(self._summary())

    def on_click(self) -> None:
        """
        点击时切换展开/折叠状态

        如果工具调用已完成，点击切换展开或折叠状态：
        - 折叠状态：添加 expanded 类，显示详情
        - 展开状态：移除 expanded 类，隐藏详情

        实现细节：
        - 未完成时不响应点击
        - 展开时更新详情内容，包含完整参数、输出和耗时
        - 使用 CSS 类控制详情的显示/隐藏
        """
        if not self._finished:
            return
        if "expanded" in self.classes:
            self.remove_class("expanded")
        else:
            detail = self.query_one(".detail", Static)
            detail.update(
                f"[dim]params[/dim]\n{self._params_full}\n\n"
                f"[dim]output[/dim]\n{self._output}\n\n"
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"
            )
            self.add_class("expanded")


class PermissionSelect(Static):
    """
    内联权限选择控件

    挂载在日志流中，支持键盘操作选择权限决策，无需 ModalScreen 弹窗。

    工作原理：
    1. 初始化时显示权限选项列表（Allow once、Always allow、Deny、Always deny）
    2. 支持方向键导航（↑↓/hj）选择选项
    3. 支持快捷键直接选择（y/a/n/d 或 1/2/3/4）
    4. 按下 Enter 或选择后发布 Decided 消息
    5. 宿主 App 监听消息并发送 IPC 响应

    设计要点：
    - 内联显示在日志流中，不打断用户视线
    - 支持快捷键快速操作
    - 使用 Textual Message 机制与宿主 App 通信
    - 自动获取焦点，方便键盘操作

    使用示例：
        >>> select = PermissionSelect("call_01")
        >>> app.mount(select, before="#prompt")
        >>> # 用户按 y/a/n/d 或方向键选择
    """

    can_focus = True

    DEFAULT_CSS = """
    PermissionSelect {
        height: auto;
        padding: 0 2;
        margin-bottom: 1;
    }
    """

    # 权限选项：(决策值, 显示标签, 快捷键提示)
    _CHOICES: tuple[tuple[str, str, str], ...] = (
        ("allow_once",   "Allow once",   "y / 1"),
        ("always_allow", "Always allow", "a / 2"),
        ("deny_once",    "Deny",         "n / 3"),
        ("always_deny",  "Always deny",  "d / 4"),
    )
    # 快捷键到决策值的映射
    _KEY_MAP: dict[str, str] = {
        "y": "allow_once",  "1": "allow_once",
        "a": "always_allow","2": "always_allow",
        "n": "deny_once",   "3": "deny_once",
        "d": "always_deny", "4": "always_deny",
    }

    class Decided(Message):
        """
        用户作出权限决策时发布的消息

        携带控件引用、工具 ID 和决策字符串，供宿主 App 监听和处理。

        属性：
            widget: PermissionSelect - 控件引用
            tool_use_id: str - 工具调用 ID
            decision: str - 决策字符串（allow_once/always_allow/deny_once/always_deny）
        """

        def __init__(self, widget: PermissionSelect, tool_use_id: str, decision: str) -> None:
            """
            初始化决策消息

            参数：
                widget: PermissionSelect - 控件引用
                tool_use_id: str - 工具调用 ID（用于 IPC 回复）
                decision: str - 决策字符串
            """
            self.widget = widget
            self.tool_use_id = tool_use_id
            self.decision = decision
            super().__init__()

    def __init__(self, tool_use_id: str) -> None:
        """
        初始化权限选择控件

        参数：
            tool_use_id: str - 工具调用 ID，用于后续 IPC 回复

        属性：
            _tool_use_id: 工具调用 ID
            _cursor: 当前光标的位置（选项索引），初始为 0
        """
        super().__init__("")
        self._tool_use_id = tool_use_id
        self._cursor = 0

    def on_mount(self) -> None:
        """
        控件挂载时的初始化

        渲染 UI，获取焦点，并记录调试日志。

        实现细节：
        - 调用 _render_ui() 生成初始选项列表
        - 调用 focus() 获取键盘焦点
        - 使用 call_after_refresh() 在下一帧检查焦点状态（用于调试）
        """
        self.update(self._render_ui())
        self.focus()
        log.debug(
            "PermissionSelect.on_mount  can_focus=%s  focused_after=%r",
            self.can_focus,
            self.app.focused,
        )
        self.app.call_after_refresh(self._log_deferred_focus)

    def _log_deferred_focus(self) -> None:
        """
        在下一帧记录焦点状态

        用于调试焦点是否真正转移到本控件，帮助排查焦点问题。
        """
        log.debug(
            "PermissionSelect.deferred_focus  app.focused=%r  has_focus=%s  focusable=%s",
            self.app.focused,
            self.has_focus,
            self.focusable,
        )

    def on_focus(self, event: events.Focus) -> None:
        """
        焦点到达时记录

        用于确认 focus() 是否真正生效，帮助调试焦点问题。
        """
        log.debug("PermissionSelect.on_focus  has_focus=%s  app.focused=%r", self.has_focus, self.app.focused)

    def on_blur(self, event: events.Blur) -> None:
        """
        焦点离开时记录

        用于追踪焦点是否被其他控件抢走，帮助调试焦点问题。
        """
        log.debug("PermissionSelect.on_blur  app.focused=%r", self.app.focused)

    def _render_ui(self) -> str:
        """
        生成带光标高亮的选项列表文本

        根据当前光标位置，生成包含光标高亮的选项列表。

        返回：
            str: 格式化的选项列表文本

        显示格式：
            ❯ Allow once    y / 1
              Always allow  a / 2
              Deny          n / 3
              Always deny   d / 4
              ↑↓ navigate   enter confirm
        """
        lines: list[str] = []
        for i, (_, label, key_hint) in enumerate(self._CHOICES):
            if i == self._cursor:
                lines.append(f"  [bold cyan]❯ {label}[/bold cyan]  [dim]{key_hint}[/dim]")
            else:
                lines.append(f"    {label}  [dim]{key_hint}[/dim]")
        lines.append("[dim]  ↑↓ navigate   enter confirm[/dim]")
        return "\n".join(lines)

    def on_key(self, event: events.Key) -> None:
        """
        键盘事件处理

        支持方向键导航、快捷键直接选择、Enter 确认光标位置。

        参数：
            event: events.Key - 键盘事件

        处理逻辑：
        - up/k: 向上移动光标（循环）
        - down/j: 向下移动光标（循环）
        - enter: 确认当前光标位置的选项
        - y/a/n/d/1/2/3/4: 直接选择对应的决策

        实现细节：
        - 使用 event.stop() 阻止事件冒泡
        - 更新光标后调用 _render_ui() 重新渲染
        """
        log.debug("PermissionSelect.on_key  key=%r  char=%r", event.key, event.character)
        key = event.key
        if key in ("up", "k"):
            event.stop()
            self._cursor = (self._cursor - 1) % len(self._CHOICES)
            self.update(self._render_ui())
        elif key in ("down", "j"):
            event.stop()
            self._cursor = (self._cursor + 1) % len(self._CHOICES)
            self.update(self._render_ui())
        elif key == "enter":
            event.stop()
            self._pick(self._CHOICES[self._cursor][0])
        else:
            decision = self._KEY_MAP.get(key)
            if decision is not None:
                event.stop()
                self._pick(decision)

    def _pick(self, decision: str) -> None:
        """
        发布决策消息

        将用户的权限决策封装为 Decided 消息并发布，由宿主 App 负责 IPC 回复和控件清理。

        参数：
            decision: str - 决策字符串（allow_once/always_allow/deny_once/always_deny）

        实现细节：
        - 使用 post_message() 发布消息
        - 消息包含控件引用、工具 ID 和决策值
        - 宿主 App 监听消息后发送 IPC 响应到 core 服务
        """
        log.debug("PermissionSelect._pick  decision=%s", decision)
        self.post_message(self.Decided(self, self._tool_use_id, decision))


class PermissionBlock(Static):
    """
    日志中的权限审批摘要块

    在日志流中显示工具调用的权限请求状态，支持待审批和已解决两种状态。

    工作原理：
    1. 初始化时显示待审批状态（红色 "? permission"）
    2. 用户作出决策后通过 _resolve() 更新为已解决状态
    3. 已解决状态显示图标（✓/✗）、工具名称和决策标签

    设计要点：
    - 使用 _resolved 标记防止重复解决
    - 通过 LABEL_MAP 将决策值映射为友好的显示文本
    - 解决时发布 Resolved 消息，供宿主 App 监听

    使用示例：
        >>> block = PermissionBlock("call_01", "bash", "command='ls'")
        >>> block._resolve("allow_once")  # 更新为允许状态
    """

    _LABEL_MAP: dict[str, str] = {
        "allow_once":   "allowed (once)",
        "always_allow": "always allowed",
        "deny_once":    "denied",
        "always_deny":  "always denied",
        "timeout":      "⏱ timed out",
    }
    LABEL_MAP = _LABEL_MAP

    class Resolved(Message):
        """
        用户作出权限决策时发布的消息

        携带权限块引用和决策字符串，供宿主 App 监听。

        属性：
            block: PermissionBlock - 权限块引用
            decision: str - 决策字符串（allow_once/always_allow/deny_once/always_deny/timeout）
        """

        def __init__(self, block: PermissionBlock, decision: str) -> None:
            """
            初始化决策消息

            参数：
                block: PermissionBlock - 权限块引用
                decision: str - 决策字符串
            """
            self.block = block
            self.decision = decision
            super().__init__()

    def __init__(self, tool_use_id: str, tool_name: str, param_preview: str) -> None:
        """
        初始化权限审批块

        参数：
            tool_use_id: str - 工具调用 ID
            tool_name: str - 工具名称
            param_preview: str - 参数预览字符串

        属性：
            _tool_use_id: 工具调用 ID
            _tool_name: 工具名称
            _param_preview: 参数预览
            _resolved: 是否已解决，初始为 False
        """
        self._tool_use_id = tool_use_id
        self._tool_name = tool_name
        self._param_preview = param_preview
        self._resolved = False
        super().__init__(self._pending_text(), classes="log-line")

    def _pending_text(self) -> str:
        """
        生成待审批状态的显示文本

        返回：
            str: 格式化的待审批文本（红色 "? permission" + 工具名称 + 参数预览）

        显示格式：
            ? permission  tool_name  params_preview
        """
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        return f"[bold red]? permission[/bold red]  [bold]{self._tool_name}[/bold]{preview}"

    def _resolve(self, decision: str) -> None:
        """
        将权限块更新为已解决状态并发布 Resolved 消息

        参数：
            decision: str - 决策字符串（allow_once/always_allow/deny_once/always_deny/timeout）

        实现细节：
        - 如果已解决，直接返回（防止重复处理）
        - 根据决策类型选择图标（允许用 ✓，拒绝用 ✗）
        - 通过 LABEL_MAP 获取友好的决策标签
        - 更新显示文本并发布 Resolved 消息
        """
        if self._resolved:
            return
        self._resolved = True
        allowed = decision in ("allow_once", "always_allow")
        icon = "[bold green]✓[/bold green]" if allowed else "[bold red]✗[/bold red]"
        label = self._LABEL_MAP.get(decision, decision)
        preview = f"[dim]{self._param_preview}[/dim]" if self._param_preview else ""
        self.update(
            f"{icon} permission  [bold]{self._tool_name}[/bold]{preview}  [dim]{label}[/dim]"
        )
        self.post_message(self.Resolved(self, decision))


class SlashCompleteWidget(Static):
    """
    斜杠命令自动补全弹出框

    用户输入 / 时显示可用的命令和 skill 列表，支持键盘筛选、导航和选择。

    工作原理：
    1. 初始化时接收全量命令列表（系统命令 + skill）
    2. 用户输入 / 后的字符时，通过 set_query() 实时筛选
    3. 使用方向键（↑↓）导航选项
    4. 按 Enter/Tab 选中当前项，发布 Selected 消息
    5. 按 Esc 收起弹窗

    设计要点：
    - can_focus=False：焦点仍在输入框，由宿主转发键盘事件
    - 保留全量列表 _all_items，筛选后的列表保存在 _filtered
    - 光标位置在筛选时自动调整，避免越界

    使用示例：
        >>> items = [("help", "显示帮助"), ("review", "代码审查")]
        >>> widget = SlashCompleteWidget(items)
        >>> widget.set_query("rev")  # 筛选出 review
        >>> widget.select_current()  # 发布 Selected("review")
    """

    can_focus = False

    DEFAULT_CSS = """
    SlashCompleteWidget {
        height: auto;
        padding: 0 1;
        margin: 0 2;
        background: $surface;
        border: round $surface-lighten-2;
    }
    """

    class Selected(Message):
        """
        用户选中某条命令时发布的消息

        携带被选中的命令/skill 名称，供宿主 App 处理。

        属性：
            skill_name: str - 被选中的命令或 skill 名称
        """

        def __init__(self, skill_name: str) -> None:
            """
            初始化选中消息

            参数：
                skill_name: str - 被选中的命令或 skill 名称
            """
            self.skill_name = skill_name
            super().__init__()

    def __init__(self, items: list[tuple[str, str]]) -> None:
        """
        初始化斜杠命令自动补全弹窗

        参数：
            items: list[tuple[str, str]] - 命令列表，每个元素为 (名称, 描述)

        属性：
            _all_items: 全量命令列表（原始数据）
            _filtered: 筛选后的命令列表（实时更新）
            _cursor: 当前光标位置（选项索引），初始为 0
        """
        super().__init__("")
        self._all_items = items
        self._filtered: list[tuple[str, str]] = list(items)
        self._cursor = 0

    def set_query(self, query: str) -> None:
        """
        根据查询字符串筛选命令列表并重新渲染

        参数：
            query: str - 查询字符串（/ 之后的部分）

        实现细节：
        - 将查询字符串转换为小写，进行大小写不敏感匹配
        - 筛选条件：查询为空或查询字符串包含在命令名称中
        - 光标位置自动调整，不超过筛选后列表的范围
        - 如果 widget 已挂载，调用 _redraw() 更新显示

        使用示例：
            >>> widget.set_query("rev")  # 筛选名称包含 "rev" 的命令
            >>> widget.set_query("")     # 显示所有命令
        """
        q = query.lower()
        self._filtered = [(n, d) for n, d in self._all_items if not q or q in n.lower()]
        self._cursor = min(self._cursor, max(0, len(self._filtered) - 1))
        if self.is_attached:
            self._redraw()

    def move_up(self) -> None:
        """
        向上移动光标并重新渲染

        光标循环移动：到达顶部后回到底部。

        使用示例：
            >>> widget.move_up()
        """
        if self._filtered:
            self._cursor = (self._cursor - 1) % len(self._filtered)
            self._redraw()

    def move_down(self) -> None:
        """
        向下移动光标并重新渲染

        光标循环移动：到达底部后回到顶部。

        使用示例：
            >>> widget.move_down()
        """
        if self._filtered:
            self._cursor = (self._cursor + 1) % len(self._filtered)
            self._redraw()

    def select_current(self) -> None:
        """
        选中当前光标指向的命令并发布 Selected 消息

        如果筛选列表非空，发布包含选中命令名称的 Selected 消息。

        使用示例：
            >>> widget.select_current()  # 发布 Selected("help")
        """
        if self._filtered:
            self.post_message(self.Selected(self._filtered[self._cursor][0]))

    def has_selection(self) -> bool:
        """
        判断当前是否有可选项

        返回：
            bool: True 表示有可选项，False 表示无匹配项
        """
        return len(self._filtered) > 0

    def on_mount(self) -> None:
        """
        控件挂载时的初始化

        调用 _redraw() 渲染初始命令列表。
        """
        self._redraw()

    def _redraw(self) -> None:
        """
        渲染筛选后的命令列表，高亮当前光标项

        实现细节：
        - 如果筛选后为空，显示 "no matching commands"
        - 遍历筛选列表，光标所在行用粗体青色高亮（❯ 标记）
        - 非光标行使用普通青色显示
        - 最后一行显示操作提示（方向键导航、Tab/Enter 选择、Esc 关闭）

        显示格式：
            ❯ /help        显示帮助信息
              /review      代码审查
              ↑↓ navigate   tab/enter select   esc dismiss
        """
        if not self._filtered:
            self.update("[dim]  no matching commands[/dim]")
            return
        lines: list[str] = []
        for i, (name, desc) in enumerate(self._filtered):
            desc_part = f"  [dim]{desc}[/dim]" if desc else ""
            if i == self._cursor:
                lines.append(f"  [bold cyan]❯ /{name}[/bold cyan]{desc_part}")
            else:
                lines.append(f"    [cyan]/{name}[/cyan]{desc_part}")
        lines.append("[dim]  ↑↓ navigate   tab/enter select   esc dismiss[/dim]")
        self.update("\n".join(lines))


class ChatTextArea(TextArea):
    """
    聊天输入框组件

    支持 Enter 提交消息、Cmd/Shift/Alt+Enter 换行，以及斜杠命令自动补全。

    工作原理：
    1. 文本变化时检测 / 前缀，触发斜杠命令弹窗
    2. Enter 键提交消息（如果有自动补全弹窗且有选中项，则选择命令）
    3. Cmd/Shift/Alt+Enter 插入换行
    4. ↑↓/Tab/Esc 路由到自动补全弹窗
    5. 其他按键交回父类 TextArea 处理

    设计要点：
    - 高度自适应（3-12 行），支持多行输入
    - 聚焦时边框变为高亮色
    - 通过消息机制（Submitted、SlashChanged）与宿主 App 通信

    使用示例：
        >>> area = ChatTextArea()
        >>> area.text = "/help"  # 触发 SlashChanged 消息
        >>> # 用户按 Enter 发布 Submitted 消息
    """

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

    class Submitted(Message):
        """
        用户提交消息时发布的消息

        携带输入框引用和输入内容，供宿主 App 监听并发送消息。

        属性：
            text_area: ChatTextArea - 输入框引用
            value: str - 输入框内容
        """

        def __init__(self, area: ChatTextArea) -> None:
            """
            初始化提交消息

            参数：
                area: ChatTextArea - 输入框引用
            """
            self.text_area = area
            self.value = area.text
            super().__init__()

    class SlashChanged(Message):
        """
        斜杠命令查询变化时发布的消息

        当输入内容以 / 开头且无空格时发布，通知宿主 App 更新自动补全弹窗。

        属性：
            query: str | None - 查询字符串（/ 之后的部分），None 表示收起弹窗
        """

        def __init__(self, query: str | None) -> None:
            """
            初始化斜杠变化消息

            参数：
                query: str | None - 查询字符串，None 表示收起弹窗
            """
            self.query = query
            super().__init__()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """
        文本变化时检测 / 前缀，通知宿主 App 更新自动补全弹窗

        参数：
            event: TextArea.Changed - 文本变化事件

        处理逻辑：
        - 如果文本以 / 开头且无空格，发布 SlashChanged(query=...) 消息
        - 否则发布 SlashChanged(query=None) 消息，收起弹窗

        使用示例：
            >>> # 用户输入 "/help" -> SlashChanged(query="help")
            >>> # 用户输入 "/help " -> SlashChanged(query=None)（有空格）
            >>> # 用户输入 "hello" -> SlashChanged(query=None)（不以 / 开头）
        """
        text = self.text
        if text.startswith("/") and " " not in text:
            self.post_message(ChatTextArea.SlashChanged(query=text[1:]))
        else:
            self.post_message(ChatTextArea.SlashChanged(query=None))

    async def _on_key(self, event: events.Key) -> None:
        """
        键盘事件处理

        支持 Enter 提交、斜杠命令导航、换行插入等操作。

        参数：
            event: events.Key - 键盘事件

        处理逻辑：
        1. 查找自动补全弹窗（如果存在）
        2. Enter：提交消息或选择弹窗中的命令
        3. Cmd/Shift/Alt+Enter：插入换行
        4. ↑↓：导航自动补全弹窗
        5. Tab：选择弹窗中的命令
        6. Esc：收起自动补全弹窗
        7. 其他：交回父类 TextArea 处理

        实现细节：
        - 使用 event.stop() 阻止事件冒泡
        - 使用 event.prevent_default() 阻止默认行为
        - 只有当弹窗存在且有选中项时，Enter/Tab 才选择命令
        - 只有非只读模式下才能插入换行
        """
        key = event.key

        popup: SlashCompleteWidget | None = None
        try:
            popup = self.app.query_one(SlashCompleteWidget)
        except NoMatches:
            popup = None

        if key == "enter":
            event.stop()
            event.prevent_default()
            if popup is not None and popup.has_selection():
                popup.select_current()
                return
            if self.text.strip():
                self.post_message(self.Submitted(self))
            return
        if key in ("alt+enter", "shift+enter", "ctrl+j", "super+enter"):
            event.stop()
            event.prevent_default()
            if not self.read_only:
                self.insert("\n")
            return
        if popup is not None:
            if key == "up":
                event.stop()
                event.prevent_default()
                popup.move_up()
                return
            elif key == "down":
                event.stop()
                event.prevent_default()
                popup.move_down()
                return
            elif key == "tab":
                event.stop()
                event.prevent_default()
                popup.select_current()
                return
            elif key == "escape":
                event.stop()
                event.prevent_default()
                self.post_message(ChatTextArea.SlashChanged(query=None))
                return
        await super()._on_key(event)


@dataclass
class _SessionState:
    """
    会话 UI 状态 - 保存单个会话的 TUI 状态

    【字段说明】
    - session_id: str - 会话 ID
    - title: str - 会话标题
    - widgets: list[Widget] - 该会话的日志 widget 列表（用于切换时恢复）
    - auto_mode: str - 自动模式（off / read_only / on）
    - effort_level: str - 努力等级（minimal / low / medium / high / max）
    - model_preset: str - 模型预设（fast / balanced / powerful）
    - busy: bool - 是否正在运行
    - last_context_pct: float - 上次上下文占用率
    - current_llm: LLMStreamBlock | None - 当前 LLM 流式输出块
    - pending_tool_blocks: dict - 待完成的工具调用块
    - pending_permission_blocks: dict - 待处理的权限审批块
    - subagent_run_ids: dict - 子 Agent 运行 ID 映射
    - subagent_start_times: dict - 子 Agent 开始时间映射

    【设计目的】
    每个会话有独立的 UI 状态，切换会话时保存当前状态并恢复目标会话状态。
    这样可以支持多标签页并行，每个会话独立运行。
    """
    session_id: str
    title: str = ""
    widgets: list[Widget] = field(default_factory=list)
    auto_mode: str = "off"
    effort_level: str = "medium"
    model_preset: str = "balanced"
    busy: bool = False
    last_context_pct: float = 0.0
    current_llm: Any = None  # LLMStreamBlock | None
    pending_tool_blocks: dict[str, Any] = field(default_factory=dict)
    pending_permission_blocks: dict[str, Any] = field(default_factory=dict)
    subagent_run_ids: dict[str, str] = field(default_factory=dict)
    subagent_start_times: dict[str, float] = field(default_factory=dict)


class IwanTuiApp(App[None]):
    """
    IwanClaude TUI 主应用类

    基于 Textual 框架构建的终端用户界面，实时展示 Agent 执行过程。

    核心功能：
    - 实时展示 Agent 执行日志、工具调用、LLM 流式输出
    - 内联权限审批，支持键盘快捷键快速授权
    - 斜杠命令自动补全，支持系统命令和 Skill 触发
    - 检查点管理，支持列出和恢复检查点
    - 会话历史搜索（Ctrl+R）
    - 上下文压缩（/compact）
    - 子 Agent 执行过程展示

    界面布局：
    - 顶部：状态栏（显示连接状态、会话 ID、引擎类型）
    - 中部：日志滚动区域（显示运行日志、工具调用、LLM 输出）
    - 底部：聊天输入框（支持 Enter 提交、多行输入）

    快捷键：
    - Ctrl+Q：退出程序
    - F6：列出检查点（仅 LangGraph 模式）
    - Ctrl+P：Textual 系统命令面板
    - Ctrl+R：搜索历史

    使用示例：
        >>> from iwan_claude.tui.app import IwanTuiApp
        >>> app = IwanTuiApp("127.0.0.1", 7437)
        >>> app.run()
    """

    TITLE = "IwanClaude"
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("f6", "checkpoint_list", "列出检查点"),
        Binding("ctrl+p", "app_command", "命令面板"),
        Binding("ctrl+r", "search_history", "搜索历史"),
        Binding("ctrl+t", "new_session", "新建会话"),
        Binding("ctrl+w", "close_session", "关闭会话"),
        Binding("alt+1", "switch_session(1)", "切换到会话1"),
        Binding("alt+2", "switch_session(2)", "切换到会话2"),
        Binding("alt+3", "switch_session(3)", "切换到会话3"),
        Binding("alt+4", "switch_session(4)", "切换到会话4"),
        Binding("alt+5", "switch_session(5)", "切换到会话5"),
        Binding("alt+6", "switch_session(6)", "切换到会话6"),
        Binding("alt+7", "switch_session(7)", "切换到会话7"),
        Binding("alt+8", "switch_session(8)", "切换到会话8"),
        Binding("alt+9", "switch_session(9)", "切换到会话9"),
    ]
    CSS = """
    Screen { background: $background; }
    #tabbar {
        height: 1;
        background: $surface;
        dock: top;
    }
    .tab {
        padding: 0 1;
        height: 1;
        color: $text-muted;
    }
    .tab.active {
        background: $background;
        color: $text;
        text-style: bold;
    }
    .tab.busy {
        color: $warning;
    }
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
    #banner { padding: 1 2 0 2; }
    Static.user-turn { color: $text; padding: 1 2 0 2; }
    Static.run-header { color: $text-muted; padding: 1 2 0 2; }
    Static.step-divider { color: $text-muted; padding: 0 2; }
    Static.run-ok { color: green; padding: 0 2 1 2; }
    Static.run-err { color: red; padding: 0 2 1 2; }
    Static.usage { padding: 0 2; }
    Static.log-line { padding: 0 2; }
    """

    _BANNER = (
        "[bold cyan]██╗██╗    ██╗ █████╗ ███╗   ██╗ ██████╗██╗      █████╗ ██╗   ██╗██████╗ ███████╗[/bold cyan]\n"
        "[bold cyan]██║██║    ██║██╔══██╗████╗  ██║██╔════╝██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝[/bold cyan]\n"
        "[bold cyan]██║██║ █╗ ██║███████║██╔██╗ ██║██║     ██║     ███████║██║   ██║██║  ██║█████╗  [/bold cyan]\n"
        "[bold cyan]██║██║███╗██║██╔══██║██║╚██╗██║██║     ██║     ██╔══██║██║   ██║██║  ██║██╔══╝  [/bold cyan]\n"
        "[bold cyan]██║╚███╔███╔╝██║  ██║██║ ╚████║╚██████╗███████╗██║  ██║╚██████╔╝██████╔╝███████╗[/bold cyan]\n"
        "[bold cyan]╚═╝ ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝[/bold cyan]\n"
        "[dim]  输入消息开始对话  ·  键入 / 查看命令  ·  F6 检查点  ·  Ctrl+T/W 新建/关闭会话  ·  Alt+1~9 切换会话  ·  Ctrl+Q 退出[/dim]"
    )

    def __init__(self, host: str, port: int, replay_run_id: str | None = None) -> None:
        """
        初始化 TUI 应用

        参数：
            host: str - core 服务主机地址
            port: int - core 服务端口号
            replay_run_id: str | None - 可选，连接后回放指定运行的事件

        属性：
            _host: 主机地址
            _port: 端口号
            _replay_run_id: 回放运行 ID（可选）
            _client: SocketClient 实例，用于与 core 服务通信
            _sessions: 所有会话的 UI 状态（session_id -> _SessionState）
            _session_order: 会话顺序列表（用于标签页排序和 Alt+数字切换）
            _engine_type: 引擎类型（legacy/langgraph）
            _checkpoint_backend: 检查点后端（none/memory/sqlite）
            _slash_items: 斜杠命令候选列表
            _history: 搜索历史列表（最多 100 条）
        """
        super().__init__()
        self._host = host
        self._port = port
        self._replay_run_id = replay_run_id
        self._client: SocketClient | None = None
        # 多会话状态管理
        self._sessions: dict[str, _SessionState] = {}
        self._session_order: list[str] = []
        # 全局配置
        self._engine_type: str = "legacy"
        self._checkpoint_backend: str = "none"
        self._slash_items: list[tuple[str, str]] = []
        self._history: list[str] = []

    @property
    def _session_id(self) -> str | None:
        """当前会话 ID（从会话顺序列表取第一个）"""
        return self._session_order[0] if self._session_order else None

    @_session_id.setter
    def _session_id(self, value: str | None) -> None:
        """设置当前会话 ID，或清空会话列表（value=None 时）"""
        if value is None:
            # 清空所有会话
            self._session_order = []
            self._sessions = {}
            self._refresh_tabbar()
        elif value in self._sessions:
            # 将指定会话移到第一个位置
            if value in self._session_order:
                self._session_order.remove(value)
            self._session_order.insert(0, value)
            self._refresh_tabbar()

    @property
    def _state(self) -> _SessionState | None:
        """当前会话的状态对象"""
        if not self._session_id:
            return None
        return self._sessions.get(self._session_id)

    @property
    def _busy(self) -> bool:
        """当前会话是否正在运行"""
        return self._state.busy if self._state else False

    @_busy.setter
    def _busy(self, value: bool) -> None:
        """设置当前会话的 busy 状态，同时刷新标签栏"""
        if self._state:
            self._state.busy = value
            self._refresh_tabbar()

    @property
    def _auto_mode(self) -> str:
        """当前会话的自动模式"""
        return self._state.auto_mode if self._state else "off"

    @_auto_mode.setter
    def _auto_mode(self, value: str) -> None:
        if self._state:
            self._state.auto_mode = value

    @property
    def _effort_level(self) -> str:
        """当前会话的努力等级"""
        return self._state.effort_level if self._state else "medium"

    @_effort_level.setter
    def _effort_level(self, value: str) -> None:
        if self._state:
            self._state.effort_level = value

    @property
    def _model_preset(self) -> str:
        """当前会话的模型预设"""
        return self._state.model_preset if self._state else "balanced"

    @_model_preset.setter
    def _model_preset(self, value: str) -> None:
        if self._state:
            self._state.model_preset = value

    @property
    def _last_context_pct(self) -> float:
        """当前会话的上次上下文占用率"""
        return self._state.last_context_pct if self._state else 0.0

    @_last_context_pct.setter
    def _last_context_pct(self, value: float) -> None:
        if self._state:
            self._state.last_context_pct = value

    @property
    def _current_llm(self) -> Any:
        """当前会话的 LLM 流式输出块"""
        return self._state.current_llm if self._state else None

    @_current_llm.setter
    def _current_llm(self, value: Any) -> None:
        if self._state:
            self._state.current_llm = value

    @property
    def _pending_tool_blocks(self) -> dict[str, Any]:
        """当前会话的待完成工具调用块"""
        return self._state.pending_tool_blocks if self._state else {}

    @property
    def _pending_permission_blocks(self) -> dict[str, Any]:
        """当前会话的待处理权限审批块"""
        return self._state.pending_permission_blocks if self._state else {}

    @property
    def _subagent_run_ids(self) -> dict[str, str]:
        """当前会话的子 Agent 运行 ID 映射"""
        return self._state.subagent_run_ids if self._state else {}

    @property
    def _subagent_start_times(self) -> dict[str, float]:
        """当前会话的子 Agent 开始时间映射"""
        return self._state.subagent_start_times if self._state else {}

    def compose(self) -> ComposeResult:
        """
        组合 TUI 界面组件

        创建四个核心组件：
        - #tabbar：顶部标签栏，显示所有会话标签
        - #header：状态栏，显示连接状态、引擎、自动模式等
        - #log-view：日志滚动区域，显示运行日志和输出
        - #prompt：聊天输入框，支持 Enter 提交

        返回：
            ComposeResult: 子 widget 生成器
        """
        yield Horizontal(id="tabbar")
        yield Label("[bold]IwanClaude[/bold]  [dim]connecting...[/dim]", id="header")
        yield VerticalScroll(id="log-view")
        yield ChatTextArea(id="prompt", show_line_numbers=False)

    def _add_session(self, session_id: str, title: str = "") -> None:
        """
        添加一个新会话到状态管理

        参数：
            session_id: str - 会话 ID
            title: str - 会话标题（可选）

        实现细节：
        - 创建 _SessionState 实例
        - 添加到 _sessions 字典
        - 添加到 _session_order 列表头部（最新的在最前）
        - 刷新标签栏
        """
        if session_id in self._sessions:
            return
        state = _SessionState(session_id=session_id, title=title or session_id)
        self._sessions[session_id] = state
        self._session_order.insert(0, session_id)
        self._refresh_tabbar()

    def _switch_session(self, session_id: str) -> None:
        """
        切换到指定会话

        参数：
            session_id: str - 目标会话 ID

        实现细节：
        1. 如果目标就是当前会话，直接返回
        2. 保存当前会话的 UI 状态（widgets、busy 等）
        3. 将目标会话移到 _session_order 头部
        4. 清空 log-view 并加载目标会话的 widgets
        5. 更新状态栏
        6. 刷新标签栏

        注意：
            切换不会影响后台会话的运行，事件会继续路由到对应会话的状态。
        """
        if not self._sessions or session_id == self._session_id:
            return
        if session_id not in self._sessions:
            return

        # 1. 保存当前会话的状态
        self._save_current_state()

        # 2. 将目标会话移到头部
        self._session_order.remove(session_id)
        self._session_order.insert(0, session_id)

        # 3. 清空 log-view 并加载目标会话的 widgets
        self._load_session_state(session_id)

        # 4. 更新状态栏和标签栏
        self._update_header("ready")
        self._refresh_tabbar()

    def _close_current_session(self) -> None:
        """
        关闭当前会话

        实现细节：
        1. 如果只有一个会话，不关闭（至少保留一个）
        2. 向服务器发送 session.close 命令
        3. 从 _sessions 和 _session_order 移除
        4. 切换到下一个会话
        5. 刷新标签栏
        """
        if len(self._session_order) <= 1:
            self._append(Static("[yellow]至少保留一个会话[/yellow]", classes="log-line"))
            return

        current_id = self._session_id
        if current_id is None:
            return

        # 发送关闭命令
        if self._client is not None:
            try:
                self.run_worker(
                    self._client.send_command("session.close", {"session_id": current_id}),
                    exclusive=False,
                )
            except Exception:
                pass

        # 从状态中移除
        self._sessions.pop(current_id, None)
        self._session_order.remove(current_id)

        # 加载下一个会话
        if self._session_order:
            self._load_session_state(self._session_order[0])

        self._update_header("ready")
        self._refresh_tabbar()

    def _refresh_tabbar(self) -> None:
        """
        刷新标签栏显示

        实现细节：
        - 清空 #tabbar 容器
        - 为每个会话创建一个 Label 作为标签
        - 当前会话添加 .active class
        - 正在运行的会话添加 .busy class
        """
        try:
            tabbar = self.query_one("#tabbar", Horizontal)
        except NoMatches:
            return

        # 清空标签栏
        tabbar.remove_children()

        # 为每个会话创建标签
        for idx, sid in enumerate(self._session_order):
            state = self._sessions.get(sid)
            if state is None:
                continue

            # 标签显示文本：序号 + 标题（截断）
            display_title = state.title or "(untitled)"
            if len(display_title) > 20:
                display_title = display_title[:18] + "…"
            label_text = f"{idx + 1} {display_title}"

            # 构建 class 列表
            classes = ["tab"]
            if sid == self._session_id:
                classes.append("active")
            if state.busy:
                classes.append("busy")

            label = Label(label_text, classes=" ".join(classes))
            # 保存 session_id 到 label，便于点击识别
            label.session_id = sid  # type: ignore[attr-defined]
            tabbar.mount(label)

    def _save_current_state(self) -> None:
        """
        保存当前会话的 UI 状态

        实现细节：
        - 收集 log-view 中的所有 widget
        - 保存到当前会话的状态对象中
        - 保存 busy、auto_mode、effort_level、model_preset 等状态
        """
        state = self._state
        if state is None:
            return

        try:
            log_view = self.query_one("#log-view", VerticalScroll)
            # 收集所有子 widget
            state.widgets = list(log_view.children)
        except NoMatches:
            pass

    def _load_session_state(self, session_id: str) -> None:
        """
        加载指定会话的 UI 状态到 log-view

        参数：
            session_id: str - 目标会话 ID

        实现细节：
        - 清空 log-view
        - 将目标会话的 widgets 挂载到 log-view
        - 滚动到底部
        """
        state = self._sessions.get(session_id)
        if state is None:
            return

        try:
            log_view = self.query_one("#log-view", VerticalScroll)
        except NoMatches:
            return

        # 清空当前内容
        log_view.remove_children()

        # 挂载该会话的所有 widgets
        for widget in state.widgets:
            log_view.mount(widget)

        # 滚动到底部
        log_view.scroll_end(animate=False)

    def _get_state(self, session_id: str) -> _SessionState | None:
        """
        获取指定会话的状态对象

        参数：
            session_id: str - 会话 ID

        返回：
            _SessionState | None - 会话状态对象，不存在返回 None
        """
        return self._sessions.get(session_id)

    def on_mount(self) -> None:
        """
        应用挂载时的初始化

        执行以下操作：
        1. 构建斜杠命令候选列表
        2. 显示应用 Banner
        3. 启动 Socket 连接 worker（独占模式）
        4. 禁用输入框，显示 "connecting..." 状态

        实现细节：
        - 使用 run_worker() 启动异步连接循环，避免阻塞 UI
        - exclusive=True 确保只有一个 socket worker 在运行
        """
        self._slash_items = self._build_slash_items()
        self._append(Static(self._BANNER, id="banner"))
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")
        prompt = self.query_one("#prompt", ChatTextArea)
        prompt.disabled = True
        prompt.border_title = "connecting..."

    def _build_slash_items(self) -> list[tuple[str, str]]:
        """
        构建斜杠命令候选列表

        组合系统内置命令和已加载的 Skill，生成完整的命令列表。

        返回：
            list[tuple[str, str]]: 命令列表，每个元素为 (名称, 描述)

        系统命令：
            - help: 显示帮助信息
            - compact: 压缩上下文窗口
            - checkpoint list: 列出所有检查点
            - checkpoint restore <n>: 恢复到指定检查点
            - history: 查看会话历史
            - close: 关闭当前会话
            - skill_list: 列出所有可用技能

        Skill 处理：
            - 从 SkillLoader 加载所有 skill
            - 提取第一个描述行（最多 50 字符）
            - 自动触发的 skill 添加 🔄 标记
            - 添加 skill 图标

        使用示例：
            >>> items = self._build_slash_items()
            >>> # 返回 [("help", "显示帮助信息"), ("review", "🔍 代码审查"), ...]
        """
        items: list[tuple[str, str]] = [
            ("help", "显示帮助信息"),
            ("auto", "切换自动模式 (off|read_only|on)"),
            ("effort", "切换努力等级 (minimal|low|medium|high|max)"),
            ("compact", "压缩上下文窗口"),
            ("checkpoint list", "列出所有检查点"),
            ("checkpoint restore <n>", "恢复到指定检查点"),
            ("history", "查看会话历史"),
            ("close", "关闭当前会话"),
            ("skill_list", "列出所有可用技能"),
        ]
        try:
            loader = SkillLoader()
            for skill in loader.list_all_skills():
                desc = skill.description.splitlines()[0] if skill.description else "skill"
                if len(desc) > 50:
                    desc = desc[:47] + "..."
                invocation_mark = "🔄" if skill.invocation.value in ("auto", "both") else ""
                items.append((skill.name, f"{skill.icon} {invocation_mark} {desc}"))
        except Exception:
            pass
        return items

    def on_chat_text_area_slash_changed(self, event: ChatTextArea.SlashChanged) -> None:
        """
        处理斜杠命令查询变化事件

        根据查询字符串挂载、更新或移除自动补全弹窗。

        参数：
            event: ChatTextArea.SlashChanged - 斜杠变化事件

        处理逻辑：
        - query=None：移除弹窗（如果存在）
        - query!=None：更新已有弹窗或创建新弹窗

        使用示例：
            >>> # 用户输入 "/help" -> 创建弹窗，显示匹配 "help" 的命令
            >>> # 用户输入 "hello" -> 移除弹窗
        """
        query = event.query
        if query is None:
            try:
                self.query_one(SlashCompleteWidget).remove()
            except NoMatches:
                pass
            return
        try:
            popup = self.query_one(SlashCompleteWidget)
            popup.set_query(query)
        except NoMatches:
            popup = SlashCompleteWidget(self._slash_items)
            self.mount(popup, before="#prompt")
            popup.set_query(query)

    def on_slash_complete_widget_selected(self, event: SlashCompleteWidget.Selected) -> None:
        """
        处理用户选中斜杠命令的事件

        将选中的命令填入输入框并移除弹窗。

        参数：
            event: SlashCompleteWidget.Selected - 选中事件

        实现细节：
        - 将 "/{skill_name} " 填入输入框（末尾加空格）
        - 将光标移动到末尾
        - 移除自动补全弹窗

        使用示例：
            >>> # 用户选中 "review" -> 输入框变为 "/review "
        """
        prompt = self._prompt()
        if prompt is not None:
            prompt.text = f"/{event.skill_name} "
            prompt.move_cursor(prompt.document.end)
        try:
            self.query_one(SlashCompleteWidget).remove()
        except NoMatches:
            pass

    def on_click(self, event: events.Click) -> None:
        """
        全局点击事件处理

        处理标签栏的标签点击，切换到对应会话。

        参数：
            event: events.Click - 点击事件
        """
        # 检查点击的是否是标签
        widget = event.control
        if widget is None:
            return
        # 向上查找带 session_id 属性的 widget（可能是 Label 或其子元素）
        current = widget
        while current is not None:
            sid = getattr(current, "session_id", None)
            if sid is not None and sid in self._sessions:
                if sid != self._session_id and not self._busy:
                    self._switch_session(sid)
                return
            current = current.parent

    def on_key(self, event: events.Key) -> None:
        """
        全局键盘事件处理（兜底权限快捷键）

        当 PermissionSelect 失去焦点但仍有待处理的权限审批时，作为兜底处理权限快捷键。

        参数：
            event: events.Key - 键盘事件

        处理逻辑：
        1. 如果没有待处理的权限审批，直接返回
        2. 如果 PermissionSelect 有焦点，让它自行处理，不拦截
        3. 否则，处理权限快捷键：
           - y/a/n/d/1/2/3/4：直接选择对应的决策
           - up/k：向上移动光标
           - down/j：向下移动光标
           - enter：确认当前光标位置的决策

        设计要点：
        - 使用 try-except 包裹，防止 PermissionSelect 控件已被移除导致的异常
        - 使用 event.stop() 阻止事件继续传播
        """
        log.debug("App.on_key  key=%r  focused=%r", event.key, self.focused)
        if not self._pending_permission_blocks:
            return
        try:
            select = self.query_one(PermissionSelect)
            if select.has_focus:
                return
            key = event.key
            decision = PermissionSelect._KEY_MAP.get(key)
            if decision:
                event.stop()
                select._pick(decision)
            elif key in ("up", "k"):
                event.stop()
                select._cursor = (select._cursor - 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            elif key in ("down", "j"):
                event.stop()
                select._cursor = (select._cursor + 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            elif key == "enter":
                event.stop()
                select._pick(PermissionSelect._CHOICES[select._cursor][0])
        except Exception:
            pass

    async def action_quit(self) -> None:
        """
        退出程序（Ctrl+Q）

        退出前尽力关闭当前 session，但失败不阻塞退出。

        实现细节：
        - 如果已连接且有 session，发送 session.close 命令
        - 如果关闭失败，显示警告但仍退出
        - 调用 self.exit() 关闭 TUI 应用

        使用示例：
            >>> # 用户按 Ctrl+Q -> 关闭 session -> 退出应用
        """
        if self._client is not None and self._session_id is not None:
            try:
                await self._client.send_command("session.close", {"session_id": self._session_id})
            except (IpcError, RuntimeError, OSError):
                self._append(Static("[yellow]warning: failed to close session[/yellow]"))
        self.exit()

    async def action_search_history(self) -> None:
        """
        显示搜索历史（Ctrl+R）

        显示最近的用户输入历史（最多 20 条）。

        实现细节：
        - 如果历史为空，显示 "暂无历史记录"
        - 否则，显示历史列表，每条记录带索引
        - 使用 cyan 颜色高亮标题

        使用示例：
            >>> # 用户按 Ctrl+R -> 显示搜索历史
        """
        if not self._history:
            self._append(Static("[yellow]暂无历史记录[/yellow]", classes="log-line"))
            return
        self._append(Static("[bold cyan]===== 搜索历史 =====[/bold cyan]", classes="log-line"))
        for i, item in enumerate(self._history[:20]):
            self._append(Static(f"  [{i}] {item}", classes="log-line"))
        self._append(Static("[bold cyan]===================[/bold cyan]", classes="log-line"))

    async def action_checkpoint_list(self) -> None:
        """
        列出检查点（F6）

        在 LangGraph 模式下列出所有检查点。

        实现细节：
        - 如果 Agent 正在运行或未连接，显示错误提示
        - 如果不是 LangGraph 模式，显示 "checkpoints only available in langgraph mode"
        - 使用 worker 执行检查点列表操作，避免阻塞 UI

        使用示例：
            >>> # 用户按 F6 -> 显示检查点列表
        """
        if self._client is None or self._session_id is None or self._busy:
            self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return
        if self._engine_type != "langgraph":
            self._append(Static("[yellow]checkpoints only available in langgraph mode[/yellow]", classes="log-line"))
            return
        self.run_worker(self._do_checkpoint("list", ""), name="checkpoint", exclusive=False)

    async def action_new_session(self) -> None:
        """
        新建会话（Ctrl+T）

        实现细节：
        - 如果未连接或 agent 正在运行，显示错误提示
        - 发送 session.create 命令创建新会话
        - 添加到会话管理并切换到新会话
        """
        if self._client is None:
            self._append(Static("[yellow]not connected[/yellow]", classes="log-line"))
            return
        self.run_worker(self._do_new_session(), name="new_session", exclusive=False)

    async def action_close_session(self) -> None:
        """
        关闭当前会话（Ctrl+W）

        实现细节：
        - 至少保留一个会话
        - 调用 _close_current_session() 关闭
        """
        self._close_current_session()

    async def action_switch_session(self, index_str: str) -> None:
        """
        切换到指定序号的会话（Alt+1~9）

        参数：
            index_str: str - 会话序号字符串（"1" 到 "9"）

        实现细节：
        - 解析序号（从 1 开始）
        - 转换为 0 索引
        - 如果序号有效，切换到对应会话
        """
        try:
            idx = int(index_str) - 1
        except ValueError:
            return
        if 0 <= idx < len(self._session_order):
            self._switch_session(self._session_order[idx])

    async def _do_new_session(self) -> None:
        """
        执行新建会话操作

        实现细节：
        - 发送 session.create 命令
        - 添加到会话管理
        - 切换到新会话
        - 显示欢迎横幅
        """
        if self._client is None:
            return
        try:
            result = await self._client.send_command(
                "session.create", {"mode": "chat"}
            )
            new_sid = str(result["session_id"])
            title = str(result.get("title", "")) or new_sid

            # 保存当前会话状态
            self._save_current_state()

            # 添加新会话
            self._add_session(new_sid, title)

            # 新会话是第一个，添加 banner
            state = self._sessions.get(new_sid)
            if state is not None:
                state.auto_mode = str(result.get("auto_mode", "off"))
                state.effort_level = str(result.get("effort_level", "medium"))
                state.model_preset = str(result.get("model_preset", "balanced"))

            # 切换到新会话（_add_session 已经把新会话放到头部了）
            self._load_session_state(new_sid)
            self._append(Static(self._BANNER, id=f"banner-{new_sid}"))

            self._update_header("ready")
            self._refresh_tabbar()
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]new session error: {e}[/red]", classes="log-line"))

    async def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        """
        处理用户提交消息事件

        将输入内容发送给当前 chat session，支持系统指令和普通消息。

        参数：
            event: ChatTextArea.Submitted - 提交事件

        处理逻辑：
        1. 检测 /compact 指令：压缩上下文窗口
        2. 检测 /checkpoint 指令：列出或恢复检查点
        3. 检测 /help 指令：显示帮助信息
        4. 检测 /auto 指令：切换自动模式
        5. 检测 /history 指令：查看会话历史
        6. 检测 /close 指令：关闭当前会话
        7. 普通消息：发送给 Agent 处理

        实现细节：
        - 使用 worker 执行异步操作，避免阻塞 UI 消息泵
        - 将用户输入添加到搜索历史（最多 100 条）
        - 更新输入框状态为 "agent is working..."
        - 更新顶部状态栏为 "running"

        使用示例：
            >>> # 用户输入 "/help" -> 显示帮助信息
            >>> # 用户输入 "hello" -> 发送消息给 Agent
        """
        content = event.value.strip()
        if not content:
            return

        if content == "/compact":
            event.text_area.text = ""
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_compact(), name="compact", exclusive=False)
            return

        if content.startswith("/checkpoint"):
            event.text_area.text = ""
            if self._client is not None and self._session_id is not None and not self._busy:
                parts = content.split()
                if len(parts) >= 2:
                    cmd = parts[1]
                    arg = parts[2] if len(parts) >= 3 else ""
                    self.run_worker(self._do_checkpoint(cmd, arg), name="checkpoint", exclusive=False)
                else:
                    self._append(Static("[yellow]usage: /checkpoint list | /checkpoint restore <id>[/yellow]", classes="log-line"))
            return

        if content == "/help":
            event.text_area.text = ""
            self._show_help()
            return

        if content.startswith("/auto"):
            event.text_area.text = ""
            parts = content.split(None, 1)
            mode = parts[1].strip() if len(parts) > 1 else ""
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_set_auto_mode(mode), name="auto_mode", exclusive=False)
            else:
                self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return

        if content.startswith("/effort"):
            event.text_area.text = ""
            parts = content.split(None, 1)
            level = parts[1].strip() if len(parts) > 1 else ""
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_set_effort_level(level), name="effort_level", exclusive=False)
            else:
                self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return

        if content.startswith("/model"):
            event.text_area.text = ""
            parts = content.split(None, 1)
            preset = parts[1].strip() if len(parts) > 1 else ""
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_set_model(preset), name="model_preset", exclusive=False)
            else:
                self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return

        if content.startswith("/name"):
            event.text_area.text = ""
            parts = content.split(None, 1)
            title = parts[1].strip() if len(parts) > 1 else ""
            if not title:
                self._append(Static("[yellow]usage: /name <title>[/yellow]", classes="log-line"))
                return
            if self._client is not None and self._session_id is not None:
                self.run_worker(self._do_rename_session(title), name="rename", exclusive=False)
            else:
                self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return

        if content == "/history":
            event.text_area.text = ""
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_get_history(), name="history", exclusive=False)
            return

        if content == "/close":
            event.text_area.text = ""
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_close_session(), name="close", exclusive=False)
            return

        if self._client is None or self._session_id is None or self._busy:
            self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return
        self._busy = True
        prompt = event.text_area
        prompt.text = ""
        prompt.disabled = True
        prompt.read_only = False
        prompt.border_title = "agent is working..."
        self._append(Static(f"[bold]>[/bold] {content}", classes="user-turn"))
        if content and not content.startswith("/"):
            if content not in self._history:
                self._history.insert(0, content)
                if len(self._history) > 100:
                    self._history = self._history[:100]
        self._update_header("running")
        self.run_worker(self._do_send_message(content), name="send_message", exclusive=False)

    async def _do_compact(self) -> None:
        """
        执行上下文压缩命令

        在 worker 中执行手动压缩命令，完成后显示结果横幅。

        实现细节：
        - 发送 session.compact 命令到 core 服务
        - 显示压缩过程提示和结果
        - 重置上下文占用率为 0
        - 显示摘要 token 数和节省的 token 数

        使用示例：
            >>> # 用户输入 "/compact" -> 执行压缩 -> 显示结果
        """
        if self._client is None or self._session_id is None:
            return
        self._append(Static("[dim]⚡ compacting context...[/dim]", classes="log-line"))
        try:
            result = await self._client.send_command(
                "session.compact",
                {"session_id": self._session_id, "focus": ""},
            )
            summary_tokens = result.get("summary_tokens", 0)
            saved_tokens = result.get("saved_tokens", 0)
            self._last_context_pct = 0.0
            self._append(Static(
                f"[bold cyan]⚡ Context compacted[/bold cyan]"
                f"  [dim]summary={summary_tokens} tokens  saved≈{saved_tokens} tokens[/dim]",
                classes="log-line",
            ))
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]compact error: {e}[/red]", classes="log-line"))

    async def _do_set_auto_mode(self, mode: str) -> None:
        """
        执行自动模式切换命令

        参数：
            mode: str - 目标自动模式（"off" / "read_only" / "on"）

        实现细节：
        - 如果 mode 为空，循环切换三种模式
        - 发送 session.set_auto_mode 命令到 core 服务
        - 成功后更新本地状态并刷新状态栏

        使用示例：
            >>> await self._do_set_auto_mode("read_only")  # 切换到只读自动模式
        """
        if self._client is None or self._session_id is None:
            return

        # 未指定模式时循环切换
        if not mode:
            cycle = {"off": "read_only", "read_only": "on", "on": "off"}
            mode = cycle.get(self._auto_mode, "off")

        if mode not in ("off", "read_only", "on"):
            self._append(Static(f"[yellow]usage: /auto [off|read_only|on], got {mode!r}[/yellow]", classes="log-line"))
            return

        try:
            result = await self._client.send_command(
                "session.set_auto_mode",
                {"session_id": self._session_id, "mode": mode},
            )
            self._auto_mode = result.get("mode", mode)
            self._append(Static(
                f"[bold cyan]⚡ Auto mode[/bold cyan]  [dim]{self._auto_mode}[/dim]",
                classes="log-line",
            ))
            self._update_header("ready")
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]auto mode error: {e}[/red]", classes="log-line"))

    async def _do_set_effort_level(self, level: str) -> None:
        """
        执行努力等级切换命令

        参数：
            level: str - 目标努力等级（"minimal" / "low" / "medium" / "high" / "max"）

        实现细节：
        - 如果 level 为空，循环切换五种等级
        - 发送 session.set_effort_level 命令到 core 服务
        - 成功后更新本地状态并刷新状态栏

        使用示例：
            >>> await self._do_set_effort_level("high")  # 切换到高努力等级
        """
        if self._client is None or self._session_id is None:
            return

        # 未指定等级时循环切换
        if not level:
            cycle = {"minimal": "low", "low": "medium", "medium": "high", "high": "max", "max": "minimal"}
            level = cycle.get(self._effort_level, "medium")

        if level not in ("minimal", "low", "medium", "high", "max"):
            self._append(Static(f"[yellow]usage: /effort [minimal|low|medium|high|max], got {level!r}[/yellow]", classes="log-line"))
            return

        try:
            result = await self._client.send_command(
                "session.set_effort_level",
                {"session_id": self._session_id, "level": level},
            )
            self._effort_level = result.get("level", level)
            self._append(Static(
                f"[bold cyan]🎯 Effort level[/bold cyan]  [dim]{self._effort_level}[/dim]",
                classes="log-line",
            ))
            self._update_header("ready")
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]effort level error: {e}[/red]", classes="log-line"))

    async def _do_set_model(self, preset: str) -> None:
        """
        执行模型预设切换命令

        参数：
            preset: str - 目标模型预设（"fast" / "balanced" / "powerful"）

        实现细节：
        - 如果 preset 为空，循环切换三种预设
        - 发送 session.set_model 命令到 core 服务
        - 成功后更新本地状态并刷新状态栏

        使用示例：
            >>> await self._do_set_model("powerful")  # 切换到强力模型
        """
        if self._client is None or self._session_id is None:
            return

        # 未指定预设时循环切换
        if not preset:
            cycle = {"fast": "balanced", "balanced": "powerful", "powerful": "fast"}
            preset = cycle.get(self._model_preset, "balanced")

        if preset not in ("fast", "balanced", "powerful"):
            self._append(Static(f"[yellow]usage: /model [fast|balanced|powerful], got {preset!r}[/yellow]", classes="log-line"))
            return

        try:
            result = await self._client.send_command(
                "session.set_model",
                {"session_id": self._session_id, "preset": preset},
            )
            self._model_preset = result.get("preset", preset)
            self._append(Static(
                f"[bold cyan]🧠 Model preset[/bold cyan]  [dim]{self._model_preset}[/dim]",
                classes="log-line",
            ))
            self._update_header("ready")
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]model preset error: {e}[/red]", classes="log-line"))

    async def _do_rename_session(self, title: str) -> None:
        """
        执行重命名会话操作

        参数：
            title: str - 新的会话标题

        实现细节：
        - 发送 session.rename 命令
        - 更新当前会话的标题和标签栏
        """
        if self._client is None or self._session_id is None:
            return
        try:
            result = await self._client.send_command(
                "session.rename",
                {"session_id": self._session_id, "title": title},
            )
            new_title = result.get("title", title)
            state = self._state
            if state is not None:
                state.title = new_title
            self._refresh_tabbar()
            self._append(Static(
                f"[bold cyan]📝 Renamed[/bold cyan]  [dim]{new_title}[/dim]",
                classes="log-line",
            ))
            self._update_header("ready")
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]rename error: {e}[/red]", classes="log-line"))

    async def _do_checkpoint(self, cmd: str, arg: str) -> None:
        """
        执行检查点操作（列出或恢复）

        参数：
            cmd: str - 操作命令（"list" 或 "restore"）
            arg: str - 操作参数（恢复时的索引或 ID）

        处理逻辑：
        1. list：列出所有检查点，显示步骤号、时间和内容预览
        2. restore：恢复到指定检查点（支持索引或 ID）

        实现细节：
        - list：调用 session.checkpoint.list，逆序显示检查点
        - restore：如果参数是数字，先查询检查点列表获取 ID，再调用 session.checkpoint.restore
        - 索引 0 表示最近的检查点（逆序排列）

        使用示例：
            >>> await self._do_checkpoint("list", "")      # 列出检查点
            >>> await self._do_checkpoint("restore", "0")  # 恢复到最近检查点
        """
        if self._client is None or self._session_id is None:
            return

        if cmd == "list":
            self._append(Static("[dim]📋 正在列出检查点...[/dim]", classes="log-line"))
            try:
                result = await self._client.send_command(
                    "session.checkpoint.list",
                    {"session_id": self._session_id},
                )
                checkpoints = result.get("checkpoints", [])

                if not checkpoints:
                    self._append(Static("[dim]  暂无检查点[/dim]", classes="log-line"))
                    self._append(Static("[dim]  提示：检查点会在 agent 运行过程中自动创建[/dim]", classes="log-line"))
                else:
                    self._append(Static(f"[bold cyan]===== 检查点列表 ({len(checkpoints)}) =====[/bold cyan]", classes="log-line"))
                    self._append(Static(f"[dim]  格式：[索引] 步骤号 | 时间 | 内容预览[/dim]", classes="log-line"))
                    self._append(Static(f"[dim]  使用：/checkpoint restore <索引> 恢复到指定检查点[/dim]", classes="log-line"))
                    self._append(Static("", classes="log-line"))
                    for i, cp in enumerate(reversed(checkpoints)):
                        ts = cp.get("timestamp", "")
                        summary = cp.get("summary", "")
                        cp_id = cp.get("checkpoint_id", "")

                        ts_display = ts.split("T")[1][:8] if "T" in ts else ts

                        step = cp["step"]
                        step_desc = "初始状态" if step == -1 else f"第 {step+1} 步"

                        self._append(Static(
                            f"  [bold green][{i}][/bold green]  "
                            f"[cyan]step={step}[/cyan] [{step_desc}]  "
                            f"[dim]{ts_display}[/dim]  "
                            f"{summary}",
                            classes="log-line",
                        ))
                        if cp_id:
                            self._append(Static(
                                f"     [dim]ID: {_preview(cp_id, 32)}[/dim]",
                                classes="log-line",
                            ))
                    self._append(Static("", classes="log-line"))
                    self._append(Static(f"[bold cyan]====================================[/bold cyan]", classes="log-line"))
                    self._append(Static(f"[dim]示例：/checkpoint restore 0 恢复到最近状态[/dim]", classes="log-line"))
            except (IpcError, RuntimeError, OSError) as e:
                self._append(Static(f"[red]checkpoint list error: {e}[/red]", classes="log-line"))

        elif cmd == "restore":
            if not arg:
                self._append(Static("[yellow]用法：/checkpoint restore <索引或ID>[/yellow]", classes="log-line"))
                return

            checkpoint_id = arg

            if arg.isdigit():
                index = int(arg)
                self._append(Static(f"[dim]🔄 正在查找检查点索引 {index}...[/dim]", classes="log-line"))
                try:
                    list_result = await self._client.send_command(
                        "session.checkpoint.list",
                        {"session_id": self._session_id},
                    )
                    checkpoints = list_result.get("checkpoints", [])
                    if index < 0 or index >= len(checkpoints):
                        self._append(Static(f"[red]✗ 索引 {index} 超出范围 (0-{len(checkpoints)-1})[/red]", classes="log-line"))
                        return
                    checkpoint_id = checkpoints[-(index + 1)]["checkpoint_id"]
                    self._append(Static(f"[dim]  -> 检查点ID: {_preview(checkpoint_id, 32)}[/dim]", classes="log-line"))
                except (IpcError, RuntimeError, OSError) as e:
                    self._append(Static(f"[red]检查点列表错误: {e}[/red]", classes="log-line"))
                    return

            self._append(Static(f"[dim]🔄 正在恢复检查点...[/dim]", classes="log-line"))
            try:
                result = await self._client.send_command(
                    "session.checkpoint.restore",
                    {"session_id": self._session_id, "checkpoint_id": checkpoint_id},
                )
                if result.get("success"):
                    self._append(Static(
                        f"[bold green]✓[/bold green] 已恢复到第 {result['step']} 步: {result['message']}",
                        classes="log-line",
                    ))
                else:
                    self._append(Static(
                        f"[red]✗ 恢复失败: {result['message']}[/red]",
                        classes="log-line",
                    ))
            except (IpcError, RuntimeError, OSError) as e:
                self._append(Static(f"[red]检查点恢复错误: {e}[/red]", classes="log-line"))

        else:
            self._append(Static("[yellow]用法：/checkpoint list | /checkpoint restore <index>[/yellow]", classes="log-line"))

    def _show_help(self) -> None:
        """
        显示帮助信息

        显示快捷键、斜杠命令和 Skill 使用说明。

        帮助内容分类：
        1. 快捷键：Ctrl+Q、F6、Ctrl+P
        2. 斜杠命令：/help、/auto、/compact、/checkpoint、/history、/close、/skill_list
        3. Skill 说明：手动触发方式、自动触发标记、内置技能列表

        使用示例：
            >>> # 用户输入 "/help" -> 显示帮助信息
        """
        self._append(Static("[bold cyan]===== 帮助信息 =====[/bold cyan]", classes="log-line"))
        self._append(Static("[bold]快捷键：[/bold]", classes="log-line"))
        self._append(Static("  [cyan]Ctrl+Q[/cyan]  退出程序", classes="log-line"))
        self._append(Static("  [cyan]F6[/cyan]       列出检查点", classes="log-line"))
        self._append(Static("  [cyan]Ctrl+P[/cyan]  系统命令面板（Textual 默认）", classes="log-line"))
        self._append(Static("", classes="log-line"))
        self._append(Static("[bold]斜杠命令（输入 / 查看）：[/bold]", classes="log-line"))
        self._append(Static("  [cyan]/help[/cyan]            显示此帮助信息", classes="log-line"))
        self._append(Static("  [cyan]/auto [off|read_only|on][/cyan]  切换自动模式", classes="log-line"))
        self._append(Static("  [cyan]/effort [minimal|low|medium|high|max][/cyan]  切换努力等级", classes="log-line"))
        self._append(Static("  [cyan]/model [fast|balanced|powerful][/cyan]  切换模型预设", classes="log-line"))
        self._append(Static("  [cyan]/compact[/cyan]         压缩上下文窗口", classes="log-line"))
        self._append(Static("  [cyan]/checkpoint list[/cyan]  列出所有检查点", classes="log-line"))
        self._append(Static("  [cyan]/checkpoint restore <n>[/cyan]  恢复到指定检查点", classes="log-line"))
        self._append(Static("  [cyan]/history[/cyan]         查看会话历史", classes="log-line"))
        self._append(Static("  [cyan]/close[/cyan]           关闭当前会话", classes="log-line"))
        self._append(Static("  [cyan]/skill_list[/cyan]      列出所有可用技能", classes="log-line"))
        self._append(Static("", classes="log-line"))
        self._append(Static("[bold]Skill（技能/提示词模板）：[/bold]", classes="log-line"))
        self._append(Static("  输入 /skill_name 手动触发技能", classes="log-line"))
        self._append(Static("  带有 🔄 标记的技能会根据关键词自动触发", classes="log-line"))
        self._append(Static("  内置技能：review(🔍), summarize(📝), orchestrate(🎯), security(🔒), docs(📚), tests(🧪)", classes="log-line"))
        self._append(Static("[bold cyan]====================[/bold cyan]", classes="log-line"))

    async def _do_get_history(self) -> None:
        """
        获取会话历史

        从 core 服务获取当前会话的消息历史并显示。

        实现细节：
        - 发送 session.get_history 命令到 core 服务
        - 按角色显示消息：user（绿色）、assistant（蓝色）、其他（灰色）
        - 使用 _preview 截断长消息（最多 80 字符）

        使用示例：
            >>> # 用户输入 "/history" -> 获取并显示会话历史
        """
        if self._client is None or self._session_id is None:
            return
        try:
            result = await self._client.send_command(
                "session.get_history",
                {"session_id": self._session_id},
            )
            messages = result.get("messages", [])
            self._append(Static(f"[bold cyan]===== 会话历史 ({len(messages)} 条) =====[/bold cyan]", classes="log-line"))
            for i, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                role_color = "green" if role == "user" else "blue" if role == "assistant" else "gray"
                self._append(Static(
                    f"  [bold {role_color}][{i}][/bold {role_color}]  [{role}] {_preview(content, 80)}",
                    classes="log-line",
                ))
            self._append(Static("[bold cyan]====================================[/bold cyan]", classes="log-line"))
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]history error: {e}[/red]", classes="log-line"))

    async def _do_close_session(self) -> None:
        """
        关闭会话

        发送 session.close 命令关闭当前会话，并重置应用状态。

        实现细节：
        - 发送 session.close 命令到 core 服务
        - 重置 _session_id 和 _busy 状态
        - 更新输入框状态为 "session closed"

        使用示例：
            >>> # 用户输入 "/close" -> 关闭会话
        """
        if self._client is None or self._session_id is None:
            return
        try:
            await self._client.send_command(
                "session.close",
                {"session_id": self._session_id},
            )
            self._append(Static(f"[bold green]✓[/bold green] 会话已关闭", classes="log-line"))
            self._session_id = None
            self._busy = False
            prompt = self.query_one("#prompt", ChatTextArea)
            prompt.border_title = "session closed"
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]close session error: {e}[/red]", classes="log-line"))

    async def _do_send_message(self, content: str) -> None:
        """
        在 worker 中发送消息到 core 服务

        使用 worker 执行 IPC 发送，使 App 消息泵在 Agent 运行期间仍能处理键盘/焦点等消息。

        参数：
            content: str - 消息内容

        实现细节：
        - 发送 session.send_message 命令到 core 服务
        - 如果发送失败，重置应用状态（_busy=False、输入框启用、状态更新为 ready）
        - 错误处理使用 try-except 包裹，避免 worker 崩溃

        使用示例：
            >>> await self._do_send_message("hello")  # 发送消息给 Agent
        """
        if self._client is None:
            return
        try:
            await self._client.send_command(
                "session.send_message",
                {"session_id": self._session_id, "content": content},
            )
        except (IpcError, RuntimeError, OSError) as e:
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.read_only = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
            self._update_header("ready")
            self._append(Static(f"[red]send error: {e}[/red]", classes="log-line"))

    async def on_permission_select_decided(self, msg: PermissionSelect.Decided) -> None:
        """
        处理用户的权限决策

        用户在内联权限选择控件中作出决策后，发送 IPC 响应并恢复输入框状态。

        参数：
            msg: PermissionSelect.Decided - 权限决策消息

        处理流程：
        1. 移除权限选择控件
        2. 更新对应的权限审批块为已解决状态
        3. 发送 permission.respond 命令到 core 服务
        4. 如果没有待处理的权限审批，恢复输入框状态

        实现细节：
        - 使用 try-except 包裹，防止异常导致权限审批卡住
        - 发送 IPC 失败时不阻塞，继续处理后续逻辑
        - 最后一个权限审批完成后，重新启用输入框并获取焦点

        使用示例：
            >>> # 用户按 y/a/n/d -> 发布 Decided 消息 -> 此方法处理
        """
        tool_use_id = msg.tool_use_id
        decision = msg.decision
        log.info("permission decided tool_use_id=%s decision=%s", tool_use_id, decision)
        try:
            msg.widget.remove()
            perm_block = self._pending_permission_blocks.pop(tool_use_id, None)
            if perm_block is not None:
                perm_block._resolve(decision)
            if self._client is not None:
                try:
                    await self._client.send_command(
                        "permission.respond",
                        {"tool_use_id": tool_use_id, "decision": decision},
                    )
                except (IpcError, RuntimeError, OSError):
                    pass
            if not self._pending_permission_blocks:
                p = self._prompt()
                if p is not None:
                    p.disabled = False
                    p.read_only = False
                    p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    p.focus()
        except Exception:
            log.exception("on_permission_select_decided failed tool_use_id=%s", tool_use_id)

    def _append(self, widget: Widget) -> None:
        """
        向日志视图追加一个 widget 并滚动到底部

        参数：
            widget: Widget - 要追加的 widget

        实现细节：
        - 获取 #log-view 滚动容器
        - 使用 mount() 添加新 widget
        - 使用 scroll_end() 滚动到底部（禁用动画）

        使用示例：
            >>> self._append(Static("hello", classes="log-line"))
        """
        log_view = self.query_one("#log-view", VerticalScroll)
        log_view.mount(widget)
        log_view.scroll_end(animate=False)

    def _break_llm(self) -> None:
        """
        结束当前 LLM 流式块

        将当前 LLM 流式输出块渲染为 Markdown，并重置 _current_llm。
        下一个 token 将开启新的流式块。

        实现细节：
        - 如果 _current_llm 不为 None，调用 finalize_markdown() 渲染
        - 将 _current_llm 置为 None，表示当前没有活动的流式块

        使用示例：
            >>> # 收到非 llm.token 事件时调用，结束当前流式输出
        """
        if self._current_llm is not None:
            self._current_llm.finalize_markdown()
        self._current_llm = None

    def _mount_permission_select(self, select: PermissionSelect) -> None:
        """
        将权限选择控件挂载到 Screen 顶层

        将选择控件挂载到 #prompt 之前，避免被 VerticalScroll 争抢焦点。

        参数：
            select: PermissionSelect - 权限选择控件

        设计要点：
        - 挂载在输入框之前，确保控件在可视区域
        - 挂载到 Screen 顶层而非滚动容器内，避免焦点问题

        使用示例：
            >>> select = PermissionSelect("call_01")
            >>> self._mount_permission_select(select)
        """
        self.mount(select, before="#prompt")

    def _prompt(self) -> ChatTextArea | None:
        """
        安全获取输入框

        封装对输入框的查询，在控件未挂载时返回 None，便于组件测试。

        返回：
            ChatTextArea | None: 输入框控件或 None

        使用示例：
            >>> prompt = self._prompt()
            >>> if prompt is not None:
            ...     prompt.disabled = False
        """
        try:
            return self.query_one("#prompt", ChatTextArea)
        except Exception:
            return None

    def _render_ctx_bar(self, pct: float) -> str:
        """
        生成上下文占用率的彩色进度条字符串

        参数：
            pct: float - 上下文占用率（0.0-1.0）

        返回：
            str: 格式化的进度条字符串

        颜色策略：
        - >= 85%: 红色（bold red），表示接近上限
        - >= 70%: 黄色（yellow），表示警告
        - < 70%: 灰色（dim），表示正常

        显示格式：
            ctx:50.0% ██████████░░░░░░░░░░

        使用示例：
            >>> bar = self._render_ctx_bar(0.75)
            >>> # 返回 "[yellow]ctx:75.0% ██████████████░░░░[/yellow]"
        """
        filled = int(pct * 20)
        bar = "█" * filled + "░" * (20 - filled)
        label = f"ctx:{pct * 100:.1f}%"
        if pct >= 0.85:
            color = "bold red"
        elif pct >= 0.70:
            color = "yellow"
        else:
            color = "dim"
        return f"[{color}]{label} {bar}[/{color}]"

    def _update_header(self, state: str) -> None:
        """
        根据连接和运行状态刷新顶部状态栏

        参数：
            state: str - 当前状态（ready/running/disconnected/connecting）

        状态栏内容：
        - 应用名称：IwanClaude
        - 连接地址：host:port
        - 会话 ID（如有）
        - 引擎类型（langgraph 用青色高亮）
        - 检查点后端（仅 langgraph 模式且非 none 时显示）
        - 状态指示（颜色编码）

        状态颜色：
        - ready: 绿色
        - running: 黄色
        - disconnected: 红色
        - connecting: 灰色

        使用示例：
            >>> self._update_header("ready")    # 绿色状态
            >>> self._update_header("running")  # 黄色状态
        """
        try:
            header = self.query_one("#header", Label)
        except NoMatches:
            return
        session = f"  [dim]{self._session_id}[/dim]" if self._session_id else ""
        color = {
            "ready": "green",
            "running": "yellow",
            "disconnected": "red",
            "connecting": "dim",
        }.get(state, "dim")

        engine_color = "cyan" if self._engine_type == "langgraph" else "dim"
        engine_info = f"  [{engine_color}]{self._engine_type}[/{engine_color}]"
        if self._engine_type == "langgraph" and self._checkpoint_backend != "none":
            engine_info += f"  [dim]({self._checkpoint_backend})[/dim]"

        auto_color = {"off": "dim", "read_only": "yellow", "on": "magenta"}.get(self._auto_mode, "dim")
        auto_info = f"  [{auto_color}]auto:{self._auto_mode}[/{auto_color}]"

        effort_color = {"minimal": "dim", "low": "cyan", "medium": "green", "high": "yellow", "max": "red"}.get(self._effort_level, "green")
        effort_info = f"  [{effort_color}]effort:{self._effort_level}[/{effort_color}]"

        model_color = {"fast": "cyan", "balanced": "green", "powerful": "magenta"}.get(self._model_preset, "green")
        model_info = f"  [{model_color}]model:{self._model_preset}[/{model_color}]"

        header.update(
            f"[bold]IwanClaude[/bold]  [dim]{self._host}:{self._port}[/dim]"
            f"{session}{engine_info}{auto_info}{effort_info}{model_info}  [{color}]{state}[/{color}]"
        )

    async def _socket_loop(self) -> None:
        """
        Socket 连接循环

        管理 SocketClient 的完整生命周期：连接、订阅事件、处理事件、断线重连。

        工作流程：
        1. 创建 SocketClient 实例
        2. 尝试连接到 core 服务
        3. 连接成功后订阅事件
        4. 创建会话并获取引擎信息
        5. 等待事件循环结束（或出错）
        6. 清理资源并尝试重新连接

        订阅的事件主题：
        - session.*: 会话相关事件
        - run.*: 运行相关事件
        - step.*: 步骤相关事件
        - tool.*: 工具调用事件
        - llm.token: LLM 流式 token
        - llm.usage: LLM 使用统计
        - log.*: 日志事件
        - permission.*: 权限相关事件
        - context.*: 上下文相关事件
        - subagent.*: 子 Agent 事件
        - skill.*: Skill 相关事件

        实现细节：
        - 使用 while True 循环实现断线重连
        - 连接失败后等待 2 秒重试
        - 使用 asyncio.create_task 启动事件循环
        - 注册 on_event 回调处理收到的事件
        - 异常时清理资源并重新连接
        """
        header = self.query_one("#header", Label)

        while True:
            client = SocketClient(self._host, self._port)
            self._client = None
            try:
                await client.connect()
            except (ConnectionRefusedError, OSError):
                log.warning("connection refused %s:%s, retrying", self._host, self._port)
                self._update_header("disconnected")
                await asyncio.sleep(2)
                continue

            log.info("connected to %s:%s", self._host, self._port)
            self._client = client
            self._update_header("connecting")
            loop_task = asyncio.create_task(client.run_event_loop())

            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event)

            client.on_event(on_event)

            try:
                loop_task.add_done_callback(
                    lambda t: log.error("loop_task failed: %s", t.exception())
                    if not t.cancelled() and t.exception() is not None
                    else None
                )
                params: dict[str, Any] = {
                    "topics": [
                        "session.*",
                        "run.*",
                        "step.*",
                        "tool.*",
                        "llm.token",
                        "llm.usage",
                        "log.*",
                        "permission.*",
                        "context.*",
                        "subagent.*",
                        "skill.*",
                    ],
                    "scope": "global",
                }
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                
                await client.send_command("event.subscribe", params)
                created = await client.send_command("session.create", {"mode": "chat"})
                sid = str(created["session_id"])
                title = str(created.get("title", "")) or sid
                self._add_session(sid, title)
                state = self._sessions.get(sid)
                if state is not None:
                    state.auto_mode = str(created.get("auto_mode", "off"))
                    state.effort_level = str(created.get("effort_level", "medium"))
                    state.model_preset = str(created.get("model_preset", "balanced"))
                log.info("session created session_id=%s auto_mode=%s effort_level=%s model_preset=%s", sid, self._auto_mode, self._effort_level, self._model_preset)

                engine_info = await client.send_command("session.engine_info", {})
                self._engine_type = engine_info.get("engine", "legacy")
                self._checkpoint_backend = engine_info.get("checkpoint_backend", "none")

                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = False
                    prompt.read_only = False
                    prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    prompt.focus()
                self._update_header("ready")
                await loop_task
            except IpcError as e:
                header.update(f"[bold]IwanClaude[/bold]  [red]subscribe error: {e}[/red]")
            finally:
                if not loop_task.done():
                    loop_task.cancel()
                self._client = None
                self._session_id = None
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = True
                    prompt.read_only = False
                    prompt.border_title = "disconnected, retrying..."
                self._break_llm()
                await client.close()

            self._update_header("disconnected")
            await asyncio.sleep(2)

    def _handle_event(self, event: dict[str, Any]) -> None:
        """
        事件处理包装器

        根据事件 type 路由到对应渲染逻辑，捕获异常防止 socket loop 因单个事件崩溃。

        参数：
            event: dict[str, Any] - 事件字典（包含 type 字段）

        设计要点：
        - 使用 try-except 包裹，防止单个事件处理异常导致整个事件循环崩溃
        - 调用 _handle_event_inner() 执行实际的事件路由逻辑
        - 异常时记录日志，包含事件类型便于调试
        """
        try:
            self._handle_event_inner(event)
        except Exception:
            log.exception("_handle_event crashed  event_type=%s", event.get("type", "?"))

    def _handle_event_inner(self, event: dict[str, Any]) -> None:
        """
        实际的事件路由逻辑

        根据事件类型将事件分发到对应的处理逻辑，实现实时渲染 Agent 执行过程。

        参数：
            event: dict[str, Any] - 事件字典

        支持的事件类型：
        - llm.token: LLM 流式 token，追加到当前流式块
        - session.waiting_for_input: 会话等待输入，恢复输入框
        - session.closed: 会话关闭，更新状态
        - run.started: 运行开始，显示运行头部
        - skill.invoked: Skill 被调用，显示 Skill 名称和参数
        - subagent.started: 子 Agent 开始，记录信息并显示
        - subagent.finished: 子 Agent 完成，显示结果和耗时
        - step.started: 步骤开始，显示步骤分隔线（非子 Agent）
        - tool.call_started: 工具调用开始，创建工具调用块
        - tool.call_finished: 工具调用完成，更新结果
        - tool.call_failed: 工具调用失败，更新错误状态
        - run.finished: 运行完成，显示成功/失败状态
        - llm.usage: LLM 使用统计，显示 token 消耗和上下文占用率
        - context.compacted: 上下文压缩完成，显示结果
        - permission.requested: 权限请求，显示审批控件
        - permission.denied: 权限被拒绝，更新状态
        - log.line: 日志行，显示日志信息

        设计要点：
        - llm.token 事件优先处理，其他事件先调用 _break_llm() 结束当前流式输出
        - 子 Agent 事件使用特殊格式显示（┌─/└─ 标记）
        - 工具调用块通过 tool_use_id 关联，确保结果正确更新
        """
        t = event.get("type", "")

        # 获取事件的会话 ID（如果有）
        event_sid = event.get("session_id")

        # 如果事件属于后台会话（非当前会话），只更新状态不渲染
        if event_sid and event_sid != self._session_id and event_sid in self._sessions:
            state = self._sessions[event_sid]
            if t == "run.started":
                state.busy = True
                self._refresh_tabbar()
            elif t in ("run.finished", "session.waiting_for_input", "session.closed"):
                state.busy = False
                self._refresh_tabbar()
            return

        if t == "llm.token":
            token = event.get("token", "")
            if self._current_llm is None:
                llm_block = LLMStreamBlock()
                self._append(llm_block)
                self._current_llm = llm_block
            self._current_llm.append_token(token)
            return

        self._break_llm()

        if t == "session.waiting_for_input":
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.read_only = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                prompt.focus()
            self._update_header("ready")

        elif t == "session.closed":
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.read_only = False
                prompt.border_title = "session closed"
            self._update_header("disconnected")

        elif t == "session.renamed":
            new_title = event.get("title", "")
            state = self._state
            if state is not None:
                state.title = new_title
            self._refresh_tabbar()
            self._update_header("ready")

        elif t == "session.auto_mode_changed":
            mode = event.get("mode", "off")
            self._auto_mode = mode
            self._update_header("ready")

        elif t == "session.effort_level_changed":
            level = event.get("level", "medium")
            self._effort_level = level
            self._update_header("ready")

        elif t == "session.model_changed":
            preset = event.get("preset", "balanced")
            self._model_preset = preset
            self._update_header("ready")

        elif t == "run.started":
            run_id = event.get("run_id", "")
            goal = event.get("goal", "")
            self._append(Static(
                f"[dim]run[/dim]  [cyan]{run_id}[/cyan]  [dim]{_preview(goal, 96)}[/dim]",
                classes="run-header",
            ))

        elif t == "skill.invoked":
            skill_name = event.get("skill_name", "")
            arguments = event.get("arguments", "")
            args_preview = _preview(arguments, 80) if arguments else ""
            args_part = f"  [dim]{args_preview}[/dim]" if args_preview else ""
            self._append(Static(
                f"[bold cyan]/{skill_name}[/bold cyan]{args_part}",
                classes="log-line",
            ))

        elif t == "subagent.started":
            run_id = event.get("run_id", "")
            description = event.get("description", "")
            self._subagent_run_ids[run_id] = description
            self._subagent_start_times[run_id] = time.monotonic()
            short_id = run_id[:8] if len(run_id) >= 8 else run_id
            self._append(Static(
                f"[dim]┌─[/dim] [cyan]{_preview(description, 72)}[/cyan]  [dim]{short_id}[/dim]",
                classes="log-line",
            ))

        elif t == "subagent.finished":
            run_id = event.get("run_id", "")
            status = event.get("status", "")
            description = self._subagent_run_ids.pop(run_id, event.get("description", ""))
            start = self._subagent_start_times.pop(run_id, None)
            elapsed = f"  [dim]{time.monotonic() - start:.1f}s[/dim]" if start is not None else ""
            desc_part = f"[cyan]{_preview(description, 72)}[/cyan]{elapsed}"
            if status == "success":
                self._append(Static(
                    f"[dim]└─[/dim] [bold green]✓[/bold green] {desc_part}",
                    classes="log-line",
                ))
            else:
                self._append(Static(
                    f"[dim]└─[/dim] [bold red]✗[/bold red] {desc_part}",
                    classes="log-line",
                ))

        elif t == "step.started":
            run_id = event.get("run_id", "")
            if run_id in self._subagent_run_ids:
                return
            step = event.get("step", "")
            self._append(Static(
                f"[dim]step {step}[/dim]",
                classes="step-divider",
            ))

        elif t == "tool.call_started":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            params = event.get("params") or {}
            run_id = event.get("run_id", "")
            tc_block = ToolCallBlock(tool_name, params)
            if run_id in self._subagent_run_ids:
                tc_block.styles.padding = (0, 2, 0, 6)
            self._pending_tool_blocks[tool_use_id] = tc_block
            self._append(tc_block)

        elif t == "tool.call_finished":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            output = str(event.get("output") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(output, elapsed_ms)

        elif t == "tool.call_failed":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            error_msg = str(event.get("error_message") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(error_msg, elapsed_ms, is_error=True)

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

        elif t == "llm.usage":
            run_id = event.get("run_id", "")
            if run_id in self._subagent_run_ids:
                return
            pct = float(event.get("context_pct") or 0.0)
            self._last_context_pct = pct
            ctx_bar = self._render_ctx_bar(pct)
            self._append(Static(
                f"[dim]  tokens  "
                f"in={event.get('input_tokens')} "
                f"out={event.get('output_tokens')} "
                f"cache={event.get('cache_read_input_tokens')}[/dim]"
                f"  {ctx_bar}",
                classes="usage",
            ))

        elif t == "context.compacted":
            orig = event.get("original_tokens", 0)
            summary = event.get("summary_tokens", 0)
            self._last_context_pct = 0.0
            self._append(Static(
                f"[bold cyan]⚡ Context compacted[/bold cyan]"
                f"  [dim]original≈{orig} tokens → summary={summary} tokens[/dim]",
                classes="log-line",
            ))

        elif t == "permission.requested":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            param_preview = str(event.get("param_preview", ""))
            try:
                _focused_repr = repr(self.focused)
            except Exception:
                _focused_repr = "?"
            log.info(
                "permission.requested tool=%s id=%s  app.focused=%s",
                tool_name, tool_use_id, _focused_repr,
            )
            perm_block = PermissionBlock(tool_use_id, tool_name, param_preview)
            self._pending_permission_blocks[tool_use_id] = perm_block
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.border_title = "permission required"
            self._append(perm_block)
            select = PermissionSelect(tool_use_id)
            self._mount_permission_select(select)
            log.debug("PermissionSelect mounted before #prompt  pending=%d", len(self._pending_permission_blocks))

        elif t == "permission.denied":
            # 处理超时或断连等非用户交互触发的 deny（用户主动 deny 已由 on_permission_select_decided 处理）
            tool_use_id = str(event.get("tool_use_id", ""))
            decision = str(event.get("decision", "denied"))
            if tool_use_id in self._pending_permission_blocks:
                perm_block = self._pending_permission_blocks.pop(tool_use_id)
                perm_block._resolve(decision)
                try:
                    select = self.query_one(PermissionSelect)
                    select.remove()
                except Exception:
                    pass
                if not self._pending_permission_blocks:
                    p = self._prompt()
                    if p is not None:
                        p.disabled = False
                        p.read_only = False
                        p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                        p.focus()

        elif t == "log.line":
            level = event.get("level", "INFO")
            color = "bold red" if level == "ERROR" else ("yellow" if level == "WARNING" else "dim")
            self._append(Static(
                f"[{color}]{level}[/{color}]  "
                f"[dim]{event.get('source', '')}[/dim]  {event.get('message', '')}",
                classes="log-line",
            ))


def run(config: IwanConfig, replay_run_id: str | None = None) -> None:
    """
    TUI 入口函数

    创建并启动 IwanTuiApp 实例，进入终端用户界面。

    参数：
        config: IwanConfig - 应用配置（包含 host、port 等）
        replay_run_id: str | None - 可选，连接后回放指定运行的事件

    实现步骤：
    1. 使用配置中的 host 和 port 创建 IwanTuiApp 实例
    2. 调用 app.run() 启动 Textual 应用事件循环

    使用示例：
        >>> from iwan_claude.tui.app import run
        >>> from iwan_claude.core.config import get_config
        >>> config = get_config()
        >>> run(config)

    调用方式：
        python -m iwan_claude.tui
        python -m iwan_claude.tui --replay run-abc123
    """
    app = IwanTuiApp(config.host, config.port, replay_run_id=replay_run_id)
    app.run()
