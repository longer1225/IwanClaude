from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kama_claude.core.bus.events import ContextCompactedEvent
from kama_claude.core.events.bus import EventBus

if TYPE_CHECKING:
    from kama_claude.core.context import ExecutionContext
    from kama_claude.core.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# 压缩提示词模板：要求 LLM 将对话历史总结为六段结构的交接摘要
# 这六段对应 agent 接续任务时最容易丢失的信息：原目标、已完成步骤、约束条件、
# 文件状态、剩余任务、必须原样保留的关键数据（ID、错误信息、配置值）
_COMPACT_PROMPT = """\
You are compressing an agent conversation into a handoff summary.
Another LLM instance will continue this task from your summary alone — make it complete.

Structure your response with exactly these six sections:

## 1. Original Goal
One sentence describing what the user asked the agent to accomplish.

## 2. Completed Steps
Bullet list of what has been done. Be specific (file paths, commands run, decisions made).

## 3. Key Constraints & Discoveries
Facts learned during the run that affect future decisions \
(e.g., API limitations, file formats, user preferences stated mid-conversation).

## 4. Current File State
For each file that was created or modified: path, a one-line description of its current state.

## 5. Remaining TODOs
Ordered list of what still needs to be done to complete the original goal.

## 6. Critical Data
Any values the next LLM needs verbatim: IDs, tokens, exact error messages, config values \
discovered during the run.

Be concise. Omit reasoning steps and intermediate attempts. Keep conclusions.\
"""


# 返回当前 UTC 时间的简短时间戳字符串（用于摘要文件名，格式：20260713_103000）
def _ts_compact() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


# 返回当前 UTC 时间的 ISO 8601 字符串（用于事件时间戳）
def _now() -> str:
    return datetime.now(UTC).isoformat()


# 压缩结果数据类：封装压缩后的摘要文本和 token 统计
# original_token_estimate: 原始消息的粗略 token 估算（字符数 / 4）
# summary_tokens: 摘要的实际 token 数
@dataclass
class CompactionResult:
    summary_text: str
    original_token_estimate: int
    summary_tokens: int


class Compactor:
    # 初始化压缩器，绑定事件总线、session 目录和 session ID
    # bus: 用于发布 ContextCompactedEvent，通知 TUI 和其他组件压缩完成
    # session_dir: session 的文件目录，用于写入摘要文件
    # session_id: 当前 session 的唯一标识
    def __init__(self, bus: EventBus, session_dir: Path, session_id: str) -> None:
        self._bus = bus
        self._session_dir = session_dir
        self._session_id = session_id

    # 【自动压缩入口】压缩 ExecutionContext.messages，就地替换消息列表并写 summary 文件
    # 调用方：AgentLoop.run() 在 context_pct 超过阈值时调用
    # 注意：此方法只修改内存中的 context.messages，不覆盖磁盘上的 thread.jsonl
    async def compact(
        self,
        context: ExecutionContext,       # 当前 run 的执行上下文
        provider: LLMProvider,           # LLM 提供者，用于调用 LLM 生成摘要
        focus: str = "",                 # 可选的聚焦提示，让摘要更关注特定内容
    ) -> CompactionResult | None:
        # 调用纯函数式压缩方法，获取摘要结果
        result = await self.compact_messages(context.messages, provider, focus=focus)
        # 如果压缩失败（LLM 出错或返回空摘要），返回 None，调用方保留原消息继续运行
        if result is None:
            return None

        # 【关键】将内存中的消息列表替换为压缩后的对话对
        # 格式：[user: 摘要文本, assistant: "Understood, I'll continue from this summary."]
        # 这样下一轮 LLM 调用时，就只看到摘要而不是完整历史
        context.messages = [
            {"role": "user", "content": result.summary_text},
            {"role": "assistant", "content": "Understood, I'll continue from this summary."},
        ]
        # 写入摘要文件到 session 目录（summary_<ts>.md）
        # 保留备份的原因：压缩是有损操作，如果后续 agent 行为变差，用户可以查看压缩前的内容
        self._write_summary(result.summary_text)
        # 发布 ContextCompactedEvent，通知 TUI 更新上下文水位显示
        await self._bus.publish(
            ContextCompactedEvent(
                session_id=self._session_id,
                run_id=context.run_id,
                original_tokens=result.original_token_estimate,
                summary_tokens=result.summary_tokens,
                ts=_now(),
            )
        )
        logger.info(
            "context compacted session=%s run=%s original≈%d summary=%d tokens",
            self._session_id, context.run_id,
            result.original_token_estimate, result.summary_tokens,
        )
        return result

    # 【纯函数式压缩】接收消息列表，返回 CompactionResult；失败时返回 None
    # 与 compact() 的区别：不修改任何状态，只做压缩计算
    # 调用方：1. compact() 内部调用  2. SessionManager.compact() 手动压缩时调用
    async def compact_messages(
        self,
        messages: list[dict[str, Any]],  # 待压缩的消息列表
        provider: LLMProvider,           # LLM 提供者
        focus: str = "",                 # 可选的聚焦提示
    ) -> CompactionResult | None:
        from kama_claude.core.events.bus import EventBus as _Bus

        # 粗略估算原始消息的 token 数：字符数 / 4（每个 token 约 4 个字符）
        original_estimate = sum(
            len(str(m.get("content", ""))) for m in messages
        ) // 4

        # 将结构化消息列表转换为 LLM 可读的纯文本格式
        history_text = _messages_to_text(messages)
        # 构建压缩提示词，如果有 focus 则追加额外提示
        prompt = _COMPACT_PROMPT
        if focus.strip():
            prompt += f"\n\nIMPORTANT: Pay special attention to: {focus.strip()}"

        # 构建发送给 LLM 的请求：只包含一个 user 消息（提示词 + 历史文本）
        compress_request: list[dict[str, object]] = [
            {"role": "user", "content": f"{prompt}\n\n---\n\n{history_text}"}
        ]

        try:
            # 创建一个静默的 EventBus，不发布任何事件到主总线
            # 原因：压缩过程的内部事件不需要让 TUI 看到
            silent_bus = _Bus()
            # 调用 LLM 生成摘要，不提供任何工具（摘要任务不应再调用工具）
            response = await provider.chat(
                messages=compress_request,
                tool_schemas=[],           # 空列表 = 禁用工具调用
                bus=silent_bus,            # 静默总线，不干扰主事件流
                run_id="compact",          # 特殊 run_id，标识这是压缩操作
                step=0,
                system="You are a helpful assistant that summarizes conversations.",
            )
        except Exception:
            # LLM 调用失败时记录日志，返回 None（压缩失败不是致命错误）
            logger.exception("compactor: LLM call failed, skipping compaction")
            return None

        # 提取摘要文本，去除首尾空格
        summary_text = response.text.strip()
        # 如果摘要为空，记录警告，返回 None
        if not summary_text:
            logger.warning("compactor: LLM returned empty summary, skipping compaction")
            return None

        # 计算摘要的 token 数：优先使用 API 返回的 usage，否则用字符数估算
        summary_tokens = response.usage.output_tokens if response.usage else len(summary_text) // 4

        # 返回压缩结果
        return CompactionResult(
            summary_text=summary_text,
            original_token_estimate=original_estimate,
            summary_tokens=summary_tokens,
        )

    # 将摘要文本写入 session 目录的 summary_<ts>.md 文件
    # 保留备份的目的：如果压缩后 agent 行为变差，用户可以查看压缩前的历史
    def _write_summary(self, text: str) -> None:
        try:
            # 确保 session 目录存在
            self._session_dir.mkdir(parents=True, exist_ok=True)
            # 构建摘要文件路径
            path = self._session_dir / f"summary_{_ts_compact()}.md"
            # 写入摘要文本
            path.write_text(text, encoding="utf-8")
        except Exception:
            # 写入失败不影响压缩流程，只记录日志
            logger.exception("compactor: failed to write summary file")


# 将 Anthropic 格式的消息列表序列化为可供 LLM 阅读的纯文本格式
# 处理三种内容类型：纯文本、工具调用、工具结果
def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages:
        # 获取消息角色（user/assistant），转为大写
        role = msg.get("role", "unknown").upper()
        # 获取消息内容
        content = msg.get("content", "")
        # 如果内容是纯文本，直接添加
        if isinstance(content, str):
            parts.append(f"[{role}]\n{content}")
        # 如果内容是块列表（包含 tool_use 和 tool_result），逐个处理
        elif isinstance(content, list):
            blocks: list[str] = []
            for block in content:
                btype = block.get("type", "")
                # 文本块：直接提取文本
                if btype == "text":
                    blocks.append(block.get("text", ""))
                # 工具调用块：格式化为 <tool_call> 标签
                elif btype == "tool_use":
                    blocks.append(
                        f"<tool_call name={block.get('name')} id={block.get('id')}>\n"
                        f"{block.get('input', {})}\n</tool_call>"
                    )
                # 工具结果块：格式化为 <tool_result> 标签
                elif btype == "tool_result":
                    blocks.append(
                        f"<tool_result id={block.get('tool_use_id')}>\n"
                        f"{block.get('content', '')}\n</tool_result>"
                    )
            # 将所有块拼接成一条消息
            parts.append(f"[{role}]\n" + "\n".join(blocks))
    # 用空行分隔所有消息
    return "\n\n".join(parts)