"""HTTP 请求工具模块

这个模块实现了一个安全的 HTTP 请求工具，允许 Agent 向远程服务器发送 HTTP 请求。

**安全机制详解：**
1. **协议黑名单**：禁止 file://、ftp://、sftp://、smb:// 等协议，防止本地文件读取和内网渗透
2. **主机黑名单**：禁止访问 localhost、127.0.0.1 等本地地址，防止攻击本地服务
3. **私有IP阻止**：禁止访问 10.x、172.x、192.168.x 等私有IP段，防止内网攻击
4. **响应体限制**：响应体最大 10MB，防止内存溢出
5. **重定向限制**：最多跟随 5 次重定向，防止重定向攻击

**技术要点：**
- 使用 httpx 异步客户端进行 HTTP 请求
- 通过 pydantic 模型验证请求参数
- 统一的错误处理和超时控制
- 自动添加 User-Agent 标识

**使用示例：**
```python
# GET 请求
result = await http_request_tool.invoke({
    "url": "https://api.example.com/data",
    "method": "GET"
})

# POST 请求
result = await http_request_tool.invoke({
    "url": "https://api.example.com/submit",
    "method": "POST",
    "headers": {"Content-Type": "application/json"},
    "body": '{"key": "value"}'
})
```
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# ===== 兜底常量（配置加载失败时使用，通常不会触发） =====
_FALLBACK_MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB，响应体上限
_FALLBACK_MAX_REDIRECTS = 5                  # 最大重定向次数
_FALLBACK_DEFAULT_TIMEOUT = 30               # 默认超时秒数


def _max_body_size() -> int:
    """从全局配置读取 http_request 响应体最大字节数"""
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.http_max_body_size)
    except Exception:
        return _FALLBACK_MAX_BODY_SIZE


def _max_redirects() -> int:
    """从全局配置读取 http_request 最大重定向次数"""
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.http_max_redirects)
    except Exception:
        return _FALLBACK_MAX_REDIRECTS


def _default_timeout_s() -> int:
    """从全局配置读取 http_request 默认超时秒数"""
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.http_timeout_s)
    except Exception:
        return _FALLBACK_DEFAULT_TIMEOUT


# 允许的 HTTP 方法集合，限制只能使用安全的 HTTP 方法
_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"}
# 禁止的协议集合，防止本地文件读取和内网渗透
_BLOCKED_PROTOCOLS = {"file", "ftp", "sftp", "smb"}
# 禁止的主机名集合，防止访问本地服务
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "localhost.localdomain"}


class HttpRequestParams(BaseModel):
    """HTTP 请求参数模型

    使用 pydantic 验证请求参数，确保数据类型和约束符合要求。
    配置 extra="ignore" 表示忽略未定义的字段，增强兼容性。
    """
    model_config = ConfigDict(extra="ignore")

    # 请求目标 URL，必填字段
    url: str = Field(description="URL to send the request to")
    # HTTP 方法，默认为 GET
    method: str = Field(default="GET", description="HTTP method")
    # HTTP 头信息，可选字典类型
    headers: dict[str, str] | None = Field(default=None, description="HTTP headers")
    # 请求体，适用于 POST/PUT 等方法
    body: str | None = Field(default=None, description="Request body (for POST/PUT)")

    # 超时时间：默认值从全局配置 tools.http_timeout_s 读取（Pydantic default_factory 动态求值）
    timeout: int = Field(default_factory=_default_timeout_s, ge=1, le=120,
                         description="Request timeout in seconds")


class HttpRequestTool(BaseTool):
    """HTTP 请求工具类

    提供安全的 HTTP 请求能力，支持多种 HTTP 方法，内置安全防护机制。
    
    **安全检查流程：**
    1. 验证 HTTP 方法是否在允许列表中
    2. 解析 URL 并验证格式
    3. 检查协议是否被禁止
    4. 检查主机是否为本地或私有IP
    5. 执行请求并处理响应
    """
    params_model = HttpRequestParams
    name = "http_request"
    description = (
        "Send an HTTP request to a remote server. "
        "Supports GET, POST, PUT, DELETE, HEAD, OPTIONS methods. "
        "Security restrictions: localhost/internal IPs are blocked. "
        "Response body is truncated at 10 MB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to send the request to",
            },
            "method": {
                "type": "string",
                "description": f"HTTP method (one of: {', '.join(sorted(_ALLOWED_METHODS))})",
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "Optional HTTP headers as key-value pairs",
            },
            "body": {
                "type": "string",
                "description": "Optional request body (for POST/PUT)",
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds (default from tools.http_timeout_s, max: 120)",
            },
        },
        "required": ["url"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行 HTTP 请求

        **参数验证流程：**
        1. 使用 pydantic 模型验证输入参数
        2. 将方法名转换为大写
        3. 检查方法是否在允许列表中
        
        **安全检查流程：**
        1. 使用 httpx.URL 解析 URL，验证格式有效性
        2. 检查协议是否在黑名单中
        3. 检查主机名是否为本地地址
        4. 检查主机是否为私有 IP 段
        
        **请求执行流程：**
        1. 设置默认 User-Agent 头
        2. 创建 httpx 异步客户端，配置超时和连接限制
        3. 发送请求并等待响应
        4. 处理超时、重定向过多等异常
        
        **响应处理流程：**
        1. 构建状态行（HTTP版本 + 状态码 + 原因短语）
        2. 将响应头转换为字符串
        3. 获取响应体文本
        4. 检查响应体大小，超过限制则截断
        
        Args:
            params: 请求参数字典，包含 url、method、headers、body、timeout 等字段
            
        Returns:
            ToolResult: 包含响应内容的结果对象，格式为：
                        HTTP/1.1 200 OK
                        Content-Type: application/json
                        ...
                        
                        {"data": "..."}
        """
        # 使用 pydantic 验证并转换参数
        p = HttpRequestParams.model_validate(params)

        # 将方法名转换为大写，确保一致性
        method = p.method.upper()
        # 检查方法是否在允许列表中，防止使用危险方法
        if method not in _ALLOWED_METHODS:
            return ToolResult(
                content=f"Invalid method: {method!r}. Allowed methods: {', '.join(sorted(_ALLOWED_METHODS))}",
                is_error=True,
                error_type="schema_error",
            )

        # 使用 httpx.URL 解析 URL，验证格式有效性
        try:
            parsed_url = httpx.URL(p.url)
        except Exception as exc:
            return ToolResult(content=f"Invalid URL: {exc}", is_error=True, error_type="schema_error")

        # 安全检查：禁止的协议
        if parsed_url.scheme.lower() in _BLOCKED_PROTOCOLS:
            return ToolResult(
                content=f"Protocol {parsed_url.scheme!r} is not allowed",
                is_error=True,
                error_type="permission_denied",
            )

        # 安全检查：禁止的主机名
        host = parsed_url.host.lower() if parsed_url.host else ""
        if host in _BLOCKED_HOSTS:
            return ToolResult(
                content=f"Access to localhost/internal services is blocked for security",
                is_error=True,
                error_type="permission_denied",
            )

        # 安全检查：私有 IP 地址段
        if host.startswith("10.") or host.startswith("172.") or host.startswith("192.168."):
            return ToolResult(
                content=f"Access to private IP addresses is blocked for security",
                is_error=True,
                error_type="permission_denied",
            )

        # 准备请求头，设置默认 User-Agent
        headers: dict[str, str] = p.headers or {}
        headers.setdefault("User-Agent", "IwanClaude/1.0")

        # 创建 httpx 异步客户端并发送请求
        # 重定向次数从全局配置读取（tools.http_max_redirects）
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(p.timeout, connect=10),        # 总超时 + 连接超时
                follow_redirects=True,                                # 自动跟随重定向
                max_redirects=_max_redirects(),                       # 最大重定向次数（配置化）
                limits=httpx.Limits(max_connections=10),              # 连接池限制
            ) as client:
                response = await client.request(
                    method=method,
                    url=p.url,
                    headers=headers,
                    content=p.body,
                )
        except httpx.TimeoutException:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except httpx.TooManyRedirects:
            return ToolResult(content="Too many redirects", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 构建状态行：HTTP版本 + 状态码 + 原因短语
        status_line = f"HTTP/{response.http_version} {response.status_code} {response.reason_phrase}"

        # 将响应头转换为字符串，格式为 "Key: Value"
        headers_str = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
        # 获取响应体文本
        body = response.text

        # 检查响应体大小，超过限制则截断（阈值从全局配置 tools.http_max_body_size 读取）
        max_b = _max_body_size()
        content_length = len(body.encode("utf-8"))
        truncated = content_length > max_b
        if truncated:
            body = body[:max_b] + "\n[truncated]"

        # 组合结果：状态行 + 响应头 + 空行 + 响应体
        result_parts = [status_line, headers_str, "", body]
        return ToolResult(content="\n".join(result_parts))
