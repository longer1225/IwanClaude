"""
Layer 0 信任门测试（S9 Part A）—— TrustStore 持久化 + PermissionManager 信任地板

【学习要点】
1. 断言重心是"地板压过谁"：trust=deny 的价值在于 bypassPermissions 和
   always_allow 缓存都洗不白它——所以每条放行路径的测试都配一个 emitter
   抛 AssertionError 的桩，证明连弹窗都不该发生（deny 不协商）。
2. 兼容契约单独钉："未登记会话 = allow" 是刻意的不改变现状决策，
   它不是疏忽，测试写明它是防日后"顺手收紧"打破全部现有脚本。
3. 守护用例（guard）用包扫描代替硬编码清单：新加写工具忘了登记
   TRUST_DENY_FORBIDDEN_TOOLS 时，CI 在这里红，而不是在生产里漏。
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.permissions.manager import PermissionManager
from iwan_claude.core.permissions.policy import TRUST_DENY_FORBIDDEN_TOOLS
from iwan_claude.core.tools.base import BaseTool
from iwan_claude.core.trust import TrustStore, normalize_dir_key


@pytest.fixture(autouse=True)
def reset_sandbox() -> Any:
    # 功能：每个测试重置沙箱全局与 contextvar，保证权限评估从无沙箱基线出发
    # 设计：沿袭 test_permission_modes 的教训——Tier2.5 沙箱强制 ASK 会抢在
    #       信任地板的断言之前改变 decision 字符串，污染"bypass 也压不过 deny"的证明
    import iwan_claude.core.sandbox as sb_module
    sb_module._sandbox_manager = None
    token = sb_module._active_sandbox.set(None)
    sb_module._sandbox_by_session.clear()
    yield
    sb_module._active_sandbox.reset(token)
    sb_module._sandbox_manager = None
    sb_module._sandbox_by_session.clear()


# ── TrustStore：持久化语义 ────────────────────────────────────────────────────


# 功能：验证 set_decision/lookup 的 allow/deny 往返与"未决定目录返回 ask"
# 设计：直接查真实文件（非内存缓存）验证每次操作即时落盘的设计——
#       跨进程（CLI 与 daemon 各持一个 TrustStore）共享同一份状态靠的就是它
async def test_store_roundtrip_and_default_ask(tmp_path: Path) -> None:
    store = TrustStore(tmp_path / "trust.toml")
    assert store.lookup(tmp_path) == "ask"  # 文件不存在 = 没决定过
    store.set_decision(tmp_path / "foo", "allow")
    store.set_decision(tmp_path / "bar", "deny")
    assert store.lookup(tmp_path / "foo") == "allow"
    assert store.lookup(tmp_path / "bar") == "deny"
    assert store.lookup(tmp_path / "baz") == "ask"
    assert (tmp_path / "trust.toml").exists()


# 功能：验证祖先继承只向下（父决定覆盖子目录，子决定不影响父）且精确匹配优先
# 设计：三个方向各钉一条——若实现把继承做成双向 glob，"trust ~/code 的某个
#       子仓库"会意外放开整个 code 树；精确赢祖先则保证 deny 可以嵌在 allow 树里
async def test_store_inheritance_direction_and_exactness(tmp_path: Path) -> None:
    parent = tmp_path / "code"
    child = parent / "repo"
    other = tmp_path / "other"
    store = TrustStore(tmp_path / "trust.toml")
    store.set_decision(child, "allow")
    assert store.lookup(child / "src") == "allow"   # 父决定向下覆盖
    assert store.lookup(parent) == "ask"            # 不向上反噬
    assert store.lookup(other) == "ask"             # 不横向泄漏
    store.set_decision(parent, "deny")
    assert store.lookup(child) == "allow"           # 精确条目赢过祖先
    assert store.lookup(parent / "x") == "deny"     # 祖先覆盖其他子目录


# 功能：验证 inherit=False 时只认精确匹配，祖先的 allow 不再传染子目录
# 设计：与上一条共用路径布局、只翻转构造参数——两版对比证明开关确实
#       切在祖先查找那一步，而不是别的环节顺带造成的差异
async def test_store_inherit_off_requires_exact_match(tmp_path: Path) -> None:
    parent = tmp_path / "code"
    store = TrustStore(tmp_path / "trust.toml", inherit=False)
    store.set_decision(parent, "allow")
    assert store.lookup(parent) == "allow"
    assert store.lookup(parent / "repo") == "ask"


# 功能：验证损坏的 trust.toml 与认识不了的 decision 值都 fail-closed 退到 ask
# 设计：两种坏法分开钉——语法坏（整个文件不可读）与语义坏（值拼错），
#       信任层是安全配置，任何解析歧义必须降级为"更严的未决定态"而非 allow，
#       也不能把 daemon 拖崩（lookup 返回 ask 而非抛异常）
async def test_store_fail_closed_on_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "trust.toml"
    store = TrustStore(path)
    path.write_text(" [[[ not toml", encoding="utf-8")
    assert store.lookup(tmp_path) == "ask"
    assert store.entries() == {}
    path.write_text(
        '[trust."x"]\ndecision = "maybe"\n', encoding="utf-8",
    )
    assert store.lookup(path.parent / "x") == "ask"
    assert store.entries() == {}


# 功能：验证键归一化让不同拼写（尾斜杠/.. 段/大小写）落到同一个键
# 设计：Windows 文件系统大小写不敏感，改个盘符大小写就能绕过 deny 记录是
#       真实攻击面；用同一目录的多种字符串拼写交叉读写，证明键空间已收敛
async def test_store_key_normalization_dedups(tmp_path: Path) -> None:
    target = tmp_path / "Proj" / "Data"
    target.mkdir(parents=True)
    store = TrustStore(tmp_path / "trust.toml")
    # <target>/sub/.. —— resolve 后与 target 同路径
    store.set_decision(str(target / "sub" / ".."), "deny")
    assert store.lookup(str(target) + "/") == "deny"
    assert list(store.entries()) == [normalize_dir_key(target)]
    upper = str(target).upper()
    # 大小写不敏感只在 Windows 成立；POSIX 上原样覆盖，两条平台都应收敛到 allow
    store.set_decision(upper if Path(upper).exists() else str(target), "allow")
    assert store.entries()[normalize_dir_key(target)] == "allow"


# 功能：验证 set_decision("ask") 与 revoke 都是"撤销持久决定"，ValueError 挡非法值
# 设计：revoke 的返回值区分"删掉了"与"本来就没有"——CLI revoke 靠它决定
#       退出码；非法 decision 必须在写入前炸，脏数据一旦落盘就是解析期的 ask 风暴
async def test_store_revoke_and_validation(tmp_path: Path) -> None:
    store = TrustStore(tmp_path / "trust.toml")
    store.set_decision(tmp_path / "a", "allow")
    assert store.revoke(tmp_path / "a") is True
    assert store.revoke(tmp_path / "a") is False
    assert store.lookup(tmp_path / "a") == "ask"
    store.set_decision(tmp_path / "b", "deny")
    store.set_decision(tmp_path / "b", "ask")   # "ask" = 撤销
    assert store.lookup(tmp_path / "b") == "ask"
    with pytest.raises(ValueError):
        store.set_decision(tmp_path / "c", "always")


# ── PermissionManager：信任地板 ───────────────────────────────────────────────


# 构造一个被调用即失败的 emitter（deny 路径不该有任何弹窗）
def _exploding_emitter() -> Any:
    async def emitter(ev: dict[str, Any]) -> None:
        raise AssertionError(f"emitter must not be called, got {ev}")
    return emitter


# 功能：验证 trust=deny 的会话里写/执行工具在 bypassPermissions 下仍被 DENY
# 设计：bypass 是"用户拆掉一切弹窗"的极端态，对齐官方 managed-deny 语义——
#       信任地板必须仍压过它；emitter 用抛异常桩，多弹一次窗都当场炸
async def test_floor_trust_deny_beats_bypass_mode() -> None:
    mgr = PermissionManager(default_mode="bypassPermissions")
    mgr.set_trust("s1", "deny")
    emitter = _exploding_emitter()
    for tool, params in [
        ("write_file", {"path": "x.txt", "content": "c"}),
        ("delete_file", {"path": "x.txt"}),
        ("bash", {"command": "echo hi"}),
        ("run_python", {"code": "print(1)"}),
    ]:
        ok, d = await mgr.check_and_wait("t1", tool, params, "s1", emitter)
        assert (ok, d) == (False, "trust_deny"), (tool, ok, d)


# 功能：验证只读工具不受信任地板影响（trust=deny 是禁写禁执行，不是禁读）
# 设计：地板按 TRUST_DENY_FORBIDDEN_TOOLS 白名单取反放行，read_file 在
#       deny+bypass 下得到 bypass_allow 证明"deny 目录照样能看代码"的叙事成立
async def test_floor_readonly_unaffected_under_deny() -> None:
    mgr = PermissionManager(default_mode="bypassPermissions")
    mgr.set_trust("s1", "deny")
    ok, d = await mgr.check_and_wait("t1", "read_file", {"path": "a"}, "s1",
                                     _exploding_emitter())
    assert (ok, d) == (True, "bypass_allow")
    ok, d = await mgr.check_and_wait("t2", "list_dir", {"path": "."}, "s1",
                                     _exploding_emitter())
    assert (ok, d) == (True, "bypass_allow")


# 功能：验证未登记会话与登记为 allow/ask 的会话在 bypass 下行为与现状完全一致
# 设计："未登记=allow" 是保护既有测试与脚本的显式兼容决策，ask 的审批语义
#       由 default 模式的工具默认策略兜底——若地板对 ask/allow 也加码，这里红
async def test_floor_only_enforces_explicit_deny() -> None:
    mgr = PermissionManager(default_mode="bypassPermissions")
    emitter = _exploding_emitter()
    ok, d = await mgr.check_and_wait("t1", "write_file", {"path": "x", "content": "c"},
                                     "ghost", emitter)
    assert (ok, d) == (True, "bypass_allow")
    mgr.set_trust("s2", "allow")
    mgr.set_trust("s3", "ask")
    for sid in ("s2", "s3"):
        ok, d = await mgr.check_and_wait("t1", "write_file", {"path": "x", "content": "c"},
                                         sid, emitter)
        assert (ok, d) == (True, "bypass_allow")


# 功能：验证信任地板先于 always_allow 缓存生效
# 设计：先用真实弹窗流程灌一条 always_allow 进 Tier3 缓存，再设 deny——
#       若地板放在缓存之后（顺序回归），用户"之前同意过"会洗白"这里不许写"
async def test_floor_beats_always_allow_cache() -> None:
    mgr = PermissionManager()  # default 模式

    async def responder(ev: dict[str, Any]) -> None:
        mgr.respond("t1", "always_allow")

    ok, d = await mgr.check_and_wait("t1", "write_file", {"path": "x.txt", "content": "c"},
                                     "s1", responder)
    assert (ok, d) == (True, "always_allow")
    mgr.set_trust("s1", "deny")
    ok, d = await mgr.check_and_wait("t2", "write_file", {"path": "x.txt", "content": "c"},
                                     "s1", _exploding_emitter())
    assert (ok, d) == (False, "trust_deny")


# 功能：验证 set_trust 拒绝三态之外的值、get_trust/has_trust 语义正确
# 设计：has_trust 是 app.py 里 resume 重查 TrustStore 的门卫谓词——
#       它必须反映"登记过"（含 ask），get 则反映"生效值"（未登记=allow）
async def test_manager_trust_api_validation() -> None:
    mgr = PermissionManager()
    assert mgr.has_trust("s1") is False
    assert mgr.get_trust("s1") == "allow"
    mgr.set_trust("s1", "ask")
    assert mgr.has_trust("s1") is True
    assert mgr.get_trust("s1") == "ask"
    with pytest.raises(ValueError):
        mgr.set_trust("s1", "sometimes")


# 功能：验证 cancel_session 清掉会话的信任内存态（持久态在 TrustStore 不受影响）
# 设计：长期运行的 daemon 里 _trust 与 _modes 同族——都是 per-session 无界字典，
#       会话死了不清是慢性泄漏；清完后 has_trust 必须回到未登记态
async def test_manager_cancel_session_pops_trust() -> None:
    mgr = PermissionManager()
    mgr.set_trust("s1", "deny")
    assert mgr.has_trust("s1") is True
    mgr.cancel_session("s1")
    assert mgr.has_trust("s1") is False
    assert mgr.get_trust("s1") == "allow"


# ── 守护用例 ──────────────────────────────────────────────────────────────────


# 功能：验证全部 builtin 写类工具都已登记进 TRUST_DENY_FORBIDDEN_TOOLS
# 设计：用包扫描动态收集 metadata["category"]=="write" 的具体工具名，
#       而不是抄一份清单——清单会随新工具腐烂，扫描保证"加了写工具忘登记"
#       在 CI 当场红；bash/run_python 无 write 类别，单独断言执行类覆盖
async def test_guard_all_write_tools_registered() -> None:
    import iwan_claude.core.tools.builtin as builtin_pkg

    write_tools: set[str] = set()
    for mod_info in pkgutil.iter_modules(builtin_pkg.__path__):
        mod = importlib.import_module(f"iwan_claude.core.tools.builtin.{mod_info.name}")
        for attr in vars(mod).values():
            if not (isinstance(attr, type) and issubclass(attr, BaseTool)
                    and attr is not BaseTool):
                continue
            name = getattr(attr, "name", None)
            meta = getattr(attr, "metadata", None) or {}
            if isinstance(name, str) and name and meta.get("category") == "write":
                write_tools.add(name)
    assert write_tools, "扫描没找到任何写类工具——工具约定变了，守护失效"
    missing = write_tools - TRUST_DENY_FORBIDDEN_TOOLS
    assert not missing, f"写工具未登记进信任地板: {missing}"
    assert {"bash", "run_python"} <= TRUST_DENY_FORBIDDEN_TOOLS
