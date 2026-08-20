"""
自动引擎选择器 - 根据任务类型智能选择最佳 Agent 引擎

【设计思路】
在 run 开始前，用一次轻量 LLM 调用分析用户目标，自动选择最佳引擎：
- 简单问答/探索性任务 → langgraph (ReAct)
- 多步骤开发任务 → plan_execute (先规划再执行)
- 质量敏感任务（代码审查、文档） → debate (worker-critic 辩论)
- 复杂协作任务 → pipeline (planner→executor→reviewer)

【与手动选择的关系】
- engine="auto" → 使用本模块自动选择
- engine="langgraph"/"plan_execute"/... → 用户手动指定，跳过自动选择
- TUI 的 /engine 命令仍可手动切换

【性能考量】
- 只在 run 开始时调用一次 LLM（约 200-300 token）
- 使用短 prompt + 限制 max_tokens=10 降低延迟和成本
- LLM 调用失败时回退到 langgraph（最通用的引擎）
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from iwan_claude.core.llm.base import LLMProvider

log = logging.getLogger(__name__)

# 分类提示词：要求 LLM 返回单个引擎名称
_CLASSIFY_PROMPT = """你是一个任务分类器。根据用户的任务描述，选择最适合的 Agent 引擎。

引擎说明：
- langgraph: 简单问答、探索性任务、边想边做（不知道需要几步）
- plan_execute: 复杂多步骤任务、可以预先规划（如"重构这个模块"、"实现完整功能"）
- debate: 质量敏感任务、需要独立审查（如"审查这段代码"、"写正式文档"、"验证正确性"）
- pipeline: 复杂协作任务、需要规划+执行+审查分离（如"开发新功能"、"大规模重构"）

用户任务：{goal}

只回复引擎名称（langgraph / plan_execute / debate / pipeline），不要其他内容。"""

# 回退引擎：LLM 调用失败时使用
_FALLBACK_ENGINE = "langgraph"

# 合法引擎名称集合
_VALID_ENGINES = frozenset({"langgraph", "plan_execute", "debate", "pipeline"})


async def select_engine(goal: str, provider: LLMProvider) -> str:
    """
    根据用户目标自动选择最佳引擎

    【参数说明】
    - goal: str - 用户原始目标
    - provider: LLMProvider - LLM 提供商（用于分类调用）

    【返回值】
    - str: 引擎名称（langgraph / plan_execute / debate / pipeline）

    【容错设计】
    - LLM 调用失败 → 回退到 langgraph
    - 返回值不在合法集合 → 回退到 langgraph
    - 空目标 → 直接返回 langgraph
    """
    # 空目标直接回退
    if not goal or not goal.strip():
        return _FALLBACK_ENGINE

    try:
        # 构建分类消息
        messages = [{
            "role": "user",
            "content": _CLASSIFY_PROMPT.format(goal=goal[:500]),
        }]

        # 调用 LLM（不使用工具，限制 max_tokens 降低成本）
        response = await provider.chat(
            messages=messages,
            tool_schemas=[],
            bus=None,
            run_id="",
            step=0,
            system="You are a task classifier. Reply with only the engine name.",
        )

        # 提取引擎名称（取第一行，去除空白和标点）
        text = (response.text or "").strip().lower().split("\n")[0].strip()
        # 去除可能的 markdown 格式
        text = text.strip("`*").strip()

        if text in _VALID_ENGINES:
            log.info("engine_selector: goal=%r → engine=%s", goal[:80], text)
            return text

        # 返回值不合法，尝试部分匹配
        for engine in _VALID_ENGINES:
            if engine in text:
                log.info("engine_selector: partial match %r → %s", text, engine)
                return engine

        # 无法识别，回退
        log.warning("engine_selector: unrecognized response=%r, fallback to %s", text, _FALLBACK_ENGINE)
        return _FALLBACK_ENGINE

    except Exception:
        log.warning("engine_selector: LLM call failed, fallback to %s", _FALLBACK_ENGINE, exc_info=True)
        return _FALLBACK_ENGINE
