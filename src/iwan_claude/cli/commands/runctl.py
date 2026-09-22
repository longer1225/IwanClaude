"""
runctl 命令模块 - iwan cancel / iwan steer（运行中任务的 CLI 控制）

【学习要点】
1. 这两个命令存在的意义：TUI 是产品界面，CLI 版本用于脚本化调试和验证
   协议本身——能 `iwan cancel <run_id>` 说明 run.cancel 通路打通，与 TUI
   的 Esc 走的是同一条 JSON-RPC 方法。
2. 为什么单独发一条连接：run 期间 daemon 的那条连接被 send_message 的
   handler 挂起（await 中），cancel/steer 必须来自另一条连接——这正是
   双进程 + NDJSON 多连接架构允许的操作。
3. 退出码语义：accepted=True → 0；False → 2（run 已结束/不存在）。
   区分"命令没送到"（1）和"送到了但没命中"（2），脚本可以据此决定重试还是放弃。
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from iwan_claude.core.bus.envelope import JsonRpcError, JsonRpcSuccess
from iwan_claude.core.config import IwanConfig


# 向 daemon 发送一条 JSON-RPC 命令并返回 result 字典（错误/连接失败直接退出）
async def _send_rpc(config: IwanConfig, method: str, params: dict) -> dict:  # type: ignore[type-arg]
    reader, writer = await asyncio.open_connection(config.host, config.port)
    req = {"jsonrpc": "2.0", "id": f"cli-{method}", "method": method, "params": params}
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    # 10 秒超时：cancel/steer 都是立即返回的操作，超时说明 daemon 卡死
    line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    writer.close()
    await writer.wait_closed()

    raw = json.loads(line)
    if "error" in raw:
        err = JsonRpcError.model_validate(raw)
        print(f"error: {err.error.code} {err.error.message}", file=sys.stderr)
        sys.exit(1)
    resp = JsonRpcSuccess.model_validate(raw)
    result: dict[str, Any] = resp.result
    return result


# iwan cancel <run_id>：请求 daemon 取消运行中的任务
def cmd_cancel(run_id: str, config: IwanConfig) -> None:
    try:
        result = asyncio.run(
            _send_rpc(config, "run.cancel", {"type": "run.cancel", "run_id": run_id}),
        )
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        sys.exit(1)
    if result.get("accepted"):
        print(f"cancelling run {run_id}（运行将优雅收尾：轨迹入库 + run.finished(cancelled)）")
    else:
        print(f"run {run_id} 不在运行中（已结束或不存在）", file=sys.stderr)
        sys.exit(2)


# iwan steer <run_id> "message"：向运行中的任务注入修正评论
def cmd_steer(run_id: str, message: str, config: IwanConfig) -> None:
    try:
        result = asyncio.run(_send_rpc(
            config, "run.steer", {"type": "run.steer", "run_id": run_id, "message": message},
        ))
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        sys.exit(1)
    if result.get("accepted"):
        print(f"已入队（该 run 累计 {result.get('queued', 0)} 条修正），下一次模型调用前生效")
    else:
        print(f"run {run_id} 不在运行中，修正未送达——请改用普通消息发送", file=sys.stderr)
        sys.exit(2)
