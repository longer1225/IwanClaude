"""
SSH 密钥与主机信任（M4a）— ~/.iwan/ssh/ 下的 ed25519 密钥对 + known_hosts

【学习要点】
1. 零新依赖方案：直接驱动系统 OpenSSH 三件套（ssh-keygen / ssh-keyscan）。
   Windows 11 自带 OpenSSH 客户端，git-for-windows 也带一份——哪份在 PATH
   里就用哪份，产物格式同属 OpenSSH 标准，互读无障碍。
2. 已知主机是安全决策点而不是便利点：host_trust 把 keyscan 结果与指纹
   一起返回给 GUI，【用户目视比对后】才落 known_hosts——静默追加等于
   把中间人攻击的门从"用户会犹豫"改成"代码不犹豫"。
3. known_hosts 用【我们的独立文件】（~/.iwan/ssh/known_hosts），所有 ssh
   调用都带 -o UserKnownHostsFile 指过来：不与用户日常终端的 known_hosts
   互相污染，删 ~/.iwan/ssh 即整体重置，故障域干净。
4. 私钥永不上传、永不显示：key_status 只回公钥路径与指纹；部署公钥的
   命令由 GUI 复制给用户自己执行——daemon 代人登录远端写 authorized_keys
   是 M4b/M4c 终端建立后的事，密钥管理这一步保持"只产材料"。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from iwan_claude.core.config import resolve_sessions_root

log = logging.getLogger(__name__)


# ssh 材料目录（~/.iwan/ssh）；参数可注入供单测指向 tmp_path
def ssh_dir(base: Path | None = None) -> Path:
    return base if base is not None else resolve_sessions_root().parent / "ssh"


# 三件套路径：私钥 / 公钥 / known_hosts
def key_paths(base: Path | None = None) -> tuple[Path, Path, Path]:
    d = ssh_dir(base)
    return d / "id_ed25519", d / "id_ed25519.pub", d / "known_hosts"


# 已知主机条目名：非默认 22 端口必须按 OpenSSH 规范写成 [host]:port
def known_hosts_entry(host: str, port: int) -> str:
    h = host.strip()
    return h if port == 22 else f"[{h}]:{port}"


# 跑一个短命 ssh 工具子进程：返回 (退出码, stdout, stderr)；永不抛出
async def _run_tool(
    exe: str, *args: str, stdin: str | None = None, timeout_s: float = 20.0
) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode("utf-8") if stdin is not None else None),
            timeout=timeout_s,
        )
    except TimeoutError:
        return 124, "", f"{exe} 超时（{timeout_s:.0f}s）"
    except FileNotFoundError:
        return 127, "", f"{exe} 命令不存在——请安装 OpenSSH 客户端（Windows：设置→可选功能）"
    return (
        proc.returncode or 0,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


# 用 ssh-keygen -lf 求公钥指纹（"256 SHA256:xxx comment" 的中间段；拿不到返回空）
async def _fingerprint_of(pubkey_text: str) -> str:
    code, out, _ = await _run_tool("ssh-keygen", "-lf", "-", stdin=pubkey_text)
    if code != 0:
        return ""
    parts = out.strip().split()
    return parts[1] if len(parts) >= 2 else ""


# 密钥现状查询：有私钥才算有；指纹读公钥（私钥文件永不回显内容）
async def key_status(base: Path | None = None) -> dict[str, Any]:
    priv, pub, _ = key_paths(base)
    has = priv.exists() and pub.exists()
    fp = ""
    if has:
        fp = await _fingerprint_of(pub.read_text(encoding="utf-8"))
    return {"has_key": has, "pubkey_path": str(pub) if has else "", "fingerprint": fp}


# 生成 ed25519 密钥对（无口令，密钥登录场景）；已存在时【绝不覆盖】
async def key_generate(base: Path | None = None) -> dict[str, Any]:
    priv, pub, _ = key_paths(base)
    if priv.exists():
        return {
            "ok": False, "pubkey": "", "fingerprint": "",
            "error": "密钥已存在——未覆盖（重置请先手动删除 ~/.iwan/ssh/id_ed25519）",
        }
    priv.parent.mkdir(parents=True, exist_ok=True)
    # -N "" 空口令走 argv 传参（无 shell，不存在引号歧义）
    code, _, err = await _run_tool("ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv))
    if code != 0 or not pub.exists():
        return {
            "ok": False, "pubkey": "", "fingerprint": "",
            "error": err.strip() or "ssh-keygen 失败",
        }
    text = pub.read_text(encoding="utf-8")
    fp = await _fingerprint_of(text)
    log.info("ssh: 已生成 ed25519 密钥对 %s（指纹 %s）", pub, fp)
    return {"ok": True, "pubkey": text.strip(), "fingerprint": fp, "error": ""}


# known_hosts 里是否已有该 host:port 的条目（行首字段精确匹配，防 10.0.0.2 误中 10.0.0.20）
def _entry_present(known_hosts: Path, entry: str) -> bool:
    if not known_hosts.exists():
        return False
    for line in known_hosts.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        first = line.split(None, 1)[0]
        # @certified/@revoked 等 marker 前缀形态：[host]:port cert-authority
        if first == entry or first.startswith(f"{entry},"):
            return True
    return False


# 主机信任：keyscan 取回主机密钥 → 算指纹 →（如未收录）追加进 known_hosts
async def host_trust(host: str, port: int, base: Path | None = None) -> dict[str, Any]:
    h = host.strip()
    if not h or any(c.isspace() for c in h):
        return {"ok": False, "fingerprints": "", "error": "主机地址无效（空或含空白）"}
    if not (1 <= int(port) <= 65535):
        return {"ok": False, "fingerprints": "", "error": "端口应为 1..65535"}
    _, _, kh = key_paths(base)
    entry = known_hosts_entry(h, int(port))
    code, out, err = await _run_tool("ssh-keyscan", "-p", str(port), "-T", "10", h, timeout_s=20.0)
    lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("#")]
    if code != 0 or not lines:
        return {
            "ok": False, "fingerprints": "",
            "error": err.strip() or "无法获取主机密钥：host 不可达或端口错误",
        }
    fps: list[str] = []
    for ln in lines:
        fp = await _fingerprint_of(ln)
        if not fp:
            parts = ln.split(None, 2)
            fp = parts[2][:32] if len(parts) > 2 else "unknown"
        fps.append(fp)
    shown = "\n".join(f"{fp}  {entry}" for fp in fps if fp)
    if _entry_present(kh, entry):
        return {
            "ok": True, "fingerprints": shown or "(已在 known_hosts)",
            "error": "该主机已在 known_hosts 中（未重复追加），指纹如上供核对",
        }
    kh.parent.mkdir(parents=True, exist_ok=True)
    with kh.open("a", encoding="utf-8", newline="\n") as f:
        for ln in lines:
            f.write(ln + "\n")
    log.info("ssh: known_hosts 追加 %s（%d 条主机密钥）", entry, len(lines))
    return {"ok": True, "fingerprints": shown, "error": ""}
