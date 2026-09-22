"""MCP 客户端协议级测试：用真实 stdio 子进程跑一个假 MCP server

覆盖 review 修复的协议语义（此前 client.py 700+ 行零协议测试）：
握手记录 / isError 上报 / tools/list 分页 / 非文本块占位 /
server→client ping 应答 / 读超时配置化 / 断线熔断 / close 幂等收尸。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.mcp.client import (
    McpClient,
    McpServerUnavailableError,
    McpToolError,
)

# 假 MCP server：NDJSON 读写 stdin/stdout，行为由 argv[1] 场景切换
_FAKE_SERVER = r"""
import json, sys, time

scenario = sys.argv[1]

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    m = msg.get("method")
    rid = msg.get("id")
    if m == "initialize":
        if scenario == "pingreq":
            # server->client ping request: a compliant client must answer
            # before we release the initialize response (deadlock test)
            send({"jsonrpc": "2.0", "id": "srv1", "method": "ping"})
            sys.stdin.readline()
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }})
    elif m == "notifications/initialized":
        pass
    elif m == "tools/list":
        cursor = (msg.get("params") or {}).get("cursor")
        if scenario == "paged":
            if cursor is None:
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "tools": [{"name": "a", "description": "da", "inputSchema": {}}],
                    "nextCursor": "c2",
                }})
            else:
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "tools": [{"name": "b", "description": "db", "inputSchema": {}}],
                }})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "tools": [{"name": "echo", "description": "echo tool",
                           "inputSchema": {"type": "object", "properties": {}}}],
            }})
    elif m == "tools/call":
        if scenario == "iserror":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "boom: bad sql"}],
                "isError": True,
            }})
        elif scenario == "blocks":
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [
                {"type": "text", "text": "head"},
                {"type": "image", "data": "zzz", "mimeType": "image/png"},
                {"type": "resource", "resource": {"uri": "file:///x", "text": "emb"}},
            ]}})
        elif scenario == "slowtool":
            time.sleep(2.0)
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "late"}],
            }})
        elif scenario == "killme":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "last"}],
            }})
            sys.stdout.flush()
            time.sleep(0.2)
            sys.exit(0)
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "echo-ok"}],
            }})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid,
              "error": {"code": -32601, "message": "no such method"}})
"""


class _McpTestCtx:
    """以场景启动假 server、握手完成后交出 client；async with 退出时负责 close"""
    def __init__(self, scenario: str, timeout_sec: float = 5.0) -> None:
        self.scenario = scenario
        self.timeout_sec = timeout_sec
        self.client: McpClient | None = None

    async def __aenter__(self) -> McpClient:
        client = McpClient(read_timeout_sec=self.timeout_sec)
        await client.connect_stdio(sys.executable, ["-c", _FAKE_SERVER, self.scenario])
        self.client = client
        return client

    async def __aexit__(self, *exc: Any) -> None:
        if self.client is not None:
            await self.client.close()


# 功能：握手成功后 client 记录 server 自报的 protocolVersion/serverInfo，list_tools+call_tool 全链路可用
# 设计：真实子进程假 server 走通 NDJSON 编解码全链路；握手应答此前被整个丢弃，
#      版本/serverInfo 现在必须可见——这是"非标 server 告警"能否生效的回归锚点
async def test_handshake_and_basic_flow() -> None:
    async with _McpTestCtx("basic") as client:
        assert client._server_protocol_version == "2024-11-05"
        assert client._server_info.get("name") == "fake"
        tools = await client.list_tools()
        assert [t.name for t in tools] == ["echo"]
        assert await client.call_tool("echo", {}) == "echo-ok"


# 功能：tools/list 返回 nextCursor 时客户端必须续页取全所有工具
# 设计：假 server 分两页（a→b）；旧实现只看第一页会只拿到 1 个工具，断言 [a,b] 即锁死分页修复
async def test_list_tools_pagination() -> None:
    async with _McpTestCtx("paged") as client:
        tools = await client.list_tools()
        assert [t.name for t in tools] == ["a", "b"]


# 功能：result.isError=true 时 call_tool 必须抛 McpToolError 且错误文本进入异常消息
# 设计：isError 是 MCP 的工具失败通道（非 JSON-RPC error），旧实现当成功返回——
#      raises+match 双断言保证模型看到的是错误，而非错误文本伪装的"成功结果"
async def test_iserror_raises_tool_error() -> None:
    async with _McpTestCtx("iserror") as client:
        with pytest.raises(McpToolError, match="bad sql"):
            await client.call_tool("echo", {})


# 功能：content 含 image/resource 块时 text 正常提取、未知块给占位说明、resource 内嵌 text 保留
# 设计：旧实现静默丢块（纯图结果="成功但空串"诱发幻觉）；占位文本让模型明确"有内容被省略"
async def test_content_blocks_placeholder() -> None:
    async with _McpTestCtx("blocks") as client:
        out = await client.call_tool("echo", {})
        assert "head" in out
        assert "[image content not supported]" in out
        assert "emb" in out


# 功能：握手期间 server 发 ping 请求，客户端必须应答使 initialize 不被卡死
# 设计：假 server 发完 ping 就等回包、之后才发 initialize 应答——旧实现静默丢 server
#      请求会造成双向死锁直到读超时；本测试能快速握手成功即证明 _answer_server_request 生效
async def test_server_ping_answered() -> None:
    async with _McpTestCtx("pingreq", timeout_sec=8.0) as client:
        assert await client.call_tool("echo", {}) == "echo-ok"


# 功能：读超时使用构造注入值而非写死 30s，且超时不置熔断标记（结果未知≠连接已死）
# 设计：慢工具 sleep 2s、客户端 timeout 0.5s——写死 30s 的话本测试要傻等半分钟；
#      快速失败即证明超时生效，断言 offline False 锁住"超时≠断线"的熔断语义边界
async def test_configurable_timeout_not_offline() -> None:
    async with _McpTestCtx("slowtool", timeout_sec=0.5) as client:
        with pytest.raises(McpServerUnavailableError) as ei:
            await client.call_tool("echo", {})
        assert "timeout" in str(ei.value).lower()
        assert "result unknown" in str(ei.value)  # 文案必须提示副作用可能已发生
        assert client.offline is False


# 功能：server 进程退出后再调用，首调用失败并置熔断，第二调用秒级快速失败且带 offline 文案
# 设计：崩溃后每次傻等 30s 超时是旧版最差体验；耗时 <1s 断言直接量化"熔断短路"的价值，
#      首调用接受 EOF 或写失败两种形态（kill 与写管道存在合法竞态）
async def test_offline_circuit_breaker() -> None:
    async with _McpTestCtx("killme", timeout_sec=5.0) as client:
        assert await client.call_tool("echo", {}) == "last"
        await asyncio.sleep(0.5)  # 等假 server 的 sys.exit 落地
        with pytest.raises(McpServerUnavailableError):
            await client.call_tool("echo", {})
        assert client.offline is True
        t0 = time.perf_counter()
        with pytest.raises(McpServerUnavailableError, match="offline"):
            await client.call_tool("echo", {})
        assert time.perf_counter() - t0 < 1.0


# 功能：connect_stdio 握手失败时子进程被就地回收（不留孤儿进程）
# 设计：用一个立即 exit(3) 的坏脚本让 initialize 撞 EOF 失败；失败路径会在 connect
#      内部调 close()，断言 returncode 非 None 即证明收尸完成（P0-2 泄漏的客户端侧兜底）
async def test_initialize_failure_reaps_process(tmp_path: Path) -> None:
    script = tmp_path / "badserver.py"
    script.write_text("import sys\nsys.exit(3)\n", encoding="ascii")
    client = McpClient(read_timeout_sec=2.0)
    with pytest.raises(Exception):
        await client.connect_stdio(sys.executable, [str(script)])
    proc = client._proc
    assert proc is not None
    assert proc.returncode is not None
    await client.close()  # 幂等：二次 close 不得抛


# 功能：close() 幂等，关闭后一切调用快速失败（offline 置位）
# 设计：manager 的 stop_all 与 connect 异常兜底可能对同一 client 重复 close；
#      两次 close 不抛 + call_tool 立 raise 即锁死幂等与守卫两条契约
async def test_close_idempotent_and_guards() -> None:
    async with _McpTestCtx("basic") as client:
        pass  # 上下文退出时已 close
    assert client.offline is True
    with pytest.raises(McpServerUnavailableError):
        await client.call_tool("echo", {})
    await client.close()
