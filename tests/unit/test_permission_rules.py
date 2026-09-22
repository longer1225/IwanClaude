"""
权限规则引擎单元测试（rules.py）+ 规则在策略评估链中的落点（policy.py）

【学习要点】
1. 规则匹配永远发生在"归一化后的命令段"上：空格折叠、逐段拆分。测试用例
   必须同时覆盖"该匹配的形态"（多空白、前后空白、冒号后缀）与"不该匹配的
   形态"（词边界粘连如 testx vs test:*）——权限系统里漏匹配=弹问（骚扰但
   安全），误匹配=放行（静默越权），后者才是测试要重点锁死的方向。
2. fail-closed 形态（动态语法、exec wrapper、引号不平衡）的期望结果一律是
   ASK 且 matched=True：引擎"看见了但拒绝放行"，与"没有规则管"（弃权）在
   后续层级走的路径不同，测试必须把 matched 一起断言，否则两种语义会混淆。
"""
from __future__ import annotations

from typing import Any

import pytest

from iwan_claude.core.permissions.policy import (
    PermissionDecision,
    ToolPolicy,
    evaluate,
)
from iwan_claude.core.permissions.rules import (
    PermissionRules,
    evaluate_bash_rules,
    evaluate_tool_rules,
    has_dynamic_syntax,
    is_wrapper_segment,
    parse_rule,
    split_segments,
)


@pytest.fixture(autouse=True)
def reset_sandbox() -> Any:
    # 功能：每个测试重置沙箱全局与 contextvar，保证评估链从"无沙箱"基线出发
    # 设计：policy.evaluate 的 Tier1/Tier2 读 get_sandbox()，其他测试文件遗留的
    #       沙箱状态会污染本文件的规则层断言（例如 command_blacklist 抢先 DENY）
    import iwan_claude.core.sandbox as sb_module
    sb_module._sandbox_manager = None
    token = sb_module._active_sandbox.set(None)
    sb_module._sandbox_by_session.clear()
    yield
    sb_module._active_sandbox.reset(token)
    sb_module._sandbox_manager = None
    sb_module._sandbox_by_session.clear()


# ── parse_rule：规则字符串的合法/非法形态 ────────────────────────────────────


# 功能：验证四种合法形态（工具级 / 无通配精确 / :* 遗留前缀 / 空格包围的位置通配）都能解析
# 设计：parse_rule 是配置笔误的第一道闸门，四种形态的 (tool, spec) 元组逐字钉死，
#       防止重构时把 ":*" 误当成 specifier 的一部分或把工具名大小写敏感化
def test_parse_rule_valid_forms() -> None:
    assert parse_rule("bash") == ("bash", None)
    assert parse_rule("Bash(git status)") == ("bash", "git status")
    assert parse_rule("bash(git status:*)") == ("bash", "git status:*")
    assert parse_rule("bash(git * diff)") == ("bash", "git * diff")
    assert parse_rule("edit") == ("edit", None)  # 非 bash 工具级合法（gitignore 语法规划中）


# 功能：验证非法 * 粘连形态、非 bash specifier、空 specifier、畸形形状全部抛 ValueError
# 设计："git*diff" 这类粘连通配一旦接受，前缀语义会被悄悄扩大（gitanydiff 也算命中）；
#       启动期抛错让配置笔误立刻暴露，而不是运行时静默不生效
def test_parse_rule_rejects_illegal_forms() -> None:
    with pytest.raises(ValueError):
        parse_rule("bash(git*diff)")
    with pytest.raises(ValueError):
        parse_rule("Read(./.env)")
    with pytest.raises(ValueError):
        parse_rule("bash()")
    with pytest.raises(ValueError):
        parse_rule("not a rule")
    with pytest.raises(ValueError):
        parse_rule("(bash)")


# ── split_segments：复合命令拆分与 fail-closed ────────────────────────────────


# 功能：验证 && || ; | 与换行都能拆段，且各段完成空白归一化
# 设计：四种操作符 + 换行各出现一次即可覆盖全部分支；归一化混在这里断言，
#       因为规则匹配只在归一化文本上进行，拆分与归一化必须同步验证才有意义
def test_split_segments_all_operators() -> None:
    cmd = "git status && npm test; ls | grep x\npytest -q"
    assert split_segments(cmd) == ["git status", "npm test", "ls", "grep x", "pytest -q"]
    assert split_segments("ls  -la    src") == ["ls -la src"]  # 多空白折叠


# 功能：验证引号内的操作符是字面量不参与拆分；不平衡引号返回 None
# 设计：`bash -c 'echo a && b'` 只运行一段——若把引号内 && 当分隔符，
#       拆出的假段"b'"永不命中 allow，正常命令被误 ASK（骚扰）；
#       反之不平衡引号必须 None（无法确定段边界，fail-closed 不许猜）
def test_split_segments_quotes_and_unbalanced() -> None:
    assert split_segments("bash -c 'echo a && b'") == ["bash -c 'echo a && b'"]
    assert split_segments('echo "x; y"') == ["echo \"x; y\""]
    assert split_segments("git status --format='%d") is None


# ── 三种匹配形态：精确 / :* 前缀 / 位置通配 ──────────────────────────────────


# 功能：验证无通配 specifier 是整串精确匹配，带任何参数都不再命中
# 设计：`git status` 与 `git status -s` 只差一个 flag——精确语义下后者未覆盖
#       （ASK），这是与 legacy 子串正则（前者也匹配后者）最关键的行为差异，
#       用一条最小对照钉死"规则模型收紧了匹配粒度"这个事实
def test_exact_spec_is_whole_segment_match() -> None:
    rules = PermissionRules(allow=["bash(git status)"])
    assert evaluate_bash_rules("git status", rules).decision == PermissionDecision.ALLOW
    assert evaluate_bash_rules("git status -s", rules).decision == PermissionDecision.ASK


# 功能：验证 :* 前缀同时覆盖裸命令 / 空格参数 / 冒号后缀三形态，且不越词边界
# 设计：三形态来自真实配置习惯（npm run test、test --watch、test:unit）；
#       testx 这类粘连词必须不命中——否则前缀规则退化成子串规则，
#       `bash(npm run test:*)` 就会放行 `npm run testx --evil`
def test_colon_prefix_matches_three_forms_not_word_glue() -> None:
    rules = PermissionRules(allow=["bash(npm run test:*)"])
    for cmd in ("npm run test", "npm run test --watch", "npm run test:unit"):
        assert evaluate_bash_rules(cmd, rules).decision == PermissionDecision.ALLOW, cmd
    assert evaluate_bash_rules("npm run testx", rules).decision == PermissionDecision.ASK
    # 空白归一化：规则里的空格在命令中是换行/多空格也要命中（匹配发生在归一化后）
    assert evaluate_bash_rules("npm  run\ttest", rules).decision == PermissionDecision.ALLOW


# 功能：验证位置通配 `git * diff` 匹配中间任意词、但要求前后空格边界
# 设计：`*` 编译为 `.*` 且段间用 \s+ 连接——`git remote diff` 是命中样例；
#       `git status --diff` 中 diff 粘连在 flag 里，词边界不成立必须 ASK，
#       防止 `.*` 把 "--diff" 也算成独立 diff 词
def test_positional_glob_requires_word_boundary() -> None:
    rules = PermissionRules(allow=["bash(git * diff)"])
    assert evaluate_bash_rules("git remote diff", rules).decision == PermissionDecision.ALLOW
    assert evaluate_bash_rules("git status --diff", rules).decision == PermissionDecision.ASK


# ── 三档优先序与 forced 语义 ──────────────────────────────────────────────────


# 功能：验证同一条命令同时命中 deny 与 allow 规则时 DENY 赢且 forced=True
# 设计：deny 是地板（缓存/模式/allow 都翻不了）；forced 标志决定 manager
#       是否跳过指纹缓存——deny 的 forced 若丢，"总是允许"就能洗白危险命令
def test_deny_beats_allow_and_is_forced() -> None:
    rules = PermissionRules(deny=["bash(git push:*)"], allow=["bash(git:*)"])
    o = evaluate_bash_rules("git push origin main", rules)
    assert o.decision == PermissionDecision.DENY
    assert o.forced is True
    assert o.matched is True


# 功能：验证显式 ask 规则盖过全段 allow 命中，且结果为 forced
# 设计：`rm *` ask 规则对 `git status && rm x` 的第二段同时命中 allow 之外——
#       这里用 git push 更干净：allow(bash(git:*)) 全段覆盖，但 ask(git push:*)
#       命中即压过；forced=True 让 manager 连"用户对整条命令的总是允许"都不给
def test_ask_rule_beats_allow_and_is_forced() -> None:
    rules = PermissionRules(ask=["bash(git push:*)"], allow=["bash(git:*)"])
    o = evaluate_bash_rules("git push", rules)
    assert o.decision == PermissionDecision.ASK
    assert o.forced is True


# 功能：验证复合命令"全段被 allow 覆盖"才 ALLOW，半覆盖 ASK 且非 forced
# 设计：这是逐段求值的核心不变式（官方 issue #29491 语义）。半覆盖的 ASK
#       必须 forced=False——用户对完整复合命令点过"总是允许"后，指纹缓存
#       （整条命令一个指纹）仍可放行它；这与官方逐子命令存规则等价偏保守
def test_compound_fully_covered_allows_semi_covered_asks() -> None:
    rules = PermissionRules(allow=["bash(git status)", "bash(npm run test:*)"])
    assert evaluate_bash_rules("git status && npm run test:unit", rules).decision == PermissionDecision.ALLOW
    semi = evaluate_bash_rules("git status && npm run build", rules)
    assert semi.decision == PermissionDecision.ASK
    assert semi.forced is False
    assert semi.matched is True


# 功能：验证复合命令任一段命中 deny 即整条 DENY——即使其他段全部 allow 覆盖
# 设计：`git status && rm -rf /` 是最经典的前缀规则绕过尝试；
#       段级 deny 短路发生在 allow 聚合之前，顺序错了就是事故
def test_compound_any_segment_deny_wins() -> None:
    rules = PermissionRules(deny=["bash(rm:*)"], allow=["bash(git status:*)"])
    o = evaluate_bash_rules("git status && rm -rf /", rules)
    assert o.decision == PermissionDecision.DENY
    assert o.forced is True


# ── 两类 fail-closed 形态：exec wrapper 与动态语法 ────────────────────────────


# 功能：验证 exec wrapper 段即使命中 allow 也失去放行资格（非 forced ASK + wrapper 说明）
# 设计：watch/xargs/env/sudo 等包装器"放行前缀=放行任意内层命令"；
#       detail 文案须能看出原因（审批弹窗展示用），matched=True 保证 legacy
#       正则 allow 让位。find -delete 同理由 is_wrapper_segment 直接断言
def test_wrapper_segment_denies_prefix_allow() -> None:
    rules = PermissionRules(allow=["bash(watch:*)", "bash(find:*)"])
    o = evaluate_bash_rules("watch git status", rules)
    assert o.decision == PermissionDecision.ASK
    assert o.matched is True
    assert "wrapper" in o.detail
    assert is_wrapper_segment("find . -delete")
    assert is_wrapper_segment("xargs -n1 rm")
    assert not is_wrapper_segment("git status")


# 功能：验证动态语法（$()、反引号、$VAR、重定向、大括号展开）令整条命令丧失 allow 资格
# 设计：动态形态静态分析看不见真正执行什么（社区实测可绕过前缀规则）。
#       注意用 $HOME 变量形态而非绝对路径做载荷——`/` 开头会先触发
#       outside-cwd 启发式，那是策略链的事，不该混进规则层断言
def test_dynamic_syntax_blocks_prefix_allow() -> None:
    rules = PermissionRules(allow=["bash(git:*)"])
    for cmd in ("git log $HOME/x", "git diff $(whoami)", "git show > out.txt", "git log --format=%h `date`"):
        assert has_dynamic_syntax(cmd), cmd
        o = evaluate_bash_rules(cmd, rules)
        assert o.decision == PermissionDecision.ASK, cmd
        assert "动态语法" in o.detail, cmd


# 功能：验证不可解析命令（引号不平衡）走"未覆盖"ASK 而非 DENY，且 matched=True
# 设计：无法拆解 ≠ 危险，只是静态分析失明——语义应是"逐段求值不可用"
#       的 ASK（用户仍可批准），DENY 会剥夺合法用法；matched=True 同样
#       为了压住 legacy allow 正则（看不见就都别放行）
def test_unparsable_command_asks_not_deny() -> None:
    rules = PermissionRules(allow=["bash(git:*)"])
    o = evaluate_bash_rules("git status --format='%d", rules)
    assert o.decision == PermissionDecision.ASK
    assert o.forced is False
    assert o.matched is True
    assert "不可解析" in o.detail


# ── 工具级规则与弃权语义 ──────────────────────────────────────────────────────


# 功能：验证工具级 bash 规则短路一切：deny/ask 为 forced、allow 非 forced
# 设计：裸 "bash" 不看命令内容即裁定（如公司政策 `deny: ["bash"]`）；
#       allow 侧非 forced 是给默认策略/缓存留活口，deny/ask 侧留口即漏洞
def test_tool_level_bash_rules_short_circuit() -> None:
    for kind, want in (("deny", PermissionDecision.DENY), ("ask", PermissionDecision.ASK),
                       ("allow", PermissionDecision.ALLOW)):
        rules = PermissionRules(**{kind: ["bash"]})
        o = evaluate_bash_rules("anything at all", rules)
        assert o.decision == want, kind
        assert o.matched is True
        assert o.forced is (kind != "allow")


# 功能：验证 deny 的工具级短路发生在 specifier 求值之前（裸 deny + 无命中的 allow 规格共存）
# 设计：`deny=["bash"]` 与 `allow=["bash(git status)"]` 同时在场时，若实现
#       先走逐段 allow 聚合，配置错误会变成静默放行；短路顺序是安全属性
def test_tool_level_deny_beats_command_allow_spec() -> None:
    rules = PermissionRules(deny=["bash"], allow=["bash(git status)"])
    assert evaluate_bash_rules("git status", rules).decision == PermissionDecision.DENY


# 功能：验证非 bash 工具只认工具级规则，bash specifier 不参与其他工具的裁定
# 设计：evaluate_tool_rules 是 edit/write_file 等工具的全部规则入口；
#       传 "bash(git:*)" 规则时 edit 必须弃权（matched=False），否则正则
#       形态被跨工具误用会放大授权面
def test_tool_rules_only_own_tool_and_abstain() -> None:
    o = evaluate_tool_rules("edit", PermissionRules(deny=["edit"]))
    assert (o.decision, o.forced, o.matched) == (PermissionDecision.DENY, True, True)
    o = evaluate_tool_rules("edit", PermissionRules(allow=["bash(git:*)"]))
    assert o.matched is False
    o = evaluate_tool_rules("read_file", PermissionRules(ask=["read_file"]))
    assert (o.decision, o.forced) == (PermissionDecision.ASK, True)


# 功能：验证没有任何 bash 相关规则时引擎弃权（matched=False），裁定值为 ASK
# 设计：弃权是向后兼容的关键——未配置规则时行为必须与规则引擎上线前
#       完全一致（交给缓存/legacy/默认策略）；ASK+matched=False 的默认
#       值若被误改成 ALLOW，空配置就从"透明"变成"全量放行"
def test_abstain_when_no_bash_rules() -> None:
    o = evaluate_bash_rules("ls", PermissionRules())
    assert o.matched is False
    o = evaluate_bash_rules("ls", PermissionRules(ask=["edit"]))
    assert o.matched is False


# ── 规则在策略评估链中的落点（policy.evaluate）───────────────────────────────


# 功能：验证规则层在 legacy deny_patterns 之后（先匹配先赢）且 DENY 地板不受 allow 规则影响
# 设计：Tier1 内部顺序 legacy-deny → blacklist → deny 规则，这里用
#       deny 规则 vs allow 规则的对抗证明地板：allow 全段覆盖也翻不了 deny
def test_rule_deny_survives_allow_rules_in_pipeline() -> None:
    rules = PermissionRules(deny=["bash(rm:*)"], allow=["bash(rm readme.md)"])
    assert evaluate("bash", {"command": "rm readme.md"}, rules=rules) == PermissionDecision.DENY


# 功能：验证 legacy allow_patterns 只放行单段命令——复合命令不再被子串正则放行
# 设计：这是规则引擎落地时顺带收紧的最大绕过面（`echo ok && rm -rf /` 曾被
#       `^echo` 正则整体放行）。对照组"echo hi"保持 ALLOW，证明收紧只针对
#       复合形态而非误伤单段——两个方向缺一个断言都测不出回归
def test_legacy_allow_blocked_for_compound_only() -> None:
    policy = ToolPolicy(default=PermissionDecision.ASK, allow_patterns=[r"^echo\b"])
    assert evaluate("bash", {"command": "echo hi"}, policy) == PermissionDecision.ALLOW
    assert evaluate("bash", {"command": "echo ok && rm -rf /"}, policy) == PermissionDecision.ASK


# 功能：验证规则引擎在场（matched）时 legacy allow 让位，未参与时 legacy 照常
# 设计：两套规则并存时"规则看见过就别想让旧正则越权"。用规则 ASK（非 forced
#       的未覆盖）+ legacy `.*` allow 构造对抗：结果必须是 ASK；
#       对照组用不相关规则（仅 edit）证明弃权时 legacy 仍是唯一放行来源
def test_legacy_allow_suppressed_when_rules_matched() -> None:
    policy = ToolPolicy(default=PermissionDecision.ASK, allow_patterns=[r".*"])
    rules = PermissionRules(allow=["bash(git status)"])
    # rm x 有段未覆盖 → 规则 matched 的 ASK → legacy 让位
    assert evaluate("bash", {"command": "rm x"}, policy, rules) == PermissionDecision.ASK
    # 规则与 bash 无关 → 弃权 → legacy allow 生效
    other = PermissionRules(allow=["edit"])
    assert evaluate("bash", {"command": "rm x"}, policy, other) == PermissionDecision.ALLOW


# 功能：验证无规则（rules=None）时评估链行为与规则引擎上线前完全一致
# 设计：向后兼容的回归门——None 与"配置了空列表"（is_empty）都必须透明；
#       若 None 被判成 DENY/ALLOW 任何一侧，所有存量部署升级即破功
def test_rules_none_is_backward_compatible() -> None:
    assert evaluate("bash", {"command": "ls"}) == PermissionDecision.ASK
    assert evaluate("bash", {"command": "ls"}, rules=PermissionRules()) == PermissionDecision.ASK
    assert evaluate("read_file", {"path": "x"}, rules=PermissionRules()) == PermissionDecision.ALLOW
