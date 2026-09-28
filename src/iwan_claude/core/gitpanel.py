"""
本地 Git 面板支撑模块（M3）— 仓库状态读取 + 工作区操作编排

【学习要点】
1. run_git 从 pr.py 上提到这里共享：PR 面板（远端 GitHub）和 Git 面板（本地
   仓库）用的是同一个"子进程 + 超时 + 永不抛出"的执行原语。放哪边？放被更多
   方依赖的一侧——pr 反过来 import 这里，方向保持"专用依赖通用"。
2. 解析全部走 git 的【机器可读格式】：porcelain=v1、--format=%x1f 单元分隔符。
   人不该解析 git 的默认彩色输出，就像机器不该解析人看的日志。
3. 破坏性分级在本模块如实打标：discard 会丢工作区内容（untracked 走
   `clean -fd -- <paths>`，路径限定是底线——绝不让参数拼错演变成全仓清空）；
   push/pull 是网络动作，调用方（app handler）负责审计日志。
4. 一切失败返回 {ok: False, error: 人类文案}：GUI 把 error 原文上屏，
   git 自己的 stderr 就是最好的诊断文案，我们不重写，只兜底翻译。
"""
from __future__ import annotations

import asyncio
from typing import Any

# 冲突状态对（porcelain XY 列）——U 参与或双 A/双 D 都是未解决合并
_CONFLICT_PAIRS = frozenset({("A", "A"), ("D", "D")})


# 单次 git 子进程：返回 (是否成功, stdout, 失败摘要)；永不抛出（原 pr._git，上提共享）
async def run_git(cwd: str, *args: str, timeout_s: float = 15.0) -> tuple[bool, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            cwd,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        return False, "", f"git {' '.join(args)} 超时（{timeout_s:.0f}s）"
    except FileNotFoundError:
        return False, "", "git 命令不存在——请先安装 git"
    out = stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        return False, out, err or f"git {args[0]} 退出码 {proc.returncode}"
    return True, out, ""


# 解析 `status --porcelain=v1 -b` 输出 → {branch,ahead,behind,files[]}（纯函数，单测靶点）
def parse_status(text: str) -> dict[str, Any]:
    branch, ahead, behind = "", 0, 0
    files: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            branch, ahead, behind = _parse_branch_line(line[3:])
            continue
        if len(line) < 4:
            continue
        x, y = line[0], line[1]
        path = line[3:]
        # 重命名行形如 `R  old -> new`：GUI 展示新路径，old 只作调试余量
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        conflicted = x == "U" or y == "U" or (x, y) in _CONFLICT_PAIRS
        files.append({
            "path": path,
            "index_status": x,
            "worktree_status": y,
            "staged": x not in (" ", "?"),
            "untracked": (x, y) == ("?", "?"),
            "conflicted": conflicted,
        })
    return {"branch": branch, "ahead": ahead, "behind": behind, "files": files}


# 解析分支头行：`main...origin/main [ahead 2, behind 1]` / 游离 HEAD / 空仓库
def _parse_branch_line(head: str) -> tuple[str, int, int]:
    ahead = behind = 0
    name = head
    if "[" in head:
        name, _, tail = head.partition("[")
        for piece in tail.rstrip("]").split(","):
            kv = piece.split()
            if len(kv) == 2 and kv[1].isdigit():
                if kv[0] == "ahead":
                    ahead = int(kv[1])
                elif kv[0] == "behind":
                    behind = int(kv[1])
    name = name.strip()
    if name.startswith("HEAD"):  # 游离 HEAD
        return "", ahead, behind
    if name.startswith("No commits yet"):
        # 空仓库头形如 `No commits yet on main`
        parts = name.split(" on ", 1)
        return (parts[1] if len(parts) == 2 else ""), ahead, behind
    if "..." in name:
        name = name.split("...", 1)[0]
    return name, ahead, behind


# 解析 `for-each-ref refs/heads --format=…%1f…` → [{name,current,upstream}]（current 由调用方传入）
def parse_branches(text: str, current: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        pieces = line.split("\x1f")
        name = pieces[0] if pieces else ""
        upstream = pieces[1] if len(pieces) > 1 else ""
        if not name:
            continue
        rows.append({"name": name, "current": name == current, "upstream": upstream})
    rows.sort(key=lambda r: (not r["current"], r["name"]))
    return rows


# 解析 `log --format=%h%x1f%an%x1f%ad%x1f%s` → [{sha,short_sha,author,date,subject}]
def parse_log(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        pieces = line.split("\x1f")
        if len(pieces) < 4:
            continue
        short, author, date, subject = pieces[0], pieces[1], pieces[2], "\x1f".join(pieces[3:])
        rows.append({
            "sha": short, "short_sha": short, "author": author,
            "date": date, "subject": subject,
        })
    return rows


# git.status 编排：porcelain 一次拿全（分支+领先落后+文件表）；非仓库给出人话
async def status(cwd: str) -> dict[str, Any]:
    ok, text, err = await run_git(cwd, "status", "--porcelain=v1", "-b")
    if not ok:
        hint = err or "git status 失败"
        if "not a git repository" in hint:
            hint = "当前目录不是 git 仓库"
        return {"ok": False, "error": hint, "branch": "", "ahead": 0, "behind": 0, "files": []}
    parsed = parse_status(text)
    return {"ok": True, "error": "", **parsed}


# git.branches 编排：当前分支 + 本地分支表（含 upstream）
async def branches(cwd: str) -> dict[str, Any]:
    ok, cur, err = await run_git(cwd, "branch", "--show-current")
    if not ok:
        return {"ok": False, "error": err or "不是 git 仓库", "current": "", "branches": []}
    ok, text, err2 = await run_git(
        cwd, "for-each-ref", "refs/heads", "--format=%(refname:short)%1f%(upstream:short)"
    )
    if not ok:
        return {"ok": False, "error": err2 or "分支列表读取失败", "current": cur, "branches": []}
    return {"ok": True, "error": "", "current": cur, "branches": parse_branches(text, cur)}


# git.log 编排：最近 limit 条提交（作者/日期/标题；无 %x1f 兼容问题，机器格式）
async def log(cwd: str, limit: int) -> dict[str, Any]:
    n = min(max(limit, 1), 200)
    fmt = "%h%x1f%an%x1f%ad%x1f%s"
    ok, text, err = await run_git(
        cwd, "log", f"--max-count={n}", f"--format={fmt}", "--date=format:%Y-%m-%d %H:%M"
    )
    if not ok:
        hint = err or "git log 失败"
        if "does not have any commits yet" in hint:
            hint = "仓库还没有任何提交"
        return {"ok": False, "error": hint, "entries": []}
    return {"ok": True, "error": "", "entries": parse_log(text)}


# 一次操作的结果壳（output 留给 GUI 折叠展示；error 为 git 原文或兜底文案）
def _op_result(ok: bool, out: str, err: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": ok, "error": err, "output": out, **(extra or {})}


# git.stage：git add -- <paths>（paths 为空视作无操作）
async def stage(cwd: str, paths: list[str]) -> dict[str, Any]:
    if not paths:
        return _op_result(True, "", "未选择文件")
    ok, out, err = await run_git(cwd, "add", "--", *paths)
    return _op_result(ok, out, err)


# git.unstage：把暂存区改动退回工作区（reset 对全版本 git 都稳）
async def unstage(cwd: str, paths: list[str]) -> dict[str, Any]:
    if not paths:
        return _op_result(True, "", "未选择文件")
    ok, out, err = await run_git(cwd, "reset", "HEAD", "--", *paths)
    return _op_result(ok, out, err)


# git.discard：tracked 用 checkout 还原，untracked 用 clean 限定删除——破坏性由 GUI 双确认兜底
async def discard(cwd: str, paths: list[str]) -> dict[str, Any]:
    if not paths:
        return _op_result(True, "", "未选择文件")
    st = await status(cwd)
    if not st["ok"]:
        return _op_result(False, "", st["error"])
    flags = {f["path"]: f for f in st["files"]}
    untracked = [p for p in paths if flags.get(p, {}).get("untracked")]
    tracked = [p for p in paths if p not in untracked]
    outs: list[str] = []
    if tracked:
        ok, out, err = await run_git(cwd, "checkout", "--", *tracked)
        if not ok:
            return _op_result(False, out, err)
        outs.append(out)
    if untracked:
        # -fd 且【必须】带 -- 路径白名单：防止任何一步演变为全仓清空
        ok, out, err = await run_git(cwd, "clean", "-fd", "--", *untracked)
        if not ok:
            return _op_result(False, out, err)
        outs.append(out)
    return _op_result(True, "\n".join(o for o in outs if o), "")


# git.commit：先验暂存区非空，提交后回读短 sha 供 GUI 回执
async def commit(cwd: str, message: str) -> dict[str, Any]:
    if not message.strip():
        return _op_result(False, "", "提交信息不能为空")
    # diff --cached --quiet：退出码 1 = 有暂存改动；0 = 暂存区干净
    okq, _, _ = await run_git(cwd, "diff", "--cached", "--quiet")
    if okq:
        return _op_result(False, "", "暂存区为空——先 Stage 要提交的文件")
    ok, out, err = await run_git(cwd, "commit", "-m", message)
    if not ok:
        return _op_result(False, out, err)
    _, sha, _ = await run_git(cwd, "rev-parse", "--short", "HEAD")
    return _op_result(True, out, "", {"sha": sha})


# git.checkout：切分支（脏树被 git 拒时 error 原文回显，GUI 不再加工）
async def checkout(cwd: str, name: str) -> dict[str, Any]:
    if not name.strip():
        return _op_result(False, "", "未指定分支名")
    ok, out, err = await run_git(cwd, "checkout", name.strip())
    return _op_result(ok, out, err)


# git.pull：--ff-only 是确定性策略——需要 rebase/merge 时让 git 报错，我们不替用户做主
async def pull(cwd: str) -> dict[str, Any]:
    ok, out, err = await run_git(cwd, "pull", "--ff-only", timeout_s=120.0)
    return _op_result(ok, out, err)


# git.push：-u origin HEAD 与 pr.create 的推送语义同形（首次自动建上游）
async def push(cwd: str) -> dict[str, Any]:
    ok, out, err = await run_git(cwd, "push", "-u", "origin", "HEAD", timeout_s=120.0)
    return _op_result(ok, out, err)
