"""
run.cancel / run.steer 的 IPC 集成冒烟测试

【学习要点】
1. 冒烟点在"通路"不在"效果"：真实取消需要 LLM 跑长任务，测试环境没有 key；
   但"方法已注册、参数模型可解析、结果模型可序列化"这三件事用不存在的
   run_id 就能全验——miss 路径同样穿过 socket 分派 → pydantic 校验 → handler
   → JSON-RPC 响应 的完整链路。
2. 客户端响应靠 run_event_loop 泵：send_command 只是把 Future 挂进 _pending，
   真正把响应行读出并 set_result 的是事件循环任务。每个测试都要显式启动它
   （这是踩过的坑：不起循环时 await 永远不返回）。
"""
from __future__ import annotations

import asyncio
import contextlib
import subprocess
from collections.abc import AsyncIterator
from typing import Any

from iwan_claude.core.transport.socket_client import SocketClient


@contextlib.asynccontextmanager
async def _pumped_client(free_port: int) -> AsyncIterator[SocketClient]:
    # 连接 daemon 并同时启动事件泵；退出时取消泵并关闭连接
    client = SocketClient("127.0.0.1", free_port)
    await client.connect()
    pump = asyncio.create_task(client.run_event_loop())
    try:
        yield client
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        await client.close()


# 功能：run.cancel 对不存在的 run 返回 accepted=False 而不是报错
# 设计：取消是幂等操作，miss 必须走 result 通道（False）而非 error 通道；
#       这条不变式保证 TUI 对"刚好已结束"的 run 按 Esc 不会弹异常
async def test_cancel_unknown_run_returns_not_accepted(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    async with _pumped_client(free_port) as client:
        result: dict[str, Any] = await client.send_command(
            "run.cancel", {"type": "run.cancel", "run_id": "no-such-run"},
        )
        assert result == {"accepted": False}


# 功能：run.steer 对不存在的 run 返回 accepted=False、queued=0
# 设计：accepted=False + queued=0 是 TUI 降级为普通消息的信号，两字段都要在场；
#       用严格相等断言防止字段名漂移
async def test_steer_unknown_run_returns_not_accepted(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    async with _pumped_client(free_port) as client:
        result: dict[str, Any] = await client.send_command(
            "run.steer", {"type": "run.steer", "run_id": "no-such-run", "message": "改方向"},
        )
        assert result == {"accepted": False, "queued": 0}


# 功能：run.cancel 缺 run_id 参数时以 JSON-RPC Invalid Request 错误失败而非挂起
# 设计：server 把 handler 里的 pydantic ValidationError 统一映射为 -32600；
#       断言收到 error（而非 result/超时）证明参数模型真的参与了校验链路
async def test_cancel_missing_params_is_rejected(
    running_daemon: subprocess.Popen[bytes],
    free_port: int,
) -> None:
    from iwan_claude.core.transport.socket_client import IpcError

    async with _pumped_client(free_port) as client:
        try:
            await client.send_command("run.cancel", {"type": "run.cancel"})
            raise AssertionError("missing run_id should be rejected")
        except IpcError as exc:
            assert exc.code in (-32600, -32602)  # 不同 server 版本映射，二者皆合法
