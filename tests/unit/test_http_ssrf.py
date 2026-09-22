"""http_request 工具 SSRF 防护测试（任务 5）

覆盖：字面内网 IP 拦截、DNS 解析后内网 IP 拦截（含 sslip.io 类通配 DNS）、
协议白名单、解析失败 fail-closed、禁用自动重定向后逐跳重新校验。
全部用 httpx.MockTransport + 假解析器，不发任何真实网络请求。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

import iwan_claude.core.tools.builtin.http as http_module
from iwan_claude.core.tools.builtin.http import HttpRequestTool


@pytest.fixture(autouse=True)
def reset_sandbox_state() -> Any:
    # 功能：测试前后清空沙箱全局单例与 contextvar
    # 设计：权限层与本文件无关，但 init_sandbox 残留会影响其他文件的基线，保持对称清理
    import iwan_claude.core.sandbox as sb_module
    sb_module._sandbox_manager = None
    token = sb_module._active_sandbox.set(None)
    sb_module._sandbox_by_session.clear()
    yield
    sb_module._active_sandbox.reset(token)
    sb_module._sandbox_manager = None
    sb_module._sandbox_by_session.clear()


# 把 httpx.AsyncClient 换成注入 MockTransport 的子类，拦截所有真实建连
def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda request: (seen.append(request), handler(request))[1])
    real_cls = httpx.AsyncClient

    class _MockClient(real_cls):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    return seen


# 让域名解析返回固定 IP 列表（绕过真实 DNS）
def _install_fake_dns(monkeypatch: pytest.MonkeyPatch, ip_map: dict[str, list[str]]) -> None:
    async def _fake_resolve(host: str) -> list[str]:
        if host not in ip_map:
            raise OSError(f"no such host: {host}")
        return ip_map[host]

    monkeypatch.setattr(http_module, "_resolve_host_ips", _fake_resolve)


_OK = lambda request: httpx.Response(200, text="public ok")  # noqa: E731

# ── 字面 IP / 主机名黑名单 ───────────────────────────────────────────────────


# 功能：验证常见内网/回环/元数据/私有段字面 IP 全部被 permission_denied 拦截
# 设计：参数化覆盖任务要求的全部网段（127/8、10/8、192.168/16、172.16-31/12、
#       169.254/16、::1、fc00::/7）加上 0.0.0.0；断言在建立任何连接之前就被拒绝
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://127.1.2.3/admin",           # 整个 127/8 都封，不只是 .0.1
    "http://10.1.2.3/internal",
    "http://192.168.0.1/router",
    "http://172.16.5.5/private",
    "http://172.31.255.255/private",
    "http://169.254.169.254/latest/meta-data/",  # 云元数据服务
    "http://0.0.0.0/",
    "http://[::1]/",
    "http://[fd00::1234]/",              # fc00::/7 ULA
    "http://[fe80::1]/",                 # 链路本地 IPv6
    "http://localhost:8000/api",
    "http://LOCALHOST:8000/api",         # 大小写不敏感
])
async def test_literal_internal_addresses_blocked(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install_mock_transport(monkeypatch, _OK)
    result = await HttpRequestTool().invoke({"url": url})
    assert result.is_error is True
    assert result.error_type == "permission_denied"
    assert seen == []  # 从未发起任何 HTTP 请求


# 功能：验证 172.15.x.x / 172.32.x.x 不在 172.16/12 私有段内，正常放行
# 设计：CIDR 边界值测试——startswith("172.") 的旧实现会误伤或漏判，
#       基于 ip_network 的新实现必须精确到 16-31 段
async def test_public_lookalike_ips_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install_mock_transport(monkeypatch, _OK)
    for url in ("http://172.15.0.1/", "http://172.32.0.1/"):
        result = await HttpRequestTool().invoke({"url": url})
        assert result.is_error is not True, url
    assert len(seen) == 2


# 功能：验证非 http/https 协议（file/gopher 等）被拒绝
# 设计：协议白名单取代旧黑名单，任何新出现的危险协议默认被拒（fail-closed）
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/pub",
    "gopher://127.0.0.1:6379/_FLUSHALL",
])
async def test_non_http_scheme_blocked(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    result = await HttpRequestTool().invoke({"url": url})
    assert result.is_error is True
    assert result.error_type == "permission_denied"


# ── DNS 解析校验 ─────────────────────────────────────────────────────────────


# 功能：验证域名解析到内网 IP 时被拦截（含 127.0.0.1.sslip.io 类通配 DNS 绕过）
# 设计：旧实现只看 URL 字符串，"公网域名→回环 IP"完全放行；新实现解析所有 IP
#       逐一校验，任一命中禁止段即拒绝；混合记录（公网+内网）同样拒绝
@pytest.mark.parametrize("host,ips", [
    ("evil.example.com", ["127.0.0.1"]),
    ("127.0.0.1.sslip.io", ["127.0.0.1"]),          # 通配 DNS 把子域解析回本机
    ("metadata.rboss.io", ["169.254.169.254"]),
    ("multi.example.com", ["93.184.216.34", "10.0.0.1"]),  # 混合记录取最坏
])
async def test_domain_resolving_internal_blocked(
    host: str, ips: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_dns(monkeypatch, {host: ips})
    seen = _install_mock_transport(monkeypatch, _OK)
    result = await HttpRequestTool().invoke({"url": f"http://{host}/"})
    assert result.is_error is True
    assert result.error_type == "permission_denied"
    assert seen == []


# 功能：验证域名解析成功且全为公网 IP 时正常请求
# 设计：正向路径对照组，防止 DNS 校验把合法外网请求也拦死
async def test_domain_resolving_public_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    seen = _install_mock_transport(monkeypatch, _OK)
    result = await HttpRequestTool().invoke({"url": "http://example.com/data"})
    assert result.is_error is not True
    assert len(seen) == 1


# 功能：验证 DNS 解析失败时 fail-closed 拒绝请求，而不是放行赌运气
# 设计：解析异常可能来自被污染/半配置的解析器；SSRF 语境下"无法证明安全=不安全"
async def test_dns_failure_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dns(monkeypatch, {})  # 任何 host 都抛 OSError
    seen = _install_mock_transport(monkeypatch, _OK)
    result = await HttpRequestTool().invoke({"url": "http://nx.example/"})
    assert result.is_error is True
    assert result.error_type == "permission_denied"
    assert seen == []


# ── 重定向逐跳校验 ───────────────────────────────────────────────────────────


# 功能：验证公网 URL 通过 302 跳转到内网地址时被拦截（旧 follow_redirects=True 的洞）
# 设计：MockTransport 记录实际到达的请求——必须只有第一跳；
#       第二跳（169.254.169.254）在发出前就被逐跳 SSRF 校验拒绝
async def test_redirect_to_internal_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dns(monkeypatch, {"good.example.com": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    seen = _install_mock_transport(monkeypatch, handler)
    result = await HttpRequestTool().invoke({"url": "http://good.example.com/"})
    assert result.is_error is True
    assert result.error_type == "permission_denied"
    assert len(seen) == 1  # 内网目标从未被请求


# 功能：验证合法公网重定向链正常跟随并返回最终响应
# 设计：两跳都过 SSRF 校验（域名+字面公网 IP），最终 200 的 body 出现在结果里；
#       证明"禁用自动重定向"后手动循环没有把合法跳转也断掉
async def test_public_redirect_chain_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dns(monkeypatch, {"start.example.com": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "start.example.com":
            return httpx.Response(301, headers={"location": "http://203.0.113.7/final"})
        return httpx.Response(200, text="arrived")

    seen = _install_mock_transport(monkeypatch, handler)
    result = await HttpRequestTool().invoke({"url": "http://start.example.com/"})
    assert result.is_error is not True
    assert "arrived" in result.content
    assert len(seen) == 2


# 功能：验证 301/302 对 POST 请求按 HTTP 惯例降级为 GET 且不再携带 body
# 设计：重定向语义错误会导致目标端收到带 body 的 POST 而行为异常；
#       MockTransport 里断言第二个请求 method/content
async def test_post_redirect_switches_to_get(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dns(monkeypatch, {"api.example.com": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(302, headers={"location": "http://api.example.com/result"})
        return httpx.Response(200, text="got it")

    seen = _install_mock_transport(monkeypatch, handler)
    result = await HttpRequestTool().invoke({
        "url": "http://api.example.com/submit", "method": "POST", "body": "payload",
    })
    assert result.is_error is not True
    assert seen[0].method == "POST"
    assert seen[1].method == "GET"
    assert seen[1].content == b""


# 功能：验证重定向次数超过配置上限时以错误终止（防重定向环路 DoS）
# 设计：MockTransport 让每一跳都跳回同一无害公网 IP，循环到 tools.http_max_redirects
#       上限；断言 runtime_error 且请求数被封顶
async def test_too_many_redirects_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dns(monkeypatch, {"loop.example.com": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://loop.example.com/again"})

    seen = _install_mock_transport(monkeypatch, handler)
    result = await HttpRequestTool().invoke({"url": "http://loop.example.com/"})
    assert result.is_error is True
    assert result.error_type == "runtime_error"
    assert "redirect" in result.content.lower()
    assert len(seen) <= http_module._max_redirects() + 1
