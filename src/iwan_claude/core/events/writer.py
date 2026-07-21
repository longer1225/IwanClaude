"""
事件写入器

该模块实现了事件持久化功能，将系统事件写入文件系统，用于日志记录、审计和调试。

核心功能：
- 将事件序列化为 JSON 行格式（JSON Lines）
- 支持异步上下文管理器协议（async with）
- 自动创建父目录
- 写入失败时记录日志但不抛出异常，保证系统稳定性

设计要点：
- 使用 JSON Lines 格式，每行一个 JSON 对象，便于后续解析和处理
- 使用追加模式写入，保留历史事件记录
- 写入后立即 flush，确保数据及时持久化
- 异常处理采用容错策略，写入失败不影响主流程
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import IO

from pydantic import BaseModel

from iwan_claude.core.events.bus import EventBus

logger = logging.getLogger(__name__)


class EventWriter:
    """
    事件写入器类

    将事件序列化为 JSON 行格式并写入文件系统，实现事件的持久化存储。

    工作原理：
    1. 通过 async with 上下文管理器打开文件
    2. 将 handle 方法注册到 EventBus 作为订阅者
    3. 当事件发布时，handle 方法被调用，将事件序列化为 JSON 行并写入文件
    4. 上下文退出时自动关闭文件

    特点：
    - 容错设计：写入失败时只记录日志，不抛出异常
    - 自动目录创建：确保目标文件的父目录存在
    - 实时写入：每次写入后立即 flush，防止数据丢失
    """

    def __init__(self, path: Path) -> None:
        """
        初始化事件写入器

        参数：
            path: 事件文件的路径，文件将以追加模式打开

        属性：
            _path: 事件文件的路径
            _file: 文件对象，初始为 None，在 __aenter__ 中打开
        """
        self._path = path
        self._file: IO[str] | None = None

    async def __aenter__(self) -> EventWriter:
        """
        异步上下文管理器进入方法

        创建目标文件的父目录（如果不存在），以追加模式打开文件，
        并返回事件写入器实例供 async with 使用。

        返回：
            EventWriter: 事件写入器实例

        实现细节：
        - 使用 mkdir(parents=True, exist_ok=True) 创建父目录
        - 使用 "a" 模式打开文件，保留历史记录
        - 指定 encoding="utf-8" 确保中文等非 ASCII 字符正确处理
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")
        return self

    async def __aexit__(self, *args: object) -> None:
        """
        异步上下文管理器退出方法

        关闭文件对象，释放资源。无论是否发生异常，都会执行此方法。

        参数：
            *args: 异常信息（类型、值、回溯对象），此处未使用

        实现细节：
        - 检查文件对象是否为 None，避免重复关闭
        - 关闭后将 _file 设置为 None，标记文件已关闭
        """
        if self._file is not None:
            self._file.close()
            self._file = None

    async def handle(self, event: BaseModel) -> None:
        """
        事件处理方法

        将事件序列化为 JSON 行格式并写入文件。

        参数：
            event: 要写入的事件对象，必须是 BaseModel 的子类

        实现原理：
        1. 检查文件对象是否已打开
        2. 使用 pydantic 的 model_dump_json() 方法将事件序列化为 JSON 字符串
        3. 添加换行符，形成 JSON Lines 格式
        4. 写入文件并立即 flush
        5. 捕获 OSError 和 ValueError 异常，记录日志但不抛出

        JSON Lines 格式说明：
        - 每行一个 JSON 对象
        - 便于逐行读取和解析
        - 支持流式处理，适合大数据量场景

        容错设计：
        - 文件未打开时直接返回，不报错
        - 写入失败时只记录日志，不中断主流程
        """
        if self._file is None:
            return
        try:
            self._file.write(event.model_dump_json() + "\n")
            self._file.flush()
        except (OSError, ValueError) as e:
            logger.error("EventWriter: failed to write event: %s", e)

    def subscribe(self, bus: EventBus) -> None:
        """
        将事件写入器注册到事件总线

        将 handle 方法注册为事件总线的订阅者，当事件发布时自动调用。

        参数：
            bus: 事件总线实例

        使用示例：
            >>> from iwan_claude.core.events import EventBus, EventWriter
            >>> from pathlib import Path
            >>> 
            >>> bus = EventBus()
            >>> async with EventWriter(Path("events.log")) as writer:
            ...     writer.subscribe(bus)
            ...     # 事件会自动写入文件
        """
        bus.subscribe(self.handle)
