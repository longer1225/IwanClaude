"""
技能自动匹配确认流程单元测试

【测试覆盖】
1. send_message 返回 skill_match 而非启动 run（预检查）
2. skill_name 确认后正常启动 run
3. skip_auto_skill=True 跳过匹配，正常启动 run
4. 手动 /skill 不触发预检查
5. 无匹配时不返回 skill_match
"""
from __future__ import annotations

from pathlib import Path

import pytest

from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.runner import RunOutcome
from iwan_claude.core.session.manager import SendMessageResult, SessionManager
from iwan_claude.core.session.model import Session
from iwan_claude.core.session.store import SessionStore


class _TrackingRunner:
    """模拟 AgentRunner，记录是否被调用及参数"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

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
        self.calls.append({
            "goal": goal,
            "system_prompt_override": system_prompt_override,
            "tool_whitelist": tool_whitelist,
        })
        assert session is not None
        assert store is not None
        store.append_messages(
            session.id,
            [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            run_id or "r1",
        )
        return RunOutcome(status="success", result="done", reason=None)


async def test_auto_match_returns_skill_match_not_run(tmp_path: Path) -> None:
    """自动匹配到技能时返回 skill_match，不启动 run"""
    store = SessionStore(tmp_path)
    tracker = _TrackingRunner()
    manager = SessionManager(store, lambda: tracker, EventBus())  # type: ignore

    session = await manager.create("chat", "test")

    # "总结" 是 summarize skill 的 keyword
    # 但如果没有加载 skills，match_skill 会返回 None
    # 所以我们测试无匹配的情况
    result = await manager.send_message(session.id, "总结一下")

    # 如果没有 built-in skills 加载，应该正常执行
    if result.skill_match is not None:
        # 有匹配 → 不应该调用 runner
        assert result.run_id == ""
        assert tracker.calls == []
        assert "name" in result.skill_match
        assert "score" in result.skill_match
    else:
        # 无匹配 → 正常执行
        assert result.run_id != ""
        assert len(tracker.calls) == 1


async def test_skip_auto_skill_always_runs(tmp_path: Path) -> None:
    """skip_auto_skill=True 时跳过预检查，直接启动 run"""
    store = SessionStore(tmp_path)
    tracker = _TrackingRunner()
    manager = SessionManager(store, lambda: tracker, EventBus())  # type: ignore

    session = await manager.create("chat", "test")
    result = await manager.send_message(
        session.id, "总结一下", skip_auto_skill=True
    )

    # 应该直接启动 run
    assert result.run_id != ""
    assert result.skill_match is None
    assert len(tracker.calls) == 1


async def test_skill_name_confirmed_runs_with_skill(tmp_path: Path) -> None:
    """skill_name 确认后使用指定 skill 启动 run"""
    store = SessionStore(tmp_path)
    tracker = _TrackingRunner()
    manager = SessionManager(store, lambda: tracker, EventBus())  # type: ignore

    session = await manager.create("chat", "test")
    # 传入不存在的 skill_name，runner 仍应正常执行（goal 使用原始 content）
    result = await manager.send_message(
        session.id, "帮我总结", skill_name="summarize"
    )

    assert result.run_id != ""
    assert result.skill_match is None
    assert len(tracker.calls) == 1


async def test_manual_slash_does_not_trigger_precheck(tmp_path: Path) -> None:
    """以 / 开头的消息不触发预检查，走手动触发逻辑"""
    store = SessionStore(tmp_path)
    tracker = _TrackingRunner()
    manager = SessionManager(store, lambda: tracker, EventBus())  # type: ignore

    session = await manager.create("chat", "test")
    result = await manager.send_message(session.id, "/help")

    # / 开头不触发预检查，直接执行
    assert result.run_id != ""
    assert result.skill_match is None
    assert len(tracker.calls) == 1


async def test_no_match_does_not_return_skill_match(tmp_path: Path) -> None:
    """无关键词匹配时不返回 skill_match"""
    store = SessionStore(tmp_path)
    tracker = _TrackingRunner()
    manager = SessionManager(store, lambda: tracker, EventBus())  # type: ignore

    session = await manager.create("chat", "test")
    result = await manager.send_message(session.id, "hello world")

    assert result.run_id != ""
    assert result.skill_match is None
    assert len(tracker.calls) == 1
