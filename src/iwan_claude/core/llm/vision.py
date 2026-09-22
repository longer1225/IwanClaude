"""
多模态（图片输入）辅助模块 - 占位实现

【学习要点】
1. 占位模式：接口已定义但未启用，不影响现有 Provider
2. 零侵入：不修改现有 Provider 的 chat 方法
3. 后续扩展：配置支持 vision 的模型后即可启用

【设计原则】
- 不修改现有 Provider 代码（避免引入 bug）
- 提供辅助函数，后续 Provider 可选择性调用
- 消息格式兼容 Anthropic API 的 image content block

【Anthropic 图片消息格式】
```python
{
    "role": "user",
    "content": [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image", "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "<base64_encoded_data>"
        }}
    ]
}
```

【后续启用步骤】
1. 在 Provider 实现类中设置 supports_vision = True
2. 在 chat 方法中调用 filter_vision_blocks(messages, supports_vision)
3. 确保 API 端点支持图片输入（如 claude-3.5-sonnet, gpt-4o）
"""
from __future__ import annotations

from typing import Any


def filter_vision_blocks(
    messages: list[dict[str, Any]],
    supports_vision: bool,
) -> list[dict[str, Any]]:
    """
    过滤消息中的图片 content block

    【参数说明】
    - messages: 消息历史列表
    - supports_vision: Provider 是否支持图片输入

    【行为】
    - supports_vision=True：原样返回消息（不过滤）
    - supports_vision=False：过滤掉 image 类型的 content block
    - 如果过滤后 content 为空，替换为占位文本"[image omitted - vision not supported]"

    【返回值】
    - list[dict]: 过滤后的消息列表（新列表，不修改原消息）

    【使用示例】
    ```python
    # 在 Provider 的 chat 方法中调用
    messages = filter_vision_blocks(messages, self.supports_vision)
    ```
    """
    if supports_vision:
        # Provider 支持图片，原样返回
        return messages

    # Provider 不支持图片，过滤掉 image block
    filtered: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            # 非列表 content（纯文本），原样保留
            filtered.append(msg)
            continue

        # 过滤掉 image block
        new_blocks: list[dict[str, Any]] = []
        has_image = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                has_image = True
                continue
            new_blocks.append(block)

        if not has_image:
            # 没有图片 block，原样保留
            filtered.append(msg)
        elif new_blocks:
            # 有图片但过滤后仍有内容，用过滤后的 content
            new_msg = dict(msg)
            new_msg["content"] = new_blocks
            filtered.append(new_msg)
        else:
            # 过滤后 content 为空，替换为占位文本
            new_msg = dict(msg)
            new_msg["content"] = "[image omitted - vision not supported]"
            filtered.append(new_msg)

    return filtered


def has_image_content(messages: list[dict[str, Any]]) -> bool:
    """
    检测消息中是否包含图片 content block

    【参数说明】
    - messages: 消息历史列表

    【返回值】
    - bool: 是否包含图片

    【用途】
    - Provider 可在调用前检测，决定是否走 vision 路径
    - 日志记录，便于调试
    """
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                return True
    return False
