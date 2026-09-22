"""
run_registry 单元测试 - 活跃运行注册表（取消 / 运行中修正队列）

【学习要点】
1. 测全局注册表要做隔离：_ACTIVE 是模块级 dict，测试间会互相污染。
   这里用 autouse fixture 在每个用例前后清空，等价于给被测单例造"干净的房间"。
2. request_cancel 命中后 task.cancel() 是"请求"而非"事实"：被取消方要 await
   到 CancelledError 才真正停。测试里用 asyncio.Event 观测收尾时机，
   断言的是协作式取消的语义，不是调度器细节。
"""
from __future__ import annotations

import asyncio

import pytest

from iwan_claude.core import run_registry
from iwan_claude.core.run_registry import (
    add_steer,
    cancel_requested,
    pop_steers,
    register_run,
    request_cancel,
    steer_as_message,
    unregister_run,
)


# autouse：每个用例前后清空 _ACTIVE 全局表，防测试间污染
@pytest.fixture(autouse=True)
def _clean_registry() -> object:
    run_registry._ACTIVE.clear()
    yield
    run_registry._ACTIVE.clear()


# 功能：register/unregister 维护 run_id 索引，unregister 对不存在的 id 幂等不抛错
# 设计：直接断言 _ACTIVE 内容——注册表本身就是索引，测"账目"比测行为更直白；
#       幂等路径（unregister 两次）是 finally 场景的关键不变式
async def test_register_and_unregister_lifecycle() -> None:
    task = asyncio.create_task(asyncio.sleep(10))
    try:
        register_run("r1", "s1", task)
        assert "r1" in run_registry._ACTIVE
        unregister_run("r1")
        assert "r1" not in run_registry._ACTIVE
        unregister_run("r1")  # 第二次也必须安静成功（finally 可能重入）
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# 功能：request_cancel 命中活跃 run 时取消其 task 并置 cancel_requested 标记
# 设计：被取消方用 except CancelledError + Event 显式"承认收到取消"，
#       断言协作式取消端到端生效，而不是只看返回值；未命中的 id 返回 False
async def test_request_cancel_hits_and_misses() -> None:
    done = asyncio.Event()

    async def _work() -> None:
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            done.set()
            raise

    task = asyncio.create_task(_work())
    await asyncio.sleep(0)  # 让 _work 真正跑到 await 点
    register_run("r1", "s1", task)

    assert request_cancel("r1") is True
    assert cancel_requested("r1") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert done.is_set()
    assert request_cancel("nope") is False


# 功能：add_steer 入队、pop_steers 一次性取空，未注册 run 返回 None（accepted=False 的依据）
# 设计：pop 的"取空"语义决定引擎不会重复注入同一条修正，必须断言第二次 pop 为空；
#       None 与 [] 的区分是协议层 RunSteerResult.accepted 的来源，不能混
async def test_steer_queue_semantics() -> None:
    task = asyncio.create_task(asyncio.sleep(10))
    try:
        assert add_steer("ghost", "hello") is None  # 未注册 → None，不是 0
        register_run("r1", "s1", task)
        assert add_steer("r1", "改用 asyncio") == 1
        assert add_steer("r1", "记得加超时") == 2
        assert pop_steers("r1") == ["改用 asyncio", "记得加超时"]
        assert pop_steers("r1") == []  # 消费即清空
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# 功能：steer_as_message 把修正文本包装成合法 user 消息并带可识别前缀
# 设计：前缀标记决定会话重放时能否区分"初始指令/运行中改向"，断言 role 与前缀即可
def test_steer_as_message_shape() -> None:
    msg = steer_as_message("方向错了")
    assert msg["role"] == "user"
    content = msg["content"]
    assert isinstance(content, str)
    assert "方向错了" in content
    assert "Steering" in content
