# 功能：验证 McpServerManager.status() 按配置逐行报告连接态、工具归属与失败原因
# 设计：status() 的语义难点在"配了但没起来也要现身"——用一个真实启动失败的
# 配置（command 指向不存在的可执行文件，_start_one 走异常分支记 last_error）
# 加一个手工注入的假 client（省掉真 stdio 服务器），两条行各自验证失败路径
# 与成功路径；工具归属断言靠 _server_name 过滤，防"张冠李戴"列错别人的工具。
from __future__ import annotations

from types import SimpleNamespace

from iwan_claude.core.config import McpServerConfig
from iwan_claude.core.mcp.server import McpServerManager


# 功能：一个连不上（真异常）+ 一个在线（假 client）的混合状态表
# 设计：失败行走真代码（start_all 起不存在的二进制→_start_one 异常分支）；
# "在线"行绕开 start_all——真 stdio 握手要完整 MCP 协议，echo 会把测试拖到
# 读超时。只把它追加进配置快照并注入假 client/工具，status() 消费的正是这三样
async def test_status_mixed_connected_and_failed() -> None:
    mgr = McpServerManager()
    bad = McpServerConfig(name="dead", transport="stdio", command="__no_such_binary_iwan_test__")
    good = McpServerConfig(name="alive", transport="stdio", command="echo")
    await mgr.start_all([bad])
    mgr._configs.append(good)  # noqa: SLF001 - 注入第二行配置（不走启动路径）

    # 注入"成功"一侧：status() 只读 offline 与工具归属，不需要真连接
    mgr._clients["alive"] = SimpleNamespace(offline=False)  # noqa: SLF001 - 注入在线态
    mgr._tools = [  # noqa: SLF001 - 注入工具归属
        SimpleNamespace(name="mcp__alive__search", _server_name="alive"),
        SimpleNamespace(name="mcp__other__x", _server_name="other"),
    ]

    rows = {r["name"]: r for r in mgr.status()}
    assert set(rows) == {"dead", "alive"}, "配置的每一台都必须现身"
    assert rows["alive"]["connected"] is True
    assert rows["alive"]["tools"] == ["mcp__alive__search"]
    assert rows["dead"]["connected"] is False
    assert "FileNotFoundError" in rows["dead"]["last_error"] or rows["dead"]["last_error"]
    assert rows["dead"]["tools"] == []


# 功能：无配置时 status() 返回空表而非报错
# 设计：空 servers 是大多数用户的真实形态（没配 MCP），GUI 首屏就调它——
# 这条守住"零配置零噪声"的展示底线
async def test_status_empty() -> None:
    mgr = McpServerManager()
    await mgr.start_all([])
    assert mgr.status() == []
