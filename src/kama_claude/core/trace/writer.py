from __future__ import annotations

import asyncio
from pathlib import Path

from kama_claude.core.trace.record import TraceRecord


class TraceWriter:
    # 初始化 TraceWriter；写入目标文件路径在 start() 前不会创建
    # 这里只是准备资源，不做任何 I/O 操作，所以是同步的（不需要 async）
    def __init__(self, path: Path) -> None:
        # 保存文件路径，但此时不创建文件（延迟创建）
        self._path = path
        # 创建一个异步队列，用于缓冲 trace 记录
        # Queue 是线程安全的，也支持异步操作
        self._queue: asyncio.Queue[TraceRecord] = asyncio.Queue()
        # 后台写入任务，start() 后才会创建
        self._task: asyncio.Task[None] | None = None

    # 创建目录、启动后台 drain task
    # 为什么是 async？因为 create_task 虽然立即返回，
    # 但它返回的是一个 Task 对象，这里只是启动任务，不是等待完成
    async def start(self) -> None:
        # 创建父目录，如果已存在则不报错（exist_ok=True）
        # mkdir 是同步操作，但很快，不会阻塞太久
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 启动后台任务：把 _drain() 放入事件循环，立即返回
        # create_task 是非阻塞的，它只是告诉事件循环"去执行这个任务"
        self._task = asyncio.create_task(self._drain())

    # 等待队列清空后取消 drain task
    # 为什么是 async？因为 await queue.join() 会阻塞（等待队列清空）
    async def stop(self) -> None:
        # 等待队列中所有任务完成（所有记录都已写入文件）
        # join() 是阻塞的，会等到 queue.task_done() 被调用足够次数
        await self._queue.join()
        
        if self._task is not None:
            # 取消后台任务（发送取消信号）
            self._task.cancel()
            try:
                # 等待任务真正结束（捕获 CancelledError）
                # 为什么要 await？因为 cancel() 只是发送信号，
                # 任务可能还在执行最后一次迭代
                await self._task
            except asyncio.CancelledError:
                # 取消错误是正常的，忽略它
                pass

    # 非阻塞地将 record 放入写入队列
    # 为什么不是 async？因为 put_nowait() 是立即返回的，不需要等待
    def emit(self, record: TraceRecord) -> None:
        # put_nowait()：立即放入队列，如果队列满了会抛出 QueueFull 异常
        # 这里没有 await，所以是非阻塞的
        self._queue.put_nowait(record)

    # 持续从队列读取 record 并追加写入文件
    # 为什么是 async？因为 await queue.get() 会阻塞（等待有数据）
    async def _drain(self) -> None:
        # 以追加模式打开文件（"a" = append）
        # with 语句会自动关闭文件
        with open(self._path, "a") as f:
            # 无限循环：持续读取队列
            while True:
                # await queue.get()：等待队列中有数据
                # 如果队列为空，这里会"暂停"（让出 CPU），不会阻塞事件循环
                # 这是异步阻塞，不是同步阻塞！
                record = await self._queue.get()
                try:
                    # 将 record 序列化为 JSON 字符串，加上换行符
                    f.write(record.model_dump_json() + "\n")
                    # 强制刷新缓冲区，确保数据立即写入磁盘
                    # flush() 是同步操作，但很快
                    f.flush()
                finally:
                    # 告诉队列"这个任务完成了"
                    # 这很重要，否则 queue.join() 永远不会返回
                    self._queue.task_done()
