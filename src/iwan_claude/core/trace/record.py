"""
追踪记录数据类

该模块定义了追踪记录的数据结构，用于存储系统运行时的追踪信息。

核心组件：
- TraceRecord: 追踪记录数据类，存储追踪信息

设计要点：
- 使用 pydantic BaseModel 确保数据结构化和类型安全
- 支持多种追踪方向、层级和类型
- 使用 Literal 类型约束枚举值
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class TraceRecord(BaseModel):
    """
    追踪记录数据类

    存储系统运行时的追踪信息，用于调试、分析和审计。

    属性：
        ts: 时间戳，ISO 8601 格式字符串
        direction: 数据流向，取值范围：
            - CLIENT→CORE: 客户端到核心服务
            - CORE→CLIENT: 核心服务到客户端
            - CORE: 核心服务内部
            - CORE→LLM: 核心服务到 LLM
            - LLM→CORE: LLM 到核心服务
        layer: 层级，取值范围：
            - ipc: 进程间通信层
            - event: 事件层
            - llm: LLM 调用层
        kind: 类型，取值范围：
            - command: 命令请求
            - response: 命令响应
            - error: 错误响应
            - push: 事件推送
            - event: 事件
            - api_call: API 调用
            - api_response: API 响应
        run_id: 运行 ID，可选，用于关联同一运行的所有记录
        step: 步骤编号，可选，用于标记对话步骤
        client_id: 客户端 ID，可选，用于标识客户端
        data: 数据字典，存储具体的追踪信息

    使用示例：
        >>> record = TraceRecord(
        ...     ts="2026-07-21T10:30:00Z",
        ...     direction="CORE→LLM",
        ...     layer="llm",
        ...     kind="api_call",
        ...     run_id="run-abc123",
        ...     step=1,
        ...     data={"message_count": 5, "tool_count": 3}
        ... )
    """
    ts: str
    direction: Literal[
        "CLIENT→CORE",
        "CORE→CLIENT",
        "CORE",
        "CORE→LLM",
        "LLM→CORE",
    ]
    layer: Literal["ipc", "event", "llm"]
    kind: str  # command / response / error / push / event / api_call / api_response
    run_id: str | None = None
    step: int | None = None
    client_id: str | None = None
    data: dict[str, Any]
