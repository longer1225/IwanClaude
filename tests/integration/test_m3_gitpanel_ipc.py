# 功能：验证 git.* 十个 RPC 方法名在真实 daemon 上可达且结果形状正确
# 设计：原始 TCP + JSON-RPC 帧（同 schedule IPC 纪律）。重活（stage/commit/
# pull/push 的 git 语义）已由 test_gitpanel_ops 在子进程层测透，这里只测
# "wire → handler → model_validate → 帧"这条通路：仓库端走一次
# status→stage→commit→log→branches 的最小闭环，错误端用"不存在的目录"
# 保证 error 人话照样穿过 pydantic 收口送达（GUI 直接上屏的就是这串字）。
# 环境无 git 时跳过。
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest


# 发一帧 JSON-RPC 并取回带指定 id 的响应（同连接多次复用）
async def _rpc(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, rid: str, method: str, params: dict) -> dict:
    req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    writer.write((json.dumps(req) + "\n").encode("utf-8"))
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=15.0)
    return json.loads(line)


# 在临时目录建一个带一次提交的小仓库（与单元层同配方：钉死身份与行尾）
def _make_repo(root: Path) -> None:
    env_git = ["git", "-C", str(root)]
    subprocess.run(env_git + ["init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(env_git + ["config", "user.name", "IPC"], check=True, capture_output=True)
    subprocess.run(env_git + ["config", "user.email", "ipc@example.com"], check=True, capture_output=True)
    subprocess.run(env_git + ["config", "core.autocrlf", "false"], check=True, capture_output=True)
    (root / "a.txt").write_text("1\n", encoding="utf-8")
    subprocess.run(env_git + ["add", "."], check=True, capture_output=True)
    subprocess.run(env_git + ["commit", "-m", "base"], check=True, capture_output=True)


# 功能：git.status→stage→commit→log→branches 端到端最小闭环 + 不存在目录错误路径
# 设计：stage/commit 用带路径参数的写命令各发一发，随后 log 必须出现新提交标题
# ——跨命令因果链是"handler 全部接对了"的最强证据，单看每条 ok 会漏掉串错方法
async def test_git_panel_ipc_roundtrip(
    running_daemon: subprocess.Popen[bytes], free_port: int, tmp_path: Path
) -> None:
    if shutil.which("git") is None:
        pytest.skip("环境无 git")
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        cwd = str(repo)
        # 错误路径用"不存在的目录"触发——跨机器确定。不能拿"非仓库空目录"当
        # 靶子：部分机器（含本机）Temp 的祖先是误 init 的仓库，向上穿透会 ok=True
        r = await _rpc(reader, writer, "g0", "git.status", {"cwd": str(tmp_path / "no-such-dir")})
        assert r["result"]["ok"] is False and r["result"]["error"]

        r = await _rpc(reader, writer, "g1", "git.status", {"cwd": cwd})
        assert "result" in r, r
        assert r["result"]["ok"] is True and r["result"]["branch"] == "main"

        (repo / "b.txt").write_text("2\n", encoding="utf-8")
        r = await _rpc(reader, writer, "g2", "git.status", {"cwd": cwd})
        assert any(f["path"] == "b.txt" and f["untracked"] for f in r["result"]["files"])

        r = await _rpc(reader, writer, "g3", "git.stage", {"cwd": cwd, "paths": ["b.txt"]})
        assert r["result"]["ok"] is True
        r = await _rpc(reader, writer, "g4", "git.commit", {"cwd": cwd, "message": "ipc: 新提交"})
        assert r["result"]["ok"] is True and len(r["result"]["sha"]) >= 4

        r = await _rpc(reader, writer, "g5", "git.log", {"cwd": cwd, "limit": 10})
        assert [e["subject"] for e in r["result"]["entries"]][0] == "ipc: 新提交"

        r = await _rpc(reader, writer, "g6", "git.branches", {"cwd": cwd})
        assert r["result"]["current"] == "main"
        assert any(b["name"] == "main" and b["current"] for b in r["result"]["branches"])

        # 未跟踪文件走 discard 的 clean 分支——确认帧返回且文件真没了
        (repo / "c.tmp").write_text("x\n", encoding="utf-8")
        r = await _rpc(reader, writer, "g7", "git.discard", {"cwd": cwd, "paths": ["c.tmp"]})
        assert r["result"]["ok"] is True
        assert not (repo / "c.tmp").exists()

        r = await _rpc(reader, writer, "g8", "git.unstage", {"cwd": cwd, "paths": []})
        assert r["result"]["ok"] is True  # 空 paths 视作无操作，不是错误
        r = await _rpc(reader, writer, "g9", "git.checkout", {"cwd": cwd, "name": "main"})
        assert r["result"]["ok"] is True
    finally:
        writer.close()
        await writer.wait_closed()
