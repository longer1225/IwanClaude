"""
Ping 命令模块 - 测试与核心服务的连接

【学习要点】
1. asyncio 异步编程：Python 的异步 I/O 框架，用于编写高效的网络通信代码
2. TCP 连接：使用 asyncio.open_connection 创建 TCP 连接
3. JSON-RPC 协议：一种轻量级的远程过程调用协议，基于 JSON
4. 异常处理：捕获连接失败的异常，提供友好的错误提示
5. 延迟计算：使用 time.monotonic() 计算精确的时间差

【协议格式】
请求：{"jsonrpc": "2.0", "id": "xxx", "method": "core.ping", "params": {...}}
响应：{"jsonrpc": "2.0", "id": "xxx", "result": {...}} 或 {"jsonrpc": "2.0", "id": "xxx", "error": {...}}
"""
from __future__ import annotations

# asyncio：Python 异步 I/O 框架，用于编写并发代码
# json：JSON 序列化/反序列化模块
# sys：系统相关操作，如退出程序、输出到 stderr
# time：时间相关功能，monotonic() 返回单调递增的时间
import asyncio
import json
import sys
import time

# 导入包版本信息
import iwan_claude

# 导入 RPC 相关的数据模型
from iwan_claude.core.bus.commands import PongResult           # Ping 响应的数据结构
from iwan_claude.core.bus.envelope import (                   # JSON-RPC 响应封装
    JsonRpcError,      # 错误响应
    JsonRpcSuccess,    # 成功响应
)
from iwan_claude.core.config import IwanConfig                # 配置数据结构


# 同步入口函数：运行异步 ping 协程，处理连接异常
def cmd_ping(config: IwanConfig) -> None:
    """
    Ping 命令的同步入口
    
    参数：
        config: IwanConfig 配置对象，包含 host 和 port 信息
    
    异常处理：
        ConnectionRefusedError: 核心服务未运行，连接被拒绝
        OSError: 其他网络相关错误
    
    退出码：
        0: 成功
        1: 连接失败
    """
    try:
        # asyncio.run() 是运行异步函数的标准方式
        # 它会创建一个新的事件循环，运行协程，然后关闭事件循环
        asyncio.run(_ping(config))
    except (ConnectionRefusedError, OSError):
        # 连接失败时，打印错误信息到 stderr（标准错误流）
        # 使用 file=sys.stderr 确保错误信息不会被当作正常输出
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        # 非零退出码表示程序执行失败
        sys.exit(1)


# 异步 Ping 函数：向核心服务发送 ping 请求
async def _ping(config: IwanConfig) -> None:
    """
    向 core 守护进程发送 ping 请求，打印 pong 响应及延迟
    
    工作流程：
    1. 记录开始时间
    2. 建立 TCP 连接
    3. 发送 JSON-RPC 请求
    4. 等待响应（带超时）
    5. 关闭连接
    6. 解析响应并打印结果
    """
    # time.monotonic() 返回单调递增的时间，适合计算时间差
    # 不受系统时间调整的影响（如 NTP 同步）
    t0 = time.monotonic()
    
    # 创建 TCP 连接，返回 (reader, writer) 对
    # reader 用于读取数据，writer 用于写入数据
    reader, writer = await asyncio.open_connection(config.host, config.port)

    # ===== 构建 JSON-RPC 请求 =====
    # JSON-RPC 2.0 规范要求：
    # - jsonrpc: "2.0"（协议版本）
    # - id: 请求标识（用于匹配响应）
    # - method: 要调用的方法名
    # - params: 方法参数
    req = {
        "jsonrpc": "2.0",
        "id": "cli-1",                          # 请求 ID，固定为 cli-1
        "method": "core.ping",                   # 调用的方法名
        "params": {"client": f"cli/{iwan_claude.__version__}"},  # 参数：客户端标识
    }
    
    # 将请求序列化为 JSON 字符串，添加换行符，然后编码为字节
    # JSON-RPC 通常使用行分隔，每行一个 JSON 对象
    writer.write((json.dumps(req) + "\n").encode())
    
    # 刷新写入缓冲区，确保数据发送出去
    # 这在异步编程中很重要，因为 write() 可能只是将数据放入缓冲区
    await writer.drain()

    # 等待响应，设置 10 秒超时
    # asyncio.wait_for() 会在超时后抛出 asyncio.TimeoutError
    line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    
    # 计算延迟：结束时间 - 开始时间，转换为毫秒
    latency_ms = int((time.monotonic() - t0) * 1000)

    # 关闭连接
    writer.close()
    await writer.wait_closed()

    # 解析响应
    raw = json.loads(line)
    
    # 检查是否为错误响应
    if "error" in raw:
        # 使用 Pydantic 模型验证错误响应
        err = JsonRpcError.model_validate(raw)
        print(f"error: {err.error.code} {err.error.message}", file=sys.stderr)
        sys.exit(1)

    # 成功响应，使用 Pydantic 模型验证
    resp = JsonRpcSuccess.model_validate(raw)
    result = PongResult.model_validate(resp.result)
    
    # 打印结果：服务器版本、运行时间、延迟
    print(f"pong server={result.server_version} uptime={result.uptime_ms}ms latency={latency_ms}ms")
