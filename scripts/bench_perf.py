# iwanclaude 性能基准：daemon 冷启动 / RPC 延迟与吞吐 / 会话回填 / 语音转写 RTF
#
# 【学习要点】1) 度量与门禁分离：pytest 回答"对不对"，本脚本回答"多快"——
# 两者节奏不同（门禁每改必跑，基准偶尔跑），混在一起会让测试套件被秒级
# 计时噪声拖垮。2) 所有计时项跑在【一次性临时 daemon】上：端口/会话目录/
# 策略文件全指临时区，绝不污染 7437 上的工作实例；测回填用的是真实会话
# 目录的【拷贝】——recover 会改磁盘状态，碰原件就是事故。3) 数字必须标注
# 测量条件（CPU 推理、单进程、本机回环），离开条件的性能数字没有意义。
#
# 用法：uv run python scripts/bench_perf.py [--skip-speech]
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from iwan_claude.core.transport.socket_client import SocketClient

REPO = Path(__file__).resolve().parents[1]
SESSIONS_REAL = Path("~/.iwan/sessions").expanduser()


# 向 OS 要一个当下空闲的 TCP 端口（bind(0) 后立即释放，存在理论竞态但够用）
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# 拉起一次性临时 daemon：日志走 stderr 丢弃、预热关闭（计时不掺模型加载）
def spawn_daemon(port: int, sessions_dir: str) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env.update(
        {
            "IWAN_PORT": str(port),
            "IWAN_SESSIONS_DIR": sessions_dir,
            "IWAN_LOG_FILE": "",
            "IWAN_LOG_LEVEL": "WARNING",
            "IWAN_POLICY_FILE": str(Path(sessions_dir).parent / "policy.toml"),
            "IWAN_SPEECH_PREWARM": "0",
        }
    )
    return subprocess.Popen(
        [sys.executable, "-m", "iwan_claude.core"],
        env=env,
        cwd=str(REPO),
        stderr=subprocess.DEVNULL,
    )


# 轮询直到端口可连，返回毫秒耗时；超时抛异常（冷启动指标的原子操作）
async def wait_ready(port: int, timeout_s: float = 20.0) -> float:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        try:
            _r, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            await w.wait_closed()
            return (time.perf_counter() - t0) * 1000
        except OSError:
            await asyncio.sleep(0.005)
    raise RuntimeError(f"daemon 未在 {timeout_s}s 内就绪")


# 连上临时 daemon 并起读循环：SocketClient 的响应分发依赖 run_event_loop，
# 不跑它 send_command 的 future 永不 settle——第一次基准就挂在这上面（教训）
async def connected(port: int) -> tuple[SocketClient, asyncio.Task[None]]:
    c = SocketClient("127.0.0.1", port)
    await c.connect()
    task = asyncio.create_task(c.run_event_loop())
    return c, task


# 冷启动 n 次取样本：中位数进表，min/max 保留（Windows 进程创建抖动大）
async def bench_cold_start(n: int, tmp_root: str) -> dict[str, Any]:
    samples = []
    for _ in range(n):
        port = free_port()
        d = tempfile.mkdtemp(dir=tmp_root)
        proc = spawn_daemon(port, d)
        try:
            samples.append(await wait_ready(port))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            shutil.rmtree(d, ignore_errors=True)
    return {
        "runs": n,
        "median_ms": round(statistics.median(samples), 1),
        "min_ms": round(min(samples), 1),
        "max_ms": round(max(samples), 1),
    }


# 在一个常驻临时 daemon 上测 RPC：ping p50/p95/p99 + 并发吞吐 + 回填延迟
async def bench_rpc(tmp_root: str) -> dict[str, Any]:
    port = free_port()
    # 真实会话目录的拷贝：让 session.list / get_history 面对生产量级的数据
    sessions = Path(tmp_root) / "sessions"
    if SESSIONS_REAL.is_dir():
        shutil.copytree(SESSIONS_REAL, sessions)
    else:
        sessions.mkdir()
    proc = spawn_daemon(port, str(sessions))
    try:
        await wait_ready(port)
        c, ctask = await connected(port)
        try:
            await c.send_command("core.ping", {"type": "core.ping"})  # 热身
            lat = []
            for _ in range(200):
                t0 = time.perf_counter()
                await c.send_command("core.ping", {"type": "core.ping"})
                lat.append((time.perf_counter() - t0) * 1000)
        finally:
            ctask.cancel()
            await c.close()
        lat_sorted = sorted(lat)
        pick = lambda p: round(lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * p))], 3)  # noqa: E731

        # 并发吞吐：20 个客户端各自串行 20 ping，测整体 req/s 与错误数
        async def one_client(idx: int) -> tuple[int, int]:
            cc, task = await connected(port)
            ok = err = 0
            try:
                for _ in range(20):
                    try:
                        await cc.send_command("core.ping", {"type": "core.ping"})
                        ok += 1
                    except Exception:
                        err += 1
            finally:
                task.cancel()
                await cc.close()
            return ok, err

        t0 = time.perf_counter()
        pairs = await asyncio.gather(*(one_client(i) for i in range(20)))
        wall = time.perf_counter() - t0
        ok = sum(p[0] for p in pairs)
        err = sum(p[1] for p in pairs)

        # 回填延迟：对拷贝目录里的会话逐个 get_history（首次全冷）
        sids = sorted(p.name for p in sessions.iterdir() if p.is_dir())
        hyd = []
        cold_first = None
        if sids:
            c2, t2 = await connected(port)
            try:
                for sid in sids[:40]:
                    t1 = time.perf_counter()
                    await c2.send_command(
                        "session.get_history", {"type": "session.get_history", "session_id": sid}
                    )
                    ms = (time.perf_counter() - t1) * 1000
                    if cold_first is None:
                        cold_first = round(ms, 2)
                    hyd.append(ms)
            finally:
                t2.cancel()
                await c2.close()
        return {
            "ping_p50_ms": pick(0.50),
            "ping_p95_ms": pick(0.95),
            "ping_p99_ms": pick(0.99),
            "concurrency": {
                "clients": 20, "requests": ok + err, "errors": err,
                "req_per_s": round(ok / wall, 1), "wall_s": round(wall, 2),
            },
            "hydrate_sessions": len(hyd),
            "hydrate_first_ms": cold_first,
            "hydrate_median_ms": round(statistics.median(hyd), 2) if hyd else None,
        }
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# 语音：预热秒数（含模型加载）+ 真实语音样本转写 RTF。必须喂真实说话音频：
# 纯音/合成噪声会被 vad_filter 整段丢弃，量到的是"静音被拒"而非转写速度
# ——第一版基准用 220Hz 音测出 RTF 0.0 的教训
def bench_speech(wav_path: Path | None) -> dict[str, Any]:
    import base64
    import wave

    import numpy as np

    from iwan_claude.core.speech import SpeechTranscriber, model_size

    t = SpeechTranscriber()
    t0 = time.perf_counter()
    t.prewarm()
    prewarm_s = time.perf_counter() - t0
    if wav_path is None:
        return {
            "model": model_size(),
            "prewarm_s": round(prewarm_s, 2),
            "note": "未给 --speech-wav：纯音会被 VAD 整段拒绝，RTF 不可测",
        }
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    dur_s = len(pcm) / sr
    if sr != 16000:  # 线性插值重采样到 whisper 要求的 16k
        idx = np.linspace(0, len(pcm) - 1, int(len(pcm) * 16000 / sr))
        pcm = np.interp(idx, np.arange(len(pcm)), pcm)
    b64 = base64.b64encode(pcm.astype(np.int16).tobytes()).decode()
    runs = []
    for _ in range(3):
        t1 = time.perf_counter()
        t.transcribe_sync(b64, 16000)
        runs.append(time.perf_counter() - t1)
    med = statistics.median(runs)
    return {
        "model": model_size(),
        "prewarm_s": round(prewarm_s, 2),
        "speech_dur_s": round(dur_s, 2),
        "transcribe_med_s": round(med, 2),
        "rtf": round(med / dur_s, 2),
    }


# 入口：组装三段基准结果为一份 JSON（缺段用 skip 标注，部分失败不拖全表）
async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cold-runs", type=int, default=5)
    ap.add_argument("--skip-cold", action="store_true")
    ap.add_argument("--skip-speech", action="store_true")
    ap.add_argument("--speech-wav", type=Path, default=None,
                    help="真实语音 wav（建议 16k 单声道），缺省只测模型预热")
    args = ap.parse_args()
    out: dict[str, Any] = {}
    tmp_root = tempfile.mkdtemp(prefix="iwan_bench_")
    try:
        if args.skip_cold:
            out["cold_start"] = {"skipped": True}
        else:
            print("[bench] 冷启动…", file=sys.stderr)
            out["cold_start"] = await bench_cold_start(args.cold_runs, tmp_root)
            print(json.dumps(out["cold_start"], ensure_ascii=False), file=sys.stderr)
        print("[bench] RPC/回填…", file=sys.stderr)
        out["rpc"] = await bench_rpc(tmp_root)
        print(json.dumps(out["rpc"], ensure_ascii=False), file=sys.stderr)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    if args.skip_speech:
        out["speech"] = {"skipped": True}
    else:
        print("[bench] 语音（预热+转写）…", file=sys.stderr)
        try:
            out["speech"] = bench_speech(args.speech_wav)
        except Exception as e:  # 缺缓存/断网只砸语音段，已测数字必须保住
            out["speech"] = {"error": str(e)[:200]}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
