# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：Python 的异步 I/O 库，用于网络通信
import asyncio
# 导入 fnmatch：用于文件名/字符串的通配符匹配（如 "run.*" 匹配 "run.started"）
import fnmatch
# 导入 logging：用于日志记录
import logging
# 导入 uuid：用于生成唯一的订阅 ID
import uuid
# 导入 dataclass：数据类装饰器，简化类的定义
from dataclasses import dataclass

# 导入 pydantic：数据验证和序列化库
# BaseModel：所有 pydantic 模型的基类
from pydantic import BaseModel

# 导入 EventPushEnvelope：事件推送的封装格式（发给客户端的事件包）
from kama_claude.core.bus.envelope import EventPushEnvelope

# 创建日志记录器
logger = logging.getLogger(__name__)


# 数据类：表示一个客户端订阅
# 什么是 dataclass？就是一个专门用来存储数据的类，自动生成 __init__、__repr__ 等方法
# 不需要手动写这些样板代码，很方便
@dataclass
class _Subscription:
    # 订阅 ID：唯一标识这个订阅，用于后续取消订阅
    sub_id: str
    # 客户端的 StreamWriter：用于向客户端发送事件
    writer: asyncio.StreamWriter
    # 订阅的 topics：一个列表，包含要订阅的事件模式（如 ["run.*", "step.*"]）
    topics: list[str]
    # 订阅的 scope："global" 表示订阅所有事件，"run:<id>" 表示只订阅某个 run 的事件
    scope: str


# IpcEventBroadcaster 类：IPC 事件广播器
# 什么是广播器？就是一个消息分发器，当收到事件时，把事件推送给所有匹配的订阅者
# 它是事件总线和客户端之间的桥梁
class IpcEventBroadcaster:
    # 初始化方法：创建广播器实例
    def __init__(self) -> None:
        # 订阅列表：存储所有客户端的订阅信息
        # 每个元素是一个 _Subscription 对象
        self._subscriptions: list[_Subscription] = []

    # 注册一个客户端订阅，返回 subscription_id
    # 什么是订阅？就是客户端告诉广播器："我想接收这些类型的事件"
    # 传参：
    #   writer - 客户端的 StreamWriter（用于发送事件）
    #   topics - 要订阅的事件模式列表（如 ["run.*", "step.*", "llm.token"]）
    #   scope - 订阅范围，默认 "global"（全局），也可以是 "run:<id>"（指定 run）
    def subscribe(
        self,
        writer: asyncio.StreamWriter,
        topics: list[str],
        scope: str = "global",
    ) -> str:
        # 生成唯一的订阅 ID：格式是 "sub-" 加上 8 位随机十六进制字符串
        # 为什么用短的 ID？因为只在内部使用，不需要完整的 UUID
        sub_id = f"sub-{uuid.uuid4().hex[:8]}"
        
        # 创建订阅对象：封装所有订阅信息
        sub = _Subscription(sub_id=sub_id, writer=writer, topics=topics, scope=scope)
        
        # 将订阅添加到列表中
        self._subscriptions.append(sub)
        
        # 返回订阅 ID，供客户端后续使用（如取消订阅）
        return sub_id

    # 移除指定 writer 的所有订阅
    # 什么是取消订阅？就是客户端告诉广播器："我不再接收事件了"
    # 为什么要取消？当客户端断开连接时，需要清理订阅，避免向无效连接发送数据
    def unsubscribe(self, writer: asyncio.StreamWriter) -> None:
        # 使用列表推导式过滤掉指定 writer 的所有订阅
        # 为什么不用 remove()？因为可能有多个订阅（同一个客户端可能多次订阅）
        self._subscriptions = [s for s in self._subscriptions if s.writer is not writer]

    # 将事件推送到所有匹配的订阅客户端，写入失败时延迟清理死连接
    # 这是广播器的核心方法，当事件总线上有新事件时被调用
    # 传参：
    #   event - pydantic 模型表示的事件
    async def handle(self, event: BaseModel) -> None:
        # 将事件模型转为字典，方便获取字段值
        event_dict = event.model_dump()
        # 获取事件类型（如 "run.started"、"llm.token"）
        event_type: str = event_dict.get("type", "")
        # 获取事件所属的 run ID（如果有的话）
        run_id: str | None = event_dict.get("run_id")

        # 死连接列表：存储发送失败的客户端连接，稍后清理
        # 为什么不在循环中直接清理？因为在循环中修改列表可能导致问题
        dead: list[asyncio.StreamWriter] = []

        # 遍历所有订阅（使用 list() 拷贝，防止循环中修改列表）
        for sub in list(self._subscriptions):
            # ====================== 第一步：检查 topic 是否匹配 ======================
            # 如果事件类型不匹配订阅的 topics，跳过
            if not self._matches_topic(event_type, sub.topics):
                continue
            
            # ====================== 第二步：检查 scope 是否匹配 ======================
            # 如果事件的 run_id 不匹配订阅的 scope，跳过
            if not self._matches_scope(run_id, sub.scope):
                continue
            
            # ====================== 第三步：发送事件 ======================
            try:
                # 创建事件推送包（封装事件数据）
                envelope = EventPushEnvelope(event=event_dict)
                # 将事件序列化为 JSON，编码为字节，加上换行符，写入客户端
                sub.writer.write(envelope.model_dump_json().encode() + b"\n")
                # 刷新缓冲区，确保数据真正发送出去
                await sub.writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                # 如果发送失败（客户端断开连接），记录日志并加入死连接列表
                logger.debug("dead connection for sub %s, scheduling cleanup", sub.sub_id)
                dead.append(sub.writer)

        # ====================== 第四步：清理死连接 ======================
        # 遍历死连接列表，取消它们的订阅
        for writer in dead:
            self.unsubscribe(writer)

    # 检查事件类型是否匹配订阅的 topic 列表（支持 fnmatch glob 模式）
    # 什么是 fnmatch？就是文件名通配符匹配，比如：
    #   "run.*" 匹配 "run.started"、"run.finished"
    #   "step.*" 匹配 "step.started"、"step.finished"
    #   "llm.token" 精确匹配 "llm.token"
    @staticmethod
    def _matches_topic(event_type: str, topics: list[str]) -> bool:
        # 检查事件类型是否匹配任何一个 topic 模式
        # any()：只要有一个匹配就返回 True
        return any(fnmatch.fnmatch(event_type, pattern) for pattern in topics)

    # 检查事件 run_id 是否匹配订阅的 scope（global 全通，run:<id> 精确匹配）
    # 什么是 scope？就是订阅的范围：
    #   "global" - 接收所有事件（不管哪个 run）
    #   "run:abc123" - 只接收 run_id 为 "abc123" 的事件
    @staticmethod
    def _matches_scope(run_id: str | None, scope: str) -> bool:
        # 如果 scope 是 "global"，匹配所有事件
        if scope == "global":
            return True
        # 如果 scope 以 "run:" 开头，检查 run_id 是否匹配
        if scope.startswith("run:"):
            return run_id == scope[4:]  # scope[4:] 去掉 "run:" 前缀
        # 其他情况，不匹配
        return False
