"""
run_registry 模块 - 活跃运行的注册表（取消与运行中修正的底座）

【学习要点】
1. 为什么需要注册表：daemon 里 run 是在客户端连接的 handler 协程内执行的，
   外部（另一条 NDJSON 连接、或同连接的下一条命令）无法凭 run_id 找到那个
   正在跑的 asyncio.Task。注册表就是"run_id → 活着的 Task + 修正队列"的索引。
2. 取消的语义选择：不是粗暴 kill 进程，而是 task.cancel() 注入
   CancelledError，由 AgentRunner 的 except 分支优雅收尾——已产生的工具
   轨迹照常写入会话历史、RunFinishedEvent 照常发布。用户 Esc 之后
   看到的是"停在半路"而不是"什么都没发生"。
3. 修正（steer）为什么不直接改对话：模型只在调用时才"读"消息，运行中
   注入消息的正确时机是"下一次 LLM 调用之前"。所以 steer 只是入队；
   引擎在回合边界（run_tool_turn / chat 节点）调 pop_steers 消费。
   这与 Claude Code 的 in-flight steering 行为一致：当前工具执行不被打断。
4. cancel_requested 标志的用途：区分"run.cancel 主动取消"（外层 await 需
   优雅处理）与"客户端断线连带取消"（需要顺手清 run_task 和会话状态），
   两种 CancelledError 的来源不同，恢复动作也不同。
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ActiveRun:
    """
    活跃运行条目

    字段：
        run_id: 运行 ID
        session_id: 所属会话 ID
        task: 正在执行 run_and_capture 的 asyncio.Task
        steers: 待消费的修正消息队列（回合边界取出）
        cancel_requested: 是否已收到 run.cancel（用于区分取消来源）
    """
    run_id: str
    session_id: str
    task: asyncio.Task[Any]
    steers: deque[str] = field(default_factory=deque)
    cancel_requested: bool = False


# 进程内注册表：run_id → ActiveRun。daemon 单进程事件循环内使用，无需加锁
_ACTIVE: dict[str, ActiveRun] = {}


# 登记一个开始执行的 run（由 session.send_message 在 create_task 后调用）
def register_run(run_id: str, session_id: str, task: asyncio.Task[Any]) -> None:
    _ACTIVE[run_id] = ActiveRun(run_id=run_id, session_id=session_id, task=task)


# 注销一个已结束的 run（幂等，finally 中调用）
def unregister_run(run_id: str) -> None:
    _ACTIVE.pop(run_id, None)


# 请求取消指定 run；返回 False 表示该 run 已结束或不存在（幂等）
def request_cancel(run_id: str) -> bool:
    entry = _ACTIVE.get(run_id)
    if entry is None:
        return False
    entry.cancel_requested = True
    entry.task.cancel()
    log.info("run.cancel accepted: run_id=%s session=%s", run_id, entry.session_id)
    return True


# 向活跃 run 排入一条修正消息；返回队列长度，run 不存在时返回 None
def add_steer(run_id: str, message: str) -> int | None:
    entry = _ACTIVE.get(run_id)
    if entry is None:
        return None
    entry.steers.append(message)
    log.info("run.steer queued: run_id=%s queue_len=%d", run_id, len(entry.steers))
    return len(entry.steers)


# 取走并清空某 run 的全部待消费修正（引擎在回合边界调用）
def pop_steers(run_id: str) -> list[str]:
    entry = _ACTIVE.get(run_id)
    if entry is None or not entry.steers:
        return []
    steers = list(entry.steers)
    entry.steers.clear()
    return steers


# 查询某 run 是否被主动请求取消（用于区分取消来源）
def cancel_requested(run_id: str) -> bool:
    entry = _ACTIVE.get(run_id)
    return bool(entry and entry.cancel_requested)


# 把修正消息转换成注入对话的 user 消息（引擎共用格式）
def steer_as_message(text: str) -> dict[str, object]:
    return {
        "role": "user",
        "content": (
            "## User Steering (mid-run correction)\n"
            f"{text}\n\n"
            "The user sent this while you were working. Adjust your approach "
            "accordingly; do not restart completed work."
        ),
    }
