"""
追踪写入器

该模块实现了追踪记录的异步写入功能，将追踪记录写入文件系统。

核心组件：
- TraceWriter: 追踪写入器，异步写入追踪记录到文件

设计要点：
- 使用异步队列实现非阻塞写入
- 使用后台任务持续从队列读取并写入文件
- 支持 JSON Lines 格式，每行一个追踪记录
- 使用 asyncio.Queue 实现生产者-消费者模式
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from iwan_claude.core.trace.record import TraceRecord


class TraceWriter:
    """
    追踪写入器

    将追踪记录异步写入文件系统，使用生产者-消费者模式。

    工作原理：
    1. 通过 start() 启动后台 drain 任务
    2. 通过 emit() 将记录放入队列（非阻塞）
    3. drain 任务持续从队列读取并写入文件
    4. 通过 stop() 停止写入并等待队列清空

    文件格式：
    - JSON Lines 格式，每行一个 JSON 对象
    - 便于后续解析和处理

    使用示例：
        >>> writer = TraceWriter(Path("traces/trace.jsonl"))
        >>> await writer.start()
        >>> writer.emit(TraceRecord(...))
        >>> await writer.stop()
    """

    def __init__(self, path: Path) -> None:
        """
        初始化追踪写入器

        参数：
            path: 追踪文件的路径

        属性：
            _path: 追踪文件路径
            _queue: 异步队列，存储待写入的追踪记录
            _task: 后台 drain 任务，None 表示未启动

        使用示例：
            >>> writer = TraceWriter(Path("traces/trace.jsonl"))
        """
        self._path = path
        self._queue: asyncio.Queue[TraceRecord] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """
        启动追踪写入器

        创建目标目录（如果不存在），并启动后台 drain 任务。

        实现步骤：
        1. 创建目标目录的父目录
        2. 创建后台任务，执行 _drain() 方法

        使用示例：
            >>> await writer.start()
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._drain())

    async def stop(self) -> None:
        """
        停止追踪写入器

        等待队列清空后取消后台任务。

        实现步骤：
        1. 调用 queue.join() 等待队列清空
        2. 取消后台任务
        3. 等待任务完成（忽略 CancelledError）

        使用示例：
            >>> await writer.stop()
        """
        await self._queue.join()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def emit(self, record: TraceRecord) -> None:
        """
        发布追踪记录

        将追踪记录放入队列，非阻塞操作。

        参数：
            record: 要写入的追踪记录

        实现原理：
        使用 put_nowait() 将记录放入队列，不会阻塞调用者。
        如果队列已满，会抛出 QueueFull 异常。

        使用示例：
            >>> record = TraceRecord(ts="...", direction="CORE", layer="llm", kind="api_call", data={})
            >>> writer.emit(record)
        """
        self._queue.put_nowait(record)

    async def _drain(self) -> None:
        """
        持续从队列读取并写入文件

        后台任务，持续从队列读取追踪记录并追加写入文件。

        实现步骤：
        1. 以追加模式打开文件
        2. 循环从队列读取记录
        3. 将记录序列化为 JSON 行并写入文件
        4. 刷新缓冲区，确保数据及时持久化
        5. 调用 task_done() 标记任务完成

        使用示例：
            由 start() 自动创建，无需手动调用
        """
        with open(self._path, "a") as f:
            while True:
                record = await self._queue.get()
                try:
                    f.write(record.model_dump_json() + "\n")
                    f.flush()
                finally:
                    self._queue.task_done()
