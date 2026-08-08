"""
IwanClaude TUI 包

该包包含 IwanClaude 的终端用户界面实现，基于 Textual 框架构建。

子模块结构：
- app: IwanTuiApp 主应用类和 run() 启动函数
- widgets: 所有自定义 UI 组件（LLMStreamBlock、ToolCallBlock 等）
- models: 数据模型（_SessionState）
- formatters: 文本格式化工具函数
- knowledge_manual.md: TUI 前端知识手册

使用示例：
    >>> from iwan_claude.tui import run
    >>> from iwan_claude.core.config import get_config
    >>> config = get_config()
    >>> run(config)
"""

from iwan_claude.tui.app import IwanTuiApp, run

__all__ = ["IwanTuiApp", "run"]
