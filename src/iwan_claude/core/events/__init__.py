"""
事件系统模块

该模块提供了一个轻量级的事件总线机制，用于实现系统内部各组件之间的解耦通信。

核心组件：
- EventBus: 事件总线，管理事件订阅和发布
- EventWriter: 事件写入器，将事件持久化到文件系统

设计模式：
- 发布-订阅模式（Publish-Subscribe Pattern）：通过事件总线实现组件间的松耦合通信
- 观察者模式（Observer Pattern）：订阅者监听特定类型的事件并响应

使用示例：
    >>> from iwan_claude.core.events import EventBus, EventWriter
    >>> from pathlib import Path
    >>> from pydantic import BaseModel
    >>> 
    >>> class MyEvent(BaseModel):
    ...     message: str
    ... 
    >>> bus = EventBus()
    >>> 
    >>> async def handler(event):
    ...     print(f"Received: {event.message}")
    ... 
    >>> bus.subscribe(handler)
    >>> 
    >>> async with EventWriter(Path("events.log")) as writer:
    ...     writer.subscribe(bus)
    ...     await bus.publish(MyEvent(message="Hello"))
    ... 
    Received: Hello
"""

from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.events.writer import EventWriter

__all__ = ["EventBus", "EventWriter"]
