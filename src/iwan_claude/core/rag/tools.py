from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.rag.index import IndexResult
from iwan_claude.core.tools.base import BaseTool, ToolResult


class SearchKnowledgeParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    filters: dict[str, Any] | None = None
    hybrid: bool = Field(default=True, description="Use hybrid search (semantic + keyword)")


class SearchKnowledgeTool(BaseTool):
    params_model = SearchKnowledgeParams
    name = "search_knowledge"
    description = (
        "Search the local knowledge base (RAG) for relevant information. "
        "Uses hybrid search combining semantic (embedding-based) and keyword matching. "
        "Use this when you need information about existing code, documentation, "
        "or project files. Returns top_k relevant chunks with source path, line numbers, "
        "and content."
    )
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

    def __init__(self, index_manager: Any) -> None:
        super().__init__()
        self._index_manager = index_manager

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SearchKnowledgeParams.model_validate(params)

        if p.hybrid:
            results = await self._index_manager.hybrid_search(p.query, p.top_k, p.filters)
        else:
            results = await self._index_manager.search(p.query, p.top_k, p.filters)

        if not results:
            return ToolResult(content="No results found in knowledge base.")

        lines: list[str] = []
        for i, (chunk, score) in enumerate(results, 1):
            header = f"--- Result {i} (score: {score:.4f}) ---"
            source = f"Source: {chunk.source_path}"
            location = f"Lines: {chunk.start_line}-{chunk.end_line}"
            if chunk.symbol:
                symbol = f"Symbol: {chunk.symbol}"
            elif chunk.section_path:
                symbol = f"Section: {' / '.join(chunk.section_path)}"
            else:
                symbol = ""

            content_preview = chunk.text.strip()
            if len(content_preview) > 500:
                content_preview = content_preview[:500] + "..."

            lines.append(header)
            lines.append(source)
            lines.append(location)
            if symbol:
                lines.append(symbol)
            lines.append("")
            lines.append(content_preview)
            lines.append("")

        return ToolResult(content="\n".join(lines))


class IndexKnowledgeParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paths: list[str]


class IndexKnowledgeTool(BaseTool):
    params_model = IndexKnowledgeParams
    name = "index_knowledge"
    description = (
        "Index files or directories into the knowledge base. "
        "Use this to refresh the index after files have been modified, "
        "or to add new files to the knowledge base."
    )
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
        super().__init__()
        self._index_manager = index_manager

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = IndexKnowledgeParams.model_validate(params)

        for path_str in p.paths:
            path = Path(path_str)
            if path.is_dir():
                result = await self._index_manager.index_directory(str(path))
            else:
                await self._index_manager.index_file(path)
                result = IndexResult(added_chunks=1)

        self._index_manager.save()

        return ToolResult(
            content=f"Indexed {len(p.paths)} path(s). "
            f"Added: {result.added_chunks} chunks, "
            f"Updated: {result.updated_chunks} chunks, "
            f"Deleted: {result.deleted_chunks} chunks."
        )


class ForgetKnowledgeParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paths: list[str]


class ForgetKnowledgeTool(BaseTool):
    params_model = ForgetKnowledgeParams
    name = "forget_knowledge"
    description = (
        "Remove files or directories from the knowledge base index. "
        "Use this to remove outdated or incorrect information from the index."
    )
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
        super().__init__()
        self._index_manager = index_manager

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = ForgetKnowledgeParams.model_validate(params)

        for path_str in p.paths:
            path = Path(path_str)
            await self._index_manager.remove_file(path)

        self._index_manager.save()

        return ToolResult(content=f"Removed {len(p.paths)} path(s) from knowledge base.")
