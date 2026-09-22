"""
影子快照回滚测试（S9 Part C）—— ShadowStore 账本语义 + invoke_tool 挂点集成

【学习要点】
1. 回滚测试的断言不是"文件回来了"，而是"回来的是哪一个版本"：
   几乎每个用例都先造一个中间态（写坏的内容 / 外部改动 / 反向快照），
   只有对照物存在，"restore 还原到最近一次变更前"的语义才可证伪。
2. 两段式各测一头：prepare 不 commit 不留账（写失败不留幽灵记录），
   commit 前失败重试不重拍（否则抓到 attempt-1 的半损坏状态，回滚作废）——
   集成用例用"第一次尝试写坏再抛"的 flaky 工具把这条钉死。
3. 降级路径全部要求"明说"：超限/对象丢失的 restore 返回 failed +
   detail，changes_view 的 captured/conflict 标志可观测——静默失败是回滚层最大的敌。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import iwan_claude.core.shadow.store as shadow_mod
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.types import ToolCallBlock
from iwan_claude.core.shadow import ShadowStore, get_active_shadow
from iwan_claude.core.tools.base import BaseTool, ToolResult
from iwan_claude.core.tools.invocation import invoke_tool
from iwan_claude.core.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def reset_shadow_ctx() -> Any:
    # 功能：每个测试进出都把 active-shadow contextvar 归零
    # 设计：invoke_tool 集成用例会 set_active_shadow——token 泄漏会把影子
    #       带进下一个测试，让"无沙箱基线"的断言（不该落账的用例）假绿或假红
    token = shadow_mod._ACTIVE_SHADOW.set(None)
    yield
    shadow_mod._ACTIVE_SHADOW.reset(token)


# 按测试目录惯例搭一个 ShadowStore：objects 与账本分居两目录（模拟会话级/ run 级）
def _store(tmp_path: Path, max_bytes: int = 5 * 1024 * 1024) -> ShadowStore:
    return ShadowStore(
        tmp_path / "shadow" / "objects",
        tmp_path / "runs" / "r1" / "file_changes.json",
        max_file_bytes=max_bytes,
    )


# ── prepare / commit：两段式记账 ──────────────────────────────────────────────


# 功能：验证 prepare+commit 的完整往返：还原精确回到写前内容，且账本工具名/指纹齐
# 设计：commit 的 after 指纹是冲突检测的基准，这里顺带断言它等于提交瞬间的
#       内容哈希——若实现写成"prepare 时的哈希"，外部改动场景会全瞎
def test_prepare_commit_restore_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    f = tmp_path / "hello.txt"
    f.write_text("orig", encoding="utf-8")
    pending = store.prepare([str(f)])
    assert len(pending) == 1
    f.write_text("new content", encoding="utf-8")
    store.commit(pending, "write_file")

    ledger = store.ledger()
    assert len(ledger) == 1
    assert ledger[0]["tool"] == "write_file"
    assert ledger[0]["captured"] is True and ledger[0]["was_new"] is False
    assert ledger[0]["after"] == shadow_mod.hashlib.sha256(
        b"new content").hexdigest()

    results = store.restore([str(f)])
    assert results[0]["status"] == "restored"
    assert f.read_text(encoding="utf-8") == "orig"


# 功能：验证 prepare 不落账、commit 后才出现账本文件——"写失败不留幽灵记录"
# 设计：只看 ledger() 长度不够（空文件与无文件语义不同），断言账本文件
#       不存在才证明未 commit 的工具调用对 files.list 完全不可见
def test_prepare_without_commit_leaves_no_ledger(tmp_path: Path) -> None:
    store = _store(tmp_path)
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    pending = store.prepare([str(f)])
    assert pending and store.ledger() == []
    assert not store.ledger_path.exists()
    store.commit(pending, "write_file")
    assert store.ledger_path.exists() and len(store.ledger()) == 1


# 功能：验证新建文件的墓碑（was_new + blob=None）在还原时执行删除
# 设计：新建文件没有"前内容"可还原，没有墓碑标记就永远撤不掉；
#       还原后断言文件消失而非内容为空——删除与清空是两种不同的副作用
def test_new_file_tombstone_restore_deletes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    f = tmp_path / "fresh.txt"
    pending = store.prepare([str(f)])
    assert pending[0]["was_new"] is True and pending[0]["blob"] is None
    f.write_text("created", encoding="utf-8")
    store.commit(pending, "write_file")

    results = store.restore([str(f)])
    assert results[0]["status"] == "restored"
    assert not f.exists()


# 功能：验证同路径去重与 .iwan/backups 产物过滤
# 设计：multi_edit 会对同一路径报多条影响面、写工具自动备份会在
#       .iwan/backups 下生成产物——两者若入账，账本自我膨胀且备份套备份
def test_prepare_dedups_and_filters_backup_artifacts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    f = tmp_path / "dup.txt"
    f.write_text("v", encoding="utf-8")
    assert len(store.prepare([str(f), str(f), str(f.resolve())])) == 1
    bak = tmp_path / ".iwan" / "backups" / "dup.txt.bak"
    assert store.prepare([str(bak)]) == []


# 功能：验证内容寻址存储天然跨文件去重（同内容两文件只落一个 blob）
# 设计：objects 目录的文件数是最直接的去重证据；内容寻址 + "存在即跳过"
#       让重复内容零成本，这也是会话级共享 objects 目录能跨 run 省空间的根因
def test_content_addressed_dedup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("same", encoding="utf-8")
    b.write_text("same", encoding="utf-8")
    store.commit(store.prepare([str(a), str(b)]), "copy_file")
    objs = list((tmp_path / "shadow" / "objects").iterdir())
    assert len(objs) == 1


# ── restore：冲突检测 / 降级 / 反向快照链 ─────────────────────────────────────


# 功能：验证 restore 后"再还原一次"回到被还原前的状态（反向快照可撤销链）
# 设计：还原本身是一次写操作，若不拍反向快照，用户点错就无法回头；
#       两段断言（orig → new → orig → new）证明链每一环都可逆
def test_restore_writes_reverse_snapshot_chain(tmp_path: Path) -> None:
    store = _store(tmp_path)
    f = tmp_path / "chain.txt"
    f.write_text("orig", encoding="utf-8")
    pending = store.prepare([str(f)])
    f.write_text("new", encoding="utf-8")
    store.commit(pending, "write_file")

    store.restore([str(f)])
    assert f.read_text(encoding="utf-8") == "orig"
    assert store.ledger()[-1]["tool"] == "restore"
    store.restore([str(f)])
    assert f.read_text(encoding="utf-8") == "new"


# 功能：验证外部改动被 changes_view 标记 conflict，restore 默认跳过、force 强制
# 设计：冲突检测靠 commit 时盖的 after 指纹对比当前盘上哈希——
#       跳过时断言文件内容分毫未动，证明"不动盘"而非"动了再算失败"
def test_conflict_detection_skip_and_force(tmp_path: Path) -> None:
    store = _store(tmp_path)
    f = tmp_path / "ext.txt"
    f.write_text("orig", encoding="utf-8")
    pending = store.prepare([str(f)])
    f.write_text("new", encoding="utf-8")
    store.commit(pending, "write_file")          # after = hash("new")
    f.write_text("user edited by hand", encoding="utf-8")  # 外部改动

    view = store.changes_view()
    assert len(view) == 1 and view[0]["conflict"] is True

    results = store.restore([str(f)])
    assert results[0]["status"] == "skipped"
    assert f.read_text(encoding="utf-8") == "user edited by hand"

    results = store.restore([str(f)], force=True)
    assert results[0]["status"] == "restored"
    assert f.read_text(encoding="utf-8") == "orig"


# 功能：验证超限文件显式降级：captured=False 留痕入账、restore 返回 failed
# 设计："拍不了"与"没发生"必须可区分——账本若无痕，用户会以为新建可删；
#       reason 进 detail 是为了 files list 面板能把原因说给人看
def test_oversize_file_degrades_loudly(tmp_path: Path) -> None:
    store = _store(tmp_path, max_bytes=10)
    f = tmp_path / "big.txt"
    f.write_text("x" * 20, encoding="utf-8")
    pending = store.prepare([str(f)])
    assert pending[0]["captured"] is False and pending[0]["reason"] == "too_large"
    store.commit(pending, "write_file")

    view = store.changes_view()
    assert view[0]["captured"] is False
    assert view[0]["conflict"] is False  # 没基准就不谎报冲突

    results = store.restore([str(f)])
    assert results[0]["status"] == "failed"
    assert "too_large" in results[0]["detail"]
    assert f.read_text(encoding="utf-8") == "x" * 20


# 功能：验证快照对象丢失时 restore 报 failed 而非谎称 restored
# 设计：手工删掉 objects 目录模拟对象丢失；restore 会先拍反向快照（重建
#       objects），所以断言点放在"目标旧 blob 缺失"的返回状态上——
#       账面谎报比失败更糟，这是 apply 循环里 read_blob is None 分支的回归钉
def test_missing_blob_reports_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    f = tmp_path / "gone.txt"
    f.write_text("orig", encoding="utf-8")
    pending = store.prepare([str(f)])
    f.write_text("new", encoding="utf-8")
    store.commit(pending, "write_file")           # before=sha(orig), after=sha(new)
    for obj in (tmp_path / "shadow" / "objects").iterdir():
        obj.unlink()                              # 模拟对象被外部清理

    results = store.restore([str(f)])
    assert results[0]["status"] == "failed"
    assert "missing" in results[0]["detail"]
    assert f.read_text(encoding="utf-8") == "new"


# 功能：验证账本文件损坏时降级为空账自愈，新 commit 能重建可用账本
# 设计：fail-closed 方向的又一体现——账本坏 = "无可回滚"而非崩溃；
#       随后 commit 从空账续写证明运行不被一条坏记录永久毒化
def test_corrupt_ledger_self_heals(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    store.ledger_path.write_text("{ broken json", encoding="utf-8")
    assert store.ledger() == [] and store.changes_view() == []
    f = tmp_path / "x.txt"
    f.write_text("v", encoding="utf-8")
    store.commit(store.prepare([str(f)]), "write_file")
    assert len(store.ledger()) == 1


# ── ContextVar 会话隔离 ───────────────────────────────────────────────────────


# 功能：验证 set/reset 配对能把 active-shadow 精确还原（含嵌套两层）
# 设计：runner 按 run set/finally reset，子代理嵌套时 token 链必须逐层
#       回退——用"内层 reset 后仍见外层 store"钉住嵌套语义
def test_active_shadow_contextvar_nesting() -> None:
    outer, inner = object(), object()  # 类型无关，只验存取
    assert get_active_shadow() is None
    t1 = shadow_mod.set_active_shadow(outer)  # type: ignore[arg-type]
    t2 = shadow_mod.set_active_shadow(inner)  # type: ignore[arg-type]
    assert get_active_shadow() is inner
    shadow_mod.reset_active_shadow(t2)
    assert get_active_shadow() is outer
    shadow_mod.reset_active_shadow(t1)
    assert get_active_shadow() is None


# ── invoke_tool 挂点集成 ──────────────────────────────────────────────────────


class _ShadowWriteTool(BaseTool):
    """带 estimate_affected_paths 的写桩工具，行为由 attempts 脚本驱动"""
    name = "shadow_write"
    description = "writes path=content; may fail per attempts"
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    # 初始化调用计数与尝试脚本（fail_times=前 N 次尝试先写坏再抛）
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.attempts = 0
        self.prepare_calls = 0

    # 声明本次调用会影响哪些路径（影子层的唯一输入）
    async def estimate_affected_paths(self, params: dict[str, Any]) -> list[str]:
        self.prepare_calls += 1
        return [str(params["path"])]

    # 按 attempts 脚本执行写入：失败尝试先污染文件再抛异常模拟半截写
    async def invoke(self, params: dict[str, Any]) -> ToolResult:
        self.attempts += 1
        f = Path(str(params["path"]))
        f.write_text(f"corrupt-{self.attempts}", encoding="utf-8")
        if self.attempts <= self.fail_times:
            raise RuntimeError(f"attempt {self.attempts} exploded")
        f.write_text(str(params["content"]), encoding="utf-8")
        return ToolResult(content="ok")


# 驱动一次无权限检查的 invoke_tool，返回结果与事件
async def _invoke(tool: BaseTool, tool_call: ToolCallBlock) -> ToolResult:
    registry = ToolRegistry()
    registry.register(tool)
    return await invoke_tool(registry, tool_call, EventBus(), run_id="r1", timeout=5.0)


# 功能：验证重试场景下影子只拍一次且还原目标是"最早的原版"而非 attempt-1 的残骸
# 设计：flaky 工具第一次尝试先写坏再抛、第二次成功——若实现把 prepare 放进
#       重试循环，before blob 会是 "corrupt-1"；断言还原后内容是 "orig"
#       才能证明捕获发生在任何写入尝试之前（这是 C1 最核心的时序不变式）
async def test_invoke_tool_captures_once_before_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import iwan_claude.core.tools.invocation as inv_mod
    monkeypatch.setattr(inv_mod, "_RETRY_BASE_S", 0.0)
    store = _store(tmp_path)
    f = tmp_path / "rw.txt"
    f.write_text("orig", encoding="utf-8")
    token = shadow_mod.set_active_shadow(store)
    try:
        tool = _ShadowWriteTool(fail_times=1)
        result = await _invoke(tool, ToolCallBlock(
            id="t1", name="shadow_write",
            input={"path": str(f), "content": "final"}))
    finally:
        shadow_mod.reset_active_shadow(token)
    assert not result.is_error
    assert tool.prepare_calls == 1
    assert f.read_text(encoding="utf-8") == "final"
    store.restore([str(f)])
    assert f.read_text(encoding="utf-8") == "orig"


class _FailingWriteTool(_ShadowWriteTool):
    """总是以不可重试错误收场的写桩（validation_error 不走重试路径）"""

    name = "failing_write"

    # 无论第几次尝试都返回 is_error 的 validation_error 结果
    async def invoke(self, params: dict[str, Any]) -> ToolResult:
        self.attempts += 1
        return ToolResult(content="nope", is_error=True,
                          error_type="validation_error")


# 功能：验证工具最终失败时不落账（账本文件根本不生成）——写失败不留幽灵记录
# 设计：is_error 走非重试的 validation_error 分支直达 _fail 路径；
#       断言 ledger_path 不存在而非空数组，连空文件都不该在"什么都没改"时留下
async def test_invoke_tool_failure_leaves_no_ledger(tmp_path: Path) -> None:
    store = _store(tmp_path)
    f = tmp_path / "never.txt"
    token = shadow_mod.set_active_shadow(store)
    try:
        result = await _invoke(_FailingWriteTool(), ToolCallBlock(
            id="t1", name="failing_write",
            input={"path": str(f), "content": "x"}))
    finally:
        shadow_mod.reset_active_shadow(token)
    assert result.is_error
    assert not store.ledger_path.exists()
    assert store.ledger() == []


# 功能：验证无 active shadow（普通 run / 子代理未启用）时 invoke_tool 一切照旧
# 设计：contextvar 默认 None 是"零开销旁路"承诺的载体——挂点代码不得在
#       None 分支产生任何异常或副作用，用成功写 + 无账本文件证明旁路干净
async def test_invoke_tool_without_active_shadow(tmp_path: Path) -> None:
    f = tmp_path / "plain.txt"
    tool = _ShadowWriteTool()
    result = await _invoke(tool, ToolCallBlock(
        id="t1", name="shadow_write", input={"path": str(f), "content": "v"}))
    assert not result.is_error
    assert f.read_text(encoding="utf-8") == "v"
    assert tool.prepare_calls == 0  # 无 store 时整个挂点短路，连 estimate 都不调


# 功能：验证没有 estimate_affected_paths 的普通工具在影子开启时安全跳过
# 设计：hasattr 探测是挂点对全体非写工具的兼容契约——用无该属性的 echo 桩
#       跑成功路径，账本保持为空且工具结果不受扰，防止未来把探测写成白名单
async def test_invoke_tool_skips_tools_without_estimator(
    tmp_path: Path,
) -> None:
    class _EchoTool(BaseTool):
        name = "echo_x"
        description = "no affected paths"
        input_schema: dict[str, object] = {
            "type": "object", "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        }

        # 回显消息（不碰文件系统，也没有 estimate_affected_paths）
        async def invoke(self, params: dict[str, Any]) -> ToolResult:
            return ToolResult(content=str(params.get("msg", "")))

    store = _store(tmp_path)
    token = shadow_mod.set_active_shadow(store)
    try:
        result = await _invoke(_EchoTool(), ToolCallBlock(
            id="t1", name="echo_x", input={"msg": "hi"}))
    finally:
        shadow_mod.reset_active_shadow(token)
    assert not result.is_error and result.content == "hi"
    assert not store.ledger_path.exists()
