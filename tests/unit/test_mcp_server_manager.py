"""McpServerManager 生命周期测试 + [mcp]/[permission] 配置校验测试

覆盖 review 修复：启动泄漏回收 / 并行启动 / stop 清工具 / 半成品拒绝；
配置层：server 内未知键、重名、非法 timeout、宽 allow 通配拒绝。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.config import McpServerConfig, get_config
from iwan_claude.core.mcp.client import McpToolDef
from iwan_claude.core.mcp.server import McpServerManager


class _FakeClient:
    """替身 McpClient：可控 list_tools 成败与连接耗时，记录是否被 close"""
    def __init__(
        self,
        tools: list[McpToolDef] | None = None,
        fail_list: bool = False,
        connect_delay: float = 0.0,
    ) -> None:
        self._tools = tools if tools is not None else [
            McpToolDef(name="t", description="d", input_schema={})
        ]
        self._fail_list = fail_list
        self._connect_delay = connect_delay
        self.closed = False

    async def list_tools(self) -> list[McpToolDef]:
        if self._connect_delay:
            await asyncio.sleep(self._connect_delay)
        if self._fail_list:
            raise RuntimeError("tools/list exploded")
        return self._tools

    async def close(self) -> None:
        self.closed = True


# 把 manager._connect 替换为返回指定替身的工厂，记录每个 cfg 用过的替身
def _patch_connect(
    manager: McpServerManager,
    monkeypatch: pytest.MonkeyPatch,
    make: Any,
) -> list[_FakeClient]:
    created: list[_FakeClient] = []

    async def _fake(cfg: McpServerConfig) -> _FakeClient:
        c = make(cfg)
        created.append(c)
        # manager 类型标注是 McpClient，替身无需真实继承（duck typing）
        return c  # type: ignore[return-value]

    monkeypatch.setattr(manager, "_connect", _fake)
    return created


# 功能：list_tools 失败时已建立的连接必须被就地 close（防子进程泄漏）
# 设计：connect 成功、list_tools 抛错的半成品场景是 P0-2 泄漏点；
#      断言替身 closed=True 且 _clients/_tools 均未被污染
async def test_start_failure_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = McpServerManager()
    cfg = McpServerConfig(name="bad", transport="stdio", command="x")
    made = _patch_connect(mgr, monkeypatch, lambda c: _FakeClient(fail_list=True))
    await mgr.start_all([cfg])
    assert made[0].closed is True
    assert mgr._clients == {}
    assert mgr._tools == []


# 功能：start_all 并行启动所有 server，总耗时≈最慢单个而非耗时之和
# 设计：3 个各 0.15s 的"连接"，串行需 0.45s+；断言 <0.35s 证明 gather 生效，
#      这是"一个慢配置 server 拖死 daemon 启动"的直接回归锁
async def test_start_all_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = McpServerManager()
    cfgs = [McpServerConfig(name=f"s{i}", transport="stdio", command="x") for i in range(3)]
    _patch_connect(mgr, monkeypatch, lambda c: _FakeClient(connect_delay=0.15))
    t0 = time.perf_counter()
    await mgr.start_all(cfgs)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.35, f"start_all 疑似串行：{elapsed:.2f}s"
    assert len(mgr._clients) == 3 and len(mgr._tools) == 3


# 功能：stop_all 同时清空 _clients 与 _tools，之后 get_tools 不再吐死工具
# 设计：旧实现只清 clients，stop 后注册的 McpTool 仍指向已关连接；
#      断言 stop 后 get_tools() == []，防止"关停后再注册"的复活路径
async def test_stop_all_clears_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = McpServerManager()
    cfg = McpServerConfig(name="ok", transport="stdio", command="x")
    _patch_connect(mgr, monkeypatch, lambda c: _FakeClient())
    await mgr.start_all([cfg])
    assert mgr.get_tools()
    await mgr.stop_all()
    assert mgr.get_tools() == []


# 功能：manager 对重名 server 丢弃后连者并 close 其连接（配置层之外的纵深防御）
# 设计：重名走旁路进入 start_all（预填 _clients）；旧实现 _clients[name]=client
#      直接覆盖 → 前者成为无人 close 的孤儿连接
async def test_duplicate_name_defense(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = McpServerManager()
    first = _FakeClient()
    mgr._clients["dup"] = first  # type: ignore[assignment]
    second = _FakeClient()
    _patch_connect(mgr, monkeypatch, lambda c: second)
    await mgr.start_all([McpServerConfig(name="dup", transport="stdio", command="x")])
    assert second.closed is True
    assert mgr._clients["dup"] is first
    assert mgr._tools == []


# ---- 配置层校验 ----

# 写一份只含 [mcp]/[permission] 关注键的 TOML 并加载
def _load(tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    toml = tmp_path / "c.toml"
    toml.write_text(body, encoding="utf-8")
    monkeypatch.setenv("IWAN_CONFIG", str(toml))
    return get_config()


# 功能：[mcp.servers] 表内未知键必须启动期硬失败（与 [mcp] 顶层同严格度）
# 设计：拼错的键静默忽略 = 用户以为配了实际没配；SystemExit 文案需含键名便于定位
def test_mcp_server_unknown_key_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = """
[mcp]
servers = [
  { name = "db", transport = "stdio", command = "python", cmd_typo = "x" },
]
"""
    with pytest.raises(SystemExit, match="cmd_typo"):
        _load(tmp_path, body, monkeypatch)


# 功能：重名 server 在配置加载期即被拒绝
# 设计：重名是 P0-2 泄漏（覆盖孤儿连接）的最上游入口，config 层拦截后
#      manager 仅剩纵深防御；match 名串验证错误信息可定位到具体 server
def test_mcp_duplicate_names_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = """
[mcp]
servers = [
  { name = "db", transport = "stdio", command = "a" },
  { name = "db", transport = "stdio", command = "b" },
]
"""
    with pytest.raises(SystemExit, match="duplicated"):
        _load(tmp_path, body, monkeypatch)


# 功能：timeout_sec 必须为正数，且缺省值为 30.0 保持旧行为
# 设计：0/负数会让每次读立即超时形同自断；bool 是 int 子类需单独挡掉，
#      两个边界（True 被拒/缺省 30）一起锁死类型判据
def test_mcp_timeout_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="timeout_sec"):
        _load(tmp_path, """
[mcp]
servers = [{ name = "db", transport = "stdio", command = "a", timeout_sec = 0 }]
""", monkeypatch)
    cfg = _load(tmp_path, """
[mcp]
servers = [{ name = "db", transport = "stdio", command = "a" },
           { name = "slow", transport = "stdio", command = "b", timeout_sec = 120 }]
""", monkeypatch)
    assert cfg.mcp.servers[0].timeout_sec == 30.0
    assert cfg.mcp.servers[1].timeout_sec == 120.0


# 功能：allow 规则中的工具名通配（mcp__*）在配置加载期被硬拒
# 设计：官方对宽 allow 是"跳过+告警"，我们按本项目"静默不生效即危险"哲学
#      升级为 SystemExit；deny/ask 侧通配必须放行，用同文件第二份合法配置反向验证
def test_allow_glob_rejected_deny_glob_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="不允许"):
        _load(tmp_path, """
[permission]
allow = ["mcp__*"]
""", monkeypatch)
    cfg = _load(tmp_path, """
[permission]
deny = ["mcp__*"]
ask = ["mcp__github__*"]
allow = ["mcp__filesystem__read_file"]
""", monkeypatch)
    assert cfg.permission.deny == ["mcp__*"]
    assert cfg.permission.ask == ["mcp__github__*"]


# 功能：evaluate_tool_rules 对 mcp__* glob 命中新式命名工具，allow 侧 glob 永不参与裁定
# 设计：规则引擎端到端（配置加载→PermissionRules→evaluate）验证一刀切语义，
#      deny 命中必须 forced=True——这是"ask 模式也不能放行外部工具"的地板证明
def test_mcp_blanket_deny_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from iwan_claude.core.permissions.policy import PermissionDecision
    from iwan_claude.core.permissions.rules import PermissionRules, evaluate_tool_rules

    rules = PermissionRules(deny=["mcp__*"], ask=[], allow=[])
    out = evaluate_tool_rules("mcp__filesystem__read_file", rules)
    assert out.matched and out.decision == PermissionDecision.DENY and out.forced
    # 内置工具不受外部工具一刀切规则牵连（fnmatch 前缀不匹配）
    out2 = evaluate_tool_rules("read_file", rules)
    assert not out2.matched
    # allow 侧的 glob 即使绕过配置注入也不得生效
    rules3 = PermissionRules(deny=[], ask=[], allow=["mcp__*"])
    out3 = evaluate_tool_rules("mcp__x__y", rules3)
    assert not out3.matched
