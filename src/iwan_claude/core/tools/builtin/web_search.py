"""
WebSearch 工具模块 - 网络搜索（占位实现）

【学习要点】
1. 占位模式：工具已注册但未接 API，调用时返回友好提示
2. 不影响现有功能：只是新增一个工具，不修改其他代码
3. 后续扩展：配置 API key 后即可启用真实搜索

【设计原则】
- 零侵入：不修改现有 provider/tool 代码
- 友好降级：未配置 API 时返回明确错误，不崩溃
- 预留接口：参数和返回格式已定义，后续只需实现 _search 方法
"""
from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult


class WebSearchParams(BaseModel):
    """
    WebSearch 工具参数

    【字段说明】
    - query: str - 搜索关键词
    - num_results: int - 返回结果数量（默认 5，最大 10）
    """
    model_config = ConfigDict(extra="ignore")
    query: str
    num_results: int = Field(default=5, ge=1, le=10)


class WebSearchTool(BaseTool):
    """
    WebSearch 工具 - 网络搜索（占位实现）

    【学习要点】
    1. 占位模式：工具已注册但未接真实搜索 API
    2. 友好降级：未配置 API key 时返回明确错误信息
    3. 预留接口：后续配置 WEB_SEARCH_API_KEY 环境变量即可启用

    【当前状态】
    工具已注册并可被 LLM 调用，但实际搜索逻辑未实现。
    调用时会检查环境变量 WEB_SEARCH_API_KEY：
    - 未配置：返回友好错误，提示用户配置
    - 已配置：调用真实搜索 API（待实现）

    【后续扩展步骤】
    1. 在 _search_impl 中实现真实搜索逻辑
    2. 支持 Google Custom Search API / Bing Search API / Brave Search API
    3. 返回结构化结果（标题、URL、摘要）
    """
    params_model = WebSearchParams
    name = "web_search"
    description = (
        "Search the web for current information. Returns search results with title, URL, "
        "and snippet. Use this when the user asks about recent events, latest documentation, "
        "or information beyond your training data. Note: requires WEB_SEARCH_API_KEY "
        "environment variable to be configured."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "num_results": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
                "description": "Number of results to return (1-10).",
            },
        },
        "required": ["query"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行网络搜索（占位实现）"""
        p = WebSearchParams.model_validate(params)

        if not p.query or not p.query.strip():
            return ToolResult(
                content="web_search: query is required",
                is_error=True,
                error_type="invalid_params",
            )

        # 检查是否配置了搜索 API key
        api_key = os.environ.get("WEB_SEARCH_API_KEY", "").strip()
        if not api_key:
            # 友好降级：返回明确错误，不崩溃
            return ToolResult(
                content=(
                    f"web_search: not configured. To enable web search, set the "
                    f"WEB_SEARCH_API_KEY environment variable (supports Google Custom "
                    f"Search, Bing Search, or Brave Search API). Query was: '{p.query}'"
                ),
                is_error=True,
                error_type="not_configured",
            )

        # TODO: 实现 _search_impl 调用真实搜索 API
        # 1. 根据 API 类型选择 provider（Google/Bing/Brave）
        # 2. 调用搜索 API
        # 3. 解析并返回结构化结果
        try:
            results = await self._search_impl(p.query, p.num_results, api_key)
            if not results:
                return ToolResult(content=f"web_search: no results for '{p.query}'")

            lines = [f"web_search: {len(results)} results for '{p.query}'"]
            for i, r in enumerate(results, start=1):
                lines.append(f"\n[{i}] {r['title']}")
                lines.append(f"    URL: {r['url']}")
                lines.append(f"    {r['snippet']}")
            return ToolResult(content="\n".join(lines))
        except Exception as exc:
            return ToolResult(
                content=f"web_search: API call failed: {exc}",
                is_error=True,
                error_type="runtime_error",
            )

    async def _search_impl(self, query: str, num_results: int, api_key: str) -> list[dict[str, str]]:
        """
        实际搜索实现（待实现）

        【参数说明】
        - query: str - 搜索关键词
        - num_results: int - 返回结果数量
        - api_key: str - API 密钥

        【返回值】
        - list[dict]: 搜索结果列表，每个 dict 包含 title、url、snippet

        【后续实现】
        1. 检测 api_key 格式判断 provider（Google/Bing/Brave）
        2. 调用对应 API
        3. 解析响应为统一格式
        """
        # 占位：未实现真实搜索逻辑
        # 配置 API key 后会走到这里，返回空结果
        raise NotImplementedError(
            "web_search: API integration not yet implemented. "
            "Configure WEB_SEARCH_API_KEY and implement _search_impl."
        )
