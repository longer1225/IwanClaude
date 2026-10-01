# 功能：验证 plugin.* 三个 RPC 面端到端——列表默认全关、启停生效并广播 plugin.changed、未知名拒绝
# 设计：走 conftest 免鉴权 daemon（IWAN_PLUGINS_DIR 已改道 tmp，测试写不到用户 ~/.iwan）；
#       事件用 asyncio.Event 等待而非 sleep 轮询；断言 toggle 前后 rows 差异
#       覆盖"账本→内存态→贡献面"整链，装包 URL 路径交给单测与 devtest（不引外部网络进 CI）
from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from iwan_claude.core.transport.socket_client import SocketClient


# 功能：plugin.list 返回三个内置包且默认未启用；set_enabled 翻转后列表与事件同步可见
# 设计：一个用例串完"读→写→观测"闭环，避免两个独立 daemon fixture 双倍启动成本；
#       by=="toggle" 断言事件来源标注，为 GUI 角标文案提供契约
async def test_plugin_list_toggle_and_event(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    client = SocketClient("127.0.0.1", free_port)
    await client.connect()

    changed: asyncio.Event = asyncio.Event()
    payload: dict[str, Any] = {}

    async def on_event(event: dict[str, Any]) -> None:
        if event.get("type") == "plugin.changed" and event.get("name") == "commit-craft":
            payload.update(event)
            changed.set()

    client.on_event(on_event)
    loop_task = asyncio.create_task(client.run_event_loop())
    try:
        await client.send_command("event.subscribe", {"topics": ["plugin.*"], "scope": "global"})
        listing = await client.send_command("plugin.list", {})
        rows = {r["name"]: r for r in listing.get("plugins", [])}
        assert {"commit-craft", "code-review-kit", "md-notes"} <= set(rows)
        assert all(not r["enabled"] for r in rows.values()), "内置插件必须默认关"

        result = await client.send_command(
            "plugin.set_enabled", {"type": "plugin.set_enabled", "name": "commit-craft", "enabled": True},
        )
        assert result.get("ok") is True

        await asyncio.wait_for(changed.wait(), timeout=5.0)
        assert payload.get("enabled") is True and payload.get("by") == "toggle"

        listing2 = await client.send_command("plugin.list", {})
        row = next(r for r in listing2["plugins"] if r["name"] == "commit-craft")
        assert row["enabled"] and row["skills"] == ["commit-message"]
        assert row["status"] == "ok" and row["hooks"]
    finally:
        # 收尾还原禁用位：账本写在 daemon 的 tmp 目录里，虽随实例销毁，
        # 但同会话后续用例若复用同一 daemon 会看见脏 enabled 态
        await client.send_command(
            "plugin.set_enabled", {"type": "plugin.set_enabled", "name": "commit-craft", "enabled": False},
        )
        loop_task.cancel()
        await client.close()


# 功能：set_enabled 未知插件名 → ok=False 带中文原因，daemon 不崩不报错帧
# 设计：错误路径走业务 result（非 JSON-RPC error），与 schedule.delete 同语义——
#       "命令送达但业务失败"和"协议层坏"必须可区分
async def test_plugin_set_enabled_unknown(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    client = SocketClient("127.0.0.1", free_port)
    await client.connect()
    loop_task = asyncio.create_task(client.run_event_loop())
    try:
        result = await client.send_command(
            "plugin.set_enabled", {"type": "plugin.set_enabled", "name": "no-such-pkg", "enabled": True},
        )
        assert result.get("ok") is False
        assert "no-such-pkg" in str(result.get("error", ""))
    finally:
        loop_task.cancel()
        await client.close()
