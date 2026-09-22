# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 内联权限审批控件模块
# ---------------------------------------------------------------------------

from __future__ import annotations
# 本模块从 app.py 中提取 PermissionSelect 和 PermissionBlock 两个类。
# PermissionSelect: 可交互的权限选择控件，支持键盘操作选择 Allow/Deny 等决策。
# PermissionBlock: 权限审批摘要块，显示待审批或已解决的权限请求状态。
# 设计要点：采用内联审批设计（无 ModalScreen 弹窗），在日志流中直接操作。
# ---------------------------------------------------------------------------

# 导入 logging 模块，用于记录权限审批过程中的调试日志
import logging

# 从 textual 导入 events 模块，用于处理键盘和焦点事件
from textual import events

# 从 textual.message 导入 Message 基类，用于定义自定义消息（Decided、Resolved）
from textual.message import Message

# 从 textual.widgets 导入 Static，PermissionSelect 和 PermissionBlock 都继承 Static
from textual.widgets import Static

# 获取当前模块的 logger，用于输出调试信息
log = logging.getLogger(__name__)


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
    - can_focus=True 使该控件可以接收键盘事件

    使用示例：
        >>> select = PermissionSelect("call_01")
        >>> app.mount(select, before="#prompt")
        >>> # 用户按 y/a/n/d 或方向键选择
    """

    # 允许该控件获取键盘焦点，从而接收 on_key 事件
    can_focus = True

    # 定义该 widget 的默认 CSS 样式：自动高度、左右 padding、底部 margin
    DEFAULT_CSS = """
    PermissionSelect {
        height: auto;
        padding: 0 2;
        margin-bottom: 1;
    }
    """

    # 权限选项元组：每项为 (决策值, 显示标签, 快捷键提示)
    # 决策值是 IPC 协议中使用的标识符
    _CHOICES: tuple[tuple[str, str, str], ...] = (
        ("allow_once",   "Allow once",   "y / 1"),
        ("always_allow", "Always allow", "a / 2"),
        ("deny_once",    "Deny",         "n / 3"),
        ("always_deny",  "Always deny",  "d / 4"),
    )

    # 键盘字符到决策值的映射表，用于快速选择
    # 支持字母键（y/a/n/d）和数字键（1/2/3/4）两种方式
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
        宿主 App 收到此消息后，会通过 IPC 将决策发送给 core 服务。

        属性：
            widget: PermissionSelect - 控件引用
            tool_use_id: str - 工具调用 ID（用于 IPC 回复配对）
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
            # 保存控件引用，宿主 App 可通过此字段操作控件
            self.widget = widget
            # 保存工具调用 ID，用于 IPC 回复时的请求-响应配对
            self.tool_use_id = tool_use_id
            # 保存决策字符串
            self.decision = decision
            # 调用父类 Message 的 __init__ 完成消息初始化
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
        # 调用父类 Static 的 __init__，传入空字符串作为初始显示内容
        super().__init__("")
        # 保存工具调用 ID
        self._tool_use_id = tool_use_id
        # 初始化光标位置为第一个选项（索引 0）
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
        # 首次渲染：生成选项列表并显示
        self.update(self._render_ui())
        # 主动获取键盘焦点，使该控件能接收 on_key 事件
        self.focus()
        # 记录调试日志：can_focus 状态和当前 app 聚焦的控件
        log.debug(
            "PermissionSelect.on_mount  can_focus=%s  focused_after=%r",
            self.can_focus,
            self.app.focused,
        )
        # 在下一帧检查焦点状态（验证 focus() 是否真正生效）
        self.app.call_after_refresh(self._log_deferred_focus)

    def _log_deferred_focus(self) -> None:
        """
        在下一帧记录焦点状态

        用于调试焦点是否真正转移到本控件，帮助排查焦点问题。
        由于 Textual 的 focus 操作可能不是立即生效的，
        所以使用 call_after_refresh 推迟到下一帧再检查。
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
        当用户通过 Tab 或编程方式将焦点移到本控件时触发。
        """
        log.debug("PermissionSelect.on_focus  has_focus=%s  app.focused=%r", self.has_focus, self.app.focused)

    def on_blur(self, event: events.Blur) -> None:
        """
        焦点离开时记录

        用于追踪焦点是否被其他控件抢走，帮助调试焦点问题。
        当焦点从本控件转移到其他控件时触发。
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
        # 创建行列表，用于拼接最终显示文本
        lines: list[str] = []
        # 遍历所有选项，i 为索引，_ 为决策值（此处不使用），label 为显示标签，key_hint 为快捷键提示
        for i, (_, label, key_hint) in enumerate(self._CHOICES):
            # 如果当前光标在此选项上，使用粗体青色和 ❯ 标记高亮
            if i == self._cursor:
                lines.append(f"  [bold cyan]❯ {label}[/bold cyan]  [dim]{key_hint}[/dim]")
            else:
                # 非光标选项使用普通缩进显示
                lines.append(f"    {label}  [dim]{key_hint}[/dim]")
        # 在末尾追加操作提示行
        lines.append("[dim]  ↑↓ navigate   enter confirm[/dim]")
        # 将所有行拼接为字符串并返回
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
        # 记录调试日志：按键名称和字符
        log.debug("PermissionSelect.on_key  key=%r  char=%r", event.key, event.character)
        # 获取按键名称（如 "up"、"down"、"enter"、"y" 等）
        key = event.key
        # 向上移动：支持 "up" 方向键和 "k" 键（vim 风格）
        if key in ("up", "k"):
            # 阻止事件继续传播到父级控件
            event.stop()
            # 光标向上移动，使用取模实现循环（到顶后跳到底部）
            self._cursor = (self._cursor - 1) % len(self._CHOICES)
            # 重新渲染 UI 以显示新的光标位置
            self.update(self._render_ui())
        # 向下移动：支持 "down" 方向键和 "j" 键（vim 风格）
        elif key in ("down", "j"):
            # 阻止事件继续传播
            event.stop()
            # 光标向下移动，使用取模实现循环（到底后跳回顶部）
            self._cursor = (self._cursor + 1) % len(self._CHOICES)
            # 重新渲染 UI
            self.update(self._render_ui())
        # Enter 键：确认当前光标位置的选项
        elif key == "enter":
            # 阻止事件继续传播
            event.stop()
            # 获取当前光标所在选项的决策值并发布
            self._pick(self._CHOICES[self._cursor][0])
        else:
            # 其他按键：查找快捷键映射表
            decision = self._KEY_MAP.get(key)
            # 如果按键在映射表中（即 y/a/n/d/1/2/3/4）
            if decision is not None:
                # 阻止事件继续传播
                event.stop()
                # 直接发布对应的决策
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
        # 记录调试日志：发布的决策值
        log.debug("PermissionSelect._pick  decision=%s", decision)
        # 创建 Decided 消息实例并发布到消息队列
        self.post_message(self.Decided(self, self._tool_use_id, decision))


class PermissionBlock(Static):
    """
    日志中的权限审批摘要块

    在日志流中显示工具调用的权限请求状态，支持待审批和已解决两种状态。
    与 PermissionSelect 不同，PermissionBlock 是只读的摘要显示，
    而 PermissionSelect 是可交互的选择控件。
    两者配合使用：PermissionBlock 显示状态，PermissionSelect 提供交互。

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

    # 决策值到友好显示标签的映射表（私有版本用于实际查找）
    _LABEL_MAP: dict[str, str] = {
        "allow_once":   "allowed (once)",
        "always_allow": "always allowed",
        "deny_once":    "denied",
        "always_deny":  "always denied",
        "timeout":      "⏱ timed out",
    }
    # 公开访问接口：外部通过 LABEL_MAP 访问映射表
    LABEL_MAP = _LABEL_MAP

    class Resolved(Message):
        """
        用户作出权限决策时发布的消息

        携带权限块引用和决策字符串，供宿主 App 监听。
        与 PermissionSelect.Decided 配合使用：
        PermissionSelect 负责交互选择，PermissionBlock.Resolved 负责状态通知。

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
            # 保存权限块引用
            self.block = block
            # 保存决策字符串
            self.decision = decision
            # 调用父类 Message 的 __init__
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
        # 保存工具调用 ID
        self._tool_use_id = tool_use_id
        # 保存工具名称
        self._tool_name = tool_name
        # 保存参数预览字符串
        self._param_preview = param_preview
        # 标记尚未解决
        self._resolved = False
        # 调用父类 Static 的 __init__，显示初始待审批文本，附加 "log-line" CSS 类
        super().__init__(self._pending_text(), classes="log-line")

    def _pending_text(self) -> str:
        """
        生成待审批状态的显示文本

        返回：
            str: 格式化的待审批文本（红色 "? permission" + 工具名称 + 参数预览）

        显示格式：
            ? permission  tool_name  params_preview
        """
        # 如果有参数预览，添加 dim 样式的预览文本
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        # 拼接完整的待审批显示文本
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
        # 防止重复解决：如果已经解决过则直接返回
        if self._resolved:
            return
        # 标记为已解决
        self._resolved = True
        # 判断是否为允许类决策（allow_once 或 always_allow）
        allowed = decision in ("allow_once", "always_allow")
        # 根据决策类型选择图标：允许显示绿色 ✓，拒绝显示红色 ✗
        icon = "[bold green]✓[/bold green]" if allowed else "[bold red]✗[/bold red]"
        # 通过 LABEL_MAP 获取决策的友好显示标签
        label = self._LABEL_MAP.get(decision, decision)
        # 如果有参数预览，添加 dim 样式
        preview = f"[dim]{self._param_preview}[/dim]" if self._param_preview else ""
        # 更新显示文本为已解决状态
        self.update(
            f"{icon} permission  [bold]{self._tool_name}[/bold]{preview}  [dim]{label}[/dim]"
        )
        # 发布 Resolved 消息，通知宿主 App 权限审批已完成
        self.post_message(self.Resolved(self, decision))