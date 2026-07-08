from __future__ import annotations

import asyncio
import json
import sys
import time

import kama_claude
from kama_claude.core.bus.commands import PongResult
from kama_claude.core.bus.envelope import JsonRpcError, JsonRpcSuccess
from kama_claude.core.config import KamaConfig


# 同步入口：运行 ping 协程，连接失败时打印错误并退出
# 函数作用：作为 ping 命令的同步入口，调用异步 _ping 函数执行实际的 ping 操作
# 传参：config - KamaConfig 对象，包含连接所需的 host 和 port
# 返回值：None
def cmd_ping(config: KamaConfig) -> None:
    # 使用 try-except 块捕获连接异常
    try:
        # 使用 asyncio.run() 运行异步 _ping 函数，将异步操作转换为同步调用
        asyncio.run(_ping(config))
    # 捕获连接被拒绝异常和操作系统相关异常（如无法建立网络连接）
    except (ConnectionRefusedError, OSError):
        # 打印错误信息到标准错误输出，提示 core 守护进程未运行及目标地址
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        # 以错误码 1 退出程序，表示连接失败
        sys.exit(1)


# 向 core 守护进程发送 ping 请求，打印 pong 响应及延迟
# 函数作用：异步执行 ping 操作，通过 TCP 连接向 core 守护进程发送 JSON-RPC 请求并解析响应
# 传参：config - KamaConfig 对象，包含连接所需的 host 和 port
# 返回值：None
async def _ping(config: KamaConfig) -> None:
    # 记录开始时间，用于计算延迟（使用 monotonic() 获取单调递增时间，不受系统时间调整影响）
    t0 = time.monotonic()
    # 建立 TCP 连接到 core 守护进程，返回异步读取器和写入器对象
    reader, writer = await asyncio.open_connection(config.host, config.port)

    # 构建 JSON-RPC 2.0 请求对象
    req = {
        "jsonrpc": "2.0",           # JSON-RPC 协议版本
        "id": "cli-1",              # 请求 ID，用于匹配响应
        "method": "core.ping",      # 调用的方法名
        "params": {"client": f"cli/{kama_claude.__version__}"},  # 方法参数，包含客户端版本信息
    }
    # 将请求字典序列化为 JSON 字符串，添加换行符后编码为字节，写入到 socket
    writer.write((json.dumps(req) + "\n").encode())
    # 等待数据完全发送到 socket 缓冲区
    await writer.drain()

    # 等待读取一行响应数据，设置超时时间为 10 秒
    line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    # 计算延迟：当前时间减去开始时间，转换为毫秒并取整
    latency_ms = int((time.monotonic() - t0) * 1000)

    # 关闭写入端
    writer.close()
    # 等待连接完全关闭
    await writer.wait_closed()

    # 将响应字节数据解码并解析为 JSON 对象
    raw = json.loads(line)
    # 判断响应是否包含错误
    if "error" in raw:
        # 使用 Pydantic 模型验证错误响应结构
        err = JsonRpcError.model_validate(raw)
        # 打印错误码和错误信息到标准错误输出
        print(f"error: {err.error.code} {err.error.message}", file=sys.stderr)
        # 以错误码 1 退出程序
        sys.exit(1)

    # 使用 Pydantic 模型验证成功响应结构
    resp = JsonRpcSuccess.model_validate(raw)
    # 解析响应结果中的 PongResult 对象
    result = PongResult.model_validate(resp.result)
    # 打印 pong 响应信息：服务器版本、运行时间、延迟
    print(f"pong server={result.server_version} uptime={result.uptime_ms}ms latency={latency_ms}ms")
