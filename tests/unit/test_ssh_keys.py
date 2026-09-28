"""
SSH 密钥与主机信任单测（M4a）

【学习要点】
1. 分两层测：纯函数（known_hosts_entry / _entry_present）不开子进程直接测；
   工具链函数（key_generate / host_trust）真跑 ssh-keygen——这是"零新依赖
   直接驱动系统 OpenSSH"决策的成本所在，测的就是这条依赖边本身。
2. host_trust 用【关闭端口】模拟不可达：比打外网快、比 mock 子进程真——
   验证的是"失败必须 ok=False 且绝不写 known_hosts"这条安全不变式。
"""
from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.ssh import keys as ssh_keys

# 工具链缺失时整组跳过：这一族的契约就是"系统 OpenSSH 可用"，mock 它没意义
_has_ssh = shutil.which("ssh-keygen") is not None and shutil.which("ssh-keyscan") is not None
pytestmark_tool = pytest.mark.skipif(not _has_ssh, reason="需要系统 OpenSSH 工具链")


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    # 功能：所有密钥操作指向临时目录，绝不碰真实 ~/.iwan/ssh
    # 设计：keys.py 每个公开函数都收 base 参数（默认 None 才锚生产路径），
    # fixture 只做转发；真实目录被测试污染是密钥类测试的头号大忌
    return tmp_path


# 功能：known_hosts_entry 对 22 端口给裸 host、非 22 给 [host]:port
# 设计：这个格式是 OpenSSH known_hosts 的硬规范——写错了 ssh 会当作
# 不同主机、每连一次报一次 MITM 警告；纯字符串函数，逐端口枚举最便宜
def test_entry_format() -> None:
    assert ssh_keys.known_hosts_entry("example.com", 22) == "example.com"
    assert ssh_keys.known_hosts_entry("10.0.0.7", 2222) == "[10.0.0.7]:2222"
    assert ssh_keys.known_hosts_entry("  h  ", 22) == "h"


# 功能：_entry_present 精确匹配行首、识别多主机行逗号前缀、不被数字前缀误中
# 设计：10.0.0.2 对 10.0.0.20 是 startswith 型 bug 的经典温床——用一对
# 互为前缀的 IP 同文件摆放，把"必须按字段边界比较"钉进测试
def test_entry_present(base: Path) -> None:
    kh = base / "known_hosts"
    kh.write_text(
        "# comment line\n"
        "\n"
        "10.0.0.20 ssh-ed25519 AAAAC3NzaC1\n"
        "a.example,b.example ssh-rsa AAAAB3\n",
        encoding="utf-8",
    )
    assert ssh_keys._entry_present(kh, "10.0.0.20")
    assert not ssh_keys._entry_present(kh, "10.0.0.2")  # 前缀不算命中
    assert ssh_keys._entry_present(kh, "a.example")  # 多主机行逗号后跟
    assert not ssh_keys._entry_present(kh, "comment")  # 注释行不算
    assert not ssh_keys._entry_present(base / "missing", "x")


@pytest.mark.skipif(not _has_ssh, reason="需要系统 OpenSSH 工具链")
async def test_key_status_empty(base: Path) -> None:
    # 功能：目录里没密钥时 has_key=False、路径与指纹为空串
    # 设计：GUI 用 has_key 决定显示「生成密钥」还是「指纹卡片」，
    # 空态返回形状必须先锁住，否则前端要在 undefined 上做条件渲染
    st = await ssh_keys.key_status(base)
    assert st == {"has_key": False, "pubkey_path": "", "fingerprint": ""}


@pytest.mark.skipif(not _has_ssh, reason="需要系统 OpenSSH 工具链")
async def test_key_generate_roundtrip(base: Path) -> None:
    # 功能：生成 → 文件落盘 → status 可见带指纹 → 再生成被拒绝且不覆盖
    # 设计：一次测试串完 M4a 密钥路径的完整生命周期；拒绝覆盖后
    # 比对公钥内容不变——"不覆盖"若实现成"覆盖但报失败"是最隐蔽的回归
    r = await ssh_keys.key_generate(base)
    assert r["ok"] and r["pubkey"].startswith("ssh-ed25519 ")
    assert r["fingerprint"].startswith("SHA256:")
    pub = (base / "id_ed25519.pub").read_text(encoding="utf-8")
    st = await ssh_keys.key_status(base)
    assert st["has_key"] and st["fingerprint"] == r["fingerprint"]
    r2 = await ssh_keys.key_generate(base)
    assert not r2["ok"] and "未覆盖" in r2["error"]
    assert (base / "id_ed25519.pub").read_text(encoding="utf-8") == pub


async def test_host_trust_bad_input(base: Path) -> None:
    # 功能：非法 host（空/含空白）与非法端口直接拒绝，不启动子进程
    # 设计：参数校验在 spawn 之前是注入面收缩的一部分——host 会被拼进
    # ssh-keyscan 的参数位，含空白的输入即使不被 shell 解析也必须拒收
    bad: list[Any] = [("", 22), ("a b", 22), ("h", 99999)]
    for h, p in bad:
        r = await ssh_keys.host_trust(h, p, base)
        assert not r["ok"] and r["fingerprints"] == ""
    assert not (base / "known_hosts").exists()


@pytest.mark.skipif(not _has_ssh, reason="需要系统 OpenSSH 工具链")
async def test_host_trust_unreachable(base: Path) -> None:
    # 功能：对回环上的关闭端口做信任 → keyscan 失败 → ok=False 且不写 known_hosts
    # 设计：bind(0) 拿一个刚释放的端口，本机几乎必然 ECONNREFUSED——
    # 用"真失败"而不是 mock 来验证安全不变式：不可达时信任绝不能建立
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = await ssh_keys.host_trust("127.0.0.1", port, base)
    assert not r["ok"] and r["error"]
    assert not (base / "known_hosts").exists()
