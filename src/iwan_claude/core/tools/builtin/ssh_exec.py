"""
ssh_exec 工具（M4b）— agent 在登记过的远端主机上执行一条命令

【学习要点】
1. 独立工具而非复用 bash：bash 的 NETWORK_COMMAND_PATTERNS 在沙箱模式拦
   ssh——那是防"沙箱内文件外传"的通道锁。ssh_exec 是显式的远端执行面，
   有自己的权限门（未注册默认 ASK + 审批指纹 conn+command），与 http_request
   同一层级："有自己的安全控制"，不再受 bash 通道锁的辖。
2. 参数里没有 host/user，只有 conn_id：目标主机锁死在用户登记的连接库里，
   模型无法把"对 lab 的批准"复用到任意主机——审批指纹因此天然含目的地。
3. 命令原文作为单个 argv 元素交给远端 shell：本地无 shell 参与，argv 直传
   ssh.exe，本地注入面为零；远端执行语义由用户批准的命令文本本身决定。
4. BatchMode + 独立 known_hosts（见 argv.py）：连不上就快速失败并把 ssh
   的原始诊断回给模型，绝不等交互输入把 run 挂死。
"""
from __future__ import annotations

import asyncio
import logging
import os

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.sandbox import scrub_env
from iwan_claude.core.ssh.argv import build_ssh_argv
from iwan_claude.core.ssh.connections import SshConnStore
from iwan_claude.core.ssh.keys import key_paths
from iwan_claude.core.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# 输出兜底上限：64 KB（与 bash 同量级；远端命令输出失控时的熔断线）
_OUTPUT_MAX_BYTES = 64 * 1024


class SshExecParams(BaseModel):
    """
    ssh_exec 参数模型

    【字段说明】
    - conn_id: str - 连接库里的登记 id（目标主机由此锁定）
    - command: str - 远端执行的命令文本
    - timeout_s: int - 总超时秒数（含连接），默认 30，上限 300
    """
    model_config = ConfigDict(extra="ignore")
    conn_id: str
    command: str
    timeout_s: int = Field(default=30, ge=1, le=300)


class SshExecTool(BaseTool):
    """
    远端命令执行工具 - 对登记连接跑一条非交互 ssh 命令

    【学习要点】
    实例自带 SshConnStore（同默认路径）：daemon 的 GUI 改动与工具读到的
    是同一份 connections.json；store 懒载入 + 每次 get 走内存，
    新建连接要重开一个 run 才可见属于可接受的陈旧度（run 本就分钟级）。
    """
    params_model = SshExecParams
    name = "ssh_exec"
    description = (
        "Execute ONE non-interactive command on a registered SSH host and return "
        "its combined output. conn_id must name a connection the user registered "
        "in the SSH view; key-based auth only (BatchMode — it never prompts). "
        "Unknown conn_id fails and lists what is available. Prefer short, "
        "focused remote commands; output is truncated at 64 KB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "conn_id": {
                "type": "string",
                "description": "ID of a connection registered in the user's SSH view.",
            },
            "command": {
                "type": "string",
                "description": "Shell command text to run on the remote host.",
            },
            "timeout_s": {
                "type": "integer",
                "description": "Max seconds for connect+run (default 30, max 300).",
            },
        },
        "required": ["conn_id", "command"],
    }

    def __init__(self) -> None:
        # 连接库与 GUI 同一份文件（默认路径）；懒载入，构造零成本
        self._store = SshConnStore()

    # 把连接库摘要成一行提示（conn_id 错误时回给模型自救）
    def _conn_hint(self) -> str:
        rows = self._store.list()
        if not rows:
            return "（连接库为空——请让用户在 SSH 终端视图里先添加连接）"
        return "、".join(f"{r['id']}={r['name']}({r['user']}@{r['host']})" for r in rows)

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行远端命令：conn 解析 → argv 拼装 → 子进程带超时 → 输出收口
        """
        p = SshExecParams.model_validate(params)
        conn = self._store.get(p.conn_id)
        if conn is None:
            # 未知 id 是模型最常见的踩错——把可用清单回给它，一次失败换一次自救
            return ToolResult(
                content=f"未知 SSH 连接 id：{p.conn_id!r}。可用连接：{self._conn_hint()}",
                is_error=True,
                error_type="invalid_input",
            )

        priv, _, kh = key_paths()
        # 私钥选择：连接自带 key_file > 我们的默认私钥（存在才传 -i）> 什么都不传
        # （交给 ssh 自己走 agent/默认——BatchMode 下失败信息照样回显）
        identity: str | None = None
        raw_key = str(conn.get("key_file") or "").strip()
        if raw_key:
            if not os.path.isfile(raw_key):
                return ToolResult(
                    content=f"连接 {conn.get('name')} 配置的私钥不存在：{raw_key}",
                    is_error=True, error_type="invalid_input",
                )
            identity = raw_key
        elif priv.exists():
            identity = str(priv)

        argv = build_ssh_argv(
            conn, p.command, known_hosts=str(kh), identity=identity,
        )
        logger.info("ssh_exec: conn=%s host=%s cmd=%r",
                    p.conn_id, conn.get("host"), p.command[:120])

        from iwan_claude.core.tools.job_object import (
            assign_process_to_job,
            close_job,
            create_process_job,
        )
        job = create_process_job()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=scrub_env(dict(os.environ)),
            )
            assign_process_to_job(job, proc.pid)
            try:
                out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=p.timeout_s)
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(
                    content=f"[timeout after {p.timeout_s}s] 远端命令未结束（进程树已清杀）",
                    is_error=True, error_type="timeout",
                )
        except FileNotFoundError:
            return ToolResult(
                content="ssh 命令不存在——请安装 OpenSSH 客户端（Windows：设置→可选功能）",
                is_error=True, error_type="runtime_error",
            )
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")
        finally:
            close_job(job)

        output = out_bytes.decode("utf-8", errors="replace")
        if len(out_bytes) > _OUTPUT_MAX_BYTES:
            output = output[:_OUTPUT_MAX_BYTES] + "\n[truncated]"
        rc = proc.returncode or 0
        if rc != 0:
            # ssh 的诊断全在合并输出里（hostkey 不信任/超时/远端非零退出）——
            # 原文回给模型：这些文本恰好教它下一步该让用户点什么
            return ToolResult(
                content=f"[exit {rc}]\n{output}",
                is_error=True, error_type="runtime_error",
            )
        return ToolResult(content=output or "[no output]")
