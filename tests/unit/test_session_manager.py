from __future__ import annotations

from pathlib import Path

import pytest

from iwan_claude.core.bus.envelope import HandlerError
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.runner import RunOutcome
from iwan_claude.core.session.manager import SESSION_CLOSED, SESSION_NOT_FOUND, SessionManager
from iwan_claude.core.session.model import Session
from iwan_claude.core.session.store import SessionStore


class _Runner:
    # 模拟 AgentRunner，将 run 新消息写入 thread 后返回成功
    async def run_and_capture(
        self,
        goal: str,
        *,
        run_id: str | None = None,
        session: Session | None = None,
        store: SessionStore | None = None,
        system_prompt_override: str | None = None,
        tool_whitelist: list[str] | None = None,
        recovery_context: str = "",
    ) -> RunOutcome:
        assert run_id is not None
        assert session is not None
        assert store is not None
        store.append_messages(
            session.id,
            [{"role": "assistant", "content": [{"type": "text", "text": f"done {goal}"}]}],
            run_id,
        )
        return RunOutcome(status="success", result="done", reason=None)


class _RestoreRunner(_Runner):
    # 模拟 AgentRunner 的 checkpoint 读取/关闭接口，restore 返回固定旧状态
    def __init__(self) -> None:
        self.closed = False

    async def restore_checkpoint(self, thread_id: str, checkpoint_id: str) -> dict:
        return {
            "messages": [{"role": "user", "content": "restored"}],
            "step": 1,
            "status": "success",
            "_tool_calls": [],
            "_stop_reason": "",
        }

    async def close(self) -> None:
        self.closed = True


# 功能：restore_checkpoint 重写 thread 消息但绝不按 step 截断 run_ids（step 是图步数非 run 序号）
# 设计：预置 2 条 run_ids、restore 返回 step=1——旧代码 run_ids[:1] 会误删 "r2"，
#      断言完整保留即回归锁；同时验证 restore 的临时 runner 被 close（不关共享资源在另一文件锁）
async def test_restore_checkpoint_keeps_run_ids(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    fake = _RestoreRunner()
    manager = SessionManager(store, lambda: fake, EventBus())  # type: ignore[arg-type]
    session = await manager.create("chat")
    session.run_ids = ["r1", "r2"]

    out = await manager.restore_checkpoint(session.id, "cp1")

    assert out is not None and out["step"] == 1
    assert session.run_ids == ["r1", "r2"]
    assert store.read_messages(session.id) == [{"role": "user", "content": "restored"}]
    assert fake.closed is True


# 功能：验证 create 会创建 active session、写入 meta 并发布 session.created 事件
# 设计：用真实 SessionStore + EventBus 收集事件，覆盖 manager 与 store/bus 的协作边界
async def test_create_session_writes_meta_and_event(tmp_path: Path) -> None:
    events: list[object] = []
    bus = EventBus()

    async def collect(event: object) -> None:
        events.append(event)

    bus.subscribe(collect)
    store = SessionStore(tmp_path)
    manager = SessionManager(store, lambda: _Runner(), bus)  # type: ignore[arg-type]

    session = await manager.create("chat", "title")

    assert session.status == "active"
    assert store.read_meta(session.id).title == "title"
    assert [e.type for e in events] == ["session.created"]  # type: ignore[attr-defined]


# 功能：验证 chat session 处理一条消息后进入 waiting_for_input，并保留 user/assistant thread
# 设计：mock runner 主动追加 assistant 消息，确认 send_message 负责 user 消息、状态流转和 run_id 记录
async def test_send_message_chat_enters_waiting_and_writes_thread(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    manager = SessionManager(store, lambda: _Runner(), EventBus())  # type: ignore[arg-type]
    session = await manager.create("chat")

    result = await manager.send_message(session.id, "hello")
    run_id = result.run_id

    loaded = store.read_meta(session.id)
    assert loaded.status == "waiting_for_input"
    assert loaded.run_ids == [run_id]
    messages = store.read_messages(session.id)
    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1]["role"] == "assistant"


# 功能：验证 one_shot session 在单次消息完成后自动 closed
# 设计：复用 mock runner 的成功路径，聚焦 mode 对最终状态的影响，保证 kama run 的统一路径正确
async def test_one_shot_auto_closes(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    manager = SessionManager(store, lambda: _Runner(), EventBus())  # type: ignore[arg-type]
    session = await manager.create("one_shot")

    await manager.send_message(session.id, "hello")

    assert store.read_meta(session.id).status == "closed"


# 功能：验证不存在的 session_id 返回 session_not_found 错误码
# 设计：直接调用 get_history 的查找路径，断言 HandlerError code，覆盖 IPC handler 可结构化返回错误
async def test_missing_session_raises_handler_error(tmp_path: Path) -> None:
    manager = SessionManager(SessionStore(tmp_path), lambda: _Runner(), EventBus())  # type: ignore[arg-type]
    with pytest.raises(HandlerError) as exc:
        await manager.get_history("missing")
    assert exc.value.code == SESSION_NOT_FOUND


# 功能：验证 closed session 不能继续 send_message
# 设计：先显式 close，再发送消息，断言 session_closed 错误码，覆盖状态机拒绝路径
async def test_closed_session_rejects_message(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    manager = SessionManager(store, lambda: _Runner(), EventBus())  # type: ignore[arg-type]
    session = await manager.create("chat")
    await manager.close(session.id)

    with pytest.raises(HandlerError) as exc:
        await manager.send_message(session.id, "again")
    assert exc.value.code == SESSION_CLOSED


# 功能：验证首轮对话结束后 LLM 精修标题生效：种子（第一句话）→ 主题标题，meta 落盘且发第二次 renamed
# 设计：假 provider 返回带引号句号的脏输出，锁死 _clean_title 的剥壳逻辑；用 _title_tasks
#       集合确定性 pump 后台任务（不 sleep 轮询）；顺带断言启发式+精修共两次 session.renamed
#       事件——TUI 靠它们先把标签从数字换成人话、再换成主题
async def test_first_run_llm_refines_session_title(tmp_path: Path) -> None:
    import asyncio

    from iwan_claude.core.llm.types import LlmResponse

    class _TitleProvider:
        def __init__(self) -> None:
            self.seen_system: str = ""

        async def chat(self, messages: object, tool_schemas: object, bus: object,
                       run_id: str, *, step: int = 0, system: object = None) -> LlmResponse:
            self.seen_system = str(system or "")
            return LlmResponse(stop_reason="end_turn", text="“实验数据对比报告”。")

    provider = _TitleProvider()
    store = SessionStore(tmp_path)
    events: list[object] = []
    bus = EventBus()

    async def collect(event: object) -> None:
        events.append(event)

    bus.subscribe(collect)
    manager = SessionManager(store, lambda: _Runner(), bus, provider=provider)  # type: ignore[arg-type]
    session = await manager.create("chat")

    await manager.send_message(session.id, "帮我把这些实验数据整理成一份对比报告 重点看三组差异", skip_auto_skill=True)
    # 启发式标题即时生效（第一句话前缀）
    assert session.title.startswith("帮我把这些实验数据")
    while manager._title_tasks:
        await asyncio.gather(*list(manager._title_tasks))

    assert session.title == "实验数据对比报告"
    assert store.read_meta(session.id).title == "实验数据对比报告"
    renamed = [e for e in events if getattr(e, "type", "") == "session.renamed"]
    assert [r.title for r in renamed] == [renamed[0].title, "实验数据对比报告"]
    assert "标题生成器" in provider.seen_system


# 功能：验证精修的两条安全阀——LLM 抛错保留启发式标题；精修前用户手动改名则不覆盖
# 设计：两个失败/竞态分支各建一个独立 manager，共用 pump 逻辑；断言标题终值而非仅不崩，
#       确保"人比模型大"的优先级和异常兜底都不是空话
async def test_title_refine_safety_valves(tmp_path: Path) -> None:
    import asyncio

    from iwan_claude.core.session.manager import SessionManager as _M  # noqa: F401  拼写防呆：确保引用稳定

    class _BoomProvider:
        async def chat(self, messages: object, tool_schemas: object, bus: object,
                       run_id: str, *, step: int = 0, system: object = None) -> object:
            raise RuntimeError("llm down")

    store = SessionStore(tmp_path)
    m1 = SessionManager(store, lambda: _Runner(), EventBus(), provider=_BoomProvider())  # type: ignore[arg-type]
    s1 = await m1.create("chat")
    await m1.send_message(s1.id, "回退行为验证：LLM 挂掉时保留第一句话标题", skip_auto_skill=True)
    while m1._title_tasks:
        await asyncio.gather(*list(m1._title_tasks))
    assert s1.title.startswith("回退行为验证")  # 启发式保留

    # 手动改名竞态：精修协程被挂起前 title 已不是种子——用先改名再 pump 模拟
    class _TitleProvider2:
        async def chat(self, messages: object, tool_schemas: object, bus: object,
                       run_id: str, *, step: int = 0, system: object = None) -> object:
            from iwan_claude.core.llm.types import LlmResponse
            return LlmResponse(stop_reason="end_turn", text="机器起的名字")

    m2 = SessionManager(SessionStore(tmp_path / "s2"), lambda: _Runner(), EventBus(),
                        provider=_TitleProvider2())  # type: ignore[arg-type]
    s2 = await m2.create("chat")
    await m2.send_message(s2.id, "这条消息将被人工标题覆盖", skip_auto_skill=True)
    await m2.rename_session(s2.id, "人写的标题")
    while m2._title_tasks:
        await asyncio.gather(*list(m2._title_tasks))
    assert s2.title == "人写的标题"
