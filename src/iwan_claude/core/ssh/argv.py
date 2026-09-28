"""
ssh 命令行的统一拼装（M4b）— build_ssh_argv 纯函数

【学习要点】
1. 单独成模块而不是塞进工具文件：M4c 的交互终端（session.py）要复用同一
   份 argv 决策——两处各拼各的迟早漂移成"终端能连、工具连不上"。
2. BatchMode=yes 是本文件的宪法：任何形态的密码/hostkey 提问都直接以
   失败退出，绝不静默挂起等输入。agent 场景里"卡住"比"报错"危险一个数量级。
3. UserKnownHostsFile 强制指到 ~/.iwan/ssh/known_hosts：与 M4a 的隔离决策
   一致——我们的信任记录（host_trust 按钮写的）必须在这里被消费，同时不
   借用用户日常终端的信任库。
4. 纯函数不碰文件系统：known_hosts/identity 的存在性由调用方解析后以
   参数传入，测试断言 argv 时不需要摆任何文件。
"""
from __future__ import annotations

from typing import Any


# 拼装非交互 ssh argv：conn 为连接行 dict，command 为远端命令（None = 不带命令）
def build_ssh_argv(
    conn: dict[str, Any],
    command: str | None,
    *,
    known_hosts: str,
    identity: str | None = None,
    allocate_tty: bool = False,
) -> list[str]:
    port = int(conn.get("port") or 22)
    argv: list[str] = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "StrictHostKeyChecking=yes",
        "-p", str(port),
    ]
    if identity:
        argv += ["-i", identity, "-o", "IdentitiesOnly=yes"]
    if allocate_tty:
        # -tt：强制分配伪终端（交互会话用；exec 型命令不需要）
        argv.append("-tt")
    target = f"{conn.get('user', '')}@{conn.get('host', '')}"
    argv.append(target)
    if command is not None:
        argv.append(command)
    return argv
