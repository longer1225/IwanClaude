#!/usr/bin/env python3
"""
生成 WIRE_PROTOCOL.md - 从 bus 模块的 pydantic 模型动态生成协议文档

【学习要点】
1. 为什么"生成"而不是手写文档：手写协议文档会在第一次加命令时就过期。
   本脚本按模块定义顺序扫描 commands.py / events.py 里的所有 BaseModel，
   新增模型自动出现在文档里；CI 用 --check 卡住"改了模型忘了重新生成"。
2. discriminated union 的文档价值：每个模型 JSON Schema 里的 type 字段是
   const（字面量类型），客户端据此路由——文档直接展示 schema，
   读者能看到权威的路由依据。
3. vars(module) 保序：Python 3.7+ 模块命名空间按源码定义顺序排列，
   所以文档小节顺序 == 源码里类的声明顺序，可控且稳定。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel

from iwan_claude.core.bus import commands as commands_mod
from iwan_claude.core.bus import events as events_mod
from iwan_claude.core.bus.envelope import EventPushEnvelope

_OUTPUT_PATH = Path(__file__).parent.parent / "WIRE_PROTOCOL.md"

# 从 pydantic 模型生成一个带字段表、JSON Schema 和可选示例的 Markdown 小节
def _model_section(name: str, model: type[BaseModel], example: dict | None = None) -> str:
    # 类 docstring 里的【设计目的】段落值得随 schema 一起展示（pydantic 已放进 description）
    schema = model.model_json_schema()
    props = schema.get("properties", {})
    required: set[str] = set(schema.get("required", []))

    table = ""
    if props:
        table = "\n| Field | Type | Required |\n|---|---|---|\n"
        for field_name, field_info in props.items():
            ftype = field_info.get("type", "object")
            if "anyOf" in field_info:
                ftype = " | ".join(t.get("type", "?") for t in field_info["anyOf"])
            req = "yes" if field_name in required else "no"
            table += f"| `{field_name}` | `{ftype}` | {req} |\n"

    schema_block = f"\n```json\n{json.dumps(schema, indent=2, ensure_ascii=False)}\n```\n"

    example_block = ""
    if example:
        example_block = f"\n**Example:**\n\n```json\n{json.dumps(example, indent=2, ensure_ascii=False)}\n```\n"

    return f"### {name}\n{table}{schema_block}{example_block}"


# 按源码定义顺序取出模块中声明的所有 pydantic 模型类
def _defined_models(module: ModuleType) -> list[type[BaseModel]]:
    # 【设计】v.__module__ == 模块名：过滤掉 import 进来的其他模块的 BaseModel，
    # 只保留"本文件定义的协议模型"；vars() 顺序即声明顺序
    return [
        v
        for v in vars(module).values()
        if isinstance(v, type) and issubclass(v, BaseModel) and v.__module__ == module.__name__
    ]


# 组装所有模型的知识性示例（键=类名）；未列出的类只出 schema 不出示例
def _examples(run_id: str, ts: str) -> dict[str, dict]:  # type: ignore[type-arg]
    session_id = "sess-abc123def456"
    return {
        "PingCommand": {
            "jsonrpc": "2.0", "id": "u-1", "method": "core.ping",
            "params": {"client": "cli/0.0.1"},
        },
        "PongResult": {
            "jsonrpc": "2.0", "id": "u-1",
            "result": {"server_version": "0.2.0", "uptime_ms": 12, "received_at": ts},
        },
        "AgentRunCommand": {
            "jsonrpc": "2.0", "id": "u-2", "method": "agent.run",
            "params": {"goal": "总结 README.md 的主要章节"},
        },
        "AgentRunResult": {"jsonrpc": "2.0", "id": "u-2", "result": {"run_id": run_id}},
        "EventSubscribeCommand": {
            "jsonrpc": "2.0", "id": "u-3", "method": "event.subscribe",
            "params": {"topics": ["run.*", "step.*", "tool.*", "llm.token"],
                       "scope": "global", "replay_from_run": None},
        },
        "EventSubscribeResult": {
            "jsonrpc": "2.0", "id": "u-3",
            "result": {"subscription_id": "sub-abc123", "replayed_count": 0},
        },
        "SessionCreateCommand": {
            "jsonrpc": "2.0", "id": "u-4", "method": "session.create",
            "params": {"mode": "chat", "title": ""},
        },
        "SessionCreateResult": {
            "jsonrpc": "2.0", "id": "u-4",
            "result": {"session_id": session_id, "status": "active"},
        },
        "SessionSendMessageCommand": {
            "jsonrpc": "2.0", "id": "u-5", "method": "session.send_message",
            "params": {"session_id": session_id, "content": "总结 README.md"},
        },
        "SessionSendMessageResult": {
            "jsonrpc": "2.0", "id": "u-5", "result": {"run_id": run_id},
        },
        "RunCancelCommand": {
            "jsonrpc": "2.0", "id": "u-6", "method": "run.cancel",
            "params": {"type": "run.cancel", "run_id": run_id},
        },
        "RunCancelResult": {"jsonrpc": "2.0", "id": "u-6", "result": {"accepted": True}},
        "RunSteerCommand": {
            "jsonrpc": "2.0", "id": "u-7", "method": "run.steer",
            "params": {"type": "run.steer", "run_id": run_id, "message": "方向错了，改用 asyncio"},
        },
        "RunSteerResult": {
            "jsonrpc": "2.0", "id": "u-7",
            "result": {"accepted": True, "queued": 1},
        },
        "EventPushEnvelope": {
            "kind": "event",
            "event": {"type": "step.started", "run_id": run_id, "step": 1, "ts": ts},
        },
        "RunStartedEvent": {"type": "run.started", "run_id": run_id, "goal": "总结 README.md", "ts": ts},
        "RunFinishedEvent": {
            "type": "run.finished", "run_id": run_id,
            "status": "success", "reason": None, "steps": 2, "ts": ts,
        },
        "StepStartedEvent": {"type": "step.started", "run_id": run_id, "step": 1, "ts": ts},
        "StepFinishedEvent": {"type": "step.finished", "run_id": run_id, "step": 1, "ts": ts},
        "ToolCallStartedEvent": {
            "type": "tool.call_started", "run_id": run_id, "tool_use_id": "toolu_01",
            "tool_name": "read_file", "params": {"path": "README.md"}, "ts": ts,
        },
        "ToolCallFinishedEvent": {
            "type": "tool.call_finished", "run_id": run_id, "tool_use_id": "toolu_01",
            "tool_name": "read_file", "elapsed_ms": 3, "ts": ts,
        },
        "ToolCallFailedEvent": {
            "type": "tool.call_failed", "run_id": run_id, "tool_use_id": "toolu_02",
            "tool_name": "read_file", "error_class": "runtime_error",
            "error_message": "file not found", "elapsed_ms": 1, "attempt": 1, "ts": ts,
        },
        "LlmModelSelectedEvent": {
            "type": "llm.model_selected", "run_id": run_id,
            "model": "claude-sonnet-4-6", "strategy": "static", "ts": ts,
        },
        "LlmTokenEvent": {"type": "llm.token", "run_id": run_id, "token": "The ", "ts": ts},
        "LlmUsageEvent": {
            "type": "llm.usage", "run_id": run_id, "input_tokens": 512, "output_tokens": 48,
            "cache_read_input_tokens": 490, "cache_creation_input_tokens": 0, "ts": ts,
        },
        "LogLineEvent": {
            "type": "log.line", "run_id": run_id, "level": "INFO",
            "source": "iwan_claude.core.loop", "message": "step 1 started", "ts": ts,
        },
        "SessionCreatedEvent": {
            "type": "session.created", "session_id": session_id, "mode": "chat", "ts": ts,
        },
        "SessionMessageReceivedEvent": {
            "type": "session.message_received", "session_id": session_id,
            "content": "总结 README.md", "ts": ts,
        },
        "SessionWaitingForInputEvent": {
            "type": "session.waiting_for_input", "session_id": session_id,
            "last_run_id": run_id, "ts": ts,
        },
        "SessionResumedEvent": {"type": "session.resumed", "session_id": session_id, "ts": ts},
        "SessionClosedEvent": {"type": "session.closed", "session_id": session_id, "ts": ts},
    }


# 生成完整的 WIRE_PROTOCOL.md 文档字符串
def generate() -> str:
    run_id = "20260516-100000-abc123"
    ts = "2026-05-16T10:00:00.001Z"
    examples = _examples(run_id, ts)

    sections: list[str] = [
        "# Wire Protocol\n\n",
        "> Generated by `scripts/gen_protocol_doc.py`. **Do not edit manually.**\n\n",
        "## Transport\n\n",
        "- TCP loopback `127.0.0.1:7437` (override via `IWAN_HOST` / `IWAN_PORT`)\n",
        "- Each message is one `\\n`-terminated JSON line (NDJSON)\n",
        "- Commands use JSON-RPC 2.0 (client → server); Events use `kind=event` envelope (server → client)\n\n",
        "## Commands\n\n",
        "All commands are sent as JSON-RPC 2.0 requests. The `type` field inside `params` is used for routing.\n\n",
    ]
    # 命令与结果成对出现：按 commands.py 源码顺序扫描，新加模型自动进文档
    for model in _defined_models(commands_mod):
        sections.append(_model_section(model.__name__, model, examples.get(model.__name__)))
        sections.append("\n")

    sections.append("## Server Push\n\n")
    sections.append("Events pushed from daemon to subscribed clients over the same TCP connection.\n\n")
    sections.append(_model_section("EventPushEnvelope", EventPushEnvelope, examples["EventPushEnvelope"]))

    sections.append("\n## IPC Events\n\n")
    sections.append("Events sent over the IPC socket (daemon → client).\n\n")
    for model in _defined_models(events_mod):
        sections.append(_model_section(model.__name__, model, examples.get(model.__name__)))
        sections.append("\n")

    sections.append(
        "## Error Codes\n\n"
        "| Code | Name | Meaning |\n"
        "|------|------|---------|\n"
        "| -32700 | Parse Error | Invalid JSON received |\n"
        "| -32600 | Invalid Request | Missing required JSON-RPC fields |\n"
        "| -32601 | Method Not Found | Unknown method |\n"
        "| -32602 | Invalid Params | Parameter validation failed |\n"
        "| -32603 | Internal Error | Handler raised an unhandled exception |\n"
        "| -32000 | Application Error | e.g. another run already in progress |\n"
    )
    return "".join(sections)


# 解析命令行参数，写出或校验 WIRE_PROTOCOL.md
def main() -> None:
    parser = argparse.ArgumentParser(description="Generate WIRE_PROTOCOL.md")
    parser.add_argument("--check", action="store_true", help="Verify file matches generated output")
    parser.add_argument("--output", default=str(_OUTPUT_PATH))
    args = parser.parse_args()

    content = generate()

    if args.check:
        output_path = Path(args.output)
        if not output_path.exists():
            print(f"ERROR: {output_path} not found — run: make docs", file=sys.stderr)
            sys.exit(1)
        if output_path.read_text(encoding="utf-8") != content:
            print(f"ERROR: {output_path} out of sync with code — run: make docs", file=sys.stderr)
            sys.exit(1)
        print(f"OK: {output_path} is up to date.")
    else:
        output_path = Path(args.output)
        output_path.write_text(content, encoding="utf-8")
        print(f"Generated {output_path}")


if __name__ == "__main__":
    main()
