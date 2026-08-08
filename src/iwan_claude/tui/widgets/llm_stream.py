# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# LLM 流式输出块模块
# 本模块从 app.py 中提取 LLMStreamBlock 类，负责在终端中实时渲染 LLM 输出。
# 设计要点：使用 _text 累积原始文本，使用 _finalized 标记渲染完成状态。
# ---------------------------------------------------------------------------

# 从 rich.markdown 导入 Markdown 类，用于将纯文本渲染为带代码高亮的 Markdown
from rich.markdown import Markdown

# 从 textual.widgets 导入 Static 类，LLMStreamBlock 继承 Static 以获得静态文本更新能力
from textual.widgets import Static


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

    # 定义该 widget 的默认 CSS 样式：左右 padding 为 2，文字颜色使用主题默认色
    DEFAULT_CSS = "LLMStreamBlock { padding: 0 2; color: $text; }"

    def __init__(self) -> None:
        """
        初始化 LLM 流式输出块

        属性：
            _text: 累积的原始文本，初始为空字符串
            _finalized: 是否已完成渲染，初始为 False
        """
        # 调用父类 Static 的 __init__，传入空字符串作为初始内容
        super().__init__("")
        # _text 存储从 LLM 接收到的所有 token 拼接后的完整原始文本
        self._text = ""
        # _finalized 标记流式输出是否已结束，结束后不再接收新 token
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
        # 如果已经 finalize，直接返回，忽略后续 token（防止 Markdown 渲染被覆盖）
        if self._finalized:
            return
        # 将新 token 追加到累积文本中
        self._text += token
        # 调用 Static.update() 将最新的纯文本推送到屏幕上，触发 Textual 重绘
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
        # 防止重复调用 finalize：如果已经完成渲染则直接返回
        if self._finalized:
            return
        # 将 _finalized 设为 True，之后任何 append_token 调用都会被忽略
        self._finalized = True
        # 仅当累积文本非空（去除空白后有内容）时才进行 Markdown 渲染
        if self._text.strip():
            # 使用 Rich 的 Markdown 类将纯文本渲染为带格式的 Markdown，
            # code_theme="monokai" 指定代码块使用 monokai 高亮主题
            self.update(Markdown(self._text, code_theme="monokai"))