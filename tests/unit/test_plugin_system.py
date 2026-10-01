# 功能：验证插件系统核心链路——清单解析、贡献合流、账本启停、错误隔离、汇入点优先级
# 设计：全部用 tmp_path 造插件目录树（不碰 repo plugins/ 与用户 ~/.iwan），断言集中在
#       "外部包能不能伤到系统"的四条纪律：宽容读清单/坏包只坏自己/禁用零贡献/
#       撞名降级 partial——这些是插件安全模型的骨架，UI 细节交给集成层与 devtest
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from iwan_claude.core.hooks.registry import HookRegistry
from iwan_claude.core.plugins.contributor import build_contribution
from iwan_claude.core.plugins.manifest import ManifestError, load_manifest
from iwan_claude.core.plugins.registry import PluginRegistry
from iwan_claude.core.skills.loader import SkillLoader


# 工具：在 tmp 里造一个最小合法插件（官方清单位），可选附加 hooks/.mcp.json
def _make_plugin(
    root: Path, name: str, *, mcp: dict | None = None, hooks: list[dict] | None = None,
) -> Path:
    d = root / name
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "brand_new_field": {"x": 1}}),
        encoding="utf-8",
    )
    if hooks is not None:
        (d / "hooks").mkdir()
        (d / "hooks" / "hooks.json").write_text(
            json.dumps({"hooks": hooks}), encoding="utf-8",
        )
    if mcp is not None:
        (d / ".mcp.json").write_text(
            json.dumps({"mcpServers": mcp}), encoding="utf-8",
        )
    return d


# 功能：官方清单位 + 未知字段静默忽略 + 原生根级 plugin.json 回退 + 非法形态拒绝
# 设计：一条测试走完"宽容读"的四个面（官方/回退/未知键/name 校验），
#       比拆四条省 fixture 噪音；ManifestError 用 pytest.raises 逐例点名
def test_manifest_parsing(tmp_path: Path) -> None:
    official = _make_plugin(tmp_path, "alpha-pkg")
    m = load_manifest(official)
    assert m.name == "alpha-pkg" and m.version == "1.0.0"
    assert m.hooks_file is None and m.mcp_file is None

    native = tmp_path / "beta"
    native.mkdir()
    (native / "plugin.json").write_text('{"name": "beta"}', encoding="utf-8")
    assert load_manifest(native).name == "beta"

    bad = tmp_path / "bad"
    bad.mkdir()
    bad_name = tmp_path / "Bad Name"
    with pytest.raises(ManifestError):
        load_manifest(bad)  # 无清单
    (bad / "plugin.json").write_text('{"name": "Bad Name"}', encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(bad)  # 非 kebab-case
    assert not bad_name.exists()


# 功能：hooks 贡献的变量展开与 argv 化——${IWAN_PYTHON}/${CLAUDE_PLUGIN_ROOT} 在解析后注入
# 设计：Windows 路径反斜杠是这套展开的已知杀手（先展开会炸 JSON），
#       故断言 argv[0]==sys.executable 且脚本路径可寻址；非法键复用配置层校验，测其"逐插件全有或全无"
def test_plugin_hooks_expansion(tmp_path: Path) -> None:
    d = _make_plugin(tmp_path, "hooked", hooks=[
        {"event": "PreToolUse", "matcher": "Bash",
         "command": "${IWAN_PYTHON} \"${CLAUDE_PLUGIN_ROOT}/guard.py\""},
    ])
    m = load_manifest(d)
    assert m.hooks_file is not None
    contrib = build_contribution(m, set())
    assert len(contrib.hooks) == 1
    spec = contrib.hooks[0]
    assert spec.source == "hooked"
    assert spec.argv[0] == sys.executable
    assert str(d) in spec.argv[1]

    d2 = _make_plugin(tmp_path, "broken", hooks=[
        {"event": "PreToolUse", "matcher": "Bash", "command": "x", "bogus": 1},
    ])
    with pytest.raises(ValueError):
        build_contribution(load_manifest(d2), set())


# 功能：MCP 贡献前缀命名 + 与用户配置撞名 → 该 server 跳过、插件标 partial
# 设计：撞名是跨插件共享 reserved 集合的活状态，build_contribution 直测比走
#       registry 少一层账本噪音；重名第二次出现时 reserved 已含前缀名，同样跳过
def test_plugin_mcp_collision(tmp_path: Path) -> None:
    d = _make_plugin(tmp_path, "mcp-one", mcp={
        "srv": {"command": "${CLAUDE_PLUGIN_ROOT}/server.py", "args": ["--x"]},
    })
    m = load_manifest(d)
    assert m.mcp_file is not None
    contrib = build_contribution(m, {"mcp-one__srv"})
    assert contrib.mcp == [] and contrib.notes  # 撞用户保留名 → 跳过
    contrib2 = build_contribution(m, set())
    assert len(contrib2.mcp) == 1
    cfg = contrib2.mcp[0]
    assert cfg.name == "mcp-one__srv" and str(d) in cfg.command and cfg.args == ["--x"]


# 功能：注册表纪律——内置默认全关；坏插件只标 error 不殃及好插件；启用才有贡献
# 设计：builtin_dir 指到 tmp（不依赖 repo plugins/，保证可重复），
#       一次 load 同时埋一个坏清单和一个好包，验证"隔离"；rows 是 GUI 唯一视图，断言它
def test_registry_isolation_and_default_off(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    _make_plugin(builtin, "good-pkg", hooks=[
        {"event": "PreToolUse", "matcher": "*", "command": "echo hi"},
    ])
    broken = builtin / "broken-pkg"
    (broken / ".claude-plugin").mkdir(parents=True)
    (broken / ".claude-plugin" / "plugin.json").write_text("{not json", encoding="utf-8")

    reg = PluginRegistry(
        install_dir=tmp_path / "inst", builtin_dir=builtin,
        ledger_path=tmp_path / "ledger.toml",
    )
    reg.load(set())
    rows = {r["name"]: r for r in reg.rows()}
    assert rows["broken-pkg"]["status"] == "error" and rows["broken-pkg"]["error"]
    assert rows["good-pkg"]["status"] == "ok"
    assert not rows["good-pkg"]["enabled"]
    assert reg.hook_specs() == [] and reg.skill_dirs() == []  # 禁用 = 零贡献

    ok, _ = reg.set_enabled("good-pkg", True)
    assert ok and len(reg.hook_specs()) == 1
    # 账本回灌：新实例只看 ledger 就该恢复启用态
    reg2 = PluginRegistry(
        install_dir=tmp_path / "inst", builtin_dir=builtin,
        ledger_path=tmp_path / "ledger.toml",
    )
    reg2.load(set())
    assert reg2.get("good-pkg") is not None and reg2.get("good-pkg").enabled


# 功能：HookRegistry 双表——配置层全量保留、插件层可整体热替换、匹配序稳定
# 设计：PermissionManager 持对象引用，这里断言"替换后原对象即见新表"的别名语义；
#       _matching 合并序=config 在前，保证用户守卫先跑（确定性）
def test_hook_registry_dual_table() -> None:
    from iwan_claude.core.hooks.spec import HookSpec as S

    def _s(cmd: str, source: str) -> S:
        return S(event="PreToolUse", matcher="Bash", argv=[cmd], timeout_s=1.0, source=source)

    reg = HookRegistry([_s("cfg", "config")])
    assert reg.is_empty() is False
    reg.replace_plugin_hooks([_s("plg", "my-plugin")])
    matched = reg._matching("PreToolUse", "Bash")
    assert [m.source for m in matched] == ["config", "my-plugin"]
    reg.replace_plugin_hooks([])
    assert [m.source for m in reg._matching("PreToolUse", "Bash")] == ["config"]
    assert not reg.is_empty()


# 功能：插件技能目录排在全部既有层之后——同名技能本地压插件
# 设计：chdir tmp 让 .iwan/skills 生效（项目级最高层），与插件目录放同名技能；
#       resolve 返回谁=优先级谁高，直接可证；测毕清空类表防串污染
def test_skill_dirs_local_overrides_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    proj = tmp_path / ".iwan" / "skills"
    proj.mkdir(parents=True)
    (proj / "dup.md").write_text(
        "---\nname: dup\ndescription: 本地版\n---\nlocal body\n", encoding="utf-8",
    )
    plug = tmp_path / "plg" / "skills"
    plug.mkdir(parents=True)
    (plug / "dup.md").write_text(
        "---\nname: dup\ndescription: 插件版\n---\nplugin body\n", encoding="utf-8",
    )
    try:
        monkeypatch.chdir(tmp_path)
        SkillLoader.set_plugin_dirs([plug])
        loader = SkillLoader()
        skill = loader.resolve("dup")
        assert skill is not None and "本地" in skill.description
        assert plug in SkillLoader._skill_dirs()  # 插件层存在且在最末
        assert SkillLoader._skill_dirs()[-1] == plug
    finally:
        # 类表是进程级共享态——必须还原，否则污染同进程后续用例
        SkillLoader.set_plugin_dirs([])
