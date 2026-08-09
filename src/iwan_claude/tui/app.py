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
- IwanTuiApp: TUI 主应用类（本模块定义）
- LLMStreamBlock: 流式 LLM 输出块（从 widgets 模块导入）
- ToolCallBlock: 可折叠的工具调用块（从 widgets 模块导入）
- PermissionSelect: 内联权限选择控件（从 widgets 模块导入）
- SlashCompleteWidget: 斜杠命令自动补全弹窗（从 widgets 模块导入）
- ChatTextArea: 聊天输入框（从 widgets 模块导入）

使用示例：
    >>> from iwan_claude.tui.app import run
    >>> from iwan_claude.core.config import get_config
    >>> config = get_config()
    >>> run(config)
"""

# 标准库导入
from __future__ import annotations  # 启用延迟类型注解求值，支持前向引用

import asyncio  # 异步编程核心库，用于事件循环和非阻塞 IO
import logging  # 日志记录，用于调试和运行时信息输出
import time  # 时间模块，用于子 Agent 执行耗时计算

# 第三方库导入
from typing import Any  # 类型注解工具，表示任意类型

log = logging.getLogger(__name__)  # 获取当前模块的日志记录器

# Textual 框架导入 - 构建终端 UI 的核心框架
from textual import events  # 事件系统，处理键盘、鼠标等输入事件
from textual.app import App, ComposeResult  # App 基类和组件组合返回类型
from textual.binding import Binding  # 快捷键绑定定义
from textual.containers import Horizontal, VerticalScroll  # 布局容器（水平排列、垂直滚动）
from textual.css.query import NoMatches  # CSS 查询异常，当查询的组件不存在时抛出
from textual.widget import Widget  # Widget 基类，所有 UI 组件的父类
from textual.widgets import Label, Static  # 静态文本标签和可更新的静态组件

# 项目内部模块导入
from iwan_claude.core.config import IwanConfig  # 应用配置类，包含 host、port 等
from iwan_claude.core.skills.loader import SkillLoader  # 技能加载器，用于获取可用 Skill 列表
from iwan_claude.core.transport.socket_client import IpcError, SocketClient  # IPC 通信客户端和错误类型

# 从子模块导入已拆分的组件和工具函数
from iwan_claude.tui.formatters import _preview, _params_str, _param_summary  # 文本格式化工具函数
from iwan_claude.tui.models import _SessionState  # 会话状态数据类
from iwan_claude.tui.widgets import (  # 所有自定义 UI 组件
    ChatTextArea,
    LLMStreamBlock,
    PermissionBlock,
    PermissionSelect,
    SlashCompleteWidget,
    ToolCallBlock,
)


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
    - Ctrl+T/W：新建/关闭会话
    - Alt+1~9：切换会话

    设计模式说明：
    - 采用单 App 多会话架构，通过 _SessionState 管理每个会话的独立 UI 状态
    - SocketClient 与 core 服务通过 IPC 通信，事件驱动更新 UI
    - Textual 的消息机制实现组件间解耦通信
    - Worker 机制确保异步操作不阻塞 UI 消息泵
    """

    # 设置应用标题，显示在终端窗口标题栏
    TITLE = "IwanClaude"

    # 定义全局快捷键绑定，Textual 会自动将这些键位映射到对应的 action_* 方法
    BINDINGS = [
        # Ctrl+Q 触发 action_quit()，退出程序
        Binding("ctrl+q", "quit", "退出"),
        # F6 触发 action_checkpoint_list()，列出检查点
        Binding("f6", "checkpoint_list", "列出检查点"),
        # Ctrl+P 触发 Textual 内置的命令面板
        Binding("ctrl+p", "app_command", "命令面板"),
        # Ctrl+R 触发 action_search_history()，搜索历史
        Binding("ctrl+r", "search_history", "搜索历史"),
        # Ctrl+T 触发 action_new_session()，新建会话
        Binding("ctrl+t", "new_session", "新建会话"),
        # Ctrl+W 触发 action_close_session()，关闭会话
        Binding("ctrl+w", "close_session", "关闭会话"),
        # Alt+1~9 触发 action_switch_session(index)，切换到指定序号的会话
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

    # 定义全局 CSS 样式，控制应用的视觉外观
    CSS = """
    /* 屏幕背景色，使用 Textual 的 $background 主题变量 */
    Screen { background: $background; }
    /* 标签栏容器：固定高度 1 行，背景色 surface，顶部停靠 */
    #tabbar {
        height: 1;
        background: $surface;
        dock: top;
    }
    /* 标签基础样式：内边距、高度、文字颜色 */
    .tab {
        padding: 0 1;
        height: 1;
        color: $text-muted;
    }
    /* 活动标签：反色高亮，粗体 */
    .tab.active {
        background: $background;
        color: $text;
        text-style: bold;
    }
    /* 忙碌标签：黄色警告色 */
    .tab.busy {
        color: $warning;
    }
    /* 顶部状态栏：固定高度 1 行，显示连接和运行状态 */
    #header {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    /* 日志滚动视图：占据剩余空间（1fr），带滚动条 */
    #log-view {
        height: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }
    /* Banner 欢迎横幅的内边距 */
    #banner { padding: 1 2 0 2; }
    /* 用户消息样式：白色文字，顶部内边距 */
    Static.user-turn { color: $text; padding: 1 2 0 2; }
    /* 运行头部样式：灰色文字 */
    Static.run-header { color: $text-muted; padding: 1 2 0 2; }
    /* 步骤分隔线样式 */
    Static.step-divider { color: $text-muted; padding: 0 2; }
    /* 运行成功样式：绿色 */
    Static.run-ok { color: green; padding: 0 2 1 2; }
    /* 运行失败样式：红色 */
    Static.run-err { color: red; padding: 0 2 1 2; }
    /* Token 使用统计样式 */
    Static.usage { padding: 0 2; }
    /* 通用日志行样式 */
    Static.log-line { padding: 0 2; }
    """

    # ASCII 艺术 Banner，在会话开始时显示欢迎信息
    # 使用 Rich 样式标记实现彩色终端输出
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
        初始化 TUI 应用实例

        参数：
            host: str - core 服务的主机地址（如 "127.0.0.1"）
            port: int - core 服务的端口号（如 7437）
            replay_run_id: str | None - 可选，连接后回放指定运行的历史事件

        设计说明：
            - 不直接在构造函数中建立 Socket 连接，而是延迟到 on_mount() 中异步建立
            - 这样做的好处是让 Textual 先完成 UI 渲染，再进行网络连接
            - 支持多会话管理，通过 _sessions 字典维护每个会话的独立状态
        """
        # 调用 App 基类的构造函数，初始化 Textual 应用框架
        super().__init__()
        # 保存 core 服务的主机地址，用于后续 Socket 连接
        self._host = host
        # 保存 core 服务的端口号
        self._port = port
        # 保存回放运行 ID，用于重放历史事件（调试/演示用途）
        self._replay_run_id = replay_run_id
        # SocketClient 实例，初始为 None，连接建立后赋值
        # 使用 SocketClient 实现与 core 服务的双向 IPC 通信
        self._client: SocketClient | None = None
        # 多会话状态管理：session_id -> _SessionState 映射
        # 支持同时维护多个会话的 UI 状态，实现会话切换功能
        self._sessions: dict[str, _SessionState] = {}
        # 会话顺序列表，控制标签栏的显示顺序
        # 列表第一个元素为当前活动会话
        self._session_order: list[str] = []
        # 引擎类型："legacy" 或 "langgraph"
        # 不同引擎支持的功能不同（如检查点仅在 langgraph 模式可用）
        self._engine_type: str = "legacy"
        # 检查点后端类型："none"、"memory" 或 "sqlite"
        # 仅 langgraph 模式下有效
        self._checkpoint_backend: str = "none"
        # 斜杠命令候选列表：[(命令名, 描述), ...]
        # 在 on_mount() 中构建，供斜杠命令自动补全使用
        self._slash_items: list[tuple[str, str]] = []
        # 用户输入历史列表，用于 Ctrl+R 搜索历史功能
        # 最多保存 100 条记录，新记录插入头部
        self._history: list[str] = []
        # ===== UI 心跳相关：顶部状态栏 running 时长显示 =====
        # 记录最近一次传给 _update_header 的 state，供心跳回调读取
        self._header_state: str = "connecting"
        # 进入 running 状态时刻的 perf_counter()；非 running 时为 None
        self._run_start_ts: float | None = None

    @property
    def _session_id(self) -> str | None:
        """
        当前活动会话的 ID（只读属性）

        从 _session_order 列表中取第一个元素作为当前会话。
        这种设计使得会话切换只需修改 _session_order 的顺序，
        无需复制或移动实际的会话状态数据。

        返回：
            str | None - 当前会话 ID，无会话时返回 None
        """
        # 如果有会话，返回第一个（最新的）会话 ID；否则返回 None
        return self._session_order[0] if self._session_order else None

    @_session_id.setter
    def _session_id(self, value: str | None) -> None:
        """
        设置当前活动会话

        参数：
            value: str | None - 目标会话 ID，None 表示清空所有会话

        设计说明：
            - value=None：清空所有会话状态（用于 session.close 后的重置）
            - value 为有效会话 ID：将会话移到 _session_order 首位
              如果会话不在 _sessions 中，自动创建新的会话状态
            - 这确保了 _session_id 属性始终返回正确的活动会话
            - 操作完成后自动刷新标签栏显示
            - 清除回退状态，确保后续操作使用真实会话状态
        """
        if value is None:
            # value=None 时：清空所有会话数据
            self._session_order = []  # 清空会话顺序列表
            self._sessions = {}  # 清空会话状态字典
            self._refresh_tabbar()  # 刷新标签栏（此时应为空）
        else:
            # 确保会话存在于 _sessions 中（不存在则创建）
            if value not in self._sessions:
                self._sessions[value] = _SessionState(session_id=value)
            # 将会话移到 _session_order 首位
            if value in self._session_order:
                self._session_order.remove(value)  # 先移除旧位置
            self._session_order.insert(0, value)  # 插入到首位
            self._refresh_tabbar()  # 刷新标签栏显示
        # 清除回退状态，确保 _state 属性返回真实会话状态
        if hasattr(self, "_fallback_state"):
            del self._fallback_state

    @property
    def _state(self) -> _SessionState | None:
        """
        获取当前活动会话的状态对象（只读属性）

        这是访问当前会话所有 UI 状态的统一入口。
        其他属性（_busy, _auto_mode 等）都通过 _state 间接访问。
        这种间接访问模式确保所有操作都作用于当前活动会话。

        返回：
            _SessionState | None - 当前会话的状态对象，无会话时返回 None

        回退机制：
            当没有活动会话时，返回一个临时的 _SessionState 实例，
            确保属性访问在初始化阶段和测试环境中也能正常工作。
        """
        if not self._session_id:
            # 无活动会话时返回临时状态对象（用于初始化和测试）
            if not hasattr(self, "_fallback_state"):
                self._fallback_state = _SessionState(session_id="__fallback__")
            return self._fallback_state
        return self._sessions.get(self._session_id)

    @property
    def _busy(self) -> bool:
        """
        当前会话是否正在运行（只读属性）

        用于判断是否允许发送新消息、切换模式等操作。
        Agent 运行期间通常禁止用户输入，避免冲突。

        返回：
            bool - True 表示 Agent 正在执行任务
        """
        return self._state.busy if self._state else False  # 从会话状态读取 busy 标志

    @_busy.setter
    def _busy(self, value: bool) -> None:
        """
        设置当前会话的忙碌状态

        设置后自动刷新标签栏，使标签的颜色（busy 黄色）立即更新。
        这是 Textual 响应式设计的体现：修改数据后 UI 自动反映变化。

        参数：
            value: bool - True 表示开始运行，False 表示运行结束
        """
        if self._state:
            self._state.busy = value  # 更新会话状态中的 busy 标志
            self._refresh_tabbar()  # 刷新标签栏以反映忙碌状态变化

    @property
    def _auto_mode(self) -> str:
        """
        当前会话的自动模式（只读属性）

        自动模式控制 Agent 的自主程度：
        - "off"：每步需要用户确认
        - "read_only"：自动执行但只读操作
        - "on"：完全自主执行

        返回：
            str - 自动模式值
        """
        return self._state.auto_mode if self._state else "off"

    @_auto_mode.setter
    def _auto_mode(self, value: str) -> None:
        """设置当前会话的自动模式"""
        if self._state:
            self._state.auto_mode = value

    @property
    def _effort_level(self) -> str:
        """
        当前会话的努力等级（只读属性）

        努力等级影响 Agent 的推理深度：
        - "minimal" → 最少推理
        - "medium" → 平衡（默认）
        - "max" → 最深入推理

        返回：
            str - 努力等级值
        """
        return self._state.effort_level if self._state else "medium"

    @_effort_level.setter
    def _effort_level(self, value: str) -> None:
        """设置当前会话的努力等级"""
        if self._state:
            self._state.effort_level = value

    @property
    def _model_preset(self) -> str:
        """
        当前会话的模型预设（只读属性）

        模型预设决定使用的 LLM 模型：
        - "fast" → 快速模型
        - "balanced" → 平衡模型（默认）
        - "powerful" → 强力模型

        返回：
            str - 模型预设值
        """
        return self._state.model_preset if self._state else "balanced"

    @_model_preset.setter
    def _model_preset(self, value: str) -> None:
        """设置当前会话的模型预设"""
        if self._state:
            self._state.model_preset = value

    @property
    def _last_context_pct(self) -> float:
        """
        上次上下文窗口占用率（只读属性）

        用于在状态栏显示上下文使用情况。
        值为 0.0-1.0 的浮点数，表示上下文窗口的占用百分比。

        返回：
            float - 上下文占用率
        """
        return self._state.last_context_pct if self._state else 0.0

    @_last_context_pct.setter
    def _last_context_pct(self, value: float) -> None:
        """设置上下文占用率"""
        if self._state:
            self._state.last_context_pct = value

    @property
    def _current_llm(self) -> Any:
        """
        当前正在进行的 LLM 流式输出块（只读属性）

        当 Agent 正在生成 LLM 响应时，此属性指向当前的 LLMStreamBlock。
        用于追加 token 和最终渲染 Markdown。
        非 LLM 事件（如工具调用）到达时会结束当前流式块。

        返回：
            Any - LLMStreamBlock 实例或 None
        """
        return self._state.current_llm if self._state else None

    @_current_llm.setter
    def _current_llm(self, value: Any) -> None:
        """设置当前 LLM 流式输出块引用"""
        if self._state:
            self._state.current_llm = value

    @property
    def _pending_tool_blocks(self) -> dict[str, Any]:
        """
        待完成的工具调用块字典（只读属性）

        键为 tool_use_id，值为 ToolCallBlock 实例。
        用于在工具调用完成事件到达时更新对应的 UI 块。
        这种通过 ID 关联事件和 UI 的模式确保了异步事件的正确匹配。

        返回：
            dict[str, Any] - 待完成工具块映射
        """
        return self._state.pending_tool_blocks if self._state else {}

    @property
    def _pending_permission_blocks(self) -> dict[str, Any]:
        """
        待处理的权限审批块字典（只读属性）

        键为 tool_use_id，值为 PermissionBlock 实例。
        当工具需要权限审批时，先创建 PermissionBlock，
        用户决策后通过 _resolve() 更新状态。

        返回：
            dict[str, Any] - 待处理权限块映射
        """
        return self._state.pending_permission_blocks if self._state else {}

    @property
    def _subagent_run_ids(self) -> dict[str, str]:
        """
        子 Agent 运行 ID 到描述的映射（只读属性）

        用于在子 Agent 完成时查找其原始描述信息。
        子 Agent 的运行 ID 由 core 服务生成，完成事件携带相同的 ID。

        返回：
            dict[str, str] - run_id → 描述 映射
        """
        return self._state.subagent_run_ids if self._state else {}

    @property
    def _subagent_start_times(self) -> dict[str, float]:
        """
        子 Agent 开始时间映射（只读属性）

        用于计算子 Agent 的执行耗时。
        时间戳使用 time.monotonic() 高精度单调时钟。

        返回：
            dict[str, float] - run_id → 开始时间戳 映射
        """
        return self._state.subagent_start_times if self._state else {}

    def compose(self) -> ComposeResult:
        """
        组合 TUI 界面组件

        Textual 框架的组件组合方法，定义应用的初始 UI 布局。
        框架会自动调用此方法构建组件树。

        界面布局（从上到下）：
        1. #tabbar：顶部标签栏，显示所有会话标签
        2. #header：状态栏，显示连接状态、引擎、自动模式等
        3. #log-view：日志滚动区域，显示运行日志和输出
        4. #prompt：聊天输入框，支持 Enter 提交

        设计说明：
        - 使用 yield 而非 return，因为 ComposeResult 是生成器
        - 每个组件通过 id 属性标识，便于后续查询和操作
        - VerticalScroll 提供自动滚动到底部的日志查看体验

        返回：
            ComposeResult - 子 widget 生成器
        """
        # 标签栏容器：水平布局，显示所有会话标签
        # Horizontal 容器会自动水平排列子元素
        yield Horizontal(id="tabbar")
        # 状态栏标签：初始显示 "connecting..."，连接建立后更新
        # Label 用于显示单行文本，支持 Rich 样式
        yield Label("[bold]IwanClaude[/bold]  [dim]connecting...[/dim]", id="header")
        # 日志滚动视图：垂直滚动容器，承载所有运行日志和输出
        # VerticalScroll 自动处理滚动条和滚动到底部
        yield VerticalScroll(id="log-view")
        # 聊天输入框：支持多行输入、Enter 提交、斜杠命令补全
        # show_line_numbers=False 隐藏行号，保持界面简洁
        yield ChatTextArea(id="prompt", show_line_numbers=False)

    def _add_session(self, session_id: str, title: str = "") -> None:
        """
        添加一个新会话到状态管理系统

        在以下场景调用：
        - 应用启动时自动创建初始会话
        - 用户按 Ctrl+T 新建会话
        - 收到 session.create 命令响应后

        参数：
            session_id: str - 会话唯一标识符（由 core 服务生成）
            title: str - 会话标题，可选，默认使用 session_id

        设计说明：
            - 如果 session_id 已存在则跳过（幂等操作）
            - 新会话总是插入到 _session_order 首位，成为当前活动会话
            - 创建 _SessionState 实例管理该会话的所有 UI 状态
        """
        # 幂等检查：如果会话已存在则直接返回
        if session_id in self._sessions:
            return
        # 创建新的会话状态对象，包含会话 ID 和标题
        state = _SessionState(session_id=session_id, title=title or session_id)
        # 将状态对象存入字典
        self._sessions[session_id] = state
        # 将新会话 ID 插入到顺序列表首位（最新的会话在最前）
        self._session_order.insert(0, session_id)
        # 刷新标签栏以显示新会话
        self._refresh_tabbar()

    def _switch_session(self, session_id: str) -> None:
        """
        切换到指定会话

        实现会话间的快速切换，每个会话的 UI 状态独立保存和恢复。
        这使得多会话并行运行成为可能，用户可以随时查看不同会话的进展。

        参数：
            session_id: str - 目标会话 ID

        执行流程：
        1. 保存当前会话的 UI 状态（widget 列表）
        2. 将目标会话移到 _session_order 首位
        3. 清空 log-view 并加载目标会话的 widgets
        4. 更新状态栏和标签栏

        注意：
            - 切换不会中断后台会话的运行
            - 后台会话的事件仍会正确路由到其状态对象
            - 仅当前会话的日志 widget 会显示在屏幕上
        """
        # 前置条件检查：无会话或目标就是当前会话时直接返回
        if not self._sessions or session_id == self._session_id:
            return
        # 检查目标会话是否存在
        if session_id not in self._sessions:
            return

        # 步骤 1：保存当前会话的 UI 状态到其 _SessionState
        self._save_current_state()

        # 步骤 2：将目标会话移到 _session_order 首位
        self._session_order.remove(session_id)  # 先移除旧位置
        self._session_order.insert(0, session_id)  # 插入首位

        # 步骤 3：清空 log-view 并加载目标会话的 widgets
        self._load_session_state(session_id)

        # 步骤 4：更新状态栏为 ready 并刷新标签栏
        self._update_header("ready")
        self._refresh_tabbar()

    def _close_current_session(self) -> None:
        """
        关闭当前活动会话

        实现会话关闭功能，支持用户主动结束会话。

        执行流程：
        1. 检查是否至少保留一个会话（不允许关闭最后一个）
        2. 向 core 服务发送 session.close 命令
        3. 从本地状态中移除会话
        4. 切换到下一个会话
        5. 更新 UI

        边界情况：
            - 最后一个会话不允许关闭，显示提示信息
            - 关闭命令发送失败不影响本地状态清理
        """
        # 至少保留一个会话
        if len(self._session_order) <= 1:
            # 显示黄色警告信息
            self._append(Static("[yellow]至少保留一个会话[/yellow]", classes="log-line"))
            return

        # 获取当前会话 ID
        current_id = self._session_id
        if current_id is None:
            return

        # 尝试向 core 服务发送关闭命令（异步，失败不阻塞）
        if self._client is not None:
            try:
                # 使用 run_worker 发送命令，避免阻塞 UI
                # exclusive=False 允许多个 worker 并行运行
                self.run_worker(
                    self._client.send_command("session.close", {"session_id": current_id}),
                    exclusive=False,
                )
            except Exception:
                pass  # 发送失败静默忽略，继续清理本地状态

        # 从本地状态中移除会话
        self._sessions.pop(current_id, None)  # 移除状态对象
        self._session_order.remove(current_id)  # 移除顺序列表中的条目

        # 切换到剩余会话中的第一个
        if self._session_order:
            self._load_session_state(self._session_order[0])

        # 更新状态栏和标签栏
        self._update_header("ready")
        self._refresh_tabbar()

    def _refresh_tabbar(self) -> None:
        """
        刷新标签栏显示

        重新构建所有会话标签，反映最新的会话状态。
        在以下场景调用：
        - 添加/删除会话后
        - 会话 busy 状态变化时
        - 切换会话后
        - 会话标题修改后

        标签样式规则：
        - 当前活动会话：.active 类（高亮、粗体）
        - 正在运行的会话：.busy 类（黄色警告）
        - 其他会话：.tab 基础样式

        注意：
            使用 NoMatches 异常捕获，因为 on_mount 之前组件可能未挂载
        """
        # 获取标签栏容器，捕获不存在的异常
        try:
            tabbar = self.query_one("#tabbar", Horizontal)
        except (NoMatches, Exception):
            return  # 标签栏未挂载或应用未运行时直接返回

        # 清空标签栏中所有现有标签
        tabbar.remove_children()

        # 为每个会话创建标签
        for idx, sid in enumerate(self._session_order):
            state = self._sessions.get(sid)
            if state is None:
                continue

            # 构建标签显示文本：序号 + 标题（超过 20 字符时截断加省略号）
            display_title = state.title or "(untitled)"
            if len(display_title) > 20:
                display_title = display_title[:18] + "…"  # 截断并添加省略号
            label_text = f"{idx + 1} {display_title}"

            # 构建 CSS 类列表，控制标签的视觉样式
            classes = ["tab"]  # 基础类
            if sid == self._session_id:
                classes.append("active")  # 当前会话添加 active 类
            if state.busy:
                classes.append("busy")  # 忙碌会话添加 busy 类

            # 创建标签 Label 组件
            label = Label(label_text, classes=" ".join(classes))
            # 将 session_id 存储到 Label 对象上（用于点击事件识别）
            # type: ignore 因为 Label 没有 session_id 属性，这是动态添加的
            label.session_id = sid  # type: ignore[attr-defined]
            # 将标签挂载到标签栏容器
            tabbar.mount(label)

    def _save_current_state(self) -> None:
        """
        保存当前会话的 UI 状态

        在切换会话前调用，将 log-view 中的所有 widget 保存到当前会话的 _SessionState。
        这些 widget 在切换回来时会被重新挂载到 log-view 中。

        保存内容：
        - log-view 中所有子 widget（包括用户消息、LLM 输出、工具调用块等）
        - widget 的层级关系由 Textual 的 mount/remove_children 机制自动处理

        设计说明：
            - 此机制实现了"会话冻结"：切换会话时 UI 状态完整保留
            - 利用 Textual 的 widget 可重复挂载特性，无需序列化/反序列化
            - 异常捕获确保即使 log-view 查询失败也不会崩溃
        """
        # 获取当前会话的状态对象
        state = self._state
        if state is None:
            return

        try:
            # 获取日志视图容器
            log_view = self.query_one("#log-view", VerticalScroll)
            # 收集所有子 widget 到会话状态中
            # list() 创建副本，避免后续操作影响原列表
            state.widgets = list(log_view.children)
        except NoMatches:
            pass  # log-view 不存在时静默忽略

    def _load_session_state(self, session_id: str) -> None:
        """
        加载指定会话的 UI 状态到 log-view

        将会话保存的 widgets 重新挂载到日志视图中。
        在以下场景调用：
        - 切换到目标会话时
        - 新建会话后首次加载时

        参数：
            session_id: str - 目标会话 ID

        执行流程：
        1. 清空当前 log-view（remove_children）
        2. 将目标会话的所有 widgets 依次挂载
        3. 滚动到底部，显示最新内容

        设计说明：
            - Textual 的 widget 在 remove_children 后可以被重新 mount
            - 这是 Textual 支持的组件复用机制，避免重新创建 widget
            - 滚动到底部（scroll_end）确保用户看到最新的输出
        """
        # 获取目标会话的状态对象
        state = self._sessions.get(session_id)
        if state is None:
            return

        # 获取日志视图容器
        try:
            log_view = self.query_one("#log-view", VerticalScroll)
        except NoMatches:
            return

        # 清空日志视图中的所有现有内容
        log_view.remove_children()

        # 将目标会话保存的 widgets 重新挂载到日志视图
        for widget in state.widgets:
            log_view.mount(widget)

        # 滚动到底部，禁用动画以提高响应速度
        log_view.scroll_end(animate=False)

    def _get_state(self, session_id: str) -> _SessionState | None:
        """
        获取指定会话的状态对象

        通用的会话状态查询方法，供其他方法按 ID 获取状态。

        参数：
            session_id: str - 目标会话 ID

        返回：
            _SessionState | None - 会话状态对象，不存在返回 None
        """
        return self._sessions.get(session_id)

    def on_mount(self) -> None:
        """
        应用挂载时的初始化入口

        Textual 框架生命周期方法，在 compose() 构建 UI 后调用。
        在此方法中启动所有异步初始化任务。

        初始化步骤：
        1. 构建斜杠命令候选列表（系统命令 + Skill）
        2. 显示应用欢迎 Banner
        3. 启动 Socket 连接循环 worker（独占模式）
        4. 禁用输入框，显示 "connecting..." 状态

        设计说明：
            - 使用 run_worker() 启动异步任务，避免阻塞 UI 消息泵
            - exclusive=True 确保只有一个 socket worker 在运行
            - 输入框先禁用，等连接建立后再启用
        """
        # 构建斜杠命令候选列表（系统命令 + 已加载的 Skill）
        self._slash_items = self._build_slash_items()
        # 在日志视图中显示欢迎 Banner
        self._append(Static(self._BANNER, id="banner"))
        # 启动 Socket 连接循环（独占模式，确保只有一个连接在运行）
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")
        # 获取输入框引用
        prompt = self.query_one("#prompt", ChatTextArea)
        # 连接建立前禁用输入框，防止用户误操作
        prompt.disabled = True
        # 设置边框标题显示连接状态
        prompt.border_title = "connecting..."
        # 启动全局 UI 心跳定时器：每 2 秒检查一次 running 状态的时长并刷新显示
        self.set_interval(2.0, self._ui_heartbeat_tick, pause=False)

    def _build_slash_items(self) -> list[tuple[str, str]]:
        """
        构建斜杠命令候选列表

        组合系统内置命令和已加载的 Skill，生成完整的命令列表。
        这些命令用于斜杠命令自动补全弹窗的筛选和显示。

        返回：
            list[tuple[str, str]] - 命令列表，每个元素为 (命令名, 描述)

        系统内置命令：
        - help: 显示帮助信息
        - auto/effort/model: 模式/等级/预设切换
        - compact: 压缩上下文
        - checkpoint list/restore: 检查点管理
        - history: 查看历史
        - close: 关闭会话
        - skill_list: 列出技能

        Skill 处理：
        - 从 SkillLoader 加载所有 skill
        - 提取第一个描述行（最多 50 字符）
        - 自动触发的 skill 添加 🔄 标记
        - 添加 skill 图标前缀
        """
        # 初始化命令列表，包含所有系统内置命令
        items: list[tuple[str, str]] = [
            ("help", "显示帮助信息"),
            ("auto", "切换自动模式 (off|read_only|on)"),
            ("effort", "切换努力等级 (minimal|low|medium|high|max)"),
            ("engine", "切换 Agent 引擎 (legacy|langgraph|plan_execute|debate|pipeline)"),
            ("compact", "压缩上下文窗口"),
            ("checkpoint list", "列出所有检查点"),
            ("checkpoint restore <n>", "恢复到指定检查点"),
            ("history", "查看会话历史"),
            ("close", "关闭当前会话"),
            ("skill_list", "列出所有可用技能"),
        ]
        # 尝试从 SkillLoader 加载 Skill 列表
        try:
            loader = SkillLoader()
            # 遍历所有已加载的 Skill
            for skill in loader.list_all_skills():
                # 提取 Skill 描述的第一行（最多 50 字符）
                desc = skill.description.splitlines()[0] if skill.description else "skill"
                if len(desc) > 50:
                    desc = desc[:47] + "..."  # 截断加省略号
                # 自动触发的 Skill（auto 或 both）添加 🔄 标记
                invocation_mark = "🔄" if skill.invocation.value in ("auto", "both") else ""
                # 添加 Skill 到命令列表，包含图标和自动触发标记
                items.append((skill.name, f"{skill.icon} {invocation_mark} {desc}"))
        except Exception:
            pass  # Skill 加载失败时静默忽略，不影响系统命令
        return items

    def on_chat_text_area_slash_changed(self, event: ChatTextArea.SlashChanged) -> None:
        """
        处理斜杠命令查询变化事件

        当用户在输入框中输入以 / 开头的文本时触发。
        根据查询字符串的状态来管理自动补全弹窗的生命周期。

        参数：
            event: ChatTextArea.SlashChanged - 斜杠变化事件
                - query: str | None - 查询字符串，None 表示收起弹窗

        处理逻辑：
        - query=None：用户输入不以 / 开头或包含空格，移除弹窗
        - query!=None：用户在输入斜杠命令，更新已有弹窗或创建新弹窗

        事件流：
            ChatTextArea.on_text_area_changed → 发布 SlashChanged → 本方法处理
        """
        # 获取查询字符串
        query = event.query
        if query is None:
            # 查询为 None 时，移除自动补全弹窗
            try:
                self.query_one(SlashCompleteWidget).remove()  # 移除弹窗组件
            except NoMatches:
                pass  # 弹窗不存在时静默忽略
            return
        # 查询不为 None 时，更新或创建弹窗
        try:
            # 尝试获取已存在的弹窗组件
            popup = self.query_one(SlashCompleteWidget)
            # 已存在则更新筛选结果
            popup.set_query(query)
        except NoMatches:
            # 不存在则创建新弹窗
            popup = SlashCompleteWidget(self._slash_items)  # 使用命令列表初始化
            # 将弹窗挂载到输入框之前（在 log-view 和 prompt 之间）
            self.mount(popup, before="#prompt")
            # 设置查询以筛选命令
            popup.set_query(query)

    def on_slash_complete_widget_selected(self, event: SlashCompleteWidget.Selected) -> None:
        """
        处理用户选中斜杠命令的事件

        将选中的命令填入输入框并移除弹窗。
        用户可以在自动补全弹窗中通过 Enter/Tab 选中命令。

        参数：
            event: SlashCompleteWidget.Selected - 选中事件
                - skill_name: str - 被选中的命令或 Skill 名称

        实现细节：
            - 填入 "/{命令名} "（末尾加空格，方便继续输入参数或直接发送）
            - 将光标移动到输入框末尾
            - 移除自动补全弹窗
        """
        # 获取输入框引用
        prompt = self._prompt()
        if prompt is not None:
            # 将选中的命令填入输入框，末尾加空格
            prompt.text = f"/{event.skill_name} "
            # 将光标移动到输入框末尾
            prompt.move_cursor(prompt.document.end)
        # 移除自动补全弹窗
        try:
            self.query_one(SlashCompleteWidget).remove()
        except NoMatches:
            pass  # 弹窗不存在时静默忽略

    def on_click(self, event: events.Click) -> None:
        """
        全局点击事件处理

        处理标签栏中标签的点击事件，实现点击标签切换会话的功能。

        参数：
            event: events.Click - 点击事件

        事件处理流程：
        1. 获取点击的 widget
        2. 向上遍历父元素链，查找带有 session_id 属性的 widget
        3. 如果找到且不是当前会话，执行切换

        设计说明：
            - 使用 while 循环向上遍历，因为点击可能发生在标签的子元素上
            - 只处理标签栏的点击，不影响其他区域的点击行为
            - 忙碌状态下禁止切换，防止会话状态不一致
        """
        # 获取点击的控件
        widget = event.control
        if widget is None:
            return
        # 向上遍历父元素链，查找带有 session_id 属性的控件
        current = widget
        while current is not None:
            # 尝试获取 session_id 属性（在 _refresh_tabbar 中动态添加的）
            sid = getattr(current, "session_id", None)
            if sid is not None and sid in self._sessions:
                # 找到有效会话 ID 且非忙碌状态时切换
                if sid != self._session_id and not self._busy:
                    self._switch_session(sid)
                return  # 处理完成，直接返回
            current = current.parent  # 继续向上遍历

    def on_key(self, event: events.Key) -> None:
        """
        全局键盘事件处理（权限快捷键兜底）

        当 PermissionSelect 失去焦点但仍有待处理的权限审批时，
        作为兜底处理权限快捷键。这确保了即使焦点不在权限控件上，
        用户仍可通过键盘快捷键快速授权。

        参数：
            event: events.Key - 键盘事件

        处理逻辑：
        1. 如果没有待处理的权限审批，直接返回
        2. 如果 PermissionSelect 有焦点，让它自行处理
        3. 否则处理权限快捷键（y/a/n/d/1-4/方向键/Enter）

        兜底设计原因：
            - PermissionSelect 挂载在 Screen 顶层，焦点可能被其他控件抢走
            - 此方法确保权限审批的键盘操作始终可用
            - 使用 try-except 防止 PermissionSelect 已被移除导致异常
        """
        # 调试日志：记录全局键盘事件
        log.debug("App.on_key  key=%r  focused=%r", event.key, self.focused)
        # 无待处理权限审批时直接返回
        if not self._pending_permission_blocks:
            return
        try:
            # 获取 PermissionSelect 控件
            select = self.query_one(PermissionSelect)
            # 如果 PermissionSelect 持有焦点，让它自行处理
            if select.has_focus:
                return
            key = event.key
            # 尝试通过 _KEY_MAP 直接映射快捷键到决策
            decision = PermissionSelect._KEY_MAP.get(key)
            if decision:
                event.stop()  # 阻止事件继续传播
                select._pick(decision)  # 直接执行选择
            elif key in ("up", "k"):
                # 向上移动光标（循环）
                event.stop()
                select._cursor = (select._cursor - 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            elif key in ("down", "j"):
                # 向下移动光标（循环）
                event.stop()
                select._cursor = (select._cursor + 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            elif key == "enter":
                # 确认当前光标位置的选项
                event.stop()
                select._pick(PermissionSelect._CHOICES[select._cursor][0])
        except Exception:
            pass  # PermissionSelect 不存在或操作失败时静默忽略

    async def action_quit(self) -> None:
        """
        退出程序（Ctrl+Q 触发）

        在退出前尽力关闭当前会话，确保资源正确释放。
        失败不阻塞退出流程。

        执行步骤：
        1. 如果已连接且有当前会话，发送 session.close 命令
        2. 调用 self.exit() 关闭 TUI 应用

        异常处理：
            - IpcError/RuntimeError/OSError：记录警告但继续退出
            - 这确保即使 core 服务已断开，TUI 也能正常退出
        """
        # 检查是否已连接且有当前会话
        if self._client is not None and self._session_id is not None:
            try:
                # 尝试发送关闭命令
                await self._client.send_command("session.close", {"session_id": self._session_id})
            except (IpcError, RuntimeError, OSError):
                # 关闭失败时显示警告
                self._append(Static("[yellow]warning: failed to close session[/yellow]"))
        # 退出 TUI 应用（Textual 内置方法）
        self.exit()

    async def action_search_history(self) -> None:
        """
        显示搜索历史（Ctrl+R 触发）

        显示最近的用户输入历史（最多 20 条），方便用户快速复用之前的提示。
        历史列表在 on_chat_text_area_submitted 中维护。
        """
        # 历史为空时显示提示
        if not self._history:
            self._append(Static("[yellow]暂无历史记录[/yellow]", classes="log-line"))
            return
        # 显示历史标题
        self._append(Static("[bold cyan]===== 搜索历史 =====[/bold cyan]", classes="log-line"))
        # 显示最多 20 条历史记录
        for i, item in enumerate(self._history[:20]):
            self._append(Static(f"  [{i}] {item}", classes="log-line"))
        # 显示历史底部边框
        self._append(Static("[bold cyan]===================[/bold cyan]", classes="log-line"))

    async def action_checkpoint_list(self) -> None:
        """
        列出检查点（F6 触发）

        在 LangGraph 模式下列出所有检查点，支持恢复到任意历史状态。
        检查点仅在 LangGraph 引擎模式下可用。

        前置条件检查：
        - 必须已连接（_client 不为 None）
        - 必须有当前会话
        - Agent 不能正在运行
        - 引擎必须是 langgraph 类型
        """
        # 前置条件检查
        if self._client is None or self._session_id is None or self._busy:
            self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return
        # 检查引擎类型（legacy 引擎不使用 LangGraph，无检查点支持）
        if self._engine_type == "legacy":
            self._append(Static("[yellow]checkpoints only available in LangGraph engines (langgraph/plan_execute/debate/pipeline)[/yellow]", classes="log-line"))
            return
        # 在 worker 中执行检查点列表操作
        self.run_worker(self._do_checkpoint("list", ""), name="checkpoint", exclusive=False)

    async def action_new_session(self) -> None:
        """
        新建会话（Ctrl+T 触发）

        创建新的聊天会话并切换过去。在 worker 中执行，
        避免阻塞 UI 响应。
        """
        # 检查连接状态
        if self._client is None:
            self._append(Static("[yellow]not connected[/yellow]", classes="log-line"))
            return
        # 在 worker 中异步执行新建会话操作
        self.run_worker(self._do_new_session(), name="new_session", exclusive=False)

    async def action_close_session(self) -> None:
        """
        关闭当前会话（Ctrl+W 触发）

        调用 _close_current_session() 完成关闭逻辑。
        """
        self._close_current_session()

    async def action_switch_session(self, index_str: str) -> None:
        """
        切换到指定序号的会话（Alt+1~9 触发）

        参数：
            index_str: str - 会话序号字符串（"1" 到 "9"）
                由 BINDINGS 中的 switch_session(N) 自动传入
        """
        # 解析序号（从 1 开始，转为 0 索引）
        try:
            idx = int(index_str) - 1
        except ValueError:
            return
        # 检查序号有效性
        if 0 <= idx < len(self._session_order):
            self._switch_session(self._session_order[idx])

    async def _do_new_session(self) -> None:
        """
        执行新建会话的异步操作

        在 worker 中执行完整的新建会话流程：
        1. 发送 session.create 命令
        2. 保存当前会话状态
        3. 添加新会话到管理
        4. 加载并显示新会话
        5. 更新 UI

        设计说明：
            - 使用 worker 执行，确保即使 IPC 响应慢也不阻塞 UI
            - 新会话的 auto_mode/effort_level/model_preset 从响应中获取
        """
        # 连接检查
        if self._client is None:
            return
        try:
            # 发送创建会话命令，模式为 "chat"
            result = await self._client.send_command(
                "session.create", {"mode": "chat"}
            )
            # 从响应中解析会话 ID 和标题
            new_sid = str(result["session_id"])
            title = str(result.get("title", "")) or new_sid

            # 保存当前会话的 UI 状态
            self._save_current_state()

            # 将新会话添加到状态管理
            self._add_session(new_sid, title)

            # 获取新会话的状态对象，初始化其配置
            state = self._sessions.get(new_sid)
            if state is not None:
                # 从响应中获取初始配置值
                state.auto_mode = str(result.get("auto_mode", "off"))
                state.effort_level = str(result.get("effort_level", "medium"))
                state.model_preset = str(result.get("model_preset", "balanced"))

            # 加载新会话的 UI 状态
            self._load_session_state(new_sid)
            # 显示新会话的欢迎 Banner
            self._append(Static(self._BANNER, id=f"banner-{new_sid}"))

            # 更新 UI 状态
            self._update_header("ready")
            self._refresh_tabbar()
        except (IpcError, RuntimeError, OSError) as e:
            # 创建失败时显示错误
            self._append(Static(f"[red]new session error: {e}[/red]", classes="log-line"))

    async def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        """
        处理用户提交消息事件

        这是用户与 Agent 交互的核心入口。支持系统指令和普通消息两种模式。

        参数：
            event: ChatTextArea.Submitted - 提交事件
                - value: str - 输入框内容
                - text_area: ChatTextArea - 输入框引用

        支持的斜杠命令：
        - /compact: 压缩上下文窗口
        - /checkpoint list|restore: 检查点管理
        - /help: 显示帮助信息
        - /auto [mode]: 切换自动模式
        - /effort [level]: 切换努力等级
        - /model [preset]: 切换模型预设
        - /name <title>: 重命名会话
        - /history: 查看会话历史
        - /close: 关闭当前会话

        普通消息处理：
        1. 清空输入框，设置 "agent is working..." 状态
        2. 将用户消息添加到日志视图
        3. 保存到搜索历史
        4. 调用 _do_send_message() 发送给 Agent

        设计说明：
            - 命令优先处理，通过前缀匹配路由到对应方法
            - 所有异步操作通过 run_worker 执行，不阻塞 UI
            - 输入框在 Agent 运行期间禁用，防止重复提交
        """
        # 获取并清理输入内容
        content = event.value.strip()
        # 空消息直接忽略
        if not content:
            return

        # ========== 斜杠命令路由 ==========

        # /compact 压缩上下文
        if content == "/compact":
            event.text_area.text = ""  # 清空输入框
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_compact(), name="compact", exclusive=False)
            return

        # /checkpoint 检查点管理
        if content.startswith("/checkpoint"):
            event.text_area.text = ""  # 清空输入框
            if self._client is not None and self._session_id is not None and not self._busy:
                parts = content.split()  # 按空格分割命令
                if len(parts) >= 2:
                    cmd = parts[1]  # 子命令：list 或 restore
                    arg = parts[2] if len(parts) >= 3 else ""  # 恢复时的参数
                    self.run_worker(self._do_checkpoint(cmd, arg), name="checkpoint", exclusive=False)
                else:
                    # 参数不足时显示用法提示
                    self._append(Static("[yellow]usage: /checkpoint list | /checkpoint restore <id>[/yellow]", classes="log-line"))
            return

        # /help 显示帮助
        if content == "/help":
            event.text_area.text = ""  # 清空输入框
            self._show_help()
            return

        # /auto 切换自动模式
        if content.startswith("/auto"):
            event.text_area.text = ""  # 清空输入框
            parts = content.split(None, 1)  # 分割命令和参数
            mode = parts[1].strip() if len(parts) > 1 else ""  # 提取模式参数
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_set_auto_mode(mode), name="auto_mode", exclusive=False)
            else:
                self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return

        # /effort 切换努力等级
        if content.startswith("/effort"):
            event.text_area.text = ""  # 清空输入框
            parts = content.split(None, 1)  # 分割命令和参数
            level = parts[1].strip() if len(parts) > 1 else ""  # 提取等级参数
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_set_effort_level(level), name="effort_level", exclusive=False)
            else:
                self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return

        # /model 切换模型预设
        if content.startswith("/model"):
            event.text_area.text = ""  # 清空输入框
            parts = content.split(None, 1)  # 分割命令和参数
            preset = parts[1].strip() if len(parts) > 1 else ""  # 提取预设参数
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_set_model(preset), name="model_preset", exclusive=False)
            else:
                self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return

        # /engine 切换 Agent 引擎
        if content.startswith("/engine"):
            event.text_area.text = ""  # 清空输入框
            parts = content.split(None, 1)  # 分割命令和参数
            engine = parts[1].strip() if len(parts) > 1 else ""  # 提取引擎参数
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_set_engine(engine), name="set_engine", exclusive=False)
            else:
                self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return

        # /name 重命名会话
        if content.startswith("/name"):
            event.text_area.text = ""  # 清空输入框
            parts = content.split(None, 1)  # 分割命令和参数
            title = parts[1].strip() if len(parts) > 1 else ""  # 提取标题
            if not title:
                self._append(Static("[yellow]usage: /name <title>[/yellow]", classes="log-line"))
                return
            if self._client is not None and self._session_id is not None:
                self.run_worker(self._do_rename_session(title), name="rename", exclusive=False)
            else:
                self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return

        # /history 查看历史
        if content == "/history":
            event.text_area.text = ""  # 清空输入框
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_get_history(), name="history", exclusive=False)
            return

        # /close 关闭会话
        if content == "/close":
            event.text_area.text = ""  # 清空输入框
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._do_close_session(), name="close", exclusive=False)
            return

        # ========== 普通消息处理 ==========

        # 检查连接和忙碌状态
        if self._client is None or self._session_id is None or self._busy:
            self._append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return
        # 设置忙碌状态
        self._busy = True
        # 获取输入框引用并设置工作状态
        prompt = event.text_area
        prompt.text = ""  # 清空输入框
        prompt.disabled = True  # 禁用输入框
        prompt.read_only = False  # 保持可聚焦
        prompt.border_title = "agent is working..."  # 更新边框标题
        # 将用户消息显示在日志视图中
        self._append(Static(f"[bold]>[/bold] {content}", classes="user-turn"))
        # 将非命令消息保存到搜索历史
        if content and not content.startswith("/"):
            if content not in self._history:
                self._history.insert(0, content)  # 插入到历史列表头部
                # 限制历史列表长度为 100 条
                if len(self._history) > 100:
                    self._history = self._history[:100]
        # 更新状态栏为 running 状态
        self._update_header("running")
        # 在 worker 中发送消息给 Agent
        self.run_worker(self._do_send_message(content), name="send_message", exclusive=False)

    async def _do_compact(self) -> None:
        """
        执行上下文压缩命令

        当上下文窗口占用过高时，通过压缩历史对话来节省 token。
        压缩后 Agent 可以继续运行而不丢失关键信息。

        执行流程：
        1. 显示压缩进度提示
        2. 发送 session.compact 命令到 core 服务
        3. 显示压缩结果（摘要 token 数和节省的 token 数）
        4. 重置上下文占用率为 0

        异常处理：
            - IpcError/RuntimeError/OSError：显示错误信息
        """
        # 连接检查
        if self._client is None or self._session_id is None:
            return
        # 显示压缩进度提示
        self._append(Static("[dim]⚡ compacting context...[/dim]", classes="log-line"))
        try:
            # 发送压缩命令到 core 服务
            result = await self._client.send_command(
                "session.compact",
                {"session_id": self._session_id, "focus": ""},  # focus 为空压缩全部
            )
            # 从结果中获取压缩统计数据
            summary_tokens = result.get("summary_tokens", 0)  # 摘要 token 数
            saved_tokens = result.get("saved_tokens", 0)  # 节省的 token 数
            # 重置上下文占用率
            self._last_context_pct = 0.0
            # 显示压缩完成结果
            self._append(Static(
                f"[bold cyan]⚡ Context compacted[/bold cyan]"
                f"  [dim]summary={summary_tokens} tokens  saved≈{saved_tokens} tokens[/dim]",
                classes="log-line",
            ))
        except (IpcError, RuntimeError, OSError) as e:
            # 压缩失败时显示错误
            self._append(Static(f"[red]compact error: {e}[/red]", classes="log-line"))

    async def _do_set_auto_mode(self, mode: str) -> None:
        """
        执行自动模式切换命令

        自动模式控制 Agent 的自主程度，影响工具调用是否需要用户确认。

        参数：
            mode: str - 目标模式
                - "off"：每步需要用户确认（默认）
                - "read_only"：自动执行只读操作
                - "on"：完全自主执行所有操作
                - 空字符串：循环切换三种模式

        设计说明：
            - 空 mode 时自动循环切换：off → read_only → on → off
            - 通过 IPC 发送命令到 core 服务，服务端保存状态
            - 成功后更新本地状态并刷新状态栏
        """
        # 连接检查
        if self._client is None or self._session_id is None:
            return

        # 未指定模式时循环切换
        if not mode:
            cycle = {"off": "read_only", "read_only": "on", "on": "off"}
            mode = cycle.get(self._auto_mode, "off")

        # 验证模式有效性
        if mode not in ("off", "read_only", "on"):
            self._append(Static(f"[yellow]usage: /auto [off|read_only|on], got {mode!r}[/yellow]", classes="log-line"))
            return

        try:
            # 发送设置命令到 core 服务
            result = await self._client.send_command(
                "session.set_auto_mode",
                {"session_id": self._session_id, "mode": mode},
            )
            # 从响应中更新本地状态
            self._auto_mode = result.get("mode", mode)
            # 显示模式切换结果
            self._append(Static(
                f"[bold cyan]⚡ Auto mode[/bold cyan]  [dim]{self._auto_mode}[/dim]",
                classes="log-line",
            ))
            # 更新状态栏
            self._update_header("ready")
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]auto mode error: {e}[/red]", classes="log-line"))

    async def _do_set_effort_level(self, level: str) -> None:
        """
        执行努力等级切换命令

        努力等级控制 Agent 的推理深度和资源消耗。

        参数：
            level: str - 目标等级
                - "minimal"：最少推理，响应最快
                - "low"：较低推理深度
                - "medium"：平衡模式（默认）
                - "high"：深入推理
                - "max"：最深入推理，消耗最多 token
                - 空字符串：循环切换五种等级

        设计说明：
            - 空 level 时自动循环切换
            - 等级影响 LLM 的 temperature 和推理 token 数
        """
        # 连接检查
        if self._client is None or self._session_id is None:
            return

        # 未指定等级时循环切换
        if not level:
            cycle = {"minimal": "low", "low": "medium", "medium": "high", "high": "max", "max": "minimal"}
            level = cycle.get(self._effort_level, "medium")

        # 验证等级有效性
        if level not in ("minimal", "low", "medium", "high", "max"):
            self._append(Static(f"[yellow]usage: /effort [minimal|low|medium|high|max], got {level!r}[/yellow]", classes="log-line"))
            return

        try:
            # 发送设置命令
            result = await self._client.send_command(
                "session.set_effort_level",
                {"session_id": self._session_id, "level": level},
            )
            # 更新本地状态
            self._effort_level = result.get("level", level)
            # 显示切换结果
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

        模型预设决定 Agent 使用的 LLM 模型。

        参数：
            preset: str - 目标预设
                - "fast"：快速模型，低延迟
                - "balanced"：平衡模型（默认）
                - "powerful"：强力模型，高能力
                - 空字符串：循环切换三种预设

        设计说明：
            - 空 preset 时自动循环切换
            - 不同预设对应不同的模型 API 和参数配置
        """
        # 连接检查
        if self._client is None or self._session_id is None:
            return

        # 未指定预设时循环切换
        if not preset:
            cycle = {"fast": "balanced", "balanced": "powerful", "powerful": "fast"}
            preset = cycle.get(self._model_preset, "balanced")

        # 验证预设有效性
        if preset not in ("fast", "balanced", "powerful"):
            self._append(Static(f"[yellow]usage: /model [fast|balanced|powerful], got {preset!r}[/yellow]", classes="log-line"))
            return

        try:
            # 发送设置命令
            result = await self._client.send_command(
                "session.set_model",
                {"session_id": self._session_id, "preset": preset},
            )
            # 更新本地状态
            self._model_preset = result.get("preset", preset)
            # 显示切换结果
            self._append(Static(
                f"[bold cyan]🧠 Model preset[/bold cyan]  [dim]{self._model_preset}[/dim]",
                classes="log-line",
            ))
            self._update_header("ready")
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]model preset error: {e}[/red]", classes="log-line"))

    async def _do_set_engine(self, engine: str) -> None:
        """
        执行 Agent 引擎切换命令

        引擎决定 Agent 的执行模式：
        - legacy: 简单循环
        - langgraph: ReAct 引擎（chat→tools 循环）
        - plan_execute: 规划→执行→反思
        - debate: worker-critic 辩论
        - pipeline: planner→executor→reviewer 三角色流水线

        参数：
            engine: str - 目标引擎名称
                - 空字符串：循环切换五种引擎
        """
        # 连接检查
        if self._client is None or self._session_id is None:
            return

        # 未指定引擎时循环切换
        valid_engines = ["legacy", "langgraph", "plan_execute", "debate", "pipeline"]
        if not engine:
            current = self._engine_type if self._engine_type in valid_engines else "legacy"
            idx = valid_engines.index(current)
            engine = valid_engines[(idx + 1) % len(valid_engines)]

        # 验证引擎名称有效性
        if engine not in valid_engines:
            self._append(Static(
                f"[yellow]usage: /engine [{'|'.join(valid_engines)}], got {engine!r}[/yellow]",
                classes="log-line",
            ))
            return

        try:
            # 发送设置命令到 core 服务
            result = await self._client.send_command(
                "session.set_engine",
                {"session_id": self._session_id, "engine": engine},
            )
            # 更新本地状态（与状态栏渲染使用的 _engine_type 保持一致）
            self._engine_type = result.get("engine", engine)
            # 显示切换结果
            self._append(Static(
                f"[bold cyan]⚙️  Engine[/bold cyan]  [dim]{self._engine_type}[/dim]",
                classes="log-line",
            ))
            self._update_header("ready")
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]engine switch error: {e}[/red]", classes="log-line"))

    async def _do_rename_session(self, title: str) -> None:
        """
        执行重命名会话操作

        允许用户通过 /name 命令自定义会话标题，便于在多会话场景下区分。

        参数：
            title: str - 新的会话标题

        设计说明：
            - 通过 IPC 发送 session.rename 命令到 core 服务
            - 成功后更新本地标题并刷新标签栏
            - 标题变更在标签栏实时反映
        """
        # 连接检查
        if self._client is None or self._session_id is None:
            return
        try:
            # 发送重命名命令
            result = await self._client.send_command(
                "session.rename",
                {"session_id": self._session_id, "title": title},
            )
            # 从响应中获取新标题
            new_title = result.get("title", title)
            # 更新本地状态
            state = self._state
            if state is not None:
                state.title = new_title
            # 刷新标签栏以显示新标题
            self._refresh_tabbar()
            # 显示重命名结果
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

        检查点允许 Agent 保存和恢复执行状态，实现可中断的长任务。
        仅在 LangGraph 引擎模式下可用。

        参数：
            cmd: str - 操作命令
                - "list"：列出所有检查点
                - "restore"：恢复到指定检查点
            arg: str - 操作参数
                - restore 时为检查点索引（数字）或检查点 ID

        实现细节：
            - list：调用 session.checkpoint.list，逆序显示（最新在前）
            - restore：如果参数是数字，先查询列表获取 ID，再调用恢复命令
            - 索引 0 表示最近的检查点（因为列表逆序排列）
        """
        # 连接检查
        if self._client is None or self._session_id is None:
            return

        # ========== 列出检查点 ==========
        if cmd == "list":
            self._append(Static("[dim]📋 正在列出检查点...[/dim]", classes="log-line"))
            try:
                # 发送列表查询命令
                result = await self._client.send_command(
                    "session.checkpoint.list",
                    {"session_id": self._session_id},
                )
                checkpoints = result.get("checkpoints", [])

                if not checkpoints:
                    # 无检查点时显示提示
                    self._append(Static("[dim]  暂无检查点[/dim]", classes="log-line"))
                    self._append(Static("[dim]  提示：检查点会在 agent 运行过程中自动创建[/dim]", classes="log-line"))
                else:
                    # 显示检查点列表头部
                    self._append(Static(f"[bold cyan]===== 检查点列表 ({len(checkpoints)}) =====[/bold cyan]", classes="log-line"))
                    self._append(Static(f"[dim]  格式：[索引] 步骤号 | 时间 | 内容预览[/dim]", classes="log-line"))
                    self._append(Static(f"[dim]  使用：/checkpoint restore <索引> 恢复到指定检查点[/dim]", classes="log-line"))
                    self._append(Static("", classes="log-line"))
                    # 逆序显示检查点（最新的在前）
                    for i, cp in enumerate(reversed(checkpoints)):
                        ts = cp.get("timestamp", "")
                        summary = cp.get("summary", "")
                        cp_id = cp.get("checkpoint_id", "")
                        # 提取时间部分（仅显示时分秒）
                        ts_display = ts.split("T")[1][:8] if "T" in ts else ts
                        # 步骤描述
                        step = cp["step"]
                        step_desc = "初始状态" if step == -1 else f"第 {step+1} 步"
                        # 显示检查点信息
                        self._append(Static(
                            f"  [bold green][{i}][/bold green]  "
                            f"[cyan]step={step}[/cyan] [{step_desc}]  "
                            f"[dim]{ts_display}[/dim]  "
                            f"{summary}",
                            classes="log-line",
                        ))
                        # 显示检查点 ID（截断显示）
                        if cp_id:
                            self._append(Static(
                                f"     [dim]ID: {_preview(cp_id, 32)}[/dim]",
                                classes="log-line",
                            ))
                    # 显示列表底部
                    self._append(Static("", classes="log-line"))
                    self._append(Static(f"[bold cyan]====================================[/bold cyan]", classes="log-line"))
                    self._append(Static(f"[dim]示例：/checkpoint restore 0 恢复到最近状态[/dim]", classes="log-line"))
            except (IpcError, RuntimeError, OSError) as e:
                self._append(Static(f"[red]checkpoint list error: {e}[/red]", classes="log-line"))

        # ========== 恢复检查点 ==========
        elif cmd == "restore":
            if not arg:
                self._append(Static("[yellow]用法：/checkpoint restore <索引或ID>[/yellow]", classes="log-line"))
                return

            checkpoint_id = arg

            # 如果参数是数字，先查询列表获取对应的检查点 ID
            if arg.isdigit():
                index = int(arg)
                self._append(Static(f"[dim]🔄 正在查找检查点索引 {index}...[/dim]", classes="log-line"))
                try:
                    # 查询检查点列表
                    list_result = await self._client.send_command(
                        "session.checkpoint.list",
                        {"session_id": self._session_id},
                    )
                    checkpoints = list_result.get("checkpoints", [])
                    # 验证索引有效性
                    if index < 0 or index >= len(checkpoints):
                        self._append(Static(f"[red]✗ 索引 {index} 超出范围 (0-{len(checkpoints)-1})[/red]", classes="log-line"))
                        return
                    # 获取对应的检查点 ID（逆序索引）
                    checkpoint_id = checkpoints[-(index + 1)]["checkpoint_id"]
                    self._append(Static(f"[dim]  -> 检查点ID: {_preview(checkpoint_id, 32)}[/dim]", classes="log-line"))
                except (IpcError, RuntimeError, OSError) as e:
                    self._append(Static(f"[red]检查点列表错误: {e}[/red]", classes="log-line"))
                    return

            # 执行恢复操作
            self._append(Static(f"[dim]🔄 正在恢复检查点...[/dim]", classes="log-line"))
            try:
                result = await self._client.send_command(
                    "session.checkpoint.restore",
                    {"session_id": self._session_id, "checkpoint_id": checkpoint_id},
                )
                if result.get("success"):
                    # 恢复成功
                    self._append(Static(
                        f"[bold green]✓[/bold green] 已恢复到第 {result['step']} 步: {result['message']}",
                        classes="log-line",
                    ))
                else:
                    # 恢复失败
                    self._append(Static(
                        f"[red]✗ 恢复失败: {result['message']}[/red]",
                        classes="log-line",
                    ))
            except (IpcError, RuntimeError, OSError) as e:
                self._append(Static(f"[red]检查点恢复错误: {e}[/red]", classes="log-line"))

        # ========== 未知命令 ==========
        else:
            self._append(Static("[yellow]用法：/checkpoint list | /checkpoint restore <index>[/yellow]", classes="log-line"))

    def _show_help(self) -> None:
        """
        显示帮助信息

        展示快捷键、斜杠命令和 Skill 使用说明，帮助用户快速上手。
        通过 /help 命令触发。
        """
        # 显示帮助标题
        self._append(Static("[bold cyan]===== 帮助信息 =====[/bold cyan]", classes="log-line"))
        # 快捷键部分
        self._append(Static("[bold]快捷键：[/bold]", classes="log-line"))
        self._append(Static("  [cyan]Ctrl+Q[/cyan]  退出程序", classes="log-line"))
        self._append(Static("  [cyan]F6[/cyan]       列出检查点", classes="log-line"))
        self._append(Static("  [cyan]Ctrl+P[/cyan]  系统命令面板（Textual 默认）", classes="log-line"))
        self._append(Static("", classes="log-line"))
        # 斜杠命令部分
        self._append(Static("[bold]斜杠命令（输入 / 查看）：[/bold]", classes="log-line"))
        self._append(Static("  [cyan]/help[/cyan]            显示此帮助信息", classes="log-line"))
        self._append(Static("  [cyan]/auto [off|read_only|on][/cyan]  切换自动模式", classes="log-line"))
        self._append(Static("  [cyan]/effort [minimal|low|medium|high|max][/cyan]  切换努力等级", classes="log-line"))
        self._append(Static("  [cyan]/model [fast|balanced|powerful][/cyan]  切换模型预设", classes="log-line"))
        self._append(Static("  [cyan]/engine [legacy|langgraph|plan_execute|debate|pipeline][/cyan]  切换 Agent 引擎", classes="log-line"))
        self._append(Static("  [cyan]/compact[/cyan]         压缩上下文窗口", classes="log-line"))
        self._append(Static("  [cyan]/checkpoint list[/cyan]  列出所有检查点", classes="log-line"))
        self._append(Static("  [cyan]/checkpoint restore <n>[/cyan]  恢复到指定检查点", classes="log-line"))
        self._append(Static("  [cyan]/history[/cyan]         查看会话历史", classes="log-line"))
        self._append(Static("  [cyan]/close[/cyan]           关闭当前会话", classes="log-line"))
        self._append(Static("  [cyan]/skill_list[/cyan]      列出所有可用技能", classes="log-line"))
        self._append(Static("", classes="log-line"))
        # Skill 说明部分
        self._append(Static("[bold]Skill（技能/提示词模板）：[/bold]", classes="log-line"))
        self._append(Static("  输入 /skill_name 手动触发技能", classes="log-line"))
        self._append(Static("  带有 🔄 标记的技能会根据关键词自动触发", classes="log-line"))
        self._append(Static("  内置技能：review(🔍), summarize(📝), orchestrate(🎯), security(🔒), docs(📚), tests(🧪)", classes="log-line"))
        self._append(Static("[bold cyan]====================[/bold cyan]", classes="log-line"))

    async def _do_get_history(self) -> None:
        """
        获取会话历史

        从 core 服务获取当前会话的消息历史并显示。
        通过 /history 命令触发。

        实现细节：
        - 发送 session.get_history 命令
        - 按角色显示消息：user（绿色）、assistant（蓝色）、其他（灰色）
        - 使用 _preview 截断长消息（最多 80 字符）
        """
        # 连接检查
        if self._client is None or self._session_id is None:
            return
        try:
            # 发送获取历史命令
            result = await self._client.send_command(
                "session.get_history",
                {"session_id": self._session_id},
            )
            messages = result.get("messages", [])
            # 显示历史标题
            self._append(Static(f"[bold cyan]===== 会话历史 ({len(messages)} 条) =====[/bold cyan]", classes="log-line"))
            # 逐条显示历史消息
            for i, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                # 根据角色选择颜色
                role_color = "green" if role == "user" else "blue" if role == "assistant" else "gray"
                self._append(Static(
                    f"  [bold {role_color}][{i}][/bold {role_color}]  [{role}] {_preview(content, 80)}",
                    classes="log-line",
                ))
            # 显示底部边框
            self._append(Static("[bold cyan]====================================[/bold cyan]", classes="log-line"))
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]history error: {e}[/red]", classes="log-line"))

    async def _do_close_session(self) -> None:
        """
        关闭当前会话

        发送 session.close 命令关闭当前会话，并重置应用状态。
        通过 /close 命令触发。

        实现细节：
        - 发送 session.close 命令到 core 服务
        - 重置 _session_id 和 _busy 状态
        - 更新输入框状态为 "session closed"
        """
        # 连接检查
        if self._client is None or self._session_id is None:
            return
        try:
            # 发送关闭命令
            await self._client.send_command(
                "session.close",
                {"session_id": self._session_id},
            )
            # 显示关闭成功
            self._append(Static(f"[bold green]✓[/bold green] 会话已关闭", classes="log-line"))
            # 重置会话状态
            self._session_id = None  # 清空当前会话（触发 setter 清空所有状态）
            self._busy = False  # 重置忙碌状态
            # 更新输入框状态
            prompt = self.query_one("#prompt", ChatTextArea)
            prompt.border_title = "session closed"
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]close session error: {e}[/red]", classes="log-line"))

    async def _do_send_message(self, content: str) -> None:
        """
        在 worker 中发送消息到 core 服务

        使用 worker 执行 IPC 发送，使 App 消息泵在 Agent 运行期间仍能处理键盘/焦点等消息。
        这是 Textual 异步设计的核心：耗时操作不阻塞 UI 消息循环。

        参数：
            content: str - 消息内容

        异常处理：
            - 发送失败时重置应用状态（_busy=False、输入框启用、状态更新为 ready）
            - 确保即使发送失败，用户仍可以继续使用 TUI
        """
        # 连接检查
        if self._client is None:
            return
        try:
            # 发送消息到 core 服务
            await self._client.send_command(
                "session.send_message",
                {"session_id": self._session_id, "content": content},
            )
        except (IpcError, RuntimeError, OSError) as e:
            # 发送失败时重置 UI 状态
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
        这是权限审批流程的关键处理环节。

        参数：
            msg: PermissionSelect.Decided - 权限决策消息
                - tool_use_id: str - 工具调用 ID
                - decision: str - 决策字符串

        处理流程：
        1. 移除权限选择控件
        2. 更新对应的权限审批块为已解决状态
        3. 发送 permission.respond 命令到 core 服务
        4. 如果没有待处理的权限审批，恢复输入框状态

        设计说明：
            - 使用 try-except 包裹，防止异常导致权限审批卡住
            - 发送 IPC 失败时不阻塞，继续处理后续逻辑
            - 最后一个权限审批完成后，重新启用输入框并获取焦点
        """
        tool_use_id = msg.tool_use_id
        decision = msg.decision
        log.info("permission decided tool_use_id=%s decision=%s", tool_use_id, decision)
        try:
            # 步骤 1：移除权限选择控件
            msg.widget.remove()
            # 步骤 2：更新权限审批块为已解决状态
            perm_block = self._pending_permission_blocks.pop(tool_use_id, None)
            if perm_block is not None:
                perm_block._resolve(decision)
            # 步骤 3：发送权限响应到 core 服务
            if self._client is not None:
                try:
                    await self._client.send_command(
                        "permission.respond",
                        {"tool_use_id": tool_use_id, "decision": decision},
                    )
                except (IpcError, RuntimeError, OSError):
                    pass  # 发送失败静默忽略
            # 步骤 4：如果没有待处理权限审批，恢复输入框
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

        所有需要在日志区域显示的内容都通过此方法添加，
        确保统一的追加和滚动行为。

        参数：
            widget: Widget - 要追加的 widget（可以是 Static、ToolCallBlock 等）

        实现细节：
        - 获取 #log-view 滚动容器
        - 使用 mount() 添加新 widget 到容器末尾
        - 使用 scroll_end() 滚动到底部（禁用动画以提高响应速度）

        设计说明：
            - 这是日志区域 widget 追加的唯一入口
            - 统一的追加方式确保了日志视图的行为一致性
        """
        log_view = self.query_one("#log-view", VerticalScroll)
        log_view.mount(widget)
        log_view.scroll_end(animate=False)

    def _break_llm(self) -> None:
        """
        结束当前 LLM 流式输出块

        将当前 LLM 流式输出块渲染为 Markdown，并重置 _current_llm。
        下一个 LLM token 事件将开启新的流式块。

        调用时机：
        - 收到非 llm.token 事件时（如工具调用、运行状态变更等）
        - Socket 连接断开时
        - Agent 运行结束时

        设计说明：
            - 如果 _current_llm 不为 None，调用 finalize_markdown() 渲染最终格式
            - 将 _current_llm 置为 None，表示当前没有活动的流式块
            - 这确保了每个 LLM 输出段都被正确渲染为 Markdown
        """
        if self._current_llm is not None:
            self._current_llm.finalize_markdown()  # 渲染为 Markdown 格式
        self._current_llm = None  # 重置，下次 token 将创建新块

    def _mount_permission_select(self, select: PermissionSelect) -> None:
        """
        将权限选择控件挂载到 Screen 顶层

        将选择控件挂载到 #prompt 之前，确保在输入框和日志视图之间显示。
        这种挂载位置避免了被 VerticalScroll 容器争抢焦点。

        参数：
            select: PermissionSelect - 权限选择控件

        设计要点：
            - 挂载在输入框之前，确保控件在可视区域
            - 挂载到 Screen 顶层而非滚动容器内，避免焦点问题
            - 这是 Textual 中处理"浮动"控件的常用模式
        """
        self.mount(select, before="#prompt")

    def _prompt(self) -> ChatTextArea | None:
        """
        安全获取输入框引用

        封装对输入框的查询，在控件未挂载时返回 None。
        这在测试和初始化阶段很有用，避免因控件不存在而抛出异常。

        返回：
            ChatTextArea | None - 输入框控件引用或 None

        设计说明：
            - 使用 try-except 包裹，捕获所有异常
            - 这是"防御性编程"的体现，确保方法在任何阶段都安全调用
        """
        try:
            return self.query_one("#prompt", ChatTextArea)
        except Exception:
            return None

    def _render_ctx_bar(self, pct: float) -> str:
        """
        生成上下文占用率的彩色进度条字符串

        用于状态栏显示当前上下文窗口的使用情况，
        帮助用户了解何时需要压缩上下文。

        参数：
            pct: float - 上下文占用率（0.0-1.0）

        返回：
            str - 格式化的进度条字符串（包含 Rich 样式标记）

        颜色策略：
            - >= 85%: 红色（bold red），表示接近上限，需要压缩
            - >= 70%: 黄色（yellow），表示警告
            - < 70%: 灰色（dim），表示正常

        显示格式示例：
            ctx:75.0% ██████████████░░░░
        """
        # 计算填充的方块数（总共 20 个方块）
        filled = int(pct * 20)
        # 构建进度条字符串
        bar = "█" * filled + "░" * (20 - filled)
        # 构建标签
        label = f"ctx:{pct * 100:.1f}%"
        # 根据占用率选择颜色
        if pct >= 0.85:
            color = "bold red"
        elif pct >= 0.70:
            color = "yellow"
        else:
            color = "dim"
        # 返回带样式的进度条
        return f"[{color}]{label} {bar}[/{color}]"

    # ========================================================================
    # 全局运行心跳（顶部状态栏 running 时长显示）
    # ========================================================================
    # _run_start_ts: 状态进入 running 时记录 perf_counter，ready/disconnected 时置 None
    # _header_state: 最近一次 _update_header 收到的 state 名称
    # 设计思路：
    #   - 进入 running 时开始计时，每 2 秒把状态重渲染为 "running (32s)"
    #   - 给用户心理锚点：知道 Agent 真的在跑而不是卡住，也知道跑了多久
    #   - 超 3 分钟和超 10 分钟还在 running 时，用更醒目的颜色提示"可能慢"
    def _ui_heartbeat_tick(self) -> None:
        """每 2 秒触发的 UI 心跳。若当前为 running，刷新带时长的状态文案。"""
        state = getattr(self, "_header_state", "ready")
        if state == "running" and self._run_start_ts is not None:
            import time as _t
            sec = _t.perf_counter() - self._run_start_ts
            # 状态颜色 + 时长后缀
            if sec >= 600:  # 10 分钟：非常长，怀疑卡住
                color = "bold red"
                hint = "(stuck? use /compact)"
            elif sec >= 180:  # 3 分钟：较长
                color = "bold magenta"
                hint = "(slow)"
            else:  # 正常
                color = "yellow"
                hint = ""
            label = f"running ({sec:.0f}s) {hint}"
            # 直接刷新 header 中的 state 部分，避免重复构造 engine_info 等
            self._render_header_with(state_name="running", override_color=color, override_label=label)

    def _render_header_with(
        self,
        *,
        state_name: str,
        override_color: str | None = None,
        override_label: str | None = None,
    ) -> None:
        """
        内部方法：渲染顶部状态栏。
        - override_color / override_label: 用于心跳刷新时替换 state 的颜色和文案，
          不改动 _header_state 语义，只影响视觉显示。
        """
        try:
            header = self.query_one("#header", Label)
        except (NoMatches, Exception):
            return
        # 构建会话 ID 显示部分
        session = f"  [dim]{self._session_id}[/dim]" if self._session_id else ""
        # 基础状态颜色
        color = override_color or {
            "ready": "green",
            "running": "yellow",
            "disconnected": "red",
            "connecting": "dim",
        }.get(state_name, "dim")
        # 状态显示文本
        label = override_label or state_name
        # 引擎类型显示（LangGraph 系引擎用青色高亮，legacy 用暗色）
        engine_color = "cyan" if self._engine_type != "legacy" else "dim"
        engine_info = f"  [{engine_color}]{self._engine_type}[/{engine_color}]"
        # 检查点后端信息（非 legacy 引擎且非 none 时显示）
        if self._engine_type != "legacy" and self._checkpoint_backend != "none":
            engine_info += f"  [dim]({self._checkpoint_backend})[/dim]"
        # 自动模式显示（带颜色编码）
        auto_color = {"off": "dim", "read_only": "yellow", "on": "magenta"}.get(self._auto_mode, "dim")
        auto_info = f"  [{auto_color}]auto:{self._auto_mode}[/{auto_color}]"
        # 努力等级显示
        effort_color = {"minimal": "dim", "low": "cyan", "medium": "green", "high": "yellow", "max": "red"}.get(self._effort_level, "green")
        effort_info = f"  [{effort_color}]effort:{self._effort_level}[/{effort_color}]"
        # 模型预设显示
        model_color = {"fast": "cyan", "balanced": "green", "powerful": "magenta"}.get(self._model_preset, "green")
        model_info = f"  [{model_color}]model:{self._model_preset}[/{model_color}]"
        # 组装并更新状态栏
        header.update(
            f"[bold]IwanClaude[/bold]  [dim]{self._host}:{self._port}[/dim]"
            f"{session}{engine_info}{auto_info}{effort_info}{model_info}  [{color}]{label}[/{color}]"
        )

    def _update_header(self, state: str) -> None:
        """
        根据连接和运行状态刷新顶部状态栏，并在进入 running 时启动/重置时长计时。

        参数：
            state: str - 当前状态
                - "ready": 就绪，绿色
                - "running": 运行中，黄色（带每 2 秒心跳刷新）
                - "disconnected": 已断连，红色
                - "connecting": 连接中，灰色
        """
        # 记录最近状态，供心跳回调复用
        self._header_state = state
        # 进入 running 时开始计时；其他状态清零
        import time as _t
        if state == "running":
            self._run_start_ts = _t.perf_counter()
        else:
            self._run_start_ts = None
        # 用默认颜色和文案正常渲染
        self._render_header_with(state_name=state)

    async def _socket_loop(self) -> None:
        """
        Socket 连接主循环

        管理 SocketClient 的完整生命周期：连接、订阅事件、处理事件、断线重连。
        这是 TUI 与 core 服务通信的核心入口。

        工作流程：
        1. 创建 SocketClient 实例
        2. 尝试连接到 core 服务（失败则等待 2 秒重试）
        3. 连接成功后订阅事件（session/run/step/tool/llm/permission 等）
        4. 创建初始会话并获取引擎信息
        5. 启动事件循环，等待事件到达
        6. 断开后清理资源并重新连接

        订阅的事件主题：
        - session.*: 会话生命周期事件
        - run.*: 运行开始/结束事件
        - step.*: 步骤开始事件
        - tool.*: 工具调用开始/完成/失败事件
        - llm.token: LLM 流式 token 事件
        - llm.usage: LLM token 使用统计事件
        - log.*: 日志行事件
        - permission.*: 权限请求/拒绝事件
        - context.*: 上下文压缩事件
        - subagent.*: 子 Agent 开始/完成事件
        - skill.*: Skill 调用事件

        重连机制：
        - 使用 while True 循环实现自动重连
        - 连接失败后等待 2 秒重试
        - 订阅失败（IpcError）时显示错误并等待重连
        - 事件循环退出后清理资源并重新开始
        """
        # 获取头部标签（用于错误提示）
        header = self.query_one("#header", Label)

        while True:
            # ========== 创建新连接 ==========
            client = SocketClient(self._host, self._port)
            self._client = None
            try:
                # 尝试连接到 core 服务
                await client.connect()
            except (ConnectionRefusedError, OSError):
                # 连接被拒绝或网络错误
                log.warning("connection refused %s:%s, retrying", self._host, self._port)
                self._update_header("disconnected")
                await asyncio.sleep(2)  # 等待 2 秒后重试
                continue

            # ========== 连接成功 ==========
            log.info("connected to %s:%s", self._host, self._port)
            self._client = client
            self._update_header("connecting")

            # 启动事件循环任务
            loop_task = asyncio.create_task(client.run_event_loop())

            # 注册事件回调
            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event)

            client.on_event(on_event)

            try:
                # 添加事件循环异常回调（用于日志记录）
                loop_task.add_done_callback(
                    lambda t: log.error("loop_task failed: %s", t.exception())
                    if not t.cancelled() and t.exception() is not None
                    else None
                )
                # 订阅所有事件主题
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
                # 如果指定了回放运行 ID，添加到订阅参数
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id

                # 发送订阅命令
                await client.send_command("event.subscribe", params)

                # 创建初始会话
                created = await client.send_command("session.create", {"mode": "chat"})
                sid = str(created["session_id"])
                title = str(created.get("title", "")) or sid
                self._add_session(sid, title)

                # 更新会话配置
                state = self._sessions.get(sid)
                if state is not None:
                    state.auto_mode = str(created.get("auto_mode", "off"))
                    state.effort_level = str(created.get("effort_level", "medium"))
                    state.model_preset = str(created.get("model_preset", "balanced"))
                log.info("session created session_id=%s auto_mode=%s effort_level=%s model_preset=%s", sid, self._auto_mode, self._effort_level, self._model_preset)

                # 获取引擎信息
                engine_info = await client.send_command("session.engine_info", {})
                self._engine_type = engine_info.get("engine", "legacy")
                self._checkpoint_backend = engine_info.get("checkpoint_backend", "none")

                # 启用输入框
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = False
                    prompt.read_only = False
                    prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    prompt.focus()
                self._update_header("ready")

                # 等待事件循环结束（阻塞在此直到连接断开）
                await loop_task

            except IpcError as e:
                # 订阅或命令失败时显示错误
                header.update(f"[bold]IwanClaude[/bold]  [red]subscribe error: {e}[/red]")
            finally:
                # ========== 清理资源 ==========
                if not loop_task.done():
                    loop_task.cancel()  # 取消事件循环任务
                self._client = None  # 清空客户端引用
                self._session_id = None  # 清空当前会话（触发 setter 清空所有状态）
                # 禁用输入框
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = True
                    prompt.read_only = False
                    prompt.border_title = "disconnected, retrying..."
                # 结束当前 LLM 流式块
                self._break_llm()
                # 关闭客户端连接
                await client.close()

            # 显示断开状态并等待重连
            self._update_header("disconnected")
            await asyncio.sleep(2)  # 等待 2 秒后重试

    def _handle_event(self, event: dict[str, Any]) -> None:
        """
        事件处理包装器

        根据事件 type 路由到对应渲染逻辑，捕获异常防止 socket loop 因单个事件崩溃。
        这是事件处理的"安全网"，确保即使某个事件处理失败，整体通信也不会中断。

        参数：
            event: dict[str, Any] - 事件字典（包含 type 字段标识事件类型）

        设计要点：
        - 使用 try-except 包裹，防止单个事件处理异常导致整个事件循环崩溃
        - 调用 _handle_event_inner() 执行实际的事件路由逻辑
        - 异常时记录日志（包含事件类型），便于调试
        """
        try:
            self._handle_event_inner(event)
        except Exception:
            log.exception("_handle_event crashed  event_type=%s", event.get("type", "?"))

    def _handle_event_inner(self, event: dict[str, Any]) -> None:
        """
        实际的事件路由逻辑

        根据事件类型将事件分发到对应的处理逻辑，实现实时渲染 Agent 执行过程。
        这是 TUI 渲染的核心方法，所有 UI 更新都通过此方法触发。

        参数：
            event: dict[str, Any] - 事件字典

        支持的事件类型：
        - llm.token: LLM 流式 token，追加到当前流式块
        - session.waiting_for_input: 会话等待输入，恢复输入框
        - session.closed: 会话关闭，更新状态
        - session.renamed: 会话重命名，更新标题
        - session.auto_mode_changed: 自动模式变更
        - session.effort_level_changed: 努力等级变更
        - session.model_changed: 模型预设变更
        - session.engine_changed: Agent 引擎变更
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
        - llm.token 事件优先处理（不打断流式输出）
        - 其他事件先调用 _break_llm() 结束当前流式输出
        - 后台会话（非当前会话）事件只更新状态不渲染
        - 工具调用块通过 tool_use_id 关联，确保结果正确更新
        """
        t = event.get("type", "")

        # 获取事件的会话 ID（如果有）
        event_sid = event.get("session_id")

        # 如果事件属于后台会话（非当前会话），只更新状态不渲染
        # 这实现了多会话后台运行功能
        if event_sid and event_sid != self._session_id and event_sid in self._sessions:
            state = self._sessions[event_sid]
            if t == "run.started":
                state.busy = True
                self._refresh_tabbar()
            elif t in ("run.finished", "session.waiting_for_input", "session.closed"):
                state.busy = False
                self._refresh_tabbar()
            return  # 后台会话事件处理完毕，不渲染 UI

        # ========== LLM Token 事件（优先处理，不打断流式输出） ==========
        if t == "llm.token":
            token = event.get("token", "")
            # 如果当前没有活动的流式块，创建新的
            if self._current_llm is None:
                llm_block = LLMStreamBlock()
                self._append(llm_block)
                self._current_llm = llm_block
            # 追加 token 到当前流式块
            self._current_llm.append_token(token)
            return

        # ========== 其他事件：先结束当前 LLM 流式块 ==========
        self._break_llm()

        # ========== 会话事件 ==========

        # session.waiting_for_input：Agent 等待用户输入
        if t == "session.waiting_for_input":
            self._busy = False  # 重置忙碌状态
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = False  # 启用输入框
                prompt.read_only = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                prompt.focus()  # 获取焦点
            self._update_header("ready")

        elif t == "session.closed":
            # 会话关闭
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.read_only = False
                prompt.border_title = "session closed"
            self._update_header("disconnected")

        elif t == "session.renamed":
            # 会话重命名：更新本地标题并刷新标签栏
            new_title = event.get("title", "")
            state = self._state
            if state is not None:
                state.title = new_title
            self._refresh_tabbar()
            self._update_header("ready")

        elif t == "session.auto_mode_changed":
            # 自动模式变更
            mode = event.get("mode", "off")
            self._auto_mode = mode
            self._update_header("ready")

        elif t == "session.effort_level_changed":
            # 努力等级变更
            level = event.get("level", "medium")
            self._effort_level = level
            self._update_header("ready")

        elif t == "session.model_changed":
            # 模型预设变更
            preset = event.get("preset", "balanced")
            self._model_preset = preset
            self._update_header("ready")

        elif t == "session.engine_changed":
            # Agent 引擎变更（多客户端同步：其他客户端切换引擎时本端也更新状态栏）
            engine = event.get("engine", "legacy")
            self._engine_type = engine
            self._update_header("ready")

        # ========== 运行事件 ==========

        elif t == "run.started":
            # 运行开始：显示运行头部信息
            run_id = event.get("run_id", "")
            goal = event.get("goal", "")
            self._append(Static(
                f"[dim]run[/dim]  [cyan]{run_id}[/cyan]  [dim]{_preview(goal, 96)}[/dim]",
                classes="run-header",
            ))

        # ========== Skill 事件 ==========

        elif t == "skill.invoked":
            # Skill 被调用：显示 Skill 名称和参数
            skill_name = event.get("skill_name", "")
            arguments = event.get("arguments", "")
            args_preview = _preview(arguments, 80) if arguments else ""
            args_part = f"  [dim]{args_preview}[/dim]" if args_preview else ""
            self._append(Static(
                f"[bold cyan]/{skill_name}[/bold cyan]{args_part}",
                classes="log-line",
            ))

        # ========== 子 Agent 事件 ==========

        elif t == "subagent.started":
            # 子 Agent 开始：记录运行 ID 和开始时间，显示起始行
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
            # 子 Agent 完成：显示结果和耗时
            run_id = event.get("run_id", "")
            status = event.get("status", "")
            # 获取之前保存的描述和开始时间
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

        # ========== 步骤事件 ==========

        elif t == "step.started":
            # 步骤开始：显示步骤分隔线（子 Agent 内的步骤不显示）
            run_id = event.get("run_id", "")
            if run_id in self._subagent_run_ids:
                return  # 子 Agent 内的步骤已由 subagent 事件处理
            step = event.get("step", "")
            self._append(Static(
                f"[dim]step {step}[/dim]",
                classes="step-divider",
            ))

        # ========== 工具调用事件 ==========

        elif t == "tool.call_started":
            # 工具调用开始：创建 ToolCallBlock 并添加到待完成列表
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            params = event.get("params") or {}
            run_id = event.get("run_id", "")
            tc_block = ToolCallBlock(tool_name, params)
            # 子 Agent 内的工具调用增加缩进
            if run_id in self._subagent_run_ids:
                tc_block.styles.padding = (0, 2, 0, 6)
            self._pending_tool_blocks[tool_use_id] = tc_block
            self._append(tc_block)

        elif t == "tool.call_finished":
            # 工具调用完成：更新对应 ToolCallBlock 的结果
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            output = str(event.get("output") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(output, elapsed_ms)

        elif t == "tool.call_failed":
            # 工具调用失败：更新对应 ToolCallBlock 为错误状态
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            error_msg = str(event.get("error_message") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(error_msg, elapsed_ms, is_error=True)

        # ========== 运行完成事件 ==========

        elif t == "run.finished":
            # 运行完成：显示成功或失败状态，并恢复输入框
            # 【关键修复】无论成功还是失败，都必须重置 _busy=False 并启用输入框，
            # 否则用户将无法继续输入（表现为"卡住"）。
            # 正常流程中 session.waiting_for_input 事件会稍后到达并再次重置，
            # 但如果该事件丢失或延迟，这里就是最后的兜底。
            status = event.get("status", "")
            steps = event.get("steps", 0)
            reason = event.get("reason") or ""
            if status == "success":
                self._append(Static(
                    f"[bold green]✓ completed[/bold green]  [dim]{steps} steps[/dim]",
                    classes="run-ok",
                ))
            else:
                # 对 exceeded_max_steps 给出更友好的提示
                if reason == "exceeded_max_steps":
                    self._append(Static(
                        f"[bold yellow]⚠ reached step limit[/bold yellow]  "
                        f"[dim]({steps} steps — try /compact or increase IWAN_MAX_STEPS)[/dim]",
                        classes="run-err",
                    ))
                else:
                    detail = f"  [dim]{reason}[/dim]" if reason else ""
                    self._append(Static(
                        f"[bold red]✗ failed[/bold red]{detail}  [dim]{steps} steps[/dim]",
                        classes="run-err",
                    ))
            # 重置忙碌状态，恢复输入框（兜底机制）
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.read_only = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                prompt.focus()
            self._update_header("ready")

        # ========== LLM 使用统计事件 ==========

        elif t == "llm.usage":
            # LLM 使用统计：显示 token 消耗和上下文占用率
            run_id = event.get("run_id", "")
            if run_id in self._subagent_run_ids:
                return  # 子 Agent 的使用统计不显示
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

        # ========== 上下文压缩事件 ==========

        elif t == "context.compacted":
            # 上下文压缩完成：显示压缩结果
            orig = event.get("original_tokens", 0)
            summary = event.get("summary_tokens", 0)
            self._last_context_pct = 0.0
            self._append(Static(
                f"[bold cyan]⚡ Context compacted[/bold cyan]"
                f"  [dim]original≈{orig} tokens → summary={summary} tokens[/dim]",
                classes="log-line",
            ))

        # ========== 权限请求事件 ==========

        elif t == "permission.requested":
            # 权限请求：显示审批控件，等待用户决策
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            param_preview = str(event.get("param_preview", ""))
            # 调试日志：记录焦点状态
            try:
                _focused_repr = repr(self.focused)
            except Exception:
                _focused_repr = "?"
            log.info(
                "permission.requested tool=%s id=%s  app.focused=%s",
                tool_name, tool_use_id, _focused_repr,
            )
            # 创建权限审批块（显示在日志流中）
            perm_block = PermissionBlock(tool_use_id, tool_name, param_preview)
            self._pending_permission_blocks[tool_use_id] = perm_block
            # 禁用输入框，提示需要权限
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.border_title = "permission required"
            # 将权限审批块添加到日志视图
            self._append(perm_block)
            # 创建权限选择控件并挂载
            select = PermissionSelect(tool_use_id)
            self._mount_permission_select(select)
            log.debug("PermissionSelect mounted before #prompt  pending=%d", len(self._pending_permission_blocks))

        # ========== 权限拒绝事件 ==========

        elif t == "permission.denied":
            # 权限被拒绝（非用户交互触发的超时或断连等情况）
            tool_use_id = str(event.get("tool_use_id", ""))
            decision = str(event.get("decision", "denied"))
            if tool_use_id in self._pending_permission_blocks:
                # 更新审批块状态
                perm_block = self._pending_permission_blocks.pop(tool_use_id)
                perm_block._resolve(decision)
                # 移除权限选择控件
                try:
                    select = self.query_one(PermissionSelect)
                    select.remove()
                except Exception:
                    pass
                # 如果没有待处理权限审批，恢复输入框
                if not self._pending_permission_blocks:
                    p = self._prompt()
                    if p is not None:
                        p.disabled = False
                        p.read_only = False
                        p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                        p.focus()

        # ========== 日志行事件 ==========

        elif t == "log.line":
            # 日志行：显示 Agent 的内部日志信息
            level = event.get("level", "INFO")
            # 根据日志级别选择颜色
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
    这是整个 TUI 模块的唯一公开入口点。

    参数：
        config: IwanConfig - 应用配置（包含 host、port 等）
        replay_run_id: str | None - 可选，连接后回放指定运行的历史事件
            用于调试或演示场景，重放历史运行事件

    实现步骤：
    1. 使用配置中的 host 和 port 创建 IwanTuiApp 实例
    2. 调用 app.run() 启动 Textual 应用事件循环
    3. 事件循环持续运行直到用户退出（Ctrl+Q）

    调用方式：
        python -m iwan_claude.tui
        python -m iwan_claude.tui --replay run-abc123

    使用示例：
        >>> from iwan_claude.tui.app import run
        >>> from iwan_claude.core.config import get_config
        >>> config = get_config()
        >>> run(config)
    """
    # 使用配置创建 TUI 应用实例
    app = IwanTuiApp(config.host, config.port, replay_run_id=replay_run_id)
    # 启动 Textual 事件循环（阻塞直到用户退出）
    app.run()