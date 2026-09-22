"""
示例自定义工具 - 展示如何通过热加载机制扩展 Agent 能力

【使用方式】
1. 复制此文件到 .iwan/tools/（项目级）或 ~/.iwan/tools/（用户级）
2. 修改 name / description / input_schema / invoke 实现
3. 重启 core 或在对话中调用 reload_tools 工具

【热加载机制说明】
- ToolLoader 会扫描 .iwan/tools/ 和 ~/.iwan/tools/ 目录下的 .py 文件
- 自动发现 BaseTool 子类并实例化注册
- 项目级（.iwan/tools/）优先级高于用户级（~/.iwan/tools/），同名覆盖
- 需要运行时依赖时，通过 required_deps 声明（见下方 DepExampleTool）

【可用的运行时依赖】（通过 required_deps 声明后，loader 自动注入）
- bus: EventBus 实例，用于发布事件
- provider: LLMProvider 实例，用于调用 LLM
- config: IwanConfig 实例，用于读取配置
- session_id: 当前会话 ID（字符串）
- permission_manager: 权限管理器实例
- index_manager: RAG 知识索引管理器（仅 RAG 启用时可用）
"""

from __future__ import annotations

from iwan_claude.core.tools.base import BaseTool, ToolResult


class EchoTool(BaseTool):
    """
    回声工具 - 最简单的无依赖工具示例

    将输入原样返回，用于演示热加载的基本用法。
    """

    name = "echo"
    description = "回声工具：将输入文本原样返回（热加载示例工具）"
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要回声的文本",
            }
        },
        "required": ["text"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """将输入文本原样返回"""
        text = str(params.get("text", ""))
        return ToolResult(content=f"Echo: {text}")


class WordCountTool(BaseTool):
    """
    字数统计工具 - 无依赖工具示例

    统计输入文本的字符数和单词数。
    """

    name = "word_count"
    description = "统计文本的字符数和单词数（热加载示例工具）"
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要统计的文本",
            }
        },
        "required": ["text"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """统计字符数和单词数"""
        text = str(params.get("text", ""))
        char_count = len(text)
        word_count = len(text.split())
        return ToolResult(
            content=f"字符数: {char_count}, 单词数: {word_count}"
        )
