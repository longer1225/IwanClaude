# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 斜杠命令自动补全控件模块
# 本模块从 app.py 中提取 SlashCompleteWidget 类。
# 当用户在聊天输入框中输入 "/" 时，该控件显示可用的命令和 skill 列表。
# 设计要点：can_focus=False，焦点保留在输入框，由宿主 App 转发键盘事件。
# ---------------------------------------------------------------------------

# 从 textual.widgets 导入 Static，SlashCompleteWidget 继承 Static 以获得静态文本更新能力
from textual.widgets import Static

# 从 textual.message 导入 Message 基类，用于定义 Selected 消息
from textual.message import Message


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
    - can_focus=False：焦点仍在输入框，由宿主 App 转发键盘事件
      这样做的好处是用户在补全过程中仍可继续编辑输入框文本
    - 保留全量列表 _all_items，筛选后的列表保存在 _filtered
    - 光标位置在筛选时自动调整，避免越界
    - 所有导航操作（move_up/move_down/select_current）由宿主 App 调用

    使用示例：
        >>> items = [("help", "显示帮助"), ("review", "代码审查")]
        >>> widget = SlashCompleteWidget(items)
        >>> widget.set_query("rev")  # 筛选出 review
        >>> widget.select_current()  # 发布 Selected("review")
    """

    # 设置 can_focus=False，使该控件不会抢占键盘焦点
    # 焦点始终保留在 ChatTextArea 上，宿主 App 负责将相关键盘事件转发给本控件
    can_focus = False

    # 定义该 widget 的默认 CSS 样式：带边框的弹出框外观
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
        宿主 App 收到此消息后，会将选中的命令填入输入框。

        属性：
            skill_name: str - 被选中的命令或 skill 名称
        """

        def __init__(self, skill_name: str) -> None:
            """
            初始化选中消息

            参数：
                skill_name: str - 被选中的命令或 skill 名称
            """
            # 保存被选中的命令/skill 名称
            self.skill_name = skill_name
            # 调用父类 Message 的 __init__ 完成消息初始化
            super().__init__()

    def __init__(self, items: list[tuple[str, str]]) -> None:
        """
        初始化斜杠命令自动补全弹窗

        参数：
            items: list[tuple[str, str]] - 命令列表，每个元素为 (名称, 描述)
                   名称如 "help"、"review"，描述如 "显示帮助"、"代码审查"

        属性：
            _all_items: 全量命令列表（原始数据，不会被修改）
            _filtered: 筛选后的命令列表（实时更新）
            _cursor: 当前光标位置（选项索引），初始为 0
        """
        # 调用父类 Static 的 __init__，传入空字符串作为初始显示
        super().__init__("")
        # 保存全量命令列表（原始数据，用于筛选）
        self._all_items = items
        # 初始化筛选后的列表为全量列表（尚未筛选）
        self._filtered: list[tuple[str, str]] = list(items)
        # 初始化光标位置为第一个选项
        self._cursor = 0

    def set_query(self, query: str) -> None:
        """
        根据查询字符串筛选命令列表并重新渲染

        参数：
            query: str - 查询字符串（/ 之后的部分），如 "hel" 会匹配 "help"

        实现细节：
        - 将查询字符串转换为小写，进行大小写不敏感匹配
        - 筛选条件：查询为空时显示全部，否则只保留包含查询字符串的命令
        - 光标位置自动调整，不超过筛选后列表的范围（防止越界）
        - 如果 widget 已挂载，调用 _redraw() 更新显示

        使用示例：
            >>> widget.set_query("rev")  # 筛选名称包含 "rev" 的命令
            >>> widget.set_query("")     # 显示所有命令
        """
        # 将查询字符串转为小写，用于大小写不敏感匹配
        q = query.lower()
        # 使用列表推导式筛选命令：查询为空时保留全部，否则匹配名称包含查询字符串的命令
        self._filtered = [(n, d) for n, d in self._all_items if not q or q in n.lower()]
        # 调整光标位置：如果筛选后列表变短，光标可能越界，需要将其限制在有效范围内
        self._cursor = min(self._cursor, max(0, len(self._filtered) - 1))
        # 如果 widget 已挂载到 DOM，则重新渲染显示
        if self.is_attached:
            self._redraw()

    def move_up(self) -> None:
        """
        向上移动光标并重新渲染

        光标循环移动：到达顶部后回到底部。
        由宿主 App 在上箭头事件中调用。

        使用示例：
            >>> widget.move_up()
        """
        # 仅当筛选后列表非空时才移动
        if self._filtered:
            # 光标向上移动，使用取模实现循环（到顶后跳到底部）
            self._cursor = (self._cursor - 1) % len(self._filtered)
            # 重新渲染显示
            self._redraw()

    def move_down(self) -> None:
        """
        向下移动光标并重新渲染

        光标循环移动：到达底部后回到顶部。
        由宿主 App 在下箭头事件中调用。

        使用示例：
            >>> widget.move_down()
        """
        # 仅当筛选后列表非空时才移动
        if self._filtered:
            # 光标向下移动，使用取模实现循环（到底后跳回顶部）
            self._cursor = (self._cursor + 1) % len(self._filtered)
            # 重新渲染显示
            self._redraw()

    def select_current(self) -> None:
        """
        选中当前光标指向的命令并发布 Selected 消息

        如果筛选列表非空，发布包含选中命令名称的 Selected 消息。
        由宿主 App 在 Enter/Tab 事件中调用。

        使用示例：
            >>> widget.select_current()  # 发布 Selected("help")
        """
        # 仅当筛选后列表非空时才发布选中消息
        if self._filtered:
            # 获取当前光标指向的命令名称，创建 Selected 消息并发布
            self.post_message(self.Selected(self._filtered[self._cursor][0]))

    def has_selection(self) -> bool:
        """
        判断当前是否有可选项

        返回：
            bool: True 表示有可选项，False 表示无匹配项
        """
        # 检查筛选后列表是否非空
        return len(self._filtered) > 0

    def on_mount(self) -> None:
        """
        控件挂载时的初始化

        调用 _redraw() 渲染初始命令列表。
        """
        # 挂载完成后立即渲染初始列表
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
        # 如果筛选后列表为空，显示无匹配提示
        if not self._filtered:
            self.update("[dim]  no matching commands[/dim]")
            return
        # 创建行列表
        lines: list[str] = []
        # 遍历筛选后的命令列表，i 为索引，name 为命令名，desc 为描述
        for i, (name, desc) in enumerate(self._filtered):
            # 如果有描述，添加 dim 样式的描述文本
            desc_part = f"  [dim]{desc}[/dim]" if desc else ""
            # 如果是当前光标项，使用粗体青色和 ❯ 标记高亮
            if i == self._cursor:
                lines.append(f"  [bold cyan]❯ /{name}[/bold cyan]{desc_part}")
            else:
                # 非光标项使用普通青色
                lines.append(f"    [cyan]/{name}[/cyan]{desc_part}")
        # 在末尾追加操作提示行
        lines.append("[dim]  ↑↓ navigate   tab/enter select   esc dismiss[/dim]")
        # 将所有行拼接为字符串并更新 widget 显示
        self.update("\n".join(lines))