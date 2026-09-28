# 功能：验证 core/pr.py 的 GitHub origin URL 解析器对三种传输形态与畸形输入的判定
# 设计：parse_github_remote 是纯函数、无 IO——用参数化表驱动把"能解析的形态"
# 与"必须拒绝的形态"各列一组用例，比逐个写 test 函数更能防规则漂移（加一种
# 新 URL 形态时只需加一行）。.git 后缀剥离与"只取前两段路径"是两个最易错点。
from __future__ import annotations

import pytest

from iwan_claude.core.pr import parse_github_remote


# 功能：常见 GitHub origin 形态都能解析出 (owner, repo)
# 设计：ssh scp 式/https/带用户名/ssh:// 前缀/.git 后缀有无 各覆盖一条，
# 主机名故意混用（不锁 github.com 是本模块的显式设计，企业版照用）
@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("git@github.com:longer1225/IwanClaude.git", ("longer1225", "IwanClaude")),
        ("https://github.com/longer1225/IwanClaude.git", ("longer1225", "IwanClaude")),
        ("https://github.com/longer1225/IwanClaude", ("longer1225", "IwanClaude")),
        ("ssh://git@github.com/longer1225/IwanClaude.git", ("longer1225", "IwanClaude")),
        ("https://git.example.com/team/project.git", ("team", "project")),
        ("  git@github.com:o/r.git  ", ("o", "r")),
    ],
)
def test_parse_ok_forms(url: str, want: tuple[str, str]) -> None:
    assert parse_github_remote(url) == want


# 功能：畸形/非 GitHub 形态返回 None，调用方据此给出人类可读错误
# 设计：缺 repo 段、裸路径、空串是 collect_context 三条失败分支的源头输入；
# 用 None 单断言确认"宁缺勿错"——解析器不猜测不兜底
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/onlyowner",
        "just a plain text",
        "",
        "/local/path/repo",
        "git@host:",
    ],
)
def test_parse_rejects(url: str) -> None:
    assert parse_github_remote(url) is None
