# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入类型提示模块：Any 表示任意类型，Literal 表示字面量类型（只能取特定值）
from typing import Any, Literal

# 导入 Pydantic 库：BaseModel 用于定义数据模型，Field 用于字段配置
from pydantic import BaseModel, Field


# 定义 JSON-RPC 请求模型，继承自 Pydantic 的 BaseModel
# 作用：验证和序列化 JSON-RPC 2.0 请求格式
class JsonRpcRequest(BaseModel):
    # jsonrpc 字段：固定值 "2.0"，Literal["2.0"] 确保只能是这个值
    jsonrpc: Literal["2.0"] = "2.0"
    # id 字段：请求唯一标识，用于匹配响应，必填字符串类型
    id: str
    # method 字段：要调用的方法名，必填字符串类型
    method: str
    # params 字段：方法参数，默认值为空字典，使用 default_factory 确保每次创建新对象
    params: dict[str, Any] = Field(default_factory=dict)


# 定义 JSON-RPC 成功响应模型
# 作用：验证和序列化 JSON-RPC 2.0 成功响应格式
class JsonRpcSuccess(BaseModel):
    # jsonrpc 字段：固定值 "2.0"
    jsonrpc: Literal["2.0"] = "2.0"
    # id 字段：与请求的 id 对应，必填字符串类型
    id: str
    # result 字段：方法执行结果，可以是任意类型
    result: Any


# 定义 JSON-RPC 错误对象模型
# 作用：封装错误码、错误消息和可选的附加数据
class JsonRpcErrorObject(BaseModel):
    # code 字段：错误码，整数类型，遵循 JSON-RPC 2.0 标准错误码规范
    code: int
    # message 字段：错误描述信息，必填字符串类型
    message: str
    # data 字段：附加错误数据，可选，默认值为 None
    data: Any = None


# 定义 JSON-RPC 错误响应模型
# 作用：验证和序列化 JSON-RPC 2.0 错误响应格式
class JsonRpcError(BaseModel):
    # jsonrpc 字段：固定值 "2.0"
    jsonrpc: Literal["2.0"] = "2.0"
    # id 字段：与请求的 id 对应；如果请求解析失败无法确定 id，则为 None
    id: str | None = None
    # error 字段：错误对象，必填，包含错误码、消息和附加数据
    error: JsonRpcErrorObject


# JSON-RPC 2.0 标准错误码常量定义

# PARSE_ERROR：JSON 解析错误（请求不是有效的 JSON）
PARSE_ERROR = -32700      # 解析错误

# INVALID_REQUEST：请求格式错误（JSON 有效但不符合 JSON-RPC 规范）
INVALID_REQUEST = -32600  # 请求格式错误

# METHOD_NOT_FOUND：请求的方法不存在
METHOD_NOT_FOUND = -32601 # 方法不存在

# INVALID_PARAMS：方法参数错误
INVALID_PARAMS = -32602   # 参数错误

# INTERNAL_ERROR：服务器内部错误
INTERNAL_ERROR = -32603   # 服务器内部错误


# 构造一个 JSON-RPC 错误响应对象的便捷函数
# 函数作用：简化错误响应的创建，避免重复代码
# 传参：
#   id - 请求 ID，用于匹配响应，可能为 None（请求解析失败时）
#   code - 错误码，使用上面定义的常量
#   message - 错误描述信息
#   data - 可选的附加错误数据
# 返回值：JsonRpcError 对象
def make_error(id: str | None, code: int, message: str, data: Any = None) -> JsonRpcError:
    # 创建 JsonRpcError 对象，内部包含 JsonRpcErrorObject 错误详情
    return JsonRpcError(id=id, error=JsonRpcErrorObject(code=code, message=message, data=data))
