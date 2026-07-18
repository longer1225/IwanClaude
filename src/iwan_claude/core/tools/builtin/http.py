from __future__ import annotations

import asyncio
from typing import Any

import httpx

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_BODY_SIZE = 10 * 1024 * 1024
_MAX_REDIRECTS = 5
_DEFAULT_TIMEOUT = 30

_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"}
_BLOCKED_PROTOCOLS = {"file", "ftp", "sftp", "smb"}
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "localhost.localdomain"}


class HttpRequestParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(description="URL to send the request to")
    method: str = Field(default="GET", description="HTTP method")
    headers: dict[str, str] | None = Field(default=None, description="HTTP headers")
    body: str | None = Field(default=None, description="Request body (for POST/PUT)")
    timeout: int = Field(default=_DEFAULT_TIMEOUT, ge=1, le=120, description="Request timeout in seconds")


class HttpRequestTool(BaseTool):
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
                "description": f"Request timeout in seconds (default: {_DEFAULT_TIMEOUT}, max: 120)",
            },
        },
        "required": ["url"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = HttpRequestParams.model_validate(params)

        method = p.method.upper()
        if method not in _ALLOWED_METHODS:
            return ToolResult(
                content=f"Invalid method: {method!r}. Allowed methods: {', '.join(sorted(_ALLOWED_METHODS))}",
                is_error=True,
                error_type="schema_error",
            )

        try:
            parsed_url = httpx.URL(p.url)
        except Exception as exc:
            return ToolResult(content=f"Invalid URL: {exc}", is_error=True, error_type="schema_error")

        if parsed_url.scheme.lower() in _BLOCKED_PROTOCOLS:
            return ToolResult(
                content=f"Protocol {parsed_url.scheme!r} is not allowed",
                is_error=True,
                error_type="permission_denied",
            )

        host = parsed_url.host.lower() if parsed_url.host else ""
        if host in _BLOCKED_HOSTS:
            return ToolResult(
                content=f"Access to localhost/internal services is blocked for security",
                is_error=True,
                error_type="permission_denied",
            )

        if host.startswith("10.") or host.startswith("172.") or host.startswith("192.168."):
            return ToolResult(
                content=f"Access to private IP addresses is blocked for security",
                is_error=True,
                error_type="permission_denied",
            )

        headers: dict[str, str] = p.headers or {}
        headers.setdefault("User-Agent", "IwanClaude/1.0")

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(p.timeout, connect=10),
                follow_redirects=True,
                limits=httpx.Limits(max_connections=10),
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

        status_line = f"HTTP/{response.http_version} {response.status_code} {response.reason_phrase}"

        headers_str = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
        body = response.text

        content_length = len(body.encode("utf-8"))
        truncated = content_length > _MAX_BODY_SIZE
        if truncated:
            body = body[:_MAX_BODY_SIZE] + "\n[truncated]"

        result_parts = [status_line, headers_str, "", body]
        return ToolResult(content="\n".join(result_parts))