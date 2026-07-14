from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from iwan_claude.core.bus.events import LlmModelSelectedEvent, LlmTokenEvent, LlmUsageEvent
from iwan_claude.core.config import LlmConfig
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStats
from iwan_claude.core.system_prompt import FALLBACK_SYSTEM_PROMPT

_MAX_STREAM_RETRIES = 3
_RETRY_BACKOFF_S = (1.0, 2.0, 4.0)
_MAX_OUTPUT_TOKENS = 8192

log = logging.getLogger(__name__)


# 返回当前 UTC 时间的 ISO 8601 字符串
def _now() -> str:
    return datetime.now(UTC).isoformat()


# Provider 层兜底 system prompt（极端情况下才会用到：主循环忘传 system 时；优先使用 loop / runner 里的完整版）
_DEFAULT_SYSTEM_PROMPT = FALLBACK_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 消息格式转换：Anthropic ↔ OpenAI Chat Completions
# ---------------------------------------------------------------------------

# 把 Anthropic 风格的 messages（tool_use / tool_result 是 content block）
# 转换成 OpenAI 风格（tool_calls 挂在 assistant 消息上，tool_result 是 role=tool 的独立消息）
def _convert_messages_to_openai(
    messages: list[dict[str, object]],
    system: str | None,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []

    # OpenAI 把 system prompt 放进 messages 的第一条 role=system 消息
    sys_text = system or _DEFAULT_SYSTEM_PROMPT
    if sys_text:
        out.append({"role": "system", "content": sys_text})

    for msg in messages:
        role = msg["role"]
        content = msg.get("content")

        # content 是字符串的简单消息
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        # content 是 list of blocks（Anthropic 格式）
        if isinstance(content, list):
            if role == "assistant":
                # assistant：拆分出 text 和 tool_calls
                text_parts: list[str] = []
                tool_calls: list[dict[str, object]] = []
                idx = 0
                for blk in content:
                    btype = blk.get("type")
                    if btype == "text":
                        text_parts.append(blk.get("text", ""))
                    elif btype == "thinking":
                        # OpenAI 没有 extended thinking 块，跳过（或者放进 reasoning_content？这里先跳过）
                        continue
                    elif btype == "tool_use":
                        tid = blk.get("id", f"tc_{idx}")
                        idx += 1
                        name = str(blk.get("name", ""))
                        raw_input = blk.get("input", {})
                        try:
                            args_str = json.dumps(raw_input, ensure_ascii=False)
                        except Exception:
                            args_str = "{}"
                        tool_calls.append({
                            "id": tid,
                            "type": "function",
                            "function": {"name": name, "arguments": args_str},
                        })
                assm: dict[str, object] = {"role": "assistant"}
                if text_parts:
                    assm["content"] = "".join(text_parts)
                else:
                    assm["content"] = ""
                if tool_calls:
                    assm["tool_calls"] = tool_calls
                out.append(assm)
            elif role == "user":
                # user：拆分出 text 和 tool_result
                # OpenAI 里 tool_result 是 *独立* 的 role=tool 消息
                text_parts: list[str] = []
                for blk in content:
                    btype = blk.get("type")
                    if btype == "text":
                        text_parts.append(blk.get("text", ""))
                    elif btype == "tool_result":
                        tc_id = blk.get("tool_use_id", "")
                        tc_content = blk.get("content", "")
                        # tool_result 的 content 可能是 list（Anthropic 支持多块），这里拍平成字符串
                        if isinstance(tc_content, list):
                            tc_content = "".join(
                                c.get("text", "") if isinstance(c, dict) else str(c)
                                for c in tc_content
                            )
                        is_error = bool(blk.get("is_error", False))
                        out.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": f"[error] {tc_content}" if is_error else str(tc_content),
                        })
                # 如果还有 text 部分（多个 tool_result 前后可能有少量 user 文字），追加为 user 消息
                if text_parts:
                    out.append({"role": "user", "content": "".join(text_parts)})
            else:
                # system 或其他：简化处理
                flat = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        flat.append(blk.get("text", ""))
                out.append({"role": role, "content": "".join(flat)})
            continue

        # 兜底：直接透传
        out.append({"role": role, "content": content if content is not None else ""})

    return out


# 把 Anthropic 风格的 tool schema 转成 OpenAI 风格
def _convert_tools_to_openai(
    tool_schemas: list[dict[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for ts in tool_schemas:
        name = ts.get("name", "")
        description = ts.get("description", "")
        input_schema = ts.get("input_schema", {"type": "object", "properties": {}})
        result.append({
            "type": "function",
            "function": {
                "name": str(name),
                "description": str(description),
                "parameters": input_schema,
            },
        })
    return result


# ---------------------------------------------------------------------------
# OpenAI Chat Completions 兼容 Provider
#   —— 支持 DeepSeek、通义千问（兼容模式）、智谱清言、月之暗面、Ollama OpenAI 等
# ---------------------------------------------------------------------------

class OpenAICompatibleProvider:
    # 初始化：读取 API Key（从配置指定的环境变量名里读），创建 httpx.AsyncClient
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str = "DEEPSEEK_API_KEY",
        context_window: int = 128_000,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise SystemExit(
                "OpenAI-compatible provider requires llm.base_url or IWAN_LLM_BASE_URL to be set"
            )
        api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit(
                f"OpenAI-compatible provider requires environment variable {api_key_env!r} (or OPENAI_API_KEY) to be set"
            )
        self._api_key = api_key
        # 去掉 base_url 末尾的 /，再拼 /chat/completions
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._context_window = context_window
        # 给每个请求带超时
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0))

    # 流式调用 /chat/completions；SSE 逐字推送，最后汇总 LlmResponse
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> LlmResponse:
        await bus.publish(
            LlmModelSelectedEvent(run_id=run_id, model=self._model, strategy="static", ts=_now())
        )

        openai_messages = _convert_messages_to_openai(messages, system)
        payload: dict[str, object] = {
            "model": self._model,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "temperature": 0.7,
        }
        openai_tools = _convert_tools_to_openai(tool_schemas)
        if openai_tools:
            payload["tools"] = openai_tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        url = f"{self._base_url}/chat/completions"

        last_err: Exception | None = None
        for attempt in range(1, _MAX_STREAM_RETRIES + 1):
            try:
                return await self._stream_once(
                    url, headers, payload, bus, run_id, is_first_attempt=(attempt == 1),
                )
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, TimeoutError) as exc:
                last_err = exc
                if attempt == _MAX_STREAM_RETRIES:
                    log.error(
                        "openai-compat stream failed after %d attempts run_id=%s step=%d: %s",
                        _MAX_STREAM_RETRIES, run_id, step, exc,
                    )
                    raise
                delay = _RETRY_BACKOFF_S[attempt - 1]
                log.warning(
                    "openai-compat stream dropped (attempt %d/%d) run_id=%s step=%d: %s — retrying in %.0fs",
                    attempt, _MAX_STREAM_RETRIES, run_id, step, exc, delay,
                )
                await asyncio.sleep(delay)
        # Should not reach here
        raise last_err or RuntimeError("no stream attempt executed")

    # 单次 SSE 流式请求处理：逐行解析 data: {...}，拼接文本 + tool_calls，最后一条带 usage
    async def _stream_once(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        bus: EventBus,
        run_id: str,
        *,
        is_first_attempt: bool,
    ) -> LlmResponse:
        text_parts: list[str] = []
        stop_reason: str = "end_turn"

        # 增量组装 tool_calls（每个 index 对应一个 tool_call 的 id/name/arguments）
        tc_index: dict[int, dict[str, Any]] = {}
        usage_in: int = 0
        usage_out: int = 0
        usage_cached: int = 0

        async with self._http.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                try:
                    err_body = await resp.aread()
                except Exception:
                    err_body = b""
                raise httpx.HTTPStatusError(
                    f"LLM HTTP {resp.status_code}: {err_body.decode('utf-8', errors='replace')[:800]}",
                    request=resp.request,
                    response=resp,
                )
            # SSE 解析：一行一行读，空行分隔事件，只关心 "data: <json>"
            line_buf = b""
            async for chunk in resp.aiter_bytes():
                line_buf += chunk
                while b"\n" in line_buf:
                    line, line_buf = line_buf.split(b"\n", 1)
                    line_str = line.decode("utf-8", errors="replace").rstrip("\r")
                    if not line_str.startswith("data:"):
                        continue
                    data = line_str[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # 处理 choices 增量
                    choices = obj.get("choices", []) or []
                    for choice in choices:
                        delta = choice.get("delta", {}) or {}
                        # 文本增量
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            if is_first_attempt:
                                await bus.publish(
                                    LlmTokenEvent(run_id=run_id, token=content, ts=_now())
                                )
                            text_parts.append(content)
                        # tool_calls 增量（按 index 累加 arguments 字符串）
                        dtc = delta.get("tool_calls") or []
                        for tcd in dtc:
                            idx = int(tcd.get("index", 0))
                            slot = tc_index.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                            tid = tcd.get("id")
                            if isinstance(tid, str) and tid:
                                slot["id"] = tid
                            fn = tcd.get("function") or {}
                            fname = fn.get("name")
                            if isinstance(fname, str) and fname:
                                slot["name"] = fname
                            fargs = fn.get("arguments")
                            if isinstance(fargs, str) and fargs:
                                slot["arguments"] += fargs
                        # finish_reason
                        fr = choice.get("finish_reason")
                        if isinstance(fr, str) and fr:
                            if fr == "tool_calls":
                                stop_reason = "tool_use"
                            elif fr == "stop":
                                stop_reason = "end_turn"
                            else:
                                stop_reason = "end_turn"
                    # usage：通常在最后一条非 choice 事件里
                    u = obj.get("usage")
                    if isinstance(u, dict):
                        pt = u.get("prompt_tokens")
                        ct = u.get("completion_tokens")
                        if isinstance(pt, int):
                            usage_in = pt
                        if isinstance(ct, int):
                            usage_out = ct
                        # 某些厂商（OpenRouter/一起等）会提供 cached tokens
                        prdt = u.get("prompt_tokens_details") or {}
                        cached = prdt.get("cached_tokens")
                        if isinstance(cached, int):
                            usage_cached = cached

        # 组装 tool_calls（按 index 排序）
        tool_calls: list[ToolCallBlock] = []
        for idx in sorted(tc_index.keys()):
            slot = tc_index[idx]
            args_str = slot.get("arguments", "") or "{}"
            try:
                args_obj: dict[str, object] = json.loads(args_str)
                if not isinstance(args_obj, dict):
                    args_obj = {"value": args_obj}
            except json.JSONDecodeError:
                args_obj = {"raw": args_str}
            tool_calls.append(ToolCallBlock(
                id=slot.get("id", f"tc_{idx}"),
                name=slot.get("name", ""),
                input=args_obj,
            ))

        # 计算 usage 和 context_pct
        usage: UsageStats | None = None
        if usage_in > 0 or usage_out > 0:
            ctx_window = max(self._context_window, 1)
            context_pct = round(min(usage_in / ctx_window, 1.0), 4)
            usage = UsageStats(
                input_tokens=usage_in,
                output_tokens=usage_out,
                cache_read_input_tokens=usage_cached,
                context_pct=context_pct,
            )
            await bus.publish(LlmUsageEvent(
                run_id=run_id,
                input_tokens=usage_in,
                output_tokens=usage_out,
                context_pct=context_pct,
                ts=_now(),
            ))

        return LlmResponse(
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            text="".join(text_parts),
            usage=usage,
        )
