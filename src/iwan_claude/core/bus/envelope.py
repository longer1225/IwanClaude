"""
JSON-RPC 协议封装模块 - 定义 JSON-RPC 2.0 协议的请求和响应封装

【学习要点】
1. JSON-RPC 2.0 协议：实现标准的 JSON-RPC 2.0 协议
2. 请求封装：JsonRpcRequest 封装客户端请求
3. 响应封装：JsonRpcSuccess 和 JsonRpcError 封装服务器响应
4. 事件封装：EventPushEnvelope 封装事件推送
5. 错误码：定义标准的 JSON-RPC 错误码

【核心组件】
- JsonRpcRequest: JSON-RPC 请求
- JsonRpcSuccess: JSON-RPC 成功响应
- JsonRpcError: JSON-RPC 错误响应
- JsonRpcErrorObject: 错误对象
- EventPushEnvelope: 事件推送封装
- HandlerError: 命令处理器异常
- make_error: 构造错误响应对象

【JSON-RPC 错误码】
- PARSE_ERROR (-32700): 解析错误（JSON 格式错误）
- INVALID_REQUEST (-32600): 请求格式错误（不符合 JSON-RPC 规范）
- METHOD_NOT_FOUND (-32601): 方法不存在
- INVALID_PARAMS (-32602): 参数错误（参数类型或值不正确）
- INTERNAL_ERROR (-32603): 服务器内部错误

【设计目的】
提供统一的 JSON-RPC 协议封装，
便于客户端和服务器之间的通信。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class JsonRpcRequest(BaseModel):
    """
    JSON-RPC 请求对象 - 封装客户端发送的 JSON-RPC 请求

    【字段说明】
    - jsonrpc: Literal["2.0"] - JSON-RPC 版本（固定为 "2.0"）
    - id: str - 请求 ID（唯一标识，用于匹配响应）
    - method: str - 方法名（如 "agent.run"）
    - params: dict[str, Any] - 请求参数（默认为空字典）

    【JSON-RPC 2.0 规范】
    ```json
    {
        "jsonrpc": "2.0",
        "id": "request_01",
        "method": "agent.run",
        "params": {"goal": "hello world"}
    }
    ```

    【设计目的】
    封装客户端发送的 JSON-RPC 请求，便于服务器解析和处理。

    【示例】
    ```python
    request = JsonRpcRequest(
        id="request_01",
        method="agent.run",
        params={"goal": "hello world"}
    )
    ```
    """
    # JSON-RPC 版本（固定为 "2.0"）
    jsonrpc: Literal["2.0"] = "2.0"
    # 请求 ID（唯一标识，用于匹配响应）
    id: str
    # 方法名（如 "agent.run"）
    method: str
    # 请求参数（默认为空字典）
    params: dict[str, Any] = Field(default_factory=dict)


class EventPushEnvelope(BaseModel):
    """
    事件推送封装对象 - 封装服务器向客户端推送的事件

    【字段说明】
    - kind: Literal["event"] - 类型标识（固定为 "event"）
    - event: dict[str, Any] - 事件数据（Event.model_dump() 的序列化结果）

    【设计目的】
    封装服务器向客户端推送的事件，
    便于客户端区分事件和 JSON-RPC 响应。

    【示例】
    ```python
    envelope = EventPushEnvelope(
        event={"type": "run.started", "run_id": "run_01", "goal": "hello world"}
    )
    ```
    """
    # 类型标识（固定为 "event"）
    kind: Literal["event"] = "event"
    # 事件数据（Event.model_dump() 的序列化结果）
    event: dict[str, Any]


class JsonRpcSuccess(BaseModel):
    """
    JSON-RPC 成功响应对象 - 封装服务器返回的成功响应

    【字段说明】
    - jsonrpc: Literal["2.0"] - JSON-RPC 版本（固定为 "2.0"）
    - id: str - 请求 ID（与请求中的 id 对应）
    - result: Any - 响应结果

    【JSON-RPC 2.0 规范】
    ```json
    {
        "jsonrpc": "2.0",
        "id": "request_01",
        "result": {"run_id": "run_01"}
    }
    ```

    【设计目的】
    封装服务器返回的成功响应，便于客户端解析和处理。

    【示例】
    ```python
    response = JsonRpcSuccess(
        id="request_01",
        result={"run_id": "run_01"}
    )
    ```
    """
    # JSON-RPC 版本（固定为 "2.0"）
    jsonrpc: Literal["2.0"] = "2.0"
    # 请求 ID（与请求中的 id 对应）
    id: str
    # 响应结果
    result: Any


class JsonRpcErrorObject(BaseModel):
    """
    JSON-RPC 错误对象 - 封装错误信息

    【字段说明】
    - code: int - 错误码（标准 JSON-RPC 错误码）
    - message: str - 错误消息（人类可读的错误描述）
    - data: Any - 额外数据（可选，用于提供更多错误信息）

    【JSON-RPC 2.0 规范】
    ```json
    {
        "code": -32601,
        "message": "Method not found",
        "data": {"method": "unknown_method"}
    }
    ```

    【设计目的】
    封装错误信息，便于客户端了解错误原因。

    【示例】
    ```python
    error_obj = JsonRpcErrorObject(
        code=-32601,
        message="Method not found",
        data={"method": "unknown_method"}
    )
    ```
    """
    # 错误码（标准 JSON-RPC 错误码）
    code: int
    # 错误消息（人类可读的错误描述）
    message: str
    # 额外数据（可选，用于提供更多错误信息）
    data: Any = None


class JsonRpcError(BaseModel):
    """
    JSON-RPC 错误响应对象 - 封装服务器返回的错误响应

    【字段说明】
    - jsonrpc: Literal["2.0"] - JSON-RPC 版本（固定为 "2.0"）
    - id: str | None - 请求 ID（与请求中的 id 对应，解析错误时为 None）
    - error: JsonRpcErrorObject - 错误对象

    【JSON-RPC 2.0 规范】
    ```json
    {
        "jsonrpc": "2.0",
        "id": "request_01",
        "error": {"code": -32601, "message": "Method not found"}
    }
    ```

    【设计目的】
    封装服务器返回的错误响应，便于客户端解析和处理。

    【示例】
    ```python
    response = JsonRpcError(
        id="request_01",
        error=JsonRpcErrorObject(code=-32601, message="Method not found")
    )
    ```
    """
    # JSON-RPC 版本（固定为 "2.0"）
    jsonrpc: Literal["2.0"] = "2.0"
    # 请求 ID（与请求中的 id 对应，解析错误时为 None）
    id: str | None = None
    # 错误对象
    error: JsonRpcErrorObject


# JSON-RPC 标准错误码
PARSE_ERROR = -32700      # 解析错误（JSON 格式错误）
INVALID_REQUEST = -32600  # 请求格式错误（不符合 JSON-RPC 规范）
METHOD_NOT_FOUND = -32601 # 方法不存在
INVALID_PARAMS = -32602   # 参数错误（参数类型或值不正确）
INTERNAL_ERROR = -32603   # 服务器内部错误


class HandlerError(Exception):
    """
    命令处理器异常 - 命令 handler 抛出此异常，SocketServer 将其转换为结构化 JSON-RPC 错误响应

    【字段说明】
    - code: int - 错误码
    - message: str - 错误消息
    - data: Any - 额外数据

    【设计目的】
    提供统一的命令处理器异常，
    便于 SocketServer 将异常转换为结构化的 JSON-RPC 错误响应。

    【使用场景】
    命令处理器在处理命令时抛出此异常，
    SocketServer 捕获并转换为 JSON-RPC 错误响应。

    【示例】
    ```python
    raise HandlerError(
        code=INVALID_PARAMS,
        message="Invalid parameters",
        data={"param": "goal", "reason": "cannot be empty"}
    )
    ```
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def make_error(id: str | None, code: int, message: str, data: Any = None) -> JsonRpcError:
    """
    构造一个 JSON-RPC 错误响应对象

    【参数说明】
    - id: str | None - 请求 ID（解析错误时为 None）
    - code: int - 错误码
    - message: str - 错误消息
    - data: Any - 额外数据（可选）

    【返回值】
    - JsonRpcError: JSON-RPC 错误响应对象

    【设计目的】
    提供便捷的错误响应构造函数，
    简化错误响应的创建过程。

    【示例】
    ```python
    error = make_error(
        id="request_01",
        code=METHOD_NOT_FOUND,
        message="Method not found",
        data={"method": "unknown_method"}
    )
    ```
    """
    return JsonRpcError(id=id, error=JsonRpcErrorObject(code=code, message=message, data=data))
