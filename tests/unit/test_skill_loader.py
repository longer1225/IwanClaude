from __future__ import annotations

from pathlib import Path

import pytest

from iwan_claude.core.skills.loader import Skill, SkillLoader


# 功能：内建 review skill 应能被 SkillLoader 查找到
# 设计：直接调用 resolve("review")，不依赖文件系统之外的任何状态
def test_builtin_skill_found() -> None:
    loader = SkillLoader()
    skill = loader.resolve("review")
    assert skill is not None
    assert skill.name == "review"
    assert "审查" in skill.description or "review" in skill.description.lower()
    assert skill.system_prompt_template != ""


# 功能：内建 init / summarize / orchestrate skill 均可找到
# 设计：列举所有内建 skill 名，断言均能解析
@pytest.mark.parametrize("name", ["init", "review", "summarize", "orchestrate"])
def test_all_builtin_skills_found(name: str) -> None:
    loader = SkillLoader()
    skill = loader.resolve(name)
    assert skill is not None, f"builtin skill '{name}' not found"


# 功能：不存在的 skill 名应返回 None
# 设计：查找一个不存在的名称，断言 resolve 返回 None 而非抛异常
def test_unknown_skill_returns_none() -> None:
    loader = SkillLoader()
    result = loader.resolve("nonexistent_skill_xyz")
    assert result is None


# 功能：render_prompt 应将 $ARGUMENTS 替换为传入的参数字符串
# 设计：构造含 $ARGUMENTS 的 skill，验证 render_prompt 结果不含 "$ARGUMENTS" 且含参数值
def test_arguments_substituted() -> None:
    loader = SkillLoader()
    skill = Skill(
        name="test",
        description="test skill",
        system_prompt_template="Review this: $ARGUMENTS\nPlease be thorough.",
        allowed_tools=[],
    )
    rendered = loader.render_prompt(skill, "src/foo.py")
    assert "$ARGUMENTS" not in rendered
    assert "src/foo.py" in rendered


# 功能：frontmatter 中的 allowed_tools 列表应被正确解析
# 设计：构造含 allowed_tools 的 Markdown 文件，通过 _parse_skill_file 解析并验证结果
def test_frontmatter_parsed(tmp_path: Path) -> None:
    from iwan_claude.core.skills.loader import _parse_skill_file

    content = """\
---
name: custom
description: 自定义 skill 测试
allowed_tools:
  - read_file
  - bash
---
你是一个测试助手，目标：$ARGUMENTS
"""
    p = tmp_path / "custom.md"
    p.write_text(content, encoding="utf-8")
    skill = _parse_skill_file(p)
    assert skill.name == "custom"
    assert skill.description == "自定义 skill 测试"
    assert "read_file" in skill.allowed_tools
    assert "bash" in skill.allowed_tools
    assert "$ARGUMENTS" in skill.system_prompt_template


# 功能：无 frontmatter 的 Markdown 文件仍可加载，allowed_tools 为空列表
# 设计：写入纯正文 Markdown，断言解析成功且 allowed_tools=[]
def test_no_frontmatter(tmp_path: Path) -> None:
    from iwan_claude.core.skills.loader import _parse_skill_file

    content = "你是助手，请帮助用户完成任务：$ARGUMENTS\n"
    p = tmp_path / "plain.md"
    p.write_text(content, encoding="utf-8")
    skill = _parse_skill_file(p)
    assert skill.name == "plain"
    assert skill.allowed_tools == []
    assert "你是助手" in skill.system_prompt_template


# 功能：项目本地 skill 应覆盖内建同名 skill
# 设计：在 .iwan/skills/ 中写入同名文件，用 monkeypatch 修改 cwd，断言加载到的是本地版本
def test_project_overrides_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_skills = tmp_path / ".iwan" / "skills"
    local_skills.mkdir(parents=True)
    (local_skills / "review.md").write_text(
        "---\nname: review\ndescription: local override\n---\nlocal system prompt $ARGUMENTS\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    loader = SkillLoader()
    skill = loader.resolve("review")
    assert skill is not None
    assert skill.description == "local override"
    assert "local system prompt" in skill.system_prompt_template


# 功能：Claude 官方风格 frontmatter（连字符字段名/内联逗号分隔/带引号值/未知字段）应完整解析
# 设计：字段组合刻意取自真实社区技能包形态——allowed-tools 内联串是 Claude 规范
# 首选写法（与我们的 - 列表式并存），license/metadata 等未知字段必须静默忽略而非
# 炸行；解析入口直接用 _parse_skill_file，绕开搜索路径层，让失败信息直指解析器
def test_claude_style_frontmatter(tmp_path: Path) -> None:
    from iwan_claude.core.skills.loader import _parse_skill_file

    content = """\
---
name: "pdf-forms"
description: >-
  Fill PDF forms by reading
  field definitions first.
allowed-tools: Bash(pdftk:*), Read, Write
license: Apache-2.0
metadata:
  author: some-community-repo
version: 1
---

处理 $ARGUMENTS 中的 PDF 表单。
"""
    p = tmp_path / "SKILL.md"
    p.write_text(content, encoding="utf-8")
    skill = _parse_skill_file(p)
    assert skill.name == "pdf-forms"  # 引号值被剥净
    assert skill.description == "Fill PDF forms by reading field definitions first."  # >- 折叠
    assert skill.allowed_tools == ["Bash(pdftk:*)", "Read", "Write"]  # 逗号+空格混合拆分
    assert "PDF 表单" in skill.system_prompt_template


# 功能：项目级 .claude/skills/<name>/SKILL.md 应被 resolve 找到且出现在 list_all
# 设计：目录格式（SKILL.md）+ 无 name 字段组合——同时验证 SKILL.md 默认名取父目录
# 而非 stem "SKILL"（后者会让所有无 name 目录技能互相撞名）；resolve 与 list_all
# 双通道都查，防止"能按名找到、列表看不见"的三清单漂移型回归
def test_claude_dir_discovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / ".claude" / "skills" / "commit-helper"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\ndescription: 生成规范 commit message\n---\n按 $ARGUMENTS 生成\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    loader = SkillLoader()
    skill = loader.resolve("commit-helper")
    assert skill is not None
    assert skill.name == "commit-helper"  # 默认名=父目录名
    assert "commit-helper" in loader.list_all()


# 功能：同名技能项目 .iwan/skills 必须压过项目 .claude/skills（借库不让位）
# 设计：两个同名技能放同一项目、内容刻意可区分；若排序反了，第三方库会悄悄
# 顶掉用户自己的技能——这是兼容策略的底线语义，必须用断言焊死。用
# list_all_skills（按 skill.name 去重）而非 resolve 验证，覆盖 dict 反向覆盖链路
def test_iwan_native_beats_claude_dir_on_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_iwan = tmp_path / ".iwan" / "skills"
    proj_iwan.mkdir(parents=True)
    (proj_iwan / "twin.md").write_text(
        "---\nname: twin\ndescription: iwan native\n---\nnative $ARGUMENTS\n",
        encoding="utf-8",
    )
    proj_claude = tmp_path / ".claude" / "skills" / "twin"
    proj_claude.mkdir(parents=True)
    (proj_claude / "SKILL.md").write_text(
        "---\nname: twin\ndescription: claude import\n---\nimport $ARGUMENTS\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    loader = SkillLoader()
    twins = [s for s in loader.list_all_skills() if s.name == "twin"]
    assert len(twins) == 1  # 去重后只剩一个
    assert twins[0].description == "iwan native"  # 且必须是原生版
