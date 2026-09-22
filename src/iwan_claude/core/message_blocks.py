"""
message_blocks 模块 - assistant / tool_result 消息格式化的共享实现

【学习要点】
1. Anthropic 消息协议：工具调用不是一种"角色"，而是 assistant 消息 content
   里的 tool_use block；工具结果则是 user 消息 content 里的 tool_result block。
   两者通过 id / tool_use_id 严格配对——缺一个配对，API 直接 400。
2. 多 block 消息：assistant 的 content 可以是字符串（纯文本回答），也可以是
   block 列表（thinking + text + tool_use 混合）。写回历史前必须统一成
   provider 能接受的形状，这就是 _assistant_msg_from_response 的职责。
3. tool_result 合并：同一次工具回合并行的多个结果，尽量塞进同一条 user 消息
   （Anthropic 推荐做法，减少消息数），见 _add_tool_result_to_messages。
4. 共享动机：这些函数原本内嵌在 langgraph_loop.py 里，plan_execute / debate /
   pipeline 三个引擎也需要同样的消息构造逻辑，抽出来避免"复制 4 份、修 1 份"。

【核心函数】
- _assistant_msg_from_response: LLM 响应 → assistant 消息
- _add_tool_result_to_messages: 工具结果 → user(tool_result) 消息
- _extract_last_assistant_text: 从历史中提取最后的可见文本回答
- _extract_user_goal: 提取用户原始任务文本（跳过 tool_result 回执）
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from iwan_claude.core.llm.types import LlmResponse


# 获取当前 UTC 时间的 ISO 8601 格式字符串
def _now() -> str:
    return datetime.now(UTC).isoformat()


# 将 LLM 响应转换为 assistant 消息（含 thinking/text/tool_use blocks）
def _assistant_msg_from_response(response: LlmResponse) -> dict[str, Any]:
    if response.stop_reason == "tool_use" or (
        response.stop_reason == "max_tokens" and response.tool_calls
    ):
        blocks: list[dict[str, Any]] = []
        if response.thinking_blocks:
            for block in response.thinking_blocks:
                if isinstance(block, dict):
                    text = block.get("thinking") or block.get("text") or str(block)
                else:
                    text = str(block)
                blocks.append({"type": "text", "text": text})
        if response.text:
            blocks.append({"type": "text", "text": response.text})
        for tc in response.tool_calls:
            blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        return {"role": "assistant", "content": blocks}

    content = response.text or ""
    if response.thinking_blocks:
        # 显式标注 list[str]：block.get 的返回类型是 Any，不标注会被推断成 object
        thinking_texts: list[str] = []
        for block in response.thinking_blocks:
            if isinstance(block, dict):
                thinking_texts.append(str(block.get("thinking") or block.get("text") or block))
            else:
                thinking_texts.append(str(block))
        content = "\n".join(thinking_texts) + "\n" + content
    return {"role": "assistant", "content": content}


# 将工具执行结果加入消息历史（Anthropic 要求 tool_result 位于 user 消息中）
def _add_tool_result_to_messages(
    messages: list[dict[str, Any]], tool_use_id: str, result: Any
) -> list[dict[str, Any]]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": result.content if hasattr(result, "content") else str(result),
    }
    if result.is_error if hasattr(result, "is_error") else False:
        block["is_error"] = True

    last = messages[-1] if messages else None
    if (
        last is not None
        and last["role"] == "user"
        and isinstance(last["content"], list)
        and last["content"]
        and all(b.get("type") == "tool_result" for b in last["content"])
    ):
        return messages[:-1] + [{**last, "content": last["content"] + [block]}]
    return messages + [{"role": "user", "content": [block]}]


# 从消息历史中提取最后一条 assistant 消息的纯文本内容
def _extract_last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                else:
                    text_parts.append(str(block))
            return "\n".join(text_parts).strip()
        return str(content).strip()
    return ""


# 提取用户原始任务文本（第一条非 tool_result 的 user 消息，截断 500 字符）
def _extract_user_goal(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            if all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                continue
            text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            continue
        text = text.strip()
        if text:
            return text[:500]
    return ""
