# gitpanel 解析器单元测试：三个纯函数吃固定文本，不碰真 git
# 整组设计意图：解析器是全模块最容易因 git 版本输出差异碎掉的地方，
# 把它们从编排里剥出来用真实 porcelain / for-each-ref / log 样本钉死。
from __future__ import annotations

from iwan_claude.core.gitpanel import _parse_branch_line, parse_branches, parse_log, parse_status


# 功能：porcelain v1 常规行——未跟踪/工作区改/暂存改三种状态标记各归其位
# 设计：直接手写 git 真实输出片段而非调 git 生成，输入完全确定；断言逐字段而非整 dict，定位失败时能看出是哪个标记判错
def test_parse_status_basic_rows() -> None:
    text = "\n".join([
        "## main...origin/main",
        "?? new.txt",
        " M work.txt",
        "M  staged.txt",
    ])
    out = parse_status(text)
    assert out["branch"] == "main"
    assert out["ahead"] == 0 and out["behind"] == 0
    by_path = {f["path"]: f for f in out["files"]}
    assert set(by_path) == {"new.txt", "work.txt", "staged.txt"}
    assert by_path["new.txt"]["untracked"] and not by_path["new.txt"]["staged"]
    assert by_path["work.txt"]["worktree_status"] == "M" and not by_path["work.txt"]["staged"]
    assert by_path["staged.txt"]["staged"] and by_path["staged.txt"]["index_status"] == "M"


# 功能：分支头行的 ahead/behind 计数——含单值与双值两种 [ahead 2, behind 1] 形态
# 设计：双值逗号分隔是最易解析错的形态（很多实现只取第一个），单独给一条断言
def test_parse_status_ahead_behind() -> None:
    out = parse_status("## feat...origin/feat [ahead 2, behind 1]\n")
    assert (out["branch"], out["ahead"], out["behind"]) == ("feat", 2, 1)
    out2 = parse_status("## main...origin/main [behind 3]")
    assert (out2["branch"], out2["ahead"], out2["behind"]) == ("main", 0, 3)


# 功能：重命名行 `R  old -> new` 取新路径展示，且仍算 staged
# 设计：rename 是 porcelain 里唯一行内含 " -> " 的形态，路径切割错了 GUI 会对不上 git add 的参数
def test_parse_status_rename() -> None:
    out = parse_status("## main\nR  a/old.txt -> b/new.txt\n")
    f = out["files"][0]
    assert f["path"] == "b/new.txt" and f["staged"]
    assert f["index_status"] == "R"


# 功能：冲突态识别——UU 与双 A 两类都要打 conflicted，且不误伤正常行
# 设计：conflicted 走"或"链，最危险的是误报（普通 M 行被标冲突会让 GUI 拒绝 discard），故同时断言正常行不受影响
def test_parse_status_conflict() -> None:
    out = parse_status("## main\nUU both.txt\nAA added.txt\n M clean.txt\n")
    by_path = {f["path"]: f for f in out["files"]}
    assert by_path["both.txt"]["conflicted"]
    assert by_path["added.txt"]["conflicted"]
    assert not by_path["clean.txt"]["conflicted"]


# 功能：游离 HEAD 与空仓库两种特殊分支头
# 设计：`HEAD (no branch)` 应产出空 branch（GUI 显示 (游离)），`No commits yet on main` 要抠出 main——两条都靠 _parse_branch_line 的分支结构，故意用独立输入钉死
def test_parse_branch_line_special_heads() -> None:
    assert _parse_branch_line("HEAD (no branch)") == ("", 0, 0)
    assert _parse_branch_line("No commits yet on main") == ("main", 0, 0)
    assert _parse_branch_line("main...origin/main") == ("main", 0, 0)


# 功能：parse_branches 解析 %1f 分隔行并让当前分支排在最前
# 设计：排序是 GUI 体验点（当前分支置顶），用"字典序在后"的分支名当 current，断言顺序而非集合，才能同时验证解析和排序
def test_parse_branches_order_and_fields() -> None:
    text = "main\x1forigin/main\nzzz\x1f\n"
    rows = parse_branches(text, current="zzz")
    assert [r["name"] for r in rows] == ["zzz", "main"]
    assert rows[0]["current"] and rows[0]["upstream"] == ""
    assert rows[1]["upstream"] == "origin/main"


# 功能：parse_log 处理 subject 里嵌 %x1f 的畸形/极端行（按设计把首格之后的全部拼回 subject）
# 设计：字段数校验用 `>= 4` 而非 `== 4` 就是为了这种行；若实现误用恰好匹配，这条测试立即红
def test_parse_log_subject_with_delimiter() -> None:
    line = "ab12cd\x1fLJ\x1f2026-09-25 10:00\x1fsub\x1fject tail"
    rows = parse_log(line)
    assert rows[0]["short_sha"] == "ab12cd"
    assert rows[0]["subject"] == "sub\x1fject tail"
