"""
端到端验证脚本 - 验证 Plan & Execute 引擎 + Memory System 集成

用 try/finally 确保异常退出时也清理 core 子进程。
"""
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ENGINE = "plan_execute"
CHECKPOINT_BACKEND = "memory"
HOST, PORT = "127.0.0.1", 7437
CWD = r"d:\IwanClaude"
LOG_PATH = Path(CWD) / "e2e_core.log"
TEST_MSG = "请直接回答：1+1等于几？只回答数字，不要调用任何工具。"

env = {**os.environ, "IWAN_AGENT_ENGINE": ENGINE, "IWAN_AGENT_CHECKPOINT_BACKEND": CHECKPOINT_BACKEND}
try:
    LOG_PATH.unlink(missing_ok=True)
except Exception:
    pass

log_file = open(LOG_PATH, "w", encoding="utf-8")
proc = subprocess.Popen(
    [sys.executable, "-m", "iwan_claude.core"],
    cwd=CWD,
    stdout=log_file,
    stderr=subprocess.STDOUT,
    env=env,
)
print(f"[launcher] core pid={proc.pid}, engine={ENGINE}")

try:
    # 等待端口可用
    started = False
    for _ in range(40):
        if proc.poll() is not None:
            print(f"[launcher] ERROR: core exited early code={proc.returncode}")
            raise SystemExit(1)
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                started = True
                break
        except OSError:
            time.sleep(1)
    if not started:
        print("[launcher] ERROR: core did not start in 40s")
        raise SystemExit(1)
    print("[launcher] core is listening")

    from iwan_claude.core.transport.socket_client import SocketClient

    async def run_client() -> None:
        client = SocketClient(HOST, PORT)
        await client.connect()
        loop_task = asyncio.create_task(client.run_event_loop())
        events: list[dict] = []
        # on_event 期望 async 回调（dispatch 时 await handler），sync lambda 会被静默丢弃
        async def _on_event(evt: dict) -> None:
            events.append(evt)

        client.on_event(_on_event)

        # 订阅事件推送（broadcaster 是订阅制，必须先调用 event.subscribe 才能收到 step.*/run.* 等事件）
        sub = await client.send_command("event.subscribe", {"topics": ["*"], "scope": "global"})
        print(f"[client] subscribed -> sub_id={sub.get('subscription_id')}")

        try:
            pong = await client.send_command("core.ping", {})
            print(f"[client] ping -> version={pong.get('server_version')}")

            create = await client.send_command("session.create", {"mode": "chat", "title": "e2e-plan-execute"})
            sid = create.get("session_id", "")
            print(f"[client] session.create -> id={sid}")

            print(f"[client] send_message: {TEST_MSG}")
            t0 = time.time()
            try:
                send = await asyncio.wait_for(
                    client.send_command("session.send_message", {"session_id": sid, "content": TEST_MSG}),
                    timeout=120,
                )
                print(f"[client] send_message -> run_id={send.get('run_id')} ({time.time()-t0:.1f}s)")
            except asyncio.TimeoutError:
                print(f"[client] TIMEOUT 120s ({time.time()-t0:.1f}s)")

            hist = await client.send_command("session.get_history", {"session_id": sid})
            msgs = hist.get("messages", [])
            print(f"[client] history: {len(msgs)} msgs")
            for m in msgs:
                if m.get("role") == "assistant":
                    c = m.get("content")
                    txt = c if isinstance(c, str) else " ".join(
                        b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
                    )
                    print(f"[client] ASSISTANT: {txt[:200]}")

            step_started = sum(1 for e in events if e.get("type") == "step.started")
            run_finished = [e for e in events if e.get("type") == "run.finished"]
            print(f"[client] events={len(events)} step.started={step_started} run.finished={len(run_finished)}")
            for rf in run_finished:
                print(f"[client] run.finished status={rf.get('status')} steps={rf.get('steps')}")
        finally:
            await client.close()
            loop_task.cancel()

    asyncio.run(run_client())

    # 检查记忆文件
    memory_dir = Path.home() / ".iwan_claude" / "memory"
    print(f"\n[verify] memory dir: {memory_dir}")
    for f in ["long_term.jsonl", "vector_memory.json"]:
        p = memory_dir / f
        print(f"[verify]   {f}: {'EXISTS ' + str(p.stat().st_size) + ' bytes' if p.exists() else 'not found'}")

    # 打印 core 日志关键行
    print("\n[verify] --- core log key lines ---")
    try:
        for line in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            low = line.lower()
            if any(k in low for k in ["memory manager", "memory:", "remember", "engine", "error", "plan node", "execute step", "reflect", "traceback", "permission manager"]):
                print(f"  {line}")
    except Exception as e:
        print(f"  (read log failed: {e})")

finally:
    log_file.close()
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    print(f"\n[launcher] core terminated (exit={proc.returncode})")
    print("[launcher] e2e done")
