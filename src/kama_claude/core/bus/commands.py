# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入类型提示模块：Annotated 用于添加元数据注解，Literal 用于字面量类型
from typing import Annotated, Literal

# 导入 Pydantic 库：BaseModel 用于定义数据模型，Discriminator 用于判别联合类型
from pydantic import BaseModel, Discriminator


# 定义 Ping 命令模型，继承自 Pydantic 的 BaseModel
# 作用：封装 core.ping 命令的参数，用于服务端验证客户端发送的命令参数
class PingCommand(BaseModel):
    # type 字段：命令类型标识，固定值 "core.ping"，用于判别联合类型
    type: Literal["core.ping"] = "core.ping"
    # client 字段：客户端标识，包含客户端类型和版本信息（如 "cli/0.1.0"），必填字符串
    client: str


# 定义 Ping 命令的响应结果模型，继承自 Pydantic 的 BaseModel
# 作用：封装 core.ping 命令的返回结果，用于客户端验证服务端返回的数据
class PongResult(BaseModel):
    # server_version 字段：服务端版本号，字符串类型
    server_version: str
    # uptime_ms 字段：服务端运行时长，单位毫秒，整数类型
    uptime_ms: int
    # received_at 字段：请求接收时间，ISO 8601 格式的 UTC 时间戳，字符串类型
    received_at: str  # ISO 8601


# 定义命令判别联合类型
# 作用：当存在多种命令类型时，通过 type 字段自动选择对应的命令模型进行验证
# 未来扩展：新增其他命令（如 core.status、core.stop 等）时，只需添加到这个联合中
Command = Annotated[
    # 当前只有 PingCommand，未来可添加更多命令类型
    PingCommand,
    # 指定使用 "type" 字段作为判别器，Pydantic 会根据 type 值自动选择对应的模型
    Discriminator("type"),
]
