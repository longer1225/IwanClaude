"""
工具错误模块 - 定义工具系统中使用的自定义异常

【学习要点】
1. 自定义异常：通过继承 Exception 类创建特定领域的异常类型
2. 异常语义化：使用有意义的异常名称和文档字符串，便于错误处理和调试
3. 异常分类：将不同类型的错误用不同的异常类表示，便于上层代码区分处理

【设计思路】
当工具调用上游服务时（如 HTTP 请求、API 调用），可能会遇到限流问题。
使用 RateLimitedError 作为专门的异常类型，让 invoke_tool 能够识别并进行重试。
"""
from __future__ import annotations


class RateLimitedError(Exception):
    """
    限流异常 - 当上游服务对请求进行限流时抛出

    【用途】
    当工具调用外部 API 或服务时，如果收到限流响应（通常是 HTTP 429），
    应抛出此异常。invoke_tool 会识别此异常并进行指数退避重试。

    【使用示例】
    ```python
    async def invoke(self, params: dict) -> ToolResult:
        response = await self._make_request(params)
        if response.status_code == 429:
            raise RateLimitedError("API rate limit exceeded")
        # ...
    ```

    【处理机制】
    invoke_tool 会捕获此异常，标记为 rate_limited 错误类型，
    如果重试次数未用完，会进行指数退避后重试。
    """
    pass
