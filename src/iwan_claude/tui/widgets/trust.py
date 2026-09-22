# -*- coding: utf-8 -*-  # noqa: UP009 - 与同目录 widget 模块保持声明一致
# ---------------------------------------------------------------------------
# 内联信任询问控件模块（Layer 0 信任门，S9 Part A）
# ---------------------------------------------------------------------------
# 与 permission.py 的 PermissionSelect 形态相同（内联列表、键盘导航），但语义有两点关键差异：
# 1. 信任询问不阻塞执行——挂载时【不抢焦点】，用户可以完全无视它继续打字，
#    会话照常可用（写操作走逐次审批的现状语义）；PermissionSelect 是阻塞等待，必须聚焦。
# 2. 决策落盘为目录级持久规则（trust.toml），改变的是"以后的会话"；
#    PermissionSelect 的 always_allow 只到"这类调用"的粒度。
# ---------------------------------------------------------------------------

from __future__ import annotations

import logging

from textual import events
from textual.message import Message
from textual.widgets import Static

log = logging.getLogger(__name__)


class TrustSelect(Static):
    """
    内联信任选择控件 - 答复"允许 iwan 在这个目录工作吗"

    可聚焦但挂载时不自动抢焦点：信任是后台询问，不是拦路审批。
    选项 decision 值与 trust.respond 协议对齐；"dismiss" 仅移除控件、不发 IPC。
    """

    # 可聚焦：Tab 到达后接管方向键/快捷键
    can_focus = True

    # 自动高度 + 左右留白的内联样式
    DEFAULT_CSS = """
    TrustSelect {
        height: auto;
        padding: 0 2;
        margin-bottom: 1;
    }
    """

    # 信任选项：(决策值, 显示标签, 快捷键提示)——决策值直接作为 trust.respond 的 decision
    _CHOICES: tuple[tuple[str, str, str], ...] = (
        ("allow", "Trust this folder", "y / 1"),
        ("deny", "Don't trust (block writes & exec)", "n / 2"),
        ("dismiss", "Not now", "esc / 3"),
    )

    # 快捷键映射：y/n 语义与安全方向对齐，esc 与数字 3 退出询问
    _KEY_MAP: dict[str, str] = {
        "y": "allow", "1": "allow",
        "n": "deny", "2": "deny",
        "escape": "dismiss", "3": "dismiss",
    }

    class Decided(Message):
        """用户答复信任询问时发布：携带控件引用、会话 ID、目录与决策值"""

        # 保存答复所需的全部上下文（宿主 App 据此发 trust.respond）
        def __init__(self, widget: TrustSelect, session_id: str, cwd: str, decision: str) -> None:
            self.widget = widget
            self.session_id = session_id
            self.cwd = cwd
            self.decision = decision
            super().__init__()

    # 绑定目标会话/目录；has_instruction_files 决定是否附风险警示行
    def __init__(self, session_id: str, cwd: str, *, has_instruction_files: bool = False) -> None:
        super().__init__("")
        self._session_id = session_id
        self._cwd = cwd
        self._has_instruction_files = has_instruction_files
        self._cursor = 0

    # 挂载：渲染并【不】focus()——信任询问不抢正在输入的用户的键盘
    def on_mount(self) -> None:
        self.update(self._render_ui())
        log.debug("TrustSelect mounted cwd=%s (no auto-focus)", self._cwd)

    # 生成标题（目录+风险警示）与带光标的选项列表
    def _render_ui(self) -> str:
        lines: list[str] = [
            f"[bold yellow]🔒 trust this folder?[/bold yellow]  [cyan]{self._cwd}[/cyan]",
        ]
        if self._has_instruction_files:
            # 含 CLAUDE.md/AGENTS.md 的陌生目录：内容会被自动读进系统提示，警示一句
            lines.append("[dim]⚠ contains instruction files (auto-loaded into prompt)[/dim]")
        for i, (_, label, key_hint) in enumerate(self._CHOICES):
            if i == self._cursor:
                lines.append(f"  [bold cyan]❯ {label}[/bold cyan]  [dim]{key_hint}[/dim]")
            else:
                lines.append(f"    {label}  [dim]{key_hint}[/dim]")
        lines.append("[dim]  tab to focus   ↑↓ navigate   enter confirm[/dim]")
        return "\n".join(lines)

    # 键盘处理：方向导航 / enter 确认光标 / 快捷键直达（仅聚焦时生效）
    def on_key(self, event: events.Key) -> None:
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

    # 发布 Decided 消息；IPC 发送与控件移除由宿主 App 处理
    def _pick(self, decision: str) -> None:
        log.debug("TrustSelect._pick decision=%s cwd=%s", decision, self._cwd)
        self.post_message(self.Decided(self, self._session_id, self._cwd, decision))
