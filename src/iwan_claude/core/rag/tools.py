"""
RAG 工具模块 - 提供 RAG 相关的工具定义

【学习要点】
1. 工具注册：继承 BaseTool 基类，定义工具名称、描述和参数 schema
2. 参数验证：使用 Pydantic BaseModel 进行参数验证
3. 工具实现：实现 invoke 方法，调用 KnowledgeIndexManager 执行实际操作
4. 工具类型：
   - SearchKnowledgeTool: 搜索知识库
   - IndexKnowledgeTool: 索引文件到知识库
   - ForgetKnowledgeTool: 从知识库移除文件

【工具使用场景】
- 用户可以通过自然语言指令触发这些工具
- Agent 根据任务需要自动调用工具
- 工具返回结果供 Agent 分析和使用

【核心依赖】
- KnowledgeIndexManager: 知识索引管理器
- BaseTool: 工具基类
- ToolResult: 工具返回结果
- Pydantic BaseModel: 参数验证

【参数验证模式】
- 使用 Pydantic BaseModel 定义参数结构
- 自动验证参数类型和约束
- 支持默认值和额外参数忽略
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.rag.index import IndexResult
from iwan_claude.core.tools.base import BaseTool, ToolResult


class SearchKnowledgeParams(BaseModel):
    """
    搜索知识库参数模型 - 定义搜索工具的参数结构

    【字段说明】
    - query: str - 搜索查询（必填）
    - top_k: int - 返回结果数量（默认 5，范围 1-20）
    - filters: dict[str, Any] | None - 过滤器（可选）
    - hybrid: bool - 是否使用混合检索（默认 True）

    【Pydantic 配置】
    - model_config = ConfigDict(extra="ignore"): 忽略额外参数

    【参数约束】
    - top_k: ge=1（大于等于 1），le=20（小于等于 20）

    【使用示例】
    ```python
    params = SearchKnowledgeParams(
        query="how to configure function",
        top_k=5,
        filters={"source_path": "src/main.py"},
        hybrid=True
    )
    ```
    """
    # Pydantic 配置：忽略额外参数
    model_config = ConfigDict(extra="ignore")
    # 搜索查询（必填）
    query: str
    # 返回结果数量（默认 5，范围 1-20）
    top_k: int = Field(default=5, ge=1, le=20)
    # 过滤器（可选）
    filters: dict[str, Any] | None = None
    # 是否使用混合检索（默认 True）
    hybrid: bool = Field(default=True, description="Use hybrid search (semantic + keyword)")


class SearchKnowledgeTool(BaseTool):
    """
    搜索知识库工具 - 在知识库中搜索相关信息

    【学习要点】
    1. 工具定义：继承 BaseTool，定义 params_model、name、description、input_schema
    2. 混合检索：支持语义检索和关键词检索
    3. 结果格式化：返回结构化的搜索结果（包含来源、行号、内容预览）

    【工具参数】
    - query: str - 搜索查询（必填）
    - top_k: int - 返回结果数量（默认 5，最大 20）
    - filters: dict[str, Any] | None - 过滤器（如 {"source_path": "src/main.py"}）
    - hybrid: bool - 是否使用混合检索（默认 True）

    【返回格式】
    ```
    --- Result 1 (score: 0.9500) ---
    Source: src/main.py
    Lines: 42-50
    Symbol: function_name

    代码内容预览...

    --- Result 2 (score: 0.8500) ---
    ...
    ```

    【设计目的】
    提供知识检索能力，帮助 Agent 查找相关代码和文档。

    【使用示例】
    ```python
    tool = SearchKnowledgeTool(index_manager)
    result = await tool.invoke({
        "query": "how to configure function",
        "top_k": 3,
        "hybrid": True
    })
    ```
    """
    # 参数模型（用于参数验证）
    params_model = SearchKnowledgeParams
    # 工具名称（必须唯一）
    name = "search_knowledge"
    # 工具描述（用于 Agent 理解工具用途）
    description = (
        "Search the local knowledge base (RAG) for relevant information. "
        "Uses hybrid search combining semantic (embedding-based) and keyword matching. "
        "Use this when you need information about existing code, documentation, "
        "or project files. Returns top_k relevant chunks with source path, line numbers, "
        "and content."
    )
    # 输入 schema（用于工具调用时的参数提示）
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to find relevant information.",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of results to return (default 5, max 20).",
                "default": 5,
            },
            "filters": {
                "type": "object",
                "description": "Optional filters to narrow results (e.g., {source_path: 'src/main.py'}).",
            },
            "hybrid": {
                "type": "boolean",
                "description": "Use hybrid search combining semantic and keyword matching (default: true).",
                "default": True,
            },
        },
        "required": ["query"],
    }

    def __init__(self, index_manager: Any, adaptive_retriever: Any = None) -> None:
        """
        初始化搜索知识库工具

        【参数说明】
        - index_manager: Any - 知识索引管理器
        - adaptive_retriever: Any - 自适应检索器（可选）
            传入时启用 Adaptive RAG + CRAG + Reranking 全流程。
            传入 None 时降级为原始的 hybrid_search/search。

        【字段说明】
        - _index_manager: Any - 知识索引管理器引用
        - _adaptive_retriever: Any - 自适应检索器引用（可选）
        """
        # 调用父类初始化
        super().__init__()
        # 保存知识索引管理器引用
        self._index_manager = index_manager
        # 保存自适应检索器引用（可选）
        self._adaptive_retriever = adaptive_retriever

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        调用工具

        【参数说明】
        - params: dict[str, object] - 工具参数

        【返回值】
        - ToolResult: 工具执行结果

        【执行流程】
        1. 使用 Pydantic 验证参数
        2. 如果有 adaptive_retriever，使用自适应检索（Adaptive RAG + CRAG + Reranking）
        3. 否则根据 hybrid 参数选择检索方式（原始逻辑）
        4. 如果没有结果，返回提示信息
        5. 格式化检索结果
        6. 返回 ToolResult

        【自适应检索模式】
        当 adaptive_retriever 存在时：
        - LLM 自动判断查询类型（direct/grep/rag）
        - CRAG 质量评估 + 回退
        - LLM Reranking 精排
        - 返回结果包含策略信息

        【原始检索模式】
        当 adaptive_retriever 为 None 时：
        - hybrid=True: 使用混合检索（语义 + 关键词）
        - hybrid=False: 使用纯语义检索

        【结果格式化】
        - 每个结果包含：分数、来源、行号、符号/章节、内容预览
        - 自适应模式额外显示策略信息
        - 内容预览超过 500 字符时截断
        """
        # 使用 Pydantic 验证参数
        p = SearchKnowledgeParams.model_validate(params)

        # 如果有 AdaptiveRetriever，使用自适应检索全流程
        if self._adaptive_retriever:
            result = await self._adaptive_retriever.retrieve(p.query, p.top_k)

            # direct 策略：不需要检索
            if result.strategy == "direct":
                return ToolResult(
                    content="Query classified as 'direct' - no retrieval needed. "
                    "This appears to be a simple question that doesn't require code search."
                )

            # 没有结果
            if not result.chunks:
                return ToolResult(content="No results found in knowledge base.")

            # 格式化结果（含策略信息）
            lines: list[str] = [
                f"[Strategy: {result.strategy} | Quality: {result.quality} "
                f"| Rewritten: {result.rewritten} | Reranked: {result.reranked}]"
            ]
            for i, (chunk, score) in enumerate(result.chunks, 1):
                header = f"--- Result {i} (score: {score:.4f}) ---"
                source = f"Source: {chunk.source_path}"
                location = f"Lines: {chunk.start_line}-{chunk.end_line}"
                if chunk.symbol:
                    symbol = f"Symbol: {chunk.symbol}"
                elif chunk.section_path:
                    symbol = f"Section: {' / '.join(chunk.section_path)}"
                else:
                    symbol = ""

                # Contextual Retrieval 上下文摘要
                context_line = ""
                if chunk.context:
                    context_line = f"Context: {chunk.context}"

                # Parent-Child 父级上下文
                parent_line = ""
                if "parent_context" in chunk.metadata:
                    parent_preview = chunk.metadata["parent_context"][:200]
                    parent_line = f"Parent: {parent_preview}..."

                content_preview = chunk.text.strip()
                if len(content_preview) > 500:
                    content_preview = content_preview[:500] + "..."

                lines.append(header)
                lines.append(source)
                lines.append(location)
                if symbol:
                    lines.append(symbol)
                if context_line:
                    lines.append(context_line)
                if parent_line:
                    lines.append(parent_line)
                lines.append("")
                lines.append(content_preview)
                lines.append("")

            return ToolResult(content="\n".join(lines))

        # 原始逻辑：根据 hybrid 参数选择检索方式
        if p.hybrid:
            # 使用混合检索
            results = await self._index_manager.hybrid_search(p.query, p.top_k, p.filters)
        else:
            # 使用纯语义检索
            results = await self._index_manager.search(p.query, p.top_k, p.filters)

        # 如果没有结果，返回提示信息
        if not results:
            return ToolResult(content="No results found in knowledge base.")

        # 格式化检索结果
        lines: list[str] = []
        for i, (chunk, score) in enumerate(results, 1):
            # 结果标题（包含分数）
            header = f"--- Result {i} (score: {score:.4f}) ---"
            # 来源文件路径
            source = f"Source: {chunk.source_path}"
            # 行号范围
            location = f"Lines: {chunk.start_line}-{chunk.end_line}"
            # 符号或章节信息
            if chunk.symbol:
                symbol = f"Symbol: {chunk.symbol}"
            elif chunk.section_path:
                symbol = f"Section: {' / '.join(chunk.section_path)}"
            else:
                symbol = ""

            # 内容预览（去除首尾空白）
            content_preview = chunk.text.strip()
            # 如果内容超过 500 字符，截断并添加省略号
            if len(content_preview) > 500:
                content_preview = content_preview[:500] + "..."

            # 添加到结果列表
            lines.append(header)
            lines.append(source)
            lines.append(location)
            if symbol:
                lines.append(symbol)
            lines.append("")
            lines.append(content_preview)
            lines.append("")

        # 返回工具执行结果
        return ToolResult(content="\n".join(lines))


class IndexKnowledgeParams(BaseModel):
    """
    索引知识库参数模型 - 定义索引工具的参数结构

    【字段说明】
    - paths: list[str] - 文件或目录路径列表（必填）

    【Pydantic 配置】
    - model_config = ConfigDict(extra="ignore"): 忽略额外参数

    【使用示例】
    ```python
    params = IndexKnowledgeParams(
        paths=["src/main.py", "docs/README.md"]
    )
    ```
    """
    # Pydantic 配置：忽略额外参数
    model_config = ConfigDict(extra="ignore")
    # 文件或目录路径列表（必填）
    paths: list[str]


class IndexKnowledgeTool(BaseTool):
    """
    索引知识库工具 - 将文件或目录索引到知识库

    【学习要点】
    1. 路径处理：支持文件和目录
    2. 增量索引：自动判断是否需要重新索引
    3. 索引保存：索引完成后自动保存到磁盘

    【工具参数】
    - paths: list[str] - 文件或目录路径列表（必填）

    【返回格式】
    ```
    Indexed 2 path(s). Added: 10 chunks, Updated: 0 chunks, Deleted: 0 chunks.
    ```

    【设计目的】
    提供索引管理能力，让用户可以手动触发索引操作。

    【使用示例】
    ```python
    tool = IndexKnowledgeTool(index_manager)
    result = await tool.invoke({
        "paths": ["src/main.py", "docs/README.md"]
    })
    ```
    """
    # 参数模型（用于参数验证）
    params_model = IndexKnowledgeParams
    # 工具名称（必须唯一）
    name = "index_knowledge"
    # 工具描述（用于 Agent 理解工具用途）
    description = (
        "Index files or directories into the knowledge base. "
        "Use this to refresh the index after files have been modified, "
        "or to add new files to the knowledge base."
    )
    # 输入 schema（用于工具调用时的参数提示）
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file or directory paths to index.",
            },
        },
        "required": ["paths"],
    }

    def __init__(self, index_manager: Any) -> None:
        """
        初始化索引知识库工具

        【参数说明】
        - index_manager: Any - 知识索引管理器
        """
        # 调用父类初始化
        super().__init__()
        # 保存知识索引管理器引用
        self._index_manager = index_manager

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        调用工具

        【参数说明】
        - params: dict[str, object] - 工具参数

        【返回值】
        - ToolResult: 工具执行结果

        【执行流程】
        1. 使用 Pydantic 验证参数
        2. 遍历路径列表
        3. 如果是目录，调用 index_directory
        4. 如果是文件，调用 index_file
        5. 保存索引到磁盘
        6. 返回 ToolResult

        【路径处理】
        - 目录：递归索引目录中的所有文件
        - 文件：索引单个文件

        【注意事项】
        - result 变量只保存最后一个路径的索引结果
        """
        # 使用 Pydantic 验证参数
        p = IndexKnowledgeParams.model_validate(params)

        # 遍历路径列表
        for path_str in p.paths:
            # 转换为 Path 对象
            path = Path(path_str)
            if path.is_dir():
                # 如果是目录，调用 index_directory
                result = await self._index_manager.index_directory(str(path))
            else:
                # 如果是文件，调用 index_file
                await self._index_manager.index_file(path)
                # 创建索引结果（假设添加了 1 个 Chunk）
                result = IndexResult(added_chunks=1)

        # 保存索引到磁盘
        self._index_manager.save()

        # 返回工具执行结果
        return ToolResult(
            content=f"Indexed {len(p.paths)} path(s). "
            f"Added: {result.added_chunks} chunks, "
            f"Updated: {result.updated_chunks} chunks, "
            f"Deleted: {result.deleted_chunks} chunks."
        )


class ForgetKnowledgeParams(BaseModel):
    """
    移除知识库参数模型 - 定义移除工具的参数结构

    【字段说明】
    - paths: list[str] - 文件或目录路径列表（必填）

    【Pydantic 配置】
    - model_config = ConfigDict(extra="ignore"): 忽略额外参数

    【使用示例】
    ```python
    params = ForgetKnowledgeParams(
        paths=["src/main.py", "docs/README.md"]
    )
    ```
    """
    # Pydantic 配置：忽略额外参数
    model_config = ConfigDict(extra="ignore")
    # 文件或目录路径列表（必填）
    paths: list[str]


class ForgetKnowledgeTool(BaseTool):
    """
    移除知识库工具 - 从知识库索引中移除文件或目录

    【学习要点】
    1. 路径处理：支持文件和目录
    2. 索引清理：移除指定路径的所有 Chunk
    3. 索引保存：清理完成后自动保存到磁盘

    【工具参数】
    - paths: list[str] - 文件或目录路径列表（必填）

    【返回格式】
    ```
    Removed 2 path(s) from knowledge base.
    ```

    【设计目的】
    提供索引清理能力，用于移除过时或错误的信息。

    【使用示例】
    ```python
    tool = ForgetKnowledgeTool(index_manager)
    result = await tool.invoke({
        "paths": ["src/main.py", "docs/README.md"]
    })
    ```

    【注意事项】
    - 此操作会删除指定路径的所有索引
    - 需要谨慎使用，避免误删重要数据
    """
    # 参数模型（用于参数验证）
    params_model = ForgetKnowledgeParams
    # 工具名称（必须唯一）
    name = "forget_knowledge"
    # 工具描述（用于 Agent 理解工具用途）
    description = (
        "Remove files or directories from the knowledge base index. "
        "Use this to remove outdated or incorrect information from the index."
    )
    # 输入 schema（用于工具调用时的参数提示）
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file or directory paths to remove from the index.",
            },
        },
        "required": ["paths"],
    }

    def __init__(self, index_manager: Any) -> None:
        """
        初始化移除知识库工具

        【参数说明】
        - index_manager: Any - 知识索引管理器
        """
        # 调用父类初始化
        super().__init__()
        # 保存知识索引管理器引用
        self._index_manager = index_manager

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        调用工具

        【参数说明】
        - params: dict[str, object] - 工具参数

        【返回值】
        - ToolResult: 工具执行结果

        【执行流程】
        1. 使用 Pydantic 验证参数
        2. 遍历路径列表
        3. 调用 remove_file 移除指定路径的索引
        4. 保存索引到磁盘
        5. 返回 ToolResult

        【注意事项】
        - 此操作会删除指定路径的所有索引
        - 需要谨慎使用，避免误删重要数据
        """
        # 使用 Pydantic 验证参数
        p = ForgetKnowledgeParams.model_validate(params)

        # 遍历路径列表
        for path_str in p.paths:
            # 转换为 Path 对象
            path = Path(path_str)
            # 移除指定路径的索引
            await self._index_manager.remove_file(path)

        # 保存索引到磁盘
        self._index_manager.save()

        # 返回工具执行结果
        return ToolResult(content=f"Removed {len(p.paths)} path(s) from knowledge base.")
