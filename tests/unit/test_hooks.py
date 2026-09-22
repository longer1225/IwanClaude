"""
hooks 子系统单元测试：spec 校验矩阵 → runner 退出码协议 → registry 聚合 → manager 接线

【学习要点】
1. 分层测试对应分层信任：spec 测"非法配置活不到运行时"，runner 用真实子进程测
   协议翻译（mock 子进程会把退出码协议测成自证），registry 测聚合与短路，
   manager 测接线语义（方向不对称）。
2. 全部 hook 脚本是临时写出的真实 python 文件——协议是进程间契约，
   只有真进程能证明 stdin JSON、stdout 编码、exit code 三通道都工作。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.hooks import HookRegistry, HookSpec, parse_hook_entries
from iwan_claude.core.hooks.runner import HookDecision, run_hook
from iwan_claude.core.permissions.manager import PermissionManager
from iwan_claude.core.permissions.rules import PermissionRules


@pytest.fixture(autouse=True)
def reset_sandbox() -> Any:
    # 功能：每个测试重置沙箱全局与 contextvar，保证权限评估从"无沙箱"基线出发
    # 设计：manager 集成测试走完整审批链，其他文件泄漏的沙箱状态会在 Tier2.5
    #       抢先强制 ASK，污染 hook 语义断言
    import iwan_claude.core.sandbox as sb_module
    sb_module._sandbox_manager = None
    token = sb_module._active_sandbox.set(None)
    sb_module._sandbox_by_session.clear()
    yield
    sb_module._active_sandbox.reset(token)
    sb_module._sandbox_manager = None
    sb_module._sandbox_by_session.clear()


# ── helpers ───────────────────────────────────────────────────────────────────


# 写一个临时 hook 脚本并返回 argv 直连的 HookSpec（runner 层测试专用）
def _script_spec(
    tmp_path: Path, body: str, *, name: str = "hook",
    event: str = "PreToolUse", matcher: str = "*", timeout_s: float = 10.0,
) -> HookSpec:
    script = tmp_path / f"{name}.py"
    script.write_text(body, encoding="utf-8")
    return HookSpec(event=event, matcher=matcher,
                    argv=[sys.executable, str(script)], timeout_s=timeout_s)


_PAYLOAD = {
    "hook_event_name": "PreToolUse", "tool_name": "bash",
    "tool_input": {"command": "ls"}, "session_id": "s1", "run_id": "r1",
}


# ── spec：配置校验矩阵 ────────────────────────────────────────────────────────


# 功能：验证合法 [[hooks]] 条目被解析为 HookSpec，默认值（matcher="*"、timeout=10s）生效
# 设计：只给必填键，断言两个可选键的默认——matcher 默认 "*" 决定"未配置=全工具守卫"，
#       若默认成空串则所有 hook 静默不匹配，是配置系统最阴的失败模式
def test_parse_valid_entry_with_defaults() -> None:
    specs = parse_hook_entries([{"event": "PreToolUse", "command": "python guard.py"}])
    assert len(specs) == 1
    s = specs[0]
    assert s.event == "PreToolUse" and s.matcher == "*" and s.timeout_s == 10.0
    assert s.argv == ["python", "guard.py"]


# 功能：验证 event 枚举外值 / 未知键 / 非正 timeout / 空 command 全部抛 ValueError
# 设计：四个最常见笔误各占一形态（枚举/键名/数值/空值），一个都漏不得——
#       hook 常当审批守卫用，静默失效比拒绝启动危险
def test_parse_rejects_illegal_entries() -> None:
    for bad in (
        {"event": "PreTool", "command": "x"},                 # 枚举拼错
        {"event": "PreToolUse", "command": "x", "regex": 1},  # 未知键
        {"event": "PreToolUse", "command": "x", "timeout_s": 0},
        {"event": "PreToolUse", "command": "   "},
    ):
        with pytest.raises(ValueError):
            parse_hook_entries([bad])


# 功能：验证 command 里混入 shell 拼接符时被当作字面 argv token 而非执行
# 设计："guard.py && rm -rf /" 在 shell=True 下会真的 rm；argv 化后 "&&"/"rm"
#       只是 argv 里的字符串，exec 找不到叫 "&&" 的程序直接失败——
#       本断言钉死"hook 配置不是命令执行通道"这一安全前提
def test_command_is_argv_split_not_shell() -> None:
    specs = parse_hook_entries(
        [{"event": "PreToolUse", "command": "guard.py && rm -rf /"}])
    assert "&&" in specs[0].argv and "rm" in specs[0].argv


# ── runner：退出码协议（真实子进程）──────────────────────────────────────────


# 功能：验证 exit 0 无输出/非 JSON/defer 三种形态都折叠为弃权（NONE）
# 设计：弃权是"对权限链透明"的默认档——脚本只 print("hello") 绝不能被
#       误读成某种裁定；三种形态走同一分支但成因不同，合并成一测锁全
async def test_runner_abstain_forms(tmp_path: Path) -> None:
    for body in ("pass", "print('hello not json')",
                 "import json;print(json.dumps({'permissionDecision':'defer'}))"):
        spec = _script_spec(tmp_path, body, name=f"a{abs(hash(body))}")
        out = await run_hook(spec, _PAYLOAD)
        assert out.decision == HookDecision.NONE, body


# 功能：验证 exit 0 + JSON permissionDecision 三档位与 reason 字段解析，载荷经 stdin 送达
# 设计：reason 内容取自 stdin 的 tool_name（"bash"）——一条断言同时证明
#       JSON 协议解析和 stdin 载荷管道都通了，不用 mock 自证
async def test_runner_json_decision_and_stdin_payload(tmp_path: Path) -> None:
    body = ("import json,sys\n"
            "d=json.load(sys.stdin)\n"
            "print(json.dumps({'permissionDecision':'allow','reason':'tool='+d['tool_name']}))")
    out = await run_hook(_script_spec(tmp_path, body), _PAYLOAD)
    assert out.decision == HookDecision.ALLOW
    assert out.reason == "tool=bash"


# 功能：验证 exit 2 是 DENY 且 stderr 进 stderr_to_model（回灌通道），同进程的 stdout 被忽略
# 设计：官方语义 "exit 2 时 stdout/JSON 一律忽略"——脚本同时 print JSON allow
#       和 stderr 文本，若实现让 stdout 参与裁定，ALLOW 会翻掉 DENY，
#       这个对抗输入就是该回归的哨兵
async def test_runner_exit2_is_hard_block_with_stderr(tmp_path: Path) -> None:
    body = ("import json\n"
            "print(json.dumps({'permissionDecision':'allow'}))\n"
            "import sys; sys.stderr.write('nope!'); sys.exit(2)")
    out = await run_hook(_script_spec(tmp_path, body), _PAYLOAD)
    assert out.decision == HookDecision.DENY
    assert "nope!" in out.stderr_to_model and "nope!" in out.reason


# 功能：验证非零非 2 退出、超时、可执行文件不存在三种故障全部 fail-closed 成 ASK
# 设计：三种故障的公共语义是"守卫失聪 ≠ 守卫同意"；超时脚本 sleep 8 而
#       timeout 1s，同时证明 kill 生效（测试整体 2s 内返回）不拖死审批链
async def test_runner_failures_are_fail_closed_ask(tmp_path: Path) -> None:
    out = await run_hook(_script_spec(tmp_path, "import sys; sys.exit(3)"), _PAYLOAD)
    assert out.decision == HookDecision.ASK
    slow = _script_spec(tmp_path, "import time; time.sleep(8)", timeout_s=1.0)
    out = await run_hook(slow, _PAYLOAD)
    assert out.decision == HookDecision.ASK and "超时" in out.reason
    missing = HookSpec(event="PreToolUse", matcher="*",
                       argv=[str(tmp_path / "no_such_binary.exe")], timeout_s=5.0)
    out = await run_hook(missing, _PAYLOAD)
    assert out.decision == HookDecision.ASK and "启动失败" in out.reason


# ── registry：匹配、聚合、短路、广播 ─────────────────────────────────────────


# 功能：验证 matcher 过滤（精确工具名 + "*"）与 hook 间互不串台
# 设计：一个 matcher=bash 的 deny hook 必须对 write_file 完全静默
#       （NONE 且没有进程被 spawn）——marker 文件计数证明"没跑"而非"跑了没说话"
async def test_registry_matcher_filtering(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    body = f"open(r'{marker}','a').write('x')\nimport sys; sys.stderr.write('blocked'); sys.exit(2)"
    spec = _script_spec(tmp_path, body, matcher="bash")
    reg = HookRegistry([spec])
    out = await reg.run_pre_tool_use("write_file", {}, "s1")
    assert out.decision == HookDecision.NONE and not marker.exists()
    out = await reg.run_pre_tool_use("bash", {"command": "ls"}, "s1")
    assert out.decision == HookDecision.DENY and marker.exists()


# 功能：验证多 hook 聚合取最严格、DENY 短路后续 hook、ALLOW+ASK 组合落 ASK
# 设计：顺序 [allow, deny, ask]——deny 在中间，短路必须发生（第三个 ask 的
#       marker 不出现）；另测 [allow, ask] 无 deny 时聚合为 ask 而非 allow
async def test_registry_aggregates_strictest_and_short_circuits(tmp_path: Path) -> None:
    marker = tmp_path / "order.txt"

    def spec_for(name: str, decision: str) -> HookSpec:
        if decision == "deny":
            body = f"open(r'{marker}','a').write('deny ')\nimport sys; sys.exit(2)"
        else:
            body = (f"open(r'{marker}','a').write('{decision} ')\n"
                    f"import json;print(json.dumps("
                    f"{{'permissionDecision':'{decision}','reason':'{decision}'}}))")
        return _script_spec(tmp_path, body, name=name)

    reg = HookRegistry([
        spec_for("h1", "allow"), spec_for("h2", "deny"), spec_for("h3", "ask"),
    ])
    out = await reg.run_pre_tool_use("bash", {}, "s1")
    assert out.decision == HookDecision.DENY
    assert marker.read_text(encoding="utf-8") == "allow deny "  # h3 被短路

    marker.unlink()
    reg2 = HookRegistry([spec_for("h1", "allow"), spec_for("h2", "ask")])
    out = await reg2.run_pre_tool_use("bash", {}, "s1")
    assert out.decision == HookDecision.ASK


# 功能：验证 bus 注入时每个实际执行的 hook 广播一条字段完整的 HookEvaluatedEvent
# 设计：用假 bus 收集 publish；断言"实际执行"——被 matcher 挡下的工具不应
#       产生事件（否则 TUI 会被幽灵裁定刷屏）
async def test_registry_emits_event_per_executed_hook(tmp_path: Path) -> None:
    published: list[Any] = []

    class FakeBus:
        async def publish(self, event: Any) -> None:
            published.append(event)

    spec = _script_spec(
        tmp_path, "import json;print(json.dumps({'permissionDecision':'allow'}))",
        matcher="bash")
    reg = HookRegistry([spec], bus=FakeBus())
    out = await reg.run_pre_tool_use("bash", {"command": "ls"}, "s1", run_id="r9")
    assert out.decision == HookDecision.ALLOW
    assert len(published) == 1
    ev = published[0]
    assert ev.type == "hook.evaluated" and ev.decision == "allow"
    assert ev.run_id == "r9" and ev.tool_name == "bash" and ev.hook_event == "PreToolUse"
    # matcher 不匹配 → 不执行 → 不广播
    await reg.run_pre_tool_use("edit", {}, "s1")
    assert len(published) == 1


# ── manager 接线：Tier 0 语义 ────────────────────────────────────────────────


def _mgr_with_hook(tmp_path: Path, body: str, **kw: Any) -> PermissionManager:
    script = tmp_path / "guard.py"
    script.write_text(body, encoding="utf-8")
    cmd = f'"{sys.executable}" "{script}"'  # 全路径可能含空格/反斜杠，走引号
    specs = parse_hook_entries([{"event": "PreToolUse", "command": cmd}])
    return PermissionManager(hooks=HookRegistry(specs), **kw)


# 功能：验证 PreToolUse hook ALLOW 终结默认 ASK（bash 无规则时本应弹问）且不产生审批事件
# 设计：bash 默认策略 ASK 是"未配置"的兜底——hook 明确说可，用户不必再被问一次；
#       emitted==[] 证明短路发生在弹窗之前
async def test_manager_hook_allow_short_circuits_default_ask(tmp_path: Path) -> None:
    mgr = _mgr_with_hook(
        tmp_path, "import json;print(json.dumps({'permissionDecision':'allow'}))")
    emitted: list[dict[str, Any]] = []

    async def emitter(ev: dict[str, Any]) -> None:
        emitted.append(ev)

    allowed, decision = await mgr.check_and_wait(
        "t1", "bash", {"command": "ls -la"}, "s1", emitter)
    assert (allowed, decision) == (True, "hook_allow")
    assert emitted == []


# 功能：验证方向不对称——hook ALLOW 翻不了 deny 规则（deny 地板在 hook 之后仍执行）
# 设计：hook 脚本明确 allow + 规则 deny rm:*，输入 `rm x`：若实现把 hook ALLOW
#       做成"立即返回"，这里会拿到 (True,"hook_allow")——那一个被攻陷的
#       hook 脚本就等于全系统旁路，本测试是官方"hook allow 不覆盖 deny"的回归门
async def test_manager_hook_allow_cannot_override_deny_rule(tmp_path: Path) -> None:
    mgr = _mgr_with_hook(
        tmp_path, "import json;print(json.dumps({'permissionDecision':'allow'}))",
        rules=PermissionRules(deny=["bash(rm:*)"]))
    allowed, decision = await mgr.check_and_wait(
        "t1", "bash", {"command": "rm x"}, "s1", lambda ev: asyncio.sleep(0))
    assert (allowed, decision) == (False, "auto_deny")


# 功能：验证 hook DENY 连默认 ALLOW 的工具（read_file）都能拦下，且理由带 stderr 文本
# 设计：read_file 无任何规则/缓存参与，(False,"hook_deny") 唯一来源只能是 hook；
#       lambda 永远不被调用即"deny 早于弹窗"，无需断言 emitted
async def test_manager_hook_deny_blocks_default_allow_tool(tmp_path: Path) -> None:
    mgr = _mgr_with_hook(
        tmp_path, "import sys; sys.stderr.write('policy: no reading secrets'); sys.exit(2)")
    allowed, decision = await mgr.check_and_wait(
        "t1", "read_file", {"path": "x"}, "s1", lambda ev: asyncio.sleep(0))
    assert (allowed, decision) == (False, "hook_deny")


# 功能：验证 hook ASK 是强制档——auto 模式全开的写工具也照常弹问
# 设计：write_file 在 auto_mode="on" 下本会被自动批准；hook 说 ask 后必须走到
#       permission.requested 事件并由 respond 决定——证明 hook ASK 并入了
#       forced_ask 通道而不是可以被 auto 洗白的普通 ASK
async def test_manager_hook_ask_survives_auto_mode(tmp_path: Path) -> None:
    mgr = _mgr_with_hook(
        tmp_path, "import json;print(json.dumps({'permissionDecision':'ask'}))")
    mgr.set_auto_mode("on")
    emitted: list[dict[str, Any]] = []

    async def emitter(ev: dict[str, Any]) -> None:
        emitted.append(ev)
        # 弹窗事件即审批请求到达：在这里同步 respond——check_and_wait 是先存
        # _pending 再 await emitter，此刻 Future 必然存在（用 sleep 定时会在
        # hook 子进程启动的 1-2 秒里 respond 落空）
        mgr.respond("t1", "allow_once")

    allowed, decision = await mgr.check_and_wait(
        "t1", "write_file", {"path": "a.txt", "content": "x"}, "s1", emitter)
    assert (allowed, decision) == (True, "allow_once")
    assert len(emitted) == 1 and emitted[0]["type"] == "permission.requested"


# 功能：验证空注册表/None 时审批链与 hook 上线前完全一致（向后兼容门）
# 设计：同一输入分别在 hooks=None、hooks=HookRegistry([])、无 hook 三态下评估，
#       结果必须全等——任何一侧漂了都说明 Tier 0 的条件判断漏了
async def test_manager_empty_hooks_is_backward_compatible(tmp_path: Path) -> None:
    mgr_none = PermissionManager()
    mgr_empty = PermissionManager(hooks=HookRegistry([]))
    r1 = await mgr_none.check_and_wait(
        "t1", "read_file", {"path": "x"}, "s1", lambda ev: asyncio.sleep(0))
    r2 = await mgr_empty.check_and_wait(
        "t1", "read_file", {"path": "x"}, "s1", lambda ev: asyncio.sleep(0))
    assert r1 == r2 == (True, "auto_allow")


# 功能：验证 PostToolUse 直通：exit 2 hook 的 stderr 以警告文本返回，供调用方追加进 tool 结果
# 设计：走 manager 的公开方法（invocation 实际调用的就是它）；空注册表必须
#       返回空串——那是"不改动 result.content"的哨兵值，非空即污染输出
async def test_manager_post_tool_use_returns_warning_text(tmp_path: Path) -> None:
    script = tmp_path / "post.py"
    script.write_text("import sys; sys.stderr.write('dirty output detected'); sys.exit(2)",
                      encoding="utf-8")
    cmd = f'"{sys.executable}" "{script}"'
    specs = parse_hook_entries([{"event": "PostToolUse", "command": cmd}])
    mgr = PermissionManager(hooks=HookRegistry(specs))
    warn = await mgr.run_post_tool_use_hooks("bash", {"command": "ls"}, "ok", "s1", "r1")
    assert "PostToolUse hook" in warn and "dirty output detected" in warn
    assert await PermissionManager().run_post_tool_use_hooks(
        "bash", {}, "ok", "s1", "r1") == ""
