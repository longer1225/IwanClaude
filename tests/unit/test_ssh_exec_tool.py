# 功能：build_ssh_argv 的旗标集与 ssh_exec 工具的分支（未知连接/argv 透传/超时/非零退出/私钥缺失）
# 设计：子进程用假 proc 整体截获——本层不测 ssh 语义（远端对错不归 daemon 管），
# 测的是 daemon 交给 ssh.exe 的 argv 是否携带全部安全旗标（BatchMode/独立
# known_hosts/-i），以及五条错误分支各自的 is_error 与文案。key_paths 指进
# tmp 防止测试结果取决于本机 ~/.iwan/ssh 是否存在密钥。
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.ssh.argv import build_ssh_argv
from iwan_claude.core.ssh.connections import SshConnStore
from iwan_claude.core.tools.builtin import ssh_exec as se

CONN = {"id": "abc", "name": "lab", "host": "10.1.1.7", "user": "ops", "port": 2222, "key_file": ""}


def test_build_argv_flags() -> None:
    # 功能：默认 argv 含 BatchMode/ConnectTimeout/独立 known_hosts/严格 hostkey/-p 与 user@host
    # 设计：整串断言按元素查——漂移顺序无害（ssh 不挑旗标顺序），但丢旗标是
    # 安全事故（丢 BatchMode=会挂等密码），所以逐个 in 而不是逐位 ==
    argv = build_ssh_argv(CONN, "uptime", known_hosts="K:/kh")
    joined = " ".join(argv)
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in joined
    assert "ConnectTimeout=10" in joined
    assert "UserKnownHostsFile=K:/kh" in joined
    assert "StrictHostKeyChecking=yes" in joined
    assert "-p" in argv and "2222" in argv
    assert "ops@10.1.1.7" in argv
    assert argv[-1] == "uptime"
    assert "-tt" not in argv and "-i" not in argv


def test_build_argv_identity_and_tty() -> None:
    # 功能：identity 触发 -i + IdentitiesOnly；allocate_tty 追加 -tt；command=None 不带尾参
    # 设计：IdentitiesOnly 与 -i 必须同进同出（否则 ssh 还会试 agent 里的其他钥匙，
    # 登录成功但用的不是被批准的那把）——同断言锁死这个配对
    argv = build_ssh_argv(CONN, None, known_hosts="kh", identity="K:/id", allocate_tty=True)
    assert "-i" in argv and argv[argv.index("-i") + 1] == "K:/id"
    assert "IdentitiesOnly=yes" in argv
    assert "-tt" in argv
    assert argv[-1] == "ops@10.1.1.7"


class _FakeProc:
    """替身子进程：communicate 回放预设输出，记录 kill"""

    def __init__(self, out: bytes = b"", rc: int = 0, hang: bool = False) -> None:
        self.pid = 424242
        self.returncode = rc
        self._out = out
        self._hang = hang
        self.killed = False

    async def communicate(self, _data: Any = None) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.sleep(3600)
        return self._out, b""

    def kill(self) -> None:
        # kill 后解除 hang：真实子进程被杀后 communicate 立即返回，假 proc 同语义
        self.killed = True
        self._hang = False


@pytest.fixture()
def tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> se.SshExecTool:
    # 功能：给出连接库/密钥路径都沙箱化的 SshExecTool 实例
    # 设计：两条注入缺一不可——store 指 tmp 让"未知 id"分支可控；
    # key_paths 指 tmp 让 identity 决策不随本机是否已有 id_ed25519 而变
    t = se.SshExecTool()
    t._store = SshConnStore(path=tmp_path / "connections.json")
    t._store.add("lab", "10.1.1.7", "ops", 2222)
    monkeypatch.setattr(se, "key_paths", lambda base=None: (
        tmp_path / "id_ed25519", tmp_path / "id_ed25519.pub", tmp_path / "known_hosts"
    ))
    return t


# 功能：未知 conn_id → is_error=invalid_input，且提示里带上可用连接清单
# 设计：给模型"错在哪+有什么可用"是工具文案的自救原则——断言清单非空即证明
# _conn_hint 真的接上了 store，而不是回一串空枚举
async def test_unknown_conn(tool: se.SshExecTool) -> None:
    r = await tool.invoke({"conn_id": "ghost", "command": "uptime"})
    assert r.is_error and r.error_type == "invalid_input"
    assert "ghost" in r.content and "lab" in r.content


# 功能：默认（无 key_file、默认私钥不存在）→ argv 无 -i，known_hosts 指沙箱路径，输出原样返回
# 设计：identity=None 时绝不硬塞 -i（存在性交给调用方判定是本模块契约）；
# rc=0 空输出走 "[no output]" 收口——模型收到空串会误以为工具没跑
async def test_exec_ok_no_identity(tool: se.SshExecTool, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_exec(*args: Any, **kw: Any) -> _FakeProc:
        captured["argv"] = list(args)
        return _FakeProc(out=b" 15:04:02 up 3 days\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    conn_id = tool._store.list()[0]["id"]
    r = await tool.invoke({"conn_id": conn_id, "command": "uptime"})
    assert not r.is_error and "up 3 days" in r.content
    argv = captured["argv"]
    assert "-i" not in argv and "ops@10.1.1.7" in argv
    assert any(str(a).startswith("UserKnownHostsFile=") and "known_hosts" in str(a) for a in argv)
    assert "BatchMode=yes" in " ".join(argv)


# 功能：连接配置了不存在的 key_file → 直接 invalid_input，不起子进程
# 设计：这是"配置烂了"而不是"远端拒了"，报错必须发生在 spawn 前——
# 若放行给 ssh -i missing，BatchMode 的失败文案会把模型引向"去信任主机"的歧路
async def test_missing_key_file(tool: se.SshExecTool, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: Any, **k: Any) -> None:
        raise AssertionError("不该起子进程")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    row = tool._store.list()[0]
    tool._store.update(row["id"], {"key_file": "Z:/nope/id"})
    r = await tool.invoke({"conn_id": row["id"], "command": "ls"})
    assert r.is_error and r.error_type == "invalid_input" and "私钥不存在" in r.content


# 功能：远端非零退出 → is_error=runtime_error 且 [exit N] 头 + ssh 诊断原文保留
# 设计：诊断文本是模型下一步行动的依据（hostkey 不信任会教它去找用户点信任），
# 断言"原文包含"而非只断 is_error——截掉诊断的错误包装等于没报错
async def test_remote_nonzero(tool: se.SshExecTool, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*args: Any, **kw: Any) -> _FakeProc:
        return _FakeProc(out=b"Permission denied (publickey).\n", rc=255)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    conn_id = tool._store.list()[0]["id"]
    r = await tool.invoke({"conn_id": conn_id, "command": "whoami"})
    assert r.is_error and r.content.startswith("[exit 255]")
    assert "publickey" in r.content


# 功能：超时 → kill 被调用且 error_type=timeout
# 设计：hang 的 communicate + timeout_s=1 真实走 wait_for 超时路径；
# 断言 fake.kill 证明"不等 ssh 自然退出"——只断文案会漏掉进程没杀的场景
async def test_timeout_kills(tool: se.SshExecTool, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeProc(hang=True)

    async def fake_exec(*args: Any, **kw: Any) -> _FakeProc:
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    conn_id = tool._store.list()[0]["id"]
    r = await tool.invoke({"conn_id": conn_id, "command": "sleep 999", "timeout_s": 1})
    assert r.is_error and r.error_type == "timeout" and fake.killed
