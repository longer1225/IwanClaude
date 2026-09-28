# 功能：验证 speech.transcribe 在真实 daemon 上的协议往返与"校验先于模型"拒错
# 设计：刻意不测真转写——首次调用要联网下载上百 MB whisper 模型，集成环境的
# 网络与耗时都不可控（同 workflow 集成不真起 LLM 子 Agent 的纪律）。空音频/
# 坏 base64/越界采样率三条都命中 transcribe_sync 校验段（保证在 _ensure_model
# 之前），因此 daemon 全程零模型加载仍能把 ok=False+中文文案原样带回；
# 缺 audio_b64 字段则走 pydantic 判别层报 JSON-RPC error——参数形状错与
# 业务拒绝分属两条通道，各测一发。
from __future__ import annotations

import asyncio
import json
import subprocess


# 发一帧 JSON-RPC 并取回带指定 id 的响应（同连接多次复用）
async def _rpc(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, rid: str, method: str, params: dict) -> dict:
    req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    writer.write((json.dumps(req) + "\n").encode("utf-8"))
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    return json.loads(line)


# 功能：三条非法音频各回 ok=False 中文文案；缺字段回 JSON-RPC error
# 设计：一次连接四发——响应 result 的 error 用 match 断言关键词而非全文
# （文案微调不该碎测试）；ok=False 时 text 必为空串（GUI 靠这区分通道）；
# 缺 audio_b64 落在 pydantic 校验层，error.code 非 0 即证明没进 handler
async def test_speech_transcribe_validation(
    running_daemon: subprocess.Popen[bytes], free_port: int
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    try:
        r = await _rpc(reader, writer, "v1", "speech.transcribe", {"audio_b64": "", "sample_rate": 16000})
        assert r["result"]["ok"] is False and "音频为空" in r["result"]["error"]
        assert r["result"]["text"] == ""

        r = await _rpc(reader, writer, "v2", "speech.transcribe", {"audio_b64": "!!!", "sample_rate": 16000})
        assert r["result"]["ok"] is False and "base64" in r["result"]["error"]

        r = await _rpc(reader, writer, "v3", "speech.transcribe", {"audio_b64": "AAA=", "sample_rate": 7999})
        assert r["result"]["ok"] is False and "采样率" in r["result"]["error"]

        r = await _rpc(reader, writer, "v4", "speech.transcribe", {"sample_rate": 16000})
        assert "error" in r and r["error"]["code"] != 0
    finally:
        writer.close()
