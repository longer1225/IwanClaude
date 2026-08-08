# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 聊天输入框模块
# ---------------------------------------------------------------------------

from __future__ import annotations
# 本模块从 app.py 中提取 ChatTextArea 类，负责用户输入的接收和提交。
# 设计要点：
# - Enter 提交消息（当补全弹窗有选中项时优先选择命令）
# - 修饰键 + Enter 插入多行
# - 斜杠命令导航（↑↓/Tab/Esc）转发到 SlashCompleteWidget
# - 通过消息机制（Submitted、SlashChanged）与宿主 App 通信
# ---------------------------------------------------------------------------

# 从 textual 导入 events 模块，用于处理键盘事件
from textual import events

# 从 textual.css.query 导入 NoMatches，用于处理查询 SlashCompleteWidget 时未找到的异常
from textual.css.query import NoMatches

# 从 textual.message 导入 Message 基类，用于定义 Submitted 和 SlashChanged 消息
from textual.message import Message

# 从 textual.widgets 导入 TextArea，ChatTextArea 继承 TextArea 获得文本编辑能力
from textual.widgets import TextArea

# 从同包的 slash_complete 模块导入 SlashCompleteWidget
# 用于在输入框上方显示斜杠命令自动补全弹窗
from iwan_claude.tui.widgets.slash_complete import SlashCompleteWidget


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
    - SlashCompleteWidget 不获取焦点（can_focus=False），焦点始终在本输入框

    使用示例：
        >>> area = ChatTextArea()
        >>> area.text = "/help"  # 触发 SlashChanged 消息
        >>> # 用户按 Enter 发布 Submitted 消息
    """

    # 定义该 widget 的默认 CSS 样式
    # 特点：高度 3-12 行自适应、圆角边框、聚焦时边框变 accent 色
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
        宿主 App 收到此消息后，会读取 text_area.text 并通过 IPC 发送给 core 服务。

        属性：
            text_area: ChatTextArea - 输入框引用
            value: str - 输入框内容（即用户提交的文本）
        """

        def __init__(self, area: ChatTextArea) -> None:
            """
            初始化提交消息

            参数：
                area: ChatTextArea - 输入框引用
            """
            # 保存输入框引用
            self.text_area = area
            # 直接获取输入框的当前文本作为 value
            self.value = area.text
            # 调用父类 Message 的 __init__ 完成消息初始化
            super().__init__()

    class SlashChanged(Message):
        """
        斜杠命令查询变化时发布的消息

        当输入内容以 / 开头且无空格时发布，通知宿主 App 更新自动补全弹窗。
        当用户输入了空格或以 / 开头以外的内容时，发布 query=None 通知收起弹窗。

        属性：
            query: str | None - 查询字符串（/ 之后的部分），None 表示收起弹窗
        """

        def __init__(self, query: str | None) -> None:
            """
            初始化斜杠变化消息

            参数：
                query: str | None - 查询字符串，None 表示收起弹窗
            """
            # 保存查询字符串
            self.query = query
            # 调用父类 Message 的 __init__
            super().__init__()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """
        文本变化时检测 / 前缀，通知宿主 App 更新自动补全弹窗

        每当输入框文本发生变化（用户输入、删除、粘贴等）时触发。
        检测是否应以 / 开头且不含空格，以决定是否显示自动补全弹窗。

        参数：
            event: TextArea.Changed - 文本变化事件（包含新旧文本信息）

        处理逻辑：
        - 如果文本以 / 开头且无空格，发布 SlashChanged(query=...) 消息
        - 否则发布 SlashChanged(query=None) 消息，收起弹窗

        使用示例：
            >>> # 用户输入 "/help" -> SlashChanged(query="help")
            >>> # 用户输入 "/help " -> SlashChanged(query=None)（有空格）
            >>> # 用户输入 "hello" -> SlashChanged(query=None)（不以 / 开头）
        """
        # 获取当前输入框文本
        text = self.text
        # 检查是否以 / 开头且不含空格（空格表示命令已输入完毕）
        if text.startswith("/") and " " not in text:
            # 发布 SlashChanged 消息，携带 / 之后的部分作为查询字符串
            self.post_message(ChatTextArea.SlashChanged(query=text[1:]))
        else:
            # 不满足斜杠命令条件，发布 query=None 通知收起弹窗
            self.post_message(ChatTextArea.SlashChanged(query=None))

    async def _on_key(self, event: events.Key) -> None:
        """
        键盘事件处理

        支持 Enter 提交、斜杠命令导航、换行插入等操作。
        这是该控件最核心的方法，处理所有键盘交互逻辑。

        参数：
            event: events.Key - 键盘事件

        处理逻辑：
        1. 查找自动补全弹窗（如果存在）
        2. Enter：提交消息或选择弹窗中的命令
        3. Cmd/Shift/Alt+Enter：插入换行（多行模式）
        4. ↑↓：导航自动补全弹窗
        5. Tab：选择弹窗中的命令
        6. Esc：收起自动补全弹窗
        7. 其他：交回父类 TextArea 处理

        实现细节：
        - 使用 event.stop() 阻止事件冒泡到父级控件
        - 使用 event.prevent_default() 阻止 TextArea 的默认行为
        - 只有当弹窗存在且有选中项时，Enter/Tab 才选择命令
        - 只有非只读模式下才能插入换行
        """
        # 获取按键名称
        key = event.key

        # 尝试查找 SlashCompleteWidget 弹窗
        popup: SlashCompleteWidget | None = None
        try:
            # 通过 app.query_one 查找 SlashCompleteWidget 实例
            popup = self.app.query_one(SlashCompleteWidget)
        except NoMatches:
            # 如果弹窗不存在（未挂载），设为 None
            popup = None

        # === Enter 键处理 ===
        if key == "enter":
            # 阻止事件冒泡和默认行为（防止 TextArea 插入换行）
            event.stop()
            event.prevent_default()
            # 如果补全弹窗存在且有可选项，优先选择弹窗中的命令
            if popup is not None and popup.has_selection():
                popup.select_current()
                return
            # 如果没有弹窗或无选中项，且输入框非空，发布提交消息
            if self.text.strip():
                self.post_message(self.Submitted(self))
            return

        # === 多行换行键处理（修饰键 + Enter） ===
        # 支持 alt+enter、shift+enter、ctrl+j、super+enter 等组合
        if key in ("alt+enter", "shift+enter", "ctrl+j", "super+enter"):
            # 阻止事件冒泡和默认行为
            event.stop()
            event.prevent_default()
            # 仅在非只读模式下插入换行
            if not self.read_only:
                self.insert("\n")
            return

        # === 斜杠命令弹窗导航 ===
        if popup is not None:
            # 上箭头：导航到上一个选项
            if key == "up":
                event.stop()
                event.prevent_default()
                popup.move_up()
                return
            # 下箭头：导航到下一个选项
            elif key == "down":
                event.stop()
                event.prevent_default()
                popup.move_down()
                return
            # Tab 键：选择当前选项
            elif key == "tab":
                event.stop()
                event.prevent_default()
                popup.select_current()
                return
            # Esc 键：收起弹窗
            elif key == "escape":
                event.stop()
                event.prevent_default()
                # 发布 SlashChanged(query=None) 通知宿主 App 收起弹窗
                self.post_message(ChatTextArea.SlashChanged(query=None))
                return

        # === 其他按键交回父类 TextArea 处理 ===
        # 包括字符输入、删除、复制粘贴等标准文本编辑操作
        await super()._on_key(event)