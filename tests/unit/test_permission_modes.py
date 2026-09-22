"""
五态权限模式（default/acceptEdits/plan/auto/bypassPermissions）行为测试

【学习要点】
1. 模式只改写审批链的 Tier3~6：测试的断言重心不是"模式放行了什么"，
   而是"模式没翻掉什么"——deny 地板、强制 ASK、hook DENY 在 bypass 下
   仍然有效，这是对齐 Claude Code 的核心不变式（bypass 拆弹窗不拆地板）。
2. per-session 模式的隔离性靠"两个会话同一工具不同结果"来证明——
   若实现仍是全局开关，这种交叉断言必碎，而单会话断言永远测不出来。
3. 兼容层测试钉的是映射契约（off→default、read_only→auto、on→acceptEdits），
   旧 RPC/配置保留一版期间，任何一侧改映射都会在这里红灯。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.hooks import HookRegistry, parse_hook_entries
from iwan_claude.core.permissions.manager import PermissionManager
from iwan_claude.core.permissions.rules import PermissionRules


@pytest.fixture(autouse=True)
def reset_sandbox() -> Any:
    # 功能：每个测试重置沙箱全局与 contextvar，保证模式评估从无沙箱基线出发
    # 设计：Tier2 的沙箱强制 ASK 会先于模式判断抢答，不清理会把
    #       "bypass 保留强制 ASK"和"模式正常放行"两类断言搅浑
    import iwan_claude.core.sandbox as sb_module
    sb_module._sandbox_manager = None
    token = sb_module._active_sandbox.set(None)
    sb_module._sandbox_by_session.clear()
    yield
    sb_module._active_sandbox.reset(token)
    sb_module._sandbox_manager = None
    sb_module._sandbox_by_session.clear()


# 收集 permission.requested 事件的 (emitted, emitter) 对（沿用既有测试的套路）
async def _collect_emitted() -> tuple[list[dict[str, Any]], Any]:
    emitted: list[dict[str, Any]] = []

    async def emitter(ev: dict[str, Any]) -> None:
        emitted.append(ev)

    return emitted, emitter


# 让 ASK 弹窗在 emitter 里同步回答——check_and_wait 先存 _pending 再 await
# emitter，此刻 Future 必然存在（区别于定时 sleep 后 respond 的竞态写法）
def _responder(mgr: PermissionManager, tool_use_id: str, decision: str) -> Any:
    async def emitter(ev: dict[str, Any]) -> None:
        mgr.respond(tool_use_id, decision)
    return emitter


# ── 构造与校验 ────────────────────────────────────────────────────────────────


# 功能：验证 default_mode 构造参数合法值生效、非法值当场 ValueError
# 设计：daemon 启动把 [permission] mode 直接喂给它——坏值必须在构造期炸，
#       否则运行期 get_permission_mode 会返回五态之外的哑值让模式判断全线静默失效
def test_ctor_validates_default_mode() -> None:
    mgr = PermissionManager(default_mode="plan")
    assert mgr.get_permission_mode("any") == "plan"
    with pytest.raises(ValueError):
        PermissionManager(default_mode="turbo")


# 功能：验证 set_permission_mode 返回切换前的值且 per-session 覆盖优先于全局默认
# 设计：previous_mode 是事件广播的字段来源；覆盖表实现若误写回默认值，
#       "A 会话进 plan、B 会话保持默认"的隔离前提就不成立
def test_set_permission_mode_returns_previous_and_scopes_per_session() -> None:
    mgr = PermissionManager(default_mode="default")
    prev = mgr.set_permission_mode("acceptEdits", "s1")
    assert prev == "default"
    assert mgr.get_permission_mode("s1") == "acceptEdits"
    assert mgr.get_permission_mode("s2") == "default"
    # None → 改全局默认，不碰已有覆盖
    prev2 = mgr.set_permission_mode("plan")
    assert prev2 == "default"
    assert mgr.get_permission_mode("s1") == "acceptEdits"  # 覆盖仍然赢
    assert mgr.get_permission_mode("s3") == "plan"


# 功能：验证 set_permission_mode 拒绝五态之外的模式值
# 设计：与 default_mode 构造校验同一族不变式——模式拼错宁炸不哑；
#       若放行，check_and_wait 的所有 mode 分支全部落空等于"没有模式"
def test_set_permission_mode_validates_input() -> None:
    mgr = PermissionManager()
    with pytest.raises(ValueError):
        mgr.set_permission_mode("yolo", "s1")


# ── 五态 × 三类工具决策矩阵 ───────────────────────────────────────────────────


# 功能：验证 default 模式的基线行为（只读 ALLOW / 写与 bash ASK）
# 设计：其余模式测试都以此为参照系——先钉死"什么都没配"时三类工具各走哪条
#       分支，后面任何一格的翻转才有对照；ASK 用 deny_once 应答取反向证据
async def test_mode_default_matrix() -> None:
    mgr = PermissionManager()
    emitted, emitter = await _collect_emitted()
    ok_read, d_read = await mgr.check_and_wait("t1", "read_file", {"path": "a"}, "s1", emitter)
    assert (ok_read, d_read) == (True, "auto_allow")
    assert emitted == []  # 只读不该弹窗

    ok, d = await mgr.check_and_wait(
        "t2", "write_file", {"path": "x.txt", "content": "c"}, "s1",
        _responder(mgr, "t2", "deny_once"))
    assert (ok, d) == (False, "deny_once")  # 弹窗发生 → 证明 default 对写是 ASK
    ok, d = await mgr.check_and_wait(
        "t3", "bash", {"command": "ls"}, "s1", _responder(mgr, "t3", "deny_once"))
    assert (ok, d) == (False, "deny_once")


# 功能：验证 acceptEdits 放行白名单写工具但 bash 依旧 ASK（Edits 不解锁 shell）
# 设计：bash 恒被 _mode_auto_allows 第一行挡回 False，与白名单成员无关——
#       单独钉一条，防止有人日后"顺手"把 bash 也收进写白名单
async def test_mode_accept_edits_allows_writes_not_bash() -> None:
    mgr = PermissionManager()
    mgr.set_permission_mode("acceptEdits", "s1")
    emitted, emitter = await _collect_emitted()
    ok, d = await mgr.check_and_wait(
        "t1", "write_file", {"path": "x.txt", "content": "c"}, "s1", emitter)
    assert (ok, d) == (True, "auto_allow")
    assert emitted == []
    ok, d = await mgr.check_and_wait(
        "t2", "bash", {"command": "ls"}, "s1", _responder(mgr, "t2", "deny_once"))
    assert (ok, d) == (False, "deny_once")


# 功能：验证 plan 模式对只读白名单外的工具是 DENY 而不是 ASK
# 设计：decision 字符串必须是专用 "plan_mode"（模型据此改道、UI 据此解释）；
#       若实现 fall-through 到 ASK，弹窗会把只读探索场景变成审批轰炸
async def test_mode_plan_denies_non_readonly() -> None:
    mgr = PermissionManager()
    mgr.set_permission_mode("plan", "s1")
    emitted, emitter = await _collect_emitted()
    ok, d = await mgr.check_and_wait("t1", "bash", {"command": "ls"}, "s1", emitter)
    assert (ok, d) == (False, "plan_mode")
    ok, d = await mgr.check_and_wait(
        "t2", "write_file", {"path": "x.txt", "content": "c"}, "s1", emitter)
    assert (ok, d) == (False, "plan_mode")
    assert emitted == []  # DENY 不弹窗：用户没在看终端，ask 只会制造噪音
    ok, d = await mgr.check_and_wait("t3", "read_file", {"path": "a"}, "s1", emitter)
    assert (ok, d) == (True, "auto_allow")


# 功能：验证 plan 的 DENY 早于 always_allow 缓存——模式是策略不是用户意愿
# 设计：先灌一次 always_allow 再切 plan：若 plan 判断放在缓存之后，
#       残留缓存会漏执行写操作，这是"策略层可被偏好层洗白"的经典回归
async def test_mode_plan_beats_always_allow_cache() -> None:
    mgr = PermissionManager()
    ok, d = await mgr.check_and_wait(
        "t1", "write_file", {"path": "x.txt", "content": "c"}, "s1",
        _responder(mgr, "t1", "always_allow"))
    assert (ok, d) == (True, "always_allow")
    mgr.set_permission_mode("plan", "s1")
    ok, d = await mgr.check_and_wait(
        "t2", "write_file", {"path": "x.txt", "content": "c"}, "s1",
        lambda ev: asyncio.sleep(0))
    assert (ok, d) == (False, "plan_mode")


# 功能：验证 auto 模式只放只读工具，写工具仍 ASK（白名单近似，不是分类器）
# 设计：auto≈官方 classifier 的下位替代——write_file 不在只读白名单，
#       必须照常弹窗；若这里 ALLOW 了，就是拿启发式冒充"模型判断"
async def test_mode_auto_is_readonly_only() -> None:
    mgr = PermissionManager()
    mgr.set_permission_mode("auto", "s1")
    emitted, emitter = await _collect_emitted()
    ok, d = await mgr.check_and_wait("t1", "read_file", {"path": "a"}, "s1", emitter)
    assert (ok, d) == (True, "auto_allow")
    ok, d = await mgr.check_and_wait(
        "t2", "write_file", {"path": "x.txt", "content": "c"}, "s1",
        _responder(mgr, "t2", "deny_once"))
    assert (ok, d) == (False, "deny_once")


# 功能：验证 bypassPermissions 直接放行 bash/write_file（decision="bypass_allow"）
# 设计：三类工具一次测齐；decision 串是审计与"auto_allow"（默认策略）的区分点——
#       放行必须留痕"是模式放的"而非"本来就是 ALLOW"
async def test_mode_bypass_allows_everything_askable() -> None:
    mgr = PermissionManager()
    mgr.set_permission_mode("bypassPermissions", "s1")
    emitted, emitter = await _collect_emitted()
    for tid, tool, params in (
        ("t1", "bash", {"command": "ls"}),
        ("t2", "write_file", {"path": "x.txt", "content": "c"}),
        ("t3", "read_file", {"path": "a"}),
    ):
        ok, d = await mgr.check_and_wait(tid, tool, params, "s1", emitter)
        assert (ok, d) == (True, "bypass_allow"), tool
    assert emitted == []


# ── bypass 的地板不变式（本批次最重要的安全测试）─────────────────────────────


# 功能：验证 deny 规则在 bypassPermissions 下仍是 DENY
# 设计：对齐官方 "deny rules still apply in bypass mode"——若 bypass 的
#       ALLOW 被实现成 Tier0 短路（"跳过一切评估"），rm:* 在这里就会被翻，
#       一个误触的 bypass 就成了全系统旁路，此断言是该回归的唯一哨兵
async def test_bypass_cannot_flip_deny_rule() -> None:
    mgr = PermissionManager(rules=PermissionRules(deny=["bash(rm:*)"]))
    mgr.set_permission_mode("bypassPermissions", "s1")
    ok, d = await mgr.check_and_wait(
        "t1", "bash", {"command": "rm x"}, "s1", lambda ev: asyncio.sleep(0))
    assert (ok, d) == (False, "auto_deny")


# 功能：验证规则显式 ask 在 bypass 下仍弹窗（强制 ASK 不可被模式洗白）
# 设计：bypass 豁免的只有"会弹问的默认档"；ask 规则是用户预先声明的
#       "这一类必须问"，被 bypass 静默掉等于用户被自己的配置背刺
async def test_bypass_keeps_forced_ask_rules() -> None:
    mgr = PermissionManager(rules=PermissionRules(ask=["bash(npm:*)"]))
    mgr.set_permission_mode("bypassPermissions", "s1")
    emitted, emitter = await _collect_emitted()

    async def respond(ev: dict[str, Any]) -> None:
        await emitter(ev)
        mgr.respond("t1", "allow_once")

    ok, d = await mgr.check_and_wait("t1", "bash", {"command": "npm install"}, "s1", respond)
    assert (ok, d) == (True, "allow_once")  # 人工应答的串，不是 bypass_allow
    assert len(emitted) == 1 and emitted[0]["type"] == "permission.requested"


# 功能：验证 PreToolUse hook DENY 在 bypassPermissions 下依然拦得住
# 设计：hook 是 Tier0——模式连它的 ASK 都翻不了，DENY 更不行；
#       用真实 python 脚本 exit 2，同时验证 hook 链与模式链的先后顺序
async def test_bypass_cannot_flip_hook_deny(tmp_path: Path) -> None:
    script = tmp_path / "guard.py"
    script.write_text("import sys; sys.stderr.write('halt'); sys.exit(2)", encoding="utf-8")
    specs = parse_hook_entries(
        [{"event": "PreToolUse", "command": f'"{sys.executable}" "{script}"'}])
    mgr = PermissionManager(hooks=HookRegistry(specs))
    mgr.set_permission_mode("bypassPermissions", "s1")
    ok, d = await mgr.check_and_wait(
        "t1", "bash", {"command": "ls"}, "s1", lambda ev: asyncio.sleep(0))
    assert (ok, d) == (False, "hook_deny")


# 功能：验证 legacy deny_patterns 地板在 bypass 下仍生效
# 设计：不依赖规则引擎——legacy 评估链的 deny 同样属于 Tier1，
#       防止 bypass 判断被插到"只看新规则引擎"的偏窄位置
async def test_bypass_keeps_legacy_deny_patterns() -> None:
    from iwan_claude.core.permissions.policy import PermissionDecision, ToolPolicy
    mgr = PermissionManager(
        policies={"bash": ToolPolicy(default=PermissionDecision.ASK, deny_patterns=["rm"])})
    mgr.set_permission_mode("bypassPermissions", "s1")
    ok, d = await mgr.check_and_wait(
        "t1", "bash", {"command": "rm -rf /"}, "s1", lambda ev: asyncio.sleep(0))
    assert (ok, d) == (False, "auto_deny")


# ── per-session 隔离与清理 ────────────────────────────────────────────────────


# 功能：验证两个会话各自独立评估——A 切 acceptEdits 不放行 B 的写操作
# 设计：交叉断言是全局开关实现的天敌：B 会话若在 A 的覆盖下被放行，
#       (False,"deny_once") 变 (True,"auto_allow")，弹窗根本没发生，respond 落空超时
async def test_mode_isolation_between_sessions() -> None:
    mgr = PermissionManager()
    mgr.set_permission_mode("acceptEdits", "sA")
    ok, d = await mgr.check_and_wait(
        "t1", "write_file", {"path": "a.txt", "content": "x"}, "sA",
        lambda ev: asyncio.sleep(0))
    assert (ok, d) == (True, "auto_allow")
    # B 会话仍在 default：同样的写必须弹窗
    ok, d = await mgr.check_and_wait(
        "t2", "write_file", {"path": "b.txt", "content": "x"}, "sB",
        _responder(mgr, "t2", "deny_once"))
    assert (ok, d) == (False, "deny_once")


# 功能：验证 cancel_session 清除该会话的模式覆盖（防无界字典泄漏）
# 设计：session 结束但 _modes 残留，新会话若复用同一 ID（重启/测试环境）
#       会继承上一个会话的 plan——清不清除是正确性问题而不只是内存问题
def test_cancel_session_clears_mode() -> None:
    mgr = PermissionManager()
    mgr.set_permission_mode("plan", "s1")
    assert mgr.get_permission_mode("s1") == "plan"
    mgr.cancel_session("s1")
    assert mgr.get_permission_mode("s1") == "default"


# ── legacy auto_mode 兼容映射 ─────────────────────────────────────────────────


# 功能：验证 set_auto_mode 三态映射进五态、get_auto_mode 反推显示值
# 设计：off→default / read_only→auto / on→acceptEdits 与反表逐行钉死；
#       旧脚本只认三态字符串，新 UI 只认五态——映射是两代客户端唯一的共同语言
def test_legacy_auto_mode_mapping_roundtrip() -> None:
    mgr = PermissionManager()
    for legacy, mode in (("on", "acceptEdits"), ("read_only", "auto"), ("off", "default")):
        mgr.set_auto_mode(legacy)
        assert mgr.get_permission_mode("any") == mode
        assert mgr.get_auto_mode() == legacy


# 功能：验证全局默认模式对 legacy 入口同样生效（get_permission_mode 的回退链）
# 设计：set_auto_mode 改 _default_mode 而非覆盖表——未显式切换过的会话
#       应立刻看到新默认，旧行为（全局 auto）的语义在这里得到保留
def test_legacy_set_auto_mode_affects_unswitched_sessions() -> None:
    mgr = PermissionManager()
    mgr.set_permission_mode("plan", "s1")  # s1 有显式覆盖
    mgr.set_auto_mode("on")                # 只动全局默认
    assert mgr.get_permission_mode("s2") == "acceptEdits"
    assert mgr.get_permission_mode("s1") == "plan"  # 覆盖不被全局默认冲掉
