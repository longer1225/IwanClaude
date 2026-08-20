"""
技能确认控件 - 自动匹配到技能时让用户确认是否使用

【学习要点】
1. 内联控件模式：挂在日志流中，不打断用户视线
2. Message 机制：通过 Decided 消息与宿主 App 通信
3. 键盘快捷键：支持 y/n 快速选择，与 PermissionSelect 模式一致

【与 PermissionSelect 的区别】
- PermissionSelect：4 个选项（allow_once / always_allow / deny_once / always_deny）
- SkillConfirm：2 个选项（use_skill / skip）—— 只需"使用"或"跳过"

【使用场景】
用户输入"帮我总结一下" → Core 自动匹配到 summarize 技能 →
TUI 弹出 SkillConfirm 控件 → 用户选择是否使用该技能
"""
from __future__ import annotations

from textual.message import Message
from textual.widgets import Static


class SkillConfirm(Static):
    """
    技能确认内联控件

    【工作原理】
    1. 显示匹配到的技能名称、分数和描述
    2. 提供两个选项：使用技能 (y) / 正常对话 (n)
    3. 用户选择后发布 Decided 消息，宿主 App 处理后续 IPC 通信

    【UI 布局】
    ┌─────────────────────────────────────────┐
    │ 检测到可能匹配的技能：summarize (score: 2.0) │
    │ 描述：总结对话并保存到会话笔记                  │
    │                                           │
    │  > 使用技能 (y)   正常对话 (n)              │
    └─────────────────────────────────────────┘
    """

    # 允许该控件获取键盘焦点
    can_focus = True

    DEFAULT_CSS = """
    SkillConfirm {
        height: auto;
        padding: 0 2;
        margin-bottom: 1;
    }
    """

    # 选项：(决策值, 显示标签, 快捷键)
    _CHOICES: tuple[tuple[str, str, str], ...] = (
        ("use_skill", "使用技能", "y / 1"),
        ("skip", "正常对话", "n / 2"),
    )

    # 键盘映射
    _KEY_MAP: dict[str, str] = {
        "y": "use_skill", "1": "use_skill",
        "n": "skip", "2": "skip",
    }

    class Decided(Message):
        """
        用户作出决策时发布的消息

        属性：
            widget: SkillConfirm - 控件引用
            skill_name: str - 匹配到的技能名称
            decision: str - 决策（use_skill / skip）
            original_content: str - 用户原始输入（用于重新发送）
            session_id: str - 会话 ID（用于重新发送）
        """

        def __init__(
            self,
            widget: SkillConfirm,
            skill_name: str,
            decision: str,
            original_content: str,
            session_id: str,
        ) -> None:
            self.widget = widget
            self.skill_name = skill_name
            self.decision = decision
            self.original_content = original_content
            self.session_id = session_id
            super().__init__()

    def __init__(
        self,
        skill_name: str,
        score: float,
        description: str,
        original_content: str,
        session_id: str,
    ) -> None:
        """
        初始化技能确认控件

        参数：
            skill_name: str - 匹配到的技能名称
            score: float - 匹配分数
            description: str - 技能描述
            original_content: str - 用户原始输入（确认后需重新发送）
            session_id: str - 会话 ID
        """
        super().__init__("")
        self._skill_name = skill_name
        self._score = score
        self._description = description
        self._original_content = original_content
        self._session_id = session_id
        self._cursor = 0
        self._render()

    def _render(self) -> None:
        """渲染控件内容"""
        lines: list[str] = []
        lines.append(
            f"[bold yellow]⚠ 检测到可能匹配的技能：[/bold yellow]"
            f"[cyan]{self._skill_name}[/cyan] "
            f"[dim](score: {self._score:.1f})[/dim]"
        )
        if self._description:
            lines.append(f"[dim]{self._description}[/dim]")
        lines.append("")

        for i, (_, label, hint) in enumerate(self._CHOICES):
            if i == self._cursor:
                lines.append(f"  [bold green]> {label}[/bold green] [dim]({hint})[/dim]")
            else:
                lines.append(f"    [dim]{label} ({hint})[/dim]")

        self.update("\n".join(lines))

    def on_key(self, event) -> None:
        """处理键盘事件"""
        from textual.events import Key

        key: Key = event
        char = key.character.lower() if key.character else ""

        # 快捷键直接选择
        if char in self._KEY_MAP:
            event.prevent_default()
            self._decide(self._KEY_MAP[char])
            return

        # 方向键导航
        if key.name == "up" or char == "k":
            event.prevent_default()
            self._cursor = (self._cursor - 1) % len(self._CHOICES)
            self._render()
        elif key.name == "down" or char == "j":
            event.prevent_default()
            self._cursor = (self._cursor + 1) % len(self._CHOICES)
            self._render()
        elif key.name in ("enter", "return"):
            event.prevent_default()
            decision = self._CHOICES[self._cursor][0]
            self._decide(decision)

    def _decide(self, decision: str) -> None:
        """发布决策消息"""
        self.post_message(
            self.Decided(
                self,
                self._skill_name,
                decision,
                self._original_content,
                self._session_id,
            )
        )
