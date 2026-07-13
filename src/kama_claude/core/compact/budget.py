from __future__ import annotations

from typing import Any

# 工具结果的截断阈值：超过 8000 字符的工具结果会被截断
TOOL_RESULT_LIMIT = 8_000
# 截断后保留的字符数：保留前 4000 字符，后面的用省略标记代替
TOOL_RESULT_KEEP = 4_000


# 对消息列表中超长的 tool_result 内容做内存截断，返回处理后的新列表
# 注意：此函数只修改内存中的消息，不修改磁盘上的 thread.jsonl
# 调用方：SessionStore.read_messages() 在读取消息后调用
def truncate_tool_results(
    messages: list[dict[str, Any]],
    limit: int = TOOL_RESULT_LIMIT,   # 截断阈值，默认 8000 字符
    keep: int = TOOL_RESULT_KEEP,     # 保留字符数，默认 4000 字符
) -> list[dict[str, Any]]:
    result = []
    for msg in messages:
        # 只处理 user 角色的消息（tool_result 只出现在 user 消息中）
        if msg.get("role") != "user":
            result.append(msg)
            continue
        # 获取消息内容
        content = msg.get("content")
        # 如果内容不是列表（即没有 tool_result），直接添加
        if not isinstance(content, list):
            result.append(msg)
            continue
        # 处理内容块列表
        new_blocks = []
        for block in content:
            # 只处理 tool_result 类型的块，且内容是字符串
            if block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                text = block["content"]
                # 如果内容超过阈值，进行截断
                if len(text) > limit:
                    # 计算被省略的字符数
                    omitted = len(text) - keep
                    # 创建块的副本（避免修改原对象）
                    block = dict(block)
                    # 截断内容，保留前 keep 个字符，添加省略标记
                    block["content"] = (
                        text[:keep]
                        + f"\n[... {omitted} chars omitted. Full output in run events.]"
                    )
            # 添加处理后的块
            new_blocks.append(block)
        # 添加处理后的消息（创建副本，避免修改原对象）
        result.append({**msg, "content": new_blocks})
    # 返回新的消息列表（原列表不变）
    return result