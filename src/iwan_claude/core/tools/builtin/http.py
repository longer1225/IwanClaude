"""HTTP 请求工具模块

这个模块实现了一个安全的 HTTP 请求工具，允许 Agent 向远程服务器发送 HTTP 请求。

**安全机制详解：**
1. **协议白名单**：只允许 http/https，防止 file://、ftp:// 等本地文件读取和内网渗透
2. **SSRF 防护**：主机名字面量黑名单 + 字面 IP 网段校验 + 域名先经 DNS 解析为 IP
   再校验，封禁 127/8、10/8、172.16/12、192.168/16、169.254/16（云元数据）、
   ::1、fc00::/7 等内网/环回/链路本地段，拦截"域名解析到内网"的绕过
3. **重定向逐跳校验**：禁用 httpx 自动重定向，手动跟随每一跳并重新执行 SSRF 校验，
   防止公网 URL 通过 302 跳转到内网服务；最多跟随次数仍受配置限制
4. **响应体限制**：响应体最大 10MB，防止内存溢出

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
import ipaddress
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
# 禁止的主机名字段（字面量匹配，真正的防线是下面的 IP 段校验 + DNS 解析校验）
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "localhost.localdomain"}

# ===== SSRF 防护：禁止访问的 IP 段 =====
# 覆盖：环回（127/8、::1）、私有网段（10/8、172.16/12、192.168/16、fc00::/7）、
# 链路本地（169.254/16 —— 云厂商元数据服务 169.254.169.254 在此段）、
# 未指定（0/8）、fe80::/10
_BLOCKED_IP_NETWORKS: list[Any] = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),   # CGNAT（运营商级 NAT，常被内网服务监听）
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


# 判断单个 IP 是否落在禁止访问的网段内
def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # 去掉 IPv6 的 zone id（如 %eth0），否则 ip_network 比较会出错
    if ip.version == 6:
        ip = ipaddress.ip_address(ip.compressed)
    return any(ip in net for net in _BLOCKED_IP_NETWORKS)


# 解析域名为 IP 字符串列表（独立函数便于单元测试注入假解析结果）
async def _resolve_host_ips(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return [info[4][0] for info in infos]


# 对目标 URL 做完整 SSRF 校验：协议 → 主机名字面量 → 字面 IP 段 → DNS 解析后的所有 IP
# 返回 None 表示通过；返回 ToolResult（is_error）表示被拒绝
async def _ssrf_check(target: httpx.URL) -> ToolResult | None:
    # 协议白名单（等效于 file/ftp/smb 等全部禁止）
    scheme = target.scheme.lower()
    if scheme not in ("http", "https"):
        return ToolResult(
            content=f"Protocol {scheme!r} is not allowed (only http/https)",
            is_error=True, error_type="permission_denied",
        )
    host = (target.host or "").lower().strip("[]")
    if not host:
        return ToolResult(content="URL has no host", is_error=True, error_type="schema_error")
    # 主机名字面量黑名单（防止 localhost 等拼写绕过——注意 127.0.0.1.sslip.io 这类
    # "域名解析回环"变体只能靠下面的 DNS 校验拦截）
    if host in _BLOCKED_HOSTS:
        return ToolResult(
            content="Access to localhost/internal services is blocked for security",
            is_error=True, error_type="permission_denied",
        )
    # 主机名是字面 IP：直接做网段校验
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if _ip_is_blocked(literal_ip):
            return ToolResult(
                content=f"Access to private/internal IP address {host} is blocked for security",
                is_error=True, error_type="permission_denied",
            )
        return None
    # 域名：先解析为 IP 再校验，拦截"域名指向内网"（含 127.0.0.1.sslip.io 这类通配 DNS）
    # 说明：这里与 httpx 实际建连时存在极小的 DNS 重绑定窗口（两次解析可能不同），
    # 彻底根治需要把连接锁定到已校验 IP（自定义 transport），留待后续阶段。
    try:
        ips = await _resolve_host_ips(host)
    except Exception:
        return ToolResult(
            content=f"DNS resolution failed for host {host!r} (request denied fail-closed)",
            is_error=True, error_type="permission_denied",
        )
    for ip_str in ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue  # 非标准地址族，跳过该条
        if _ip_is_blocked(ip):
            return ToolResult(
                content=(
                    f"Host {host!r} resolves to blocked internal address {ip} "
                    "(loopback/private/link-local ranges are not allowed)"
                ),
                is_error=True, error_type="permission_denied",
            )
    return None


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
    3. SSRF 校验：协议白名单 + 主机名/字面 IP/DNS 解析后 IP 的网段检查
    4. 手动跟随重定向，每一跳重新执行 SSRF 校验
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
                "description": "Request timeout in seconds "
                               "(default from tools.http_timeout_s, max: 120)",
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
        2. 每个请求目标（含重定向跳转后的）都执行 _ssrf_check：
           协议白名单 → 主机名字面量黑名单 → 字面 IP 网段 → DNS 解析后所有 IP 网段

        **请求执行流程：**
        1. 设置默认 User-Agent 头
        2. 创建 httpx 异步客户端（follow_redirects=False），手动循环跟随重定向并逐跳校验
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
                content=(
                    f"Invalid method: {method!r}. "
                    f"Allowed methods: {', '.join(sorted(_ALLOWED_METHODS))}"
                ),
                is_error=True,
                error_type="schema_error",
            )

        # 使用 httpx.URL 解析 URL，验证格式有效性
        try:
            parsed_url = httpx.URL(p.url)
        except Exception as exc:
            return ToolResult(
                content=f"Invalid URL: {exc}", is_error=True, error_type="schema_error"
            )

        # 准备请求头，设置默认 User-Agent
        headers: dict[str, str] = dict(p.headers or {})
        headers.setdefault("User-Agent", "IwanClaude/1.0")

        # ===== 请求执行：手动跟随重定向，逐跳重新做 SSRF 校验 =====
        # 不再使用 follow_redirects=True：自动重定向会跳过安全检查，
        # 允许"公网 URL 302 → http://169.254.169.254/..."打到云元数据服务。
        # 每一跳都先过 _ssrf_check（协议/主机/DNS 解析 IP），再发请求。
        max_redirects = _max_redirects()
        url = parsed_url
        cur_method = method
        cur_body: str | None = p.body
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(p.timeout, connect=10),        # 总超时 + 连接超时
                # 关闭自动重定向，由下面循环手动处理（逐跳 SSRF 校验）
                follow_redirects=False,
                limits=httpx.Limits(max_connections=10),             # 连接池限制
            ) as client:
                response: httpx.Response | None = None
                redirects = 0
                while True:
                    # 逐跳安全校验：初始 URL 与每一次重定向目标都走同一套 SSRF 检查
                    check = await _ssrf_check(url)
                    if check is not None:
                        return check
                    response = await client.request(
                        method=cur_method,
                        url=url,
                        headers=headers,
                        content=cur_body,
                    )
                    if response.status_code not in (301, 302, 303, 307, 308):
                        break  # 非重定向响应，作为最终结果
                    location = response.headers.get("location")
                    redirected_from = response.status_code
                    await response.aclose()
                    if not location:
                        break  # 3xx 但无 Location 头：按最终响应返回（与浏览器行为一致）
                    redirects += 1
                    if redirects > max_redirects:
                        return ToolResult(
                            content="Too many redirects", is_error=True, error_type="runtime_error",
                        )
                    # 相对 Location 基于当前 URL 解析（urljoin 语义由 httpx.URL.join 提供）
                    try:
                        url = url.join(location)
                    except Exception as exc:
                        return ToolResult(
                            content=f"Invalid redirect location: {exc}",
                            is_error=True, error_type="runtime_error",
                        )
                    # 303 强制转 GET；301/302 对 POST 也转 GET（HTTP 惯例），307/308 保持原方法
                    if redirected_from == 303 or (
                        redirected_from in (301, 302) and cur_method == "POST"
                    ):
                        cur_method = "GET"
                        cur_body = None
                        headers.pop("content-length", None)  # 换方法后清理实体头，避免服务端误判
        except httpx.TimeoutException:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 循环出口必有最终响应（首轮就赋值），assert 仅为 mypy 窄化 Optional
        assert response is not None
        # 构建状态行：HTTP版本 + 状态码 + 原因短语
        status_line = (
            f"HTTP/{response.http_version} "
            f"{response.status_code} {response.reason_phrase}"
        )

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
