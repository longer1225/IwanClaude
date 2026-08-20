"""
崩溃恢复单元测试

【测试覆盖】
1. SnapshotWriter：验证快照写入和事件处理
2. read_snapshot / format_recovery_context：验证快照读取和格式化
3. SessionManager.recover_interrupted_sessions：验证崩溃检测
4. SessionManager._build_recovery_context：验证恢复上下文生成
5. SessionModel 新增状态：验证 running/interrupted 状态序列化
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iwan_claude.core.bus.events import (
    StepFinishedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.runs import new_run_id
from iwan_claude.core.session.manager import SessionManager
from iwan_claude.core.session.model import Session
from iwan_claude.core.session.store import SessionStore
from iwan_claude.core.snapshot import (
    SnapshotWriter,
    format_recovery_context,
    read_snapshot,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ==================== SnapshotWriter 测试 ====================


async def test_snapshot_writer_writes_on_step_finished(tmp_path: Path) -> None:
    """验证 StepFinishedEvent 触发快照写入"""
    snapshot_path = tmp_path / "snapshot.json"
    writer = SnapshotWriter(snapshot_path, goal="实现登录功能")

    # 模拟工具调用开始
    await writer.handle(ToolCallStartedEvent(
        run_id="r1",
        tool_use_id="tu1",
        tool_name="write_file",
        params={"path": "src/auth.py", "content": "..."},
        ts=_now(),
    ))
    # 模拟工具调用完成
    await writer.handle(ToolCallFinishedEvent(
        run_id="r1",
        tool_use_id="tu1",
        tool_name="write_file",
        elapsed_ms=100,
        output="ok",
        ts=_now(),
    ))
    # 模拟步骤完成
    await writer.handle(StepFinishedEvent(
        run_id="r1",
        step=1,
        ts=_now(),
    ))

    # 验证快照已写入
    assert snapshot_path.exists()
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert data["goal"] == "实现登录功能"
    assert data["step"] == 1
    assert data["status"] == "running"
    assert "src/auth.py" in data["file_changes"]
    assert data["last_tool"]["name"] == "write_file"
    assert data["last_tool"]["success"] is True


async def test_snapshot_writer_tracks_multiple_files(tmp_path: Path) -> None:
    """验证多个文件变更被去重记录"""
    writer = SnapshotWriter(tmp_path / "snapshot.json", goal="test")

    # 第一次写入文件 A
    await writer.handle(ToolCallStartedEvent(
        run_id="r1", tool_use_id="t1", tool_name="write_file",
        params={"path": "a.py"}, ts=_now(),
    ))
    await writer.handle(ToolCallFinishedEvent(
        run_id="r1", tool_use_id="t1", tool_name="write_file",
        elapsed_ms=10, ts=_now(),
    ))

    # 第二次写入文件 A（重复）
    await writer.handle(ToolCallStartedEvent(
        run_id="r1", tool_use_id="t2", tool_name="write_file",
        params={"path": "a.py"}, ts=_now(),
    ))
    await writer.handle(ToolCallFinishedEvent(
        run_id="r1", tool_use_id="t2", tool_name="write_file",
        elapsed_ms=10, ts=_now(),
    ))

    # 写入文件 B
    await writer.handle(ToolCallStartedEvent(
        run_id="r1", tool_use_id="t3", tool_name="edit_by_lines",
        params={"path": "b.py"}, ts=_now(),
    ))
    await writer.handle(ToolCallFinishedEvent(
        run_id="r1", tool_use_id="t3", tool_name="edit_by_lines",
        elapsed_ms=10, ts=_now(),
    ))

    await writer.handle(StepFinishedEvent(run_id="r1", step=1, ts=_now()))

    data = read_snapshot(tmp_path / "snapshot.json")
    assert data is not None
    # a.py 应该只出现一次（去重）
    assert data["file_changes"].count("a.py") == 1
    assert "b.py" in data["file_changes"]


async def test_snapshot_writer_ignores_non_relevant_events(tmp_path: Path) -> None:
    """验证非相关事件不会导致崩溃"""
    writer = SnapshotWriter(tmp_path / "snapshot.json", goal="test")

    # 发送一个不相关的事件（RunStartedEvent）
    from iwan_claude.core.bus.events import RunStartedEvent
    await writer.handle(RunStartedEvent(run_id="r1", goal="test", ts=_now()))

    # 不应该崩溃，也不应该写入快照
    assert not (tmp_path / "snapshot.json").exists()


# ==================== read_snapshot / format_recovery_context 测试 ====================


async def test_read_snapshot_returns_none_for_missing_file(tmp_path: Path) -> None:
    """验证文件不存在时返回 None"""
    result = read_snapshot(tmp_path / "nonexistent.json")
    assert result is None


async def test_format_recovery_context_contains_key_info() -> None:
    """验证恢复上下文包含任务目标、进度、文件变更"""
    snapshot = {
        "goal": "实现用户登录功能",
        "step": 5,
        "status": "running",
        "file_changes": ["src/auth.py", "tests/test_auth.py"],
        "last_tool": {"name": "write_file", "params": {"path": "src/auth.py"}, "success": True},
    }
    context = format_recovery_context(snapshot)

    assert "实现用户登录功能" in context
    assert "5" in context  # 步骤数
    assert "src/auth.py" in context
    assert "tests/test_auth.py" in context
    assert "write_file" in context
    assert "成功" in context


async def test_format_recovery_context_with_no_file_changes() -> None:
    """验证无文件变更时的格式"""
    snapshot = {
        "goal": "回答问题",
        "step": 1,
        "file_changes": [],
        "last_tool": None,
    }
    context = format_recovery_context(snapshot)
    assert "回答问题" in context
    assert "（无）" in context


# ==================== SessionManager 崩溃恢复测试 ====================


async def test_recover_interrupted_sessions_marks_running_as_interrupted(tmp_path: Path) -> None:
    """验证 recover_interrupted_sessions 将 running 状态标记为 interrupted"""
    store = SessionStore(tmp_path)
    manager = SessionManager(store, lambda: None, EventBus())  # type: ignore[arg-type]

    # 创建一个会话并手动设为 running（模拟崩溃时的状态）
    session = await manager.create("chat", "崩溃的任务")
    session.status = "running"
    store.write_meta(session)

    # 调用恢复方法
    count = await manager.recover_interrupted_sessions()

    assert count == 1
    # 验证状态已更新
    recovered = store.read_meta(session.id)
    assert recovered.status == "interrupted"


async def test_recover_interrupted_sessions_skips_non_running(tmp_path: Path) -> None:
    """验证只恢复 running 状态的会话"""
    store = SessionStore(tmp_path)
    manager = SessionManager(store, lambda: None, EventBus())  # type: ignore[arg-type]

    # 创建两个会话
    s1 = await manager.create("chat", "崩溃的")
    s2 = await manager.create("chat", "正常的")
    # s1 设为 running（崩溃），s2 保持 waiting_for_input
    s1.status = "running"
    store.write_meta(s1)
    s2.status = "waiting_for_input"
    store.write_meta(s2)

    count = await manager.recover_interrupted_sessions()

    assert count == 1
    assert store.read_meta(s1.id).status == "interrupted"
    assert store.read_meta(s2.id).status == "waiting_for_input"


async def test_list_interrupted_sessions(tmp_path: Path) -> None:
    """验证列出中断的会话"""
    store = SessionStore(tmp_path)
    manager = SessionManager(store, lambda: None, EventBus())  # type: ignore[arg-type]

    s1 = await manager.create("chat", "崩溃会话1")
    s1.status = "interrupted"
    store.write_meta(s1)

    s2 = await manager.create("chat", "正常会话")
    s2.status = "waiting_for_input"
    store.write_meta(s2)

    interrupted = manager.list_interrupted_sessions()
    assert len(interrupted) == 1
    assert interrupted[0].id == s1.id


# ==================== SessionModel 序列化测试 ====================


async def test_session_model_new_statuses_roundtrip(tmp_path: Path) -> None:
    """验证 running 和 interrupted 状态的序列化/反序列化"""
    store = SessionStore(tmp_path)

    # 创建 running 状态的会话
    session = Session(
        id="sess-test",
        mode="chat",
        status="running",
        title="test",
        created_at=_now(),
        updated_at=_now(),
    )
    store.write_meta(session)

    # 读取并验证
    loaded = store.read_meta("sess-test")
    assert loaded.status == "running"

    # 修改为 interrupted
    loaded.status = "interrupted"
    store.write_meta(loaded)

    # 再次读取验证
    loaded2 = store.read_meta("sess-test")
    assert loaded2.status == "interrupted"


# ==================== 端到端恢复流程测试 ====================


class _MockRunner:
    """模拟 AgentRunner，记录 recovery_context"""

    def __init__(self) -> None:
        self.last_recovery_context = "NOT_SET"

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
    ) -> object:
        from iwan_claude.core.runner import RunOutcome
        self.last_recovery_context = recovery_context
        assert session is not None
        assert store is not None
        store.append_messages(
            session.id,
            [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            run_id or "r1",
        )
        return RunOutcome(status="success", result="done", reason=None)


async def test_full_recovery_flow(tmp_path: Path) -> None:
    """验证完整的崩溃恢复流程：崩溃→检测→恢复→注入上下文"""
    store = SessionStore(tmp_path)
    mock_runner = _MockRunner()
    bus = EventBus()
    manager = SessionManager(store, lambda: mock_runner, bus)  # type: ignore[arg-type]

    # 1. 创建会话并模拟崩溃（设为 running）
    session = await manager.create("chat", "实现登录")
    session.status = "running"
    store.write_meta(session)

    # 2. 模拟快照文件（在 runs 目录下）
    runs_dir = store.runs_dir(session.id)
    run_id = new_run_id()
    run_path = runs_dir / run_id
    run_path.mkdir(parents=True, exist_ok=True)
    snapshot_data = {
        "goal": "实现登录",
        "step": 3,
        "status": "running",
        "file_changes": ["src/auth.py"],
        "last_tool": {"name": "write_file", "params": {"path": "src/auth.py"}, "success": True},
        "updated_at": _now(),
    }
    (run_path / "snapshot.json").write_text(
        json.dumps(snapshot_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 3. Core 重启，调用 recover_interrupted_sessions
    count = await manager.recover_interrupted_sessions()
    assert count == 1
    assert store.read_meta(session.id).status == "interrupted"

    # 4. 用户发送恢复消息
    await manager.send_message(session.id, "请从上次中断的地方继续执行")

    # 5. 验证恢复上下文被注入
    assert mock_runner.last_recovery_context != ""
    assert "实现登录" in mock_runner.last_recovery_context
    assert "src/auth.py" in mock_runner.last_recovery_context

    # 6. 验证会话状态已恢复为 waiting_for_input
    assert store.read_meta(session.id).status == "waiting_for_input"
