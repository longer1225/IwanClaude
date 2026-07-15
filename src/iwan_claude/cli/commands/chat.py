from __future__ import annotations

import asyncio
import sys
from typing import Any

from iwan_claude.core.config import IwanConfig
from iwan_claude.core.transport.socket_client import IpcError, SocketClient

_DECISION_MAP: dict[str, str] = {
    "y": "allow_once",
    "a": "always_allow",
    "n": "deny_once",
    "d": "always_deny",
}


class ChatPrinter:
    # 初始化 chat 模式的流式输出状态和待审批权限请求
    def __init__(self) -> None:
        self._inline = False
        self.pending_permission_id: str | None = None

    # 若当前 LLM token 尚未换行，则补一个换行
    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # 按事件类型打印 chat 输出、等待提示和权限审批请求
    async def handle(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")
        if t == "llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True
        elif t == "tool.call_started":
            self._ensure_newline()
            print(f"[tool] {event.get('tool_name', '')}")
        elif t == "permission.requested":
            self._ensure_newline()
            tool_name = str(event.get("tool_name", ""))
            param_preview = str(event.get("param_preview", ""))
            tool_use_id = str(event.get("tool_use_id", ""))
            print(f"[permission] {tool_name}  {param_preview}")
            print("  y=allow once  a=always allow  n=deny once  d=always deny")
            self.pending_permission_id = tool_use_id
        elif t == "session.waiting_for_input":
            self._ensure_newline()
            self.pending_permission_id = None
            print("[waiting for input]")
        elif t == "session.closed":
            self._ensure_newline()
            print("session closed.")


# 在线程池中读取 stdin，避免阻塞 socket event loop
async def _readline(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# 异步核心：创建 chat session，循环读取用户输入并发送到 daemon；权限请求时优先处理审批
async def _chat_async(config: IwanConfig) -> int:
    client = SocketClient(config.host, config.port)
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1

    printer = ChatPrinter()
    client.on_event(printer.handle)
    loop_task = asyncio.create_task(client.run_event_loop())

    try:
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["session.*", "run.*", "tool.*", "llm.token", "permission.*"],
                "scope": "global",
            },
        )
        created = await client.send_command("session.create", {"mode": "chat"})
        session_id = str(created["session_id"])
        print(f"[session: {session_id}]")

        while True:
            try:
                line = await _readline("> ")
            except (EOFError, KeyboardInterrupt):
                break
            content = line.strip()
            if not content:
                continue

            # 有待审批的权限请求时，将用户输入解释为决策而非聊天消息
            if printer.pending_permission_id:
                decision = _DECISION_MAP.get(content.lower())
                if decision is None:
                    print("  enter y (allow once), a (always allow), "
                          "n (deny once), d (always deny)")
                    continue
                tool_use_id = printer.pending_permission_id
                printer.pending_permission_id = None
                await client.send_command(
                    "permission.respond",
                    {"tool_use_id": tool_use_id, "decision": decision},
                )
                continue

            if content.startswith("/checkpoint"):
                parts = content.split()
                if len(parts) >= 2 and parts[1] == "list":
                    result = await client.send_command(
                        "session.checkpoint.list",
                        {"session_id": session_id},
                    )
                    checkpoints = result.get("checkpoints", [])
                    thread_id = result.get("thread_id", "")
                    print(f"\nCheckpoints for session {thread_id}:")
                    if not checkpoints:
                        print("  (no checkpoints found - make sure engine=langgraph and checkpoint_backend!=none)")
                    else:
                        for i, cp in enumerate(reversed(checkpoints)):
                            ts = cp.get("timestamp", "")
                            summary = cp.get("summary", "")
                            node = cp.get("node", "")
                            print(f"  [{i}] step={cp['step']}  {ts}  {summary}")
                            print(f"     id: {cp['checkpoint_id']}")
                    print()
                    continue
                elif len(parts) >= 3 and parts[1] == "restore":
                    idx_or_id = parts[2]
                    list_result = await client.send_command(
                        "session.checkpoint.list",
                        {"session_id": session_id},
                    )
                    checkpoints = list_result.get("checkpoints", [])
                    if not checkpoints:
                        print("  error: no checkpoints available")
                        continue

                    if idx_or_id.isdigit():
                        idx = int(idx_or_id)
                        if idx < 0 or idx >= len(checkpoints):
                            print(f"  error: index {idx} out of range (0-{len(checkpoints)-1})")
                            continue
                        cp = checkpoints[len(checkpoints) - 1 - idx]
                        checkpoint_id = cp["checkpoint_id"]
                    else:
                        checkpoint_id = idx_or_id

                    result = await client.send_command(
                        "session.checkpoint.restore",
                        {"session_id": session_id, "checkpoint_id": checkpoint_id},
                    )
                    if result.get("success"):
                        print(f"\n  restored to step {result['step']}: {result['message']}")
                    else:
                        print(f"\n  restore failed: {result['message']}")
                    print()
                    continue
                else:
                    print("  usage: /checkpoint list | /checkpoint restore <index_or_id>")
                    continue

            await client.send_command(
                "session.send_message",
                {"session_id": session_id, "content": content},
            )

        await client.send_command("session.close", {"session_id": session_id})
    except IpcError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await client.close()
    return 0


# 执行 iwan chat 命令
def cmd_chat(config: IwanConfig) -> None:
    try:
        exit_code = asyncio.run(_chat_async(config))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(exit_code)
