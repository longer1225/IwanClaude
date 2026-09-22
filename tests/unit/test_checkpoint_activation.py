"""S9-R1 回溯激活测试：checkpoint↔run 交叉索引 / 默认后端 / DB 路径锚定 / sqlite 保留裁剪

覆盖设计文档 docs/plans/S9_security_rollback_plan.md §C.4 的四个验收点。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from iwan_claude.core.config import AgentConfig, IwanConfig, resolve_checkpoint_db_path
from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.langgraph_loop import LangGraphAgentLoop
from iwan_claude.core.llm.types import LlmResponse, UsageStats
from iwan_claude.core.runner import AgentRunner, prune_sqlite_checkpoints
from iwan_claude.core.tools.registry import ToolRegistry


def _provider() -> MagicMock:
    p = MagicMock()
    p.chat = AsyncMock(return_value=LlmResponse(
        stop_reason="end_turn", tool_calls=[], text="hello",
        usage=UsageStats(input_tokens=10, output_tokens=20),
    ))
    return p


async def _one_run(saver: Any, session_id: str, run_id: str) -> None:
    loop = LangGraphAgentLoop(_provider(), ToolRegistry(), EventBus(),
                              checkpointer=saver, session_id=session_id)
    ctx = ExecutionContext(run_id=run_id, goal="g", max_steps=3)
    ctx.messages = [{"role": "user", "content": "hi"}]
    await loop.run(ctx)


# 功能：同 thread 两次 run 后，最新一批 checkpoint 的 run_id 标注为第二次运行（交叉索引生效）
# 设计：走真实 LangGraphAgentLoop+InMemorySaver 全链路而非手插通道，证明"通道→checkpoint→
#      runner.list_checkpoints"三层贯通；只断言"存在 run_id==r2 的条目"，容忍 superstep 数漂移
async def test_run_id_attribution_in_list() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()
    await _one_run(saver, "sess-r1", "run-A")
    await _one_run(saver, "sess-r1", "run-B")
    runner = AgentRunner(IwanConfig(), checkpointer=saver)  # type: ignore[arg-type]
    cps = await runner.list_checkpoints("sess-r1")
    assert cps, "checkpoint 列表不应为空"
    assert any(c["run_id"] == "run-B" for c in cps)
    # 最新 checkpoint（按 step 排序尾部）应属于第二次运行
    assert cps[-1]["run_id"] == "run-B"


# 功能：checkpoint_backend 默认值必须是 memory（回溯开箱可用），且 sqlite keep_last 默认 50
# 设计：默认值是本轮"激活"决策的载体，用裸 IwanConfig() 直接锁死，防止未来无感回退成 none
def test_defaults_activated() -> None:
    cfg = IwanConfig()
    assert cfg.agent.checkpoint_backend == "memory"
    assert cfg.agent.checkpoint_keep_last == 50


# 功能：相对 checkpoint_db_path 锚定 IWAN_SESSIONS_DIR 父级，绝对路径原样返回
# 设计：懒启动使 daemon cwd 不可预测，锚 cwd 的旧行为会让 DB 文件漂移；
#      两个方向（相对→锚定、绝对→不动）都要锁，防止改动过头或不足
def test_resolve_db_path(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("IWAN_SESSIONS_DIR", str(tmp_path / "sessions"))
    agent = AgentConfig(checkpoint_db_path="checkpoints.db")
    assert resolve_checkpoint_db_path(agent) == tmp_path / "checkpoints.db"
    absolute = tmp_path / "custom" / "cp.sqlite"
    agent2 = AgentConfig(checkpoint_db_path=str(absolute))
    assert resolve_checkpoint_db_path(agent2) == absolute


# 功能：prune_sqlite_checkpoints 每 thread 保留最近 keep_last 个且删掉对应 writes 记录
# 设计：真实 AsyncSqliteSaver 上跑同 thread 两次 run（checkpoint 数 >3），prune(3) 后
#      直接查底表验证剩余条数与"保留的是最新一簇"（run_id 含 run-B 的仍在），
#      同时验证 writes 表无孤儿行——只查 checkpoints 会漏掉 pending-writes 泄漏
async def test_prune_sqlite_checkpoints(tmp_path: Path) -> None:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db = tmp_path / "cp.db"
    async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
        await _one_run(saver, "thread-prune", "run-A")
        await _one_run(saver, "thread-prune", "run-B")
        await saver.setup()
        cur = await saver.conn.execute(
            "select checkpoint_id from checkpoints where thread_id='thread-prune'"
            " order by checkpoint_id desc"
        )
        all_ids = [r[0] for r in await cur.fetchall()]
        assert len(all_ids) > 3, f"前置不足：只有 {len(all_ids)} 个 checkpoint"

        removed = await prune_sqlite_checkpoints(saver, keep_last=3)
        assert removed == len(all_ids) - 3

        cur = await saver.conn.execute(
            "select checkpoint_id from checkpoints where thread_id='thread-prune'"
            " order by checkpoint_id desc"
        )
        kept = [r[0] for r in await cur.fetchall()]
        assert kept == all_ids[:3]  # 保留的必须是最新 3 个（id 单调 = 时间序）
        # writes 表不得残留指向已删 checkpoint 的孤儿行
        cur = await saver.conn.execute(
            "select count(*) from writes w where not exists"
            " (select 1 from checkpoints c where c.thread_id=w.thread_id"
            "   and c.checkpoint_ns=w.checkpoint_ns and c.checkpoint_id=w.checkpoint_id)"
        )
        assert (await cur.fetchone())[0] == 0


# 功能：keep_last<=0 关闭裁剪，一个都不删
# 设计：这是配置逃生门的唯一验证点；若实现把 0 当"全删"将是灾难性反义，必须显式锁
async def test_prune_disabled_by_zero(tmp_path: Path) -> None:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        await _one_run(saver, "t", "r")
        assert await prune_sqlite_checkpoints(saver, keep_last=0) == 0


# 功能：agent.checkpoint_keep_last 只接受整数（bool/字符串在配置加载期硬拒）
# 设计：与 timeout_sec 同法挡 bool（int 子类的经典陷阱）；错误文案含键名保证可定位
def test_keep_last_validation(tmp_path: Path, monkeypatch: Any) -> None:
    import pytest

    def load(body: str) -> Any:
        toml = tmp_path / "c.toml"
        toml.write_text(body, encoding="utf-8")
        monkeypatch.setenv("IWAN_CONFIG", str(toml))
        from iwan_claude.core.config import get_config

        return get_config()

    with pytest.raises(SystemExit, match="checkpoint_keep_last"):
        load('[agent]\ncheckpoint_keep_last = "many"\n')
    with pytest.raises(SystemExit, match="checkpoint_keep_last"):
        load("[agent]\ncheckpoint_keep_last = true\n")
    cfg = load("[agent]\ncheckpoint_keep_last = -1\n")
    assert cfg.agent.checkpoint_keep_last == -1  # 负数是合法"关闭裁剪"，不是配置错误
