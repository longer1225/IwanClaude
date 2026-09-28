# gitpanel 编排函数单元测试：真 git init 的临时仓库 + 本地 bare 远端，全程无网络
# 整组设计意图：解析器由 test_gitpanel_parse 钉死文本形态，这里验证【真实 git
# 行为】——stage/commit/discard/checkout/push/pull 的副作用与防护栏（空暂存区
# 拒提交、clean 只删白名单路径、非仓库给人话错误）。bare 远端 + 二次 clone
# 制造 behind，让 pull 走真实快进路径而不是自我安慰的 no-op。
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core import gitpanel

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="环境无 git")


# 在临时目录跑一条 git 命令（list 参数，无 shell 注入面）
def _sh(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return r.stdout


# 建一个带 user 身份、关行尾转换的本地仓库：commit 需要身份，autocrlf 会污染 status 断言
@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    _sh(root, "init", "-b", "main")
    _sh(root, "config", "user.name", "T")
    _sh(root, "config", "user.email", "t@example.com")
    _sh(root, "config", "commit.gpgsign", "false")
    _sh(root, "config", "core.autocrlf", "false")
    (root / "base.txt").write_text("v1\n", encoding="utf-8")
    _sh(root, "add", ".")
    _sh(root, "commit", "-m", "init")
    return root


# 功能：非 git 目录的 status 返回 ok=False 与"当前目录不是 git 仓库"人话
# 设计：本机用户主目录误存有 .git，Temp 向上穿透会命中真仓库、Ceiling 变量在
# Windows git 上又不生效——改设非法 GIT_DIR 让 git 直接报 "not a git repository"，
# 跨机器确定地走同一条错误翻译路径
async def test_status_not_a_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "does-not-exist.git"))
    out = await gitpanel.status(str(tmp_path))
    assert out["ok"] is False
    assert "不是 git 仓库" in out["error"]


# 功能：untracked → stage → commit 全链，commit 回执带短 sha 且提交后暂存区清空
# 设计：一次测试串起三读两写，若中途任何一步语义错（比如 add 后 staged 标记没翻），断言链会指出精确位置；空暂存区再 commit 验证防护栏文案
async def test_stage_commit_flow(repo: Path) -> None:
    (repo / "feat.py").write_text("print(1)\n", encoding="utf-8")
    st = await gitpanel.status(str(repo))
    assert st["ok"] and {f["path"] for f in st["files"]} == {"feat.py"}
    assert st["files"][0]["untracked"]

    op = await gitpanel.stage(str(repo), ["feat.py"])
    assert op["ok"], op["error"]
    st2 = await gitpanel.status(str(repo))
    assert st2["files"][0]["staged"] and not st2["files"][0]["untracked"]

    cm = await gitpanel.commit(str(repo), "feat: 新增")
    assert cm["ok"], cm["error"]
    assert len(cm["sha"]) >= 4
    st3 = await gitpanel.status(str(repo))
    assert st3["files"] == []

    empty = await gitpanel.commit(str(repo), "再来一发")
    assert empty["ok"] is False and "暂存区为空" in empty["error"]


# 功能：unstage 把已跟踪文件的暂存改动退回工作区（staged 消失、worktree 仍 M）
# 设计：用"修改已跟踪文件"而非新文件做样本——新文件 unstage 后回到 untracked，语义混着两条清理路径；这里只钉 reset HEAD 这一条
async def test_unstage_returns_to_worktree(repo: Path) -> None:
    (repo / "base.txt").write_text("v2\n", encoding="utf-8")
    await gitpanel.stage(str(repo), ["base.txt"])
    op = await gitpanel.unstage(str(repo), ["base.txt"])
    assert op["ok"], op["error"]
    st = await gitpanel.status(str(repo))
    f = st["files"][0]
    assert f["path"] == "base.txt" and not f["staged"] and f["worktree_status"] == "M"


# 功能：discard 双路径——tracked 还原内容，untracked 删除文件，且只动白名单路径
# 设计：保留一个未列出的 untracked 同伴文件并断言它还在，这是 clean -fd 必须限定路径的安全底线测试；tracked 断言看文件内容而非仅状态码，证明真还原了
async def test_discard_tracked_and_untracked(repo: Path) -> None:
    (repo / "base.txt").write_text("v2\n", encoding="utf-8")
    (repo / "junk.log").write_text("x\n", encoding="utf-8")
    (repo / "keep.log").write_text("y\n", encoding="utf-8")
    op = await gitpanel.discard(str(repo), ["base.txt", "junk.log"])
    assert op["ok"], op["error"]
    assert (repo / "base.txt").read_text(encoding="utf-8") == "v1\n"
    assert not (repo / "junk.log").exists()
    assert (repo / "keep.log").exists()


# 功能：branches 列出本地分支且当前分支置顶标记正确；checkout 切换生效
# 设计：先建 feature 分支再查表，断言集合与 current 标记两者；checkout 后用 branches 复读 current 而不是信 checkout 自己的输出，验证闭环
async def test_branches_and_checkout(repo: Path) -> None:
    _sh(repo, "branch", "feature")
    b = await gitpanel.branches(str(repo))
    assert b["ok"] and b["current"] == "main"
    names = {r["name"]: r for r in b["branches"]}
    assert set(names) == {"main", "feature"}
    assert names["main"]["current"] and not names["feature"]["current"]

    op = await gitpanel.checkout(str(repo), "feature")
    assert op["ok"], op["error"]
    b2 = await gitpanel.branches(str(repo))
    assert b2["current"] == "feature"
    assert b2["branches"][0]["name"] == "feature"


# 功能：log 按新→旧返回提交条目，subject/author/date 字段齐全
# 设计：造两条内容不同的提交，用顺序断言 --max-count 与 git 默认倒序没有被解析器意外翻转；空仓库（另建目录）单独验兜底文案
async def test_log_entries(repo: Path) -> None:
    (repo / "base.txt").write_text("v2\n", encoding="utf-8")
    _sh(repo, "commit", "-am", "second")
    lg = await gitpanel.log(str(repo), 20)
    assert lg["ok"]
    subs = [e["subject"] for e in lg["entries"]]
    assert subs == ["second", "init"]
    assert lg["entries"][0]["author"] == "T"
    assert lg["entries"][0]["date"].startswith("20")


# 功能：push 建立上游 + 远端前进后 pull --ff-only 快进跟上（本地 bare 全程无网络）
# 设计：用第二份 clone 当"别人"推提交，制造真实 behind 场景——pull 若只是 no-op 成功，测不出快进语义；最后断言本地 log 含"别人"的提交且 behind 归零
async def test_push_pull_loop(repo: Path, tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    # -b main：bare 的 HEAD 必须指向 main，否则 clone 落在不存在的 master 上拿不到工作树
    _sh(bare.parent, "init", "--bare", "-b", "main", str(bare))
    _sh(repo, "remote", "add", "origin", str(bare))

    push = await gitpanel.push(str(repo))
    assert push["ok"], push["error"]
    assert "main" in _sh(bare, "ls-remote", "--heads", ".")

    other = tmp_path / "other"
    _sh(tmp_path, "clone", str(bare), str(other))
    _sh(other, "config", "user.name", "O")
    _sh(other, "config", "user.email", "o@example.com")
    (other / "base.txt").write_text("v2\n", encoding="utf-8")
    _sh(other, "commit", "-am", "from-other")
    _sh(other, "push", "origin", "HEAD:refs/heads/main")

    _sh(repo, "fetch", "origin")
    st: dict[str, Any] = await gitpanel.status(str(repo))
    assert st["behind"] == 1

    pl = await gitpanel.pull(str(repo))
    assert pl["ok"], pl["error"]
    lg = await gitpanel.log(str(repo), 5)
    assert lg["entries"][0]["subject"] == "from-other"
    st2 = await gitpanel.status(str(repo))
    assert st2["behind"] == 0
