"""
事件总线实现

该模块定义了事件总线的核心实现，是整个事件系统的基础设施。

核心概念：
- EventHandler: 事件处理器类型，接收事件对象并返回异步任务
- EventBus: 事件总线，管理订阅者列表并负责事件分发

设计要点：
- 使用 pydantic BaseModel 作为事件的基类，确保数据结构化和类型安全
- 支持异步事件处理，通过 asyncio 实现并发执行
- 按注册顺序依次调用订阅者，保证事件处理的可预测性
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel

# 事件处理器类型定义
# 接收一个 BaseModel 类型的事件对象，返回一个异步任务（Awaitable[None]）
# 这种类型定义确保了所有事件处理器都遵循统一的接口规范
type EventHandler = Callable[[BaseModel], Awaitable[None]]


class EventBus:
    """
    事件总线类

    实现发布-订阅模式，允许系统中的组件订阅和发布事件，实现解耦通信。

    工作原理：
    1. 组件通过 subscribe() 方法注册事件处理函数
    2. 组件通过 publish() 方法发布事件
    3. 事件总线按注册顺序依次调用所有订阅者的处理函数
    4. 所有事件处理都是异步执行的，提高系统并发性能

    特点：
    - 松耦合：发布者和订阅者之间不需要知道彼此的存在
    - 可扩展性：可以随时添加或移除订阅者，不影响其他组件
    - 类型安全：事件必须是 BaseModel 的子类，确保数据结构规范
    """

    def __init__(self) -> None:
        """
        初始化事件总线

        创建一个空的订阅者列表，用于存储所有注册的事件处理函数。
        """
        # 订阅者列表，存储所有已注册的事件处理器
        self._subscribers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """
        注册事件处理函数

        将事件处理器添加到订阅者列表中，当事件发布时会被调用。

        参数：
            handler: 事件处理器函数，接收一个 BaseModel 类型的事件对象
                     并返回一个异步任务（Awaitable[None]）

        使用示例：
            >>> async def my_handler(event):
            ...     print(f"处理事件: {event}")
            ... 
            >>> bus = EventBus()
            >>> bus.subscribe(my_handler)
        """
        self._subscribers.append(handler)

    async def publish(self, event: BaseModel) -> None:
        """
        发布事件

        将事件分发给所有已注册的订阅者，按注册顺序依次调用处理函数。

        参数：
            event: 要发布的事件对象，必须是 BaseModel 的子类

        实现原理：
        - 使用 async for 循环遍历所有订阅者
        - 依次 await 每个处理器，确保按顺序执行
        - 如果某个处理器抛出异常，会中断后续处理器的执行

        使用示例：
            >>> from pydantic import BaseModel
            >>> 
            >>> class MyEvent(BaseModel):
            ...     data: str
            ... 
            >>> async def handler(event):
            ...     print(event.data)
            ... 
            >>> bus = EventBus()
            >>> bus.subscribe(handler)
            >>> await bus.publish(MyEvent(data="hello"))
            hello
        """
        for handler in self._subscribers:
            await handler(event)
