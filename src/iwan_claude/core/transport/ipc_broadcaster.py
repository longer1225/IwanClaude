"""
IPC 事件广播器

该模块实现了事件推送功能，将服务器端产生的事件广播到所有订阅的客户端。

核心功能：
- 管理客户端订阅，支持 topic 和 scope 过滤
- 将事件序列化为 JSON 行格式并推送到匹配的客户端
- 自动检测并清理死连接
- 支持事件追踪和日志记录

设计要点：
- 使用 fnmatch 实现 topic 的 glob 模式匹配
- 使用 scope 实现事件的作用域过滤（global 或特定 run）
- 采用延迟清理策略，避免在事件推送过程中修改订阅列表
- 使用 TraceWriter 记录事件推送的详细信息
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from iwan_claude.core.bus.envelope import EventPushEnvelope
from iwan_claude.core.trace.record import TraceRecord
from iwan_claude.core.trace.writer import TraceWriter

logger = logging.getLogger(__name__)


def _now() -> str:
    """
    获取当前时间的 ISO 格式字符串

    返回：
        str: 当前 UTC 时间的 ISO 8601 格式字符串，如 "2026-07-21T10:30:00Z"
    """
    return datetime.now(UTC).isoformat()


@dataclass
class _Subscription:
    """
    订阅信息数据类

    存储单个客户端的订阅配置，包括客户端连接、订阅的 topic 和 scope。

    属性：
        sub_id: 订阅 ID，格式为 "sub-<8位十六进制>"
        writer: 客户端的 StreamWriter，用于发送事件
        topics: 订阅的事件类型列表，支持 glob 模式
        scope: 订阅的作用域，"global" 表示全局，"run:<id>" 表示特定运行
    """
    sub_id: str
    writer: asyncio.StreamWriter
    topics: list[str]
    scope: str


class IpcEventBroadcaster:
    """
    IPC 事件广播器类

    实现事件推送机制，将服务器端事件广播到所有匹配的客户端。

    工作原理：
    1. 客户端通过 subscribe() 方法注册订阅，指定感兴趣的 topic 和 scope
    2. 事件产生时，handle() 方法被调用，遍历所有订阅
    3. 根据 topic 和 scope 过滤匹配的订阅
    4. 将事件封装为 EventPushEnvelope 并发送到匹配的客户端
    5. 检测死连接并延迟清理

    特点：
    - 支持 topic 的 glob 模式匹配（如 "run.*"）
    - 支持 scope 过滤，实现事件的精确推送
    - 延迟清理死连接，避免在推送过程中修改订阅列表
    - 支持事件追踪，记录推送详情
    """

    def __init__(self, trace: TraceWriter | None = None) -> None:
        """
        初始化事件广播器

        参数：
            trace: 追踪写入器，用于记录事件推送的详细信息，默认为 None

        属性：
            _subscriptions: 订阅列表，存储所有客户端的订阅信息
            _trace: 追踪写入器实例
        """
        self._subscriptions: list[_Subscription] = []
        self._trace = trace

    def subscribe(
        self,
        writer: asyncio.StreamWriter,
        topics: list[str],
        scope: str = "global",
    ) -> str:
        """
        注册客户端订阅

        将客户端的 StreamWriter 和订阅配置添加到订阅列表中。

        参数：
            writer: 客户端的 StreamWriter，用于发送事件
            topics: 订阅的事件类型列表，支持 glob 模式（如 ["run.*", "session.*"]）
            scope: 订阅的作用域，默认为 "global"，也可以是 "run:<id>"

        返回：
            str: 订阅 ID，用于后续管理

        使用示例：
            >>> broadcaster = IpcEventBroadcaster()
            >>> sub_id = broadcaster.subscribe(writer, ["run.*"], scope="run:abc123")
        """
        sub_id = f"sub-{uuid.uuid4().hex[:8]}"
        sub = _Subscription(sub_id=sub_id, writer=writer, topics=topics, scope=scope)
        self._subscriptions.append(sub)
        return sub_id

    def unsubscribe(self, writer: asyncio.StreamWriter) -> None:
        """
        移除指定客户端的所有订阅

        根据 StreamWriter 移除该客户端的所有订阅记录。

        参数：
            writer: 要移除订阅的客户端 StreamWriter

        实现原理：
        使用列表推导式过滤掉指定 writer 的所有订阅，创建新列表替换原列表。
        """
        self._subscriptions = [s for s in self._subscriptions if s.writer is not writer]

    async def handle(self, event: BaseModel) -> None:
        """
        处理事件并推送到匹配的客户端

        将事件序列化为 JSON 行格式，推送到所有匹配 topic 和 scope 的客户端。

        参数：
            event: 要推送的事件对象，必须是 BaseModel 的子类

        实现原理：
        1. 将事件转换为字典，提取 event_type 和 run_id
        2. 遍历所有订阅，根据 topic 和 scope 过滤匹配的订阅
        3. 将事件封装为 EventPushEnvelope，序列化为 JSON 并发送
        4. 如果启用了追踪，记录推送详情
        5. 收集死连接，延迟清理

        容错设计：
        - 使用 list() 创建订阅列表的副本，避免在遍历过程中修改
        - 捕获 ConnectionResetError、BrokenPipeError、OSError 异常
        - 死连接收集到 dead 列表，在推送完成后统一清理
        """
        event_dict = event.model_dump()
        event_type: str = event_dict.get("type", "")
        run_id: str | None = event_dict.get("run_id")

        dead: list[asyncio.StreamWriter] = []

        for sub in list(self._subscriptions):
            if not self._matches_topic(event_type, sub.topics):
                continue
            if not self._matches_scope(run_id, sub.scope):
                continue
            try:
                envelope = EventPushEnvelope(event=event_dict)
                sub.writer.write(envelope.model_dump_json().encode() + b"\n")
                await sub.writer.drain()
                if self._trace is not None:
                    client_id = str(sub.writer.get_extra_info("peername", "<unknown>"))
                    self._trace.emit(
                        TraceRecord(
                            ts=_now(),
                            direction="CORE→CLIENT",
                            layer="ipc",
                            kind="push",
                            run_id=run_id,
                            client_id=client_id,
                            data={"sub_id": sub.sub_id, "event_type": event_type},
                        )
                    )
            except (ConnectionResetError, BrokenPipeError, OSError):
                logger.debug("dead connection for sub %s, scheduling cleanup", sub.sub_id)
                dead.append(sub.writer)

        for writer in dead:
            self.unsubscribe(writer)

    @staticmethod
    def _matches_topic(event_type: str, topics: list[str]) -> bool:
        """
        检查事件类型是否匹配订阅的 topic 列表

        使用 fnmatch 实现 glob 模式匹配，支持通配符 * 和 ?。

        参数：
            event_type: 事件类型字符串，如 "run.started"
            topics: 订阅的 topic 列表，每个元素可以是精确匹配或 glob 模式

        返回：
            bool: 如果事件类型匹配任何一个 topic 模式，返回 True；否则返回 False

        匹配示例：
            >>> _matches_topic("run.started", ["run.*"])  # True
            >>> _matches_topic("session.created", ["run.*"])  # False
            >>> _matches_topic("run.completed", ["run.started", "run.completed"])  # True
        """
        return any(fnmatch.fnmatch(event_type, pattern) for pattern in topics)

    @staticmethod
    def _matches_scope(run_id: str | None, scope: str) -> bool:
        """
        检查事件的 run_id 是否匹配订阅的 scope

        支持两种 scope 模式：
        - "global": 匹配所有事件
        - "run:<id>": 精确匹配指定 run_id 的事件

        参数：
            run_id: 事件的 run_id，可能为 None
            scope: 订阅的 scope

        返回：
            bool: 如果匹配，返回 True；否则返回 False

        匹配示例：
            >>> _matches_scope("abc123", "global")  # True
            >>> _matches_scope("abc123", "run:abc123")  # True
            >>> _matches_scope("abc123", "run:def456")  # False
            >>> _matches_scope(None, "run:abc123")  # False
        """
        if scope == "global":
            return True
        if scope.startswith("run:"):
            return run_id == scope[4:]
        return False
