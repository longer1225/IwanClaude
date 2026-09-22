"""
追踪模块

该模块提供了系统运行时的追踪和日志功能，用于记录和分析系统行为。

核心组件：
- TraceRecord: 追踪记录数据类，存储追踪信息
- TraceWriter: 追踪写入器，异步写入追踪记录到文件
- TracingProvider: 追踪提供者，包裹 LLMProvider，记录 API 调用

设计要点：
- 使用异步队列实现非阻塞写入
- 支持多种追踪方向（CLIENT→CORE, CORE→CLIENT, CORE, CORE→LLM, LLM→CORE）
- 支持多种层级（ipc, event, llm）
- 支持多种类型（command, response, error, push, event, api_call, api_response）
"""

from iwan_claude.core.trace.provider import TracingProvider
from iwan_claude.core.trace.record import TraceRecord
from iwan_claude.core.trace.writer import TraceWriter

__all__ = ["TraceRecord", "TraceWriter", "TracingProvider"]
