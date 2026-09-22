# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 可折叠的工具调用块模块
# 本模块从 app.py 中提取 ToolCallBlock 类，负责在日志流中展示工具调用的摘要与详情。
# 设计要点：使用 CSS 类（expanded）控制展开/折叠状态，点击切换显示；
#         工具执行中每秒刷新 elapsed 耗时（心跳），让用户知道"还在跑，没卡死"。
# ---------------------------------------------------------------------------

# time.perf_counter：高精度计时（工具运行中心跳秒数计算）
import time

# 从 textual.widget 导入 Widget 基类，ToolCallBlock 继承 Widget 而非 Static
# 因为需要包含多个子 widget（摘要 + 详情）
from textual.widget import Widget

# 从 textual.widgets 导入 Static，用于创建摘要和详情子 widget
from textual.widgets import Static

# 从 textual.app 导入 ComposeResult，compose() 方法的返回值类型
from textual.app import ComposeResult

# 从 textual.markup 导入 escape：把任意字符串中的 [ ] 转义成 \[ \]，
# 防止 Textual 把错误消息里的 [sandbox] 这种方括号误解析为 markup 标签。
# 注意：Textual 8.x 版名为 escape，新版本才改名为 escape_markup
from textual.markup import escape

# 从 typing 导入 Any，用于类型注解
from typing import Any

# 从 iwan_claude.tui.formatters 导入工具参数格式化辅助函数
# _params_str: 将参数转为格式化 JSON 字符串
# _param_summary: 从参数中提取关键字段生成摘要
# 注意：这两个函数实际定义在 app.py 中，后续将迁移至 formatters.py
from iwan_claude.tui.formatters import _params_str, _param_summary


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

    # 定义该 widget 的默认 CSS 样式
    # 关键点：.detail 默认 display:none，当父组件有 .expanded 类时显示
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
            _start_ts: 工具调用开始时间戳（perf_counter），用于运行中心跳刷新
        """
        # 调用父类 Widget 的 __init__
        super().__init__()
        # 保存工具名称（如 bash、read_file、write_file 等）
        self._tool_name = tool_name
        # 保存原始参数字典
        self._params = params
        # 调用 _params_str() 将参数格式化为带缩进的 JSON 字符串，供详情展开时显示
        # 同时 escape_markup 把可能出现的方括号（JSON 里的数组、错误消息片段等）转义，
        # 防止 Static(markup=True) 在展开详情时把方括号解析成 Textual markup 标签导致崩溃
        self._params_full = escape(_params_str(params))
        # 工具输出内容，初始为空（尚未执行）
        self._output = ""
        # 工具执行耗时（毫秒），初始为 0（尚未完成）
        self._elapsed_ms = 0
        # 标记是否为错误状态，初始为 False
        self._is_error = False
        # 标记工具调用是否已完成，初始为 False
        self._finished = False
        # 【新增】工具调用开始的高精度时间戳，用于运行中心跳刷新
        # 工具块创建 ≈ 工具开始执行，所以这里直接记录当前 perf_counter
        self._start_ts: float = time.perf_counter()

    def _refresh_summary(self) -> None:
        """
        便捷方法：刷新 summary 子组件的内容（若已挂载）。
        避免在多处重复写 if self.children: query_one(...).update(...)
        """
        if self.children:
            self.query_one(".summary", Static).update(self._summary())

    def on_mount(self) -> None:
        """
        Widget 挂载后启动运行中心跳定时器。
        【心跳行为】
        - 仅在 _finished=False 时刷新摘要，显示实时 elapsed
        - 刷新间隔 1.0s，避免频繁渲染
        - set_result() 完成时会调用 stop() 停止心跳
        """
        # set_interval 返回一个 Timer 对象，可以在 set_result 中 cancel
        self._timer = self.set_interval(1.0, self._on_heartbeat, pause=False)

    def _on_heartbeat(self) -> None:
        """
        每秒触发的心跳回调。
        - 如果尚未完成：根据 perf_counter 计算实时耗时，刷新摘要
        - 如果已完成：停止心跳定时器
        """
        if self._finished:
            # 工具已经完成，停止心跳（防止"僵尸定时器"）
            self._timer.pause()
            return
        # 根据当前时间与 _start_ts 差值计算实时毫秒数
        now_ms = int((time.perf_counter() - self._start_ts) * 1000)
        self._elapsed_ms = now_ms
        # 刷新摘要（显示 "running  XX.Xs" 的状态）
        self._refresh_summary()

    def compose(self) -> ComposeResult:
        """
        组合子 widget

        创建摘要和详情两个子 widget：
        - .summary: 显示工具调用摘要（默认可见）
        - .detail: 显示完整参数和输出（默认隐藏，通过 CSS 控制）

        返回：
            ComposeResult: 子 widget 生成器
        """
        # 生成摘要子 widget，使用 _summary() 生成初始文本，附加 CSS 类 "summary"
        yield Static(self._summary(), classes="summary")
        # 生成详情子 widget，初始为空字符串，附加 CSS 类 "detail"
        yield Static("", classes="detail")

    def _summary(self) -> str:
        """
        生成摘要行文本

        根据工具名称、参数和状态生成摘要行，用于折叠状态下的显示。

        返回：
            str: 格式化的摘要文本

        特殊处理：
        - 运行中：显示 "running ... Xs" 心跳状态，>30s 黄色警告，>120s 红色提示"可能卡住"
        - note_save 工具完成且无错误时显示 "remembered" 标签
        - 其他完成工具显示工具名称、参数摘要和 done/failed 状态

        显示格式：
            "tool" tool_name params_pre running|done|failed elapsed (click to expand)
        """
        # note_save 工具在成功完成后显示特殊的 "remembered" 标签
        if self._tool_name == "note_save" and self._finished and not self._is_error:
            return f"  [green]remembered[/green]  [dim]{self._elapsed_ms}ms[/dim]"

        # 调用 _param_summary() 生成参数摘要（从关键字段提取）
        params_pre = _param_summary(self._tool_name, self._params)
        # 构建摘要行：以 "tool" 前缀开头，后跟工具名称
        line = f"  [dim]tool[/dim] [bold]{self._tool_name}[/bold]"
        # 如果有参数摘要，追加到摘要行
        if params_pre:
            line += f"  [dim]{params_pre}[/dim]"

        # ========== 状态追加 ==========
        if not self._finished:
            # --- 运行中状态（带心跳 elapsed 和长时间警告）---
            sec = self._elapsed_ms / 1000.0
            # 根据耗时长度选择不同颜色和文案
            if sec >= 120:
                # 超过 2 分钟：红色提示，可能卡住
                color = "bold red"
                status = "running (slow?)"
            elif sec >= 30:
                # 超过 30 秒：黄色警告，脚本可能本来就慢
                color = "bold yellow"
                status = "running"
            else:
                # 30 秒内：正常运行，蓝色/青色
                color = "bold cyan"
                status = "running"
            # 格式化成秒（一位小数），避免用户看"517046ms"看得头晕
            line += f"  [{color}]{status}[/{color}]  [dim]{sec:.1f}s[/dim]"
        else:
            # --- 完成状态 ---
            # 根据是否为错误状态选择颜色
            color = "red" if self._is_error else "green"
            # 根据错误状态选择状态文本
            status = "failed" if self._is_error else "done"
            # 如果有输出内容，显示 "(click to expand)" 提示
            hint = "  [dim](click to expand)[/dim]" if self._output else ""
            # 追加状态、耗时和点击提示
            line += f"  [{color}]{status}[/{color}]  [dim]{self._elapsed_ms}ms[/dim]{hint}"
        # 返回完整的摘要行
        return line

    def set_result(self, output: str, elapsed_ms: int, *, is_error: bool = False) -> None:
        """
        工具调用完成时更新结果并刷新摘要

        更新工具调用的结果、耗时和状态，并刷新摘要显示。
        【额外行为】完成后立即停止心跳定时器，避免无谓刷新。

        参数：
            output: str - 工具输出内容
            elapsed_ms: int - 执行耗时（毫秒）
            is_error: bool - 是否为错误状态，默认为 False

        实现细节：
        - 更新内部状态（_output, _elapsed_ms, _is_error, _finished）
        - 如果 widget 已挂载（有子 widget），更新摘要显示
        - 如果 widget 未挂载，跳过 DOM 更新（稍后挂载时会自动显示最新状态）
        """
        # 保存工具输出内容（escape 保证任何方括号都不会被 Textual 解析为 markup 标签）
        # 典型场景：sandbox 错误消息 "[sandbox] quota exceeded" 中的方括号；
        # 以及 json 输出里的任何方括号，都要安全渲染为普通文本。
        self._output = escape(output)
        # 保存执行耗时（以服务端返回的 elapsed_ms 为准，不使用本地心跳）
        self._elapsed_ms = elapsed_ms
        # 保存错误状态
        self._is_error = is_error
        # 标记工具调用已完成
        self._finished = True
        # 完成后停止心跳定时器（防止"僵尸定时器"继续每秒刷新）
        try:
            if hasattr(self, "_timer") and self._timer is not None:
                self._timer.pause()
        except Exception:
            # 极端情况：timer 操作抛异常不影响核心结果显示
            pass
        # 如果 widget 已有子组件（即已挂载到 DOM），实时更新摘要显示
        self._refresh_summary()

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
        # 如果工具调用尚未完成，忽略点击事件
        if not self._finished:
            return
        # 检查当前是否已展开（即 "expanded" 是否在 CSS 类列表中）
        if "expanded" in self.classes:
            # 已展开则移除 expanded 类，CSS 规则会自动隐藏 .detail
            self.remove_class("expanded")
        else:
            # 未展开则更新详情内容（包含完整参数、输出和耗时）
            detail = self.query_one(".detail", Static)
            # 更新详情 widget 的内容：参数 JSON + 输出 + 耗时
            detail.update(
                f"[dim]params[/dim]\n{self._params_full}\n\n"
                f"[dim]output[/dim]\n{self._output}\n\n"
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"
            )
            # 添加 expanded 类，CSS 规则会自动显示 .detail
            self.add_class("expanded")