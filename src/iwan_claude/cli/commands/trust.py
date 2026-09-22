"""
trust 命令模块 - iwan trust list/grant/deny/revoke（Layer 0 项目信任管理，S9 Part A）

【学习要点】
1. 为什么 CLI 能脱离会话改信任：信任是"目录级持久规则"（trust.toml），
   不是"某次调用的放行"——grant/deny 只写文件，不影响任何正在运行的会话的
   内存态（那要等下一条消息重查，或 trust.respond 带 session_id 才行）。
2. 与 TUI 弹窗共享同一 RPC：trust.respond/list/revoke——CLI 通了就说明协议
   通了，两端行为不可能漂移（同一 handler）。
3. 目录键归一化在 daemon 侧做（normalize_dir_key），CLI 原样传用户输入的
   路径即可——大小写/分隔符/相对路径的坑只有一份实现要修。
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

from iwan_claude.cli.commands.runctl import _send_rpc
from iwan_claude.core.config import IwanConfig


# iwan trust <list|grant|deny|revoke>：查询或变更持久信任条目
def cmd_trust(verb: str, target_dir: str, config: IwanConfig) -> None:
    """
    分发 trust 子命令：list 走 trust.list，其余三个动 trust.respond/revoke

    参数：
        verb: "list" | "grant" | "deny" | "revoke"
        target_dir: 目标目录（list 时忽略；相对路径由 daemon 归一化）
        config: 用于定位 host:port
    """
    try:
        if verb == "list":
            result = asyncio.run(_send_rpc(config, "trust.list", {"type": "trust.list"}))
            entries: dict[str, str] = result.get("entries", {})
            if not entries:
                print("(no trust entries - every folder is undecided/ask)")
                return
            for key in sorted(entries):
                print(f"  [{entries[key]:5}]  {key}")
            return
        params: dict[str, Any]
        if verb == "revoke":
            rpc, params = "trust.revoke", {"type": "trust.revoke", "cwd": target_dir}
        else:
            decision = "allow" if verb == "grant" else "deny"
            # session_id 传空：纯持久层操作，不动任何运行中会话（见模块注释 1）
            rpc = "trust.respond"
            params = {
                "type": rpc, "session_id": "", "cwd": target_dir,
                "decision": decision, "persistent": True,
            }
        result = asyncio.run(_send_rpc(config, rpc, params))
        if verb == "revoke" and not result.get("ok"):
            print(f"no persistent entry for {target_dir}", file=sys.stderr)
            sys.exit(2)
        print(f"{verb}: {target_dir} ok")
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        sys.exit(1)
