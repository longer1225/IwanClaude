"""
模型预设模块 - 定义不同任务复杂度对应的模型预设

【学习要点】
1. 模型预设（Model Preset）：将常用模型组合打包成预设，用户一键切换
   - fast：快速模型，适合简单任务（如改 typo、回答问题）
   - balanced：平衡模型，适合日常开发（默认）
   - powerful：强力模型，适合复杂任务（如架构设计、复杂 bug）
2. 预设内容：模型名称 + context window + 描述
3. 与 Effort Level 配合：effort 控制"做多深"，model preset 控制"用多聪明的脑子"

【核心数据结构】
- ModelPreset：数据类，每个预设包含模型名、context window、描述
- MODEL_PRESETS：预设映射，名称 → ModelPreset

【设计理念】
让用户在不同任务场景下快速切换模型：
- 写一行注释 → fast（秒回）
- 修一个 bug → balanced（够用）
- 设计整个模块 → powerful（最聪明）
"""
from __future__ import annotations

# dataclasses：数据类装饰器，用于创建简单的数据容器
from dataclasses import dataclass

# enum：枚举类型，用于定义有限的合法值集合
from enum import StrEnum


class ModelPresetName(StrEnum):
    """
    模型预设名称枚举 - 定义可选的模型预设

    【学习要点】
    StrEnum 继承自 str + Enum，枚举值本身就是字符串，
    可以直接用于 JSON 序列化和比较。

    【枚举值】
    - FAST: 快速模型，适合简单任务（成本低、速度快）
    - BALANCED: 平衡模型，适合日常开发（默认）
    - POWERFUL: 强力模型，适合复杂任务（最强但最贵最慢）
    """
    FAST = "fast"                # 快速模型
    BALANCED = "balanced"        # 平衡模型（默认）
    POWERFUL = "powerful"        # 强力模型


# 所有合法的模型预设名称（用于验证输入）
MODEL_PRESET_NAMES: tuple[str, ...] = tuple(p.value for p in ModelPresetName)

# 默认模型预设
_DEFAULT_MODEL_PRESET = ModelPresetName.BALANCED.value


@dataclass(frozen=True)
class ModelPreset:
    """
    模型预设 - 每个预设对应一个具体的模型配置

    【学习要点】
    frozen=True 使实例不可变，防止运行时被意外修改。

    【字段说明】
    - model: 模型名称（传给 LLM API 的 model 参数）
    - context_window: 模型的上下文窗口大小（token 数，用于计算 context_pct）
    - description: 预设的人类可读描述

    【使用场景】
    - fast：简单问答、改 typo、格式化代码
    - balanced：日常开发、修 bug、写功能
    - powerful：架构设计、复杂调试、代码审查
    """
    # 模型名称（传给 LLM API 的 model 参数）
    model: str
    # 模型的上下文窗口大小（token 数）
    context_window: int
    # 预设的人类可读描述
    description: str


# 模型预设映射 - 每个预设名称对应一组模型配置
# 【设计要点】
# - fast: 使用小模型，context window 较小，成本低速度快
# - balanced: 使用中等模型，context window 适中（默认）
# - powerful: 使用最强模型，context window 最大
#
# 【模型名称说明】
# 以下模型名称是常见的 API 模型名，用户可以在 config.toml 中覆盖：
# - "deepseek-chat"：DeepSeek 的快速模型
# - "claude-sonnet-4-6"：Claude Sonnet（平衡）
# - "claude-opus-4-1-20250805"：Claude Opus（强力）
#
# 用户可以根据自己的 API 提供商修改这些值。
MODEL_PRESETS: dict[str, ModelPreset] = {
    ModelPresetName.FAST.value: ModelPreset(
        model="deepseek-chat",           # DeepSeek 快速模型
        context_window=64_000,           # 64K context window
        description="快速模型，适合简单任务（低成本、高速度）",
    ),
    ModelPresetName.BALANCED.value: ModelPreset(
        model="claude-sonnet-4-6",       # Claude Sonnet 平衡模型
        context_window=128_000,          # 128K context window
        description="平衡模型，适合日常开发（性能与成本均衡）",
    ),
    ModelPresetName.POWERFUL.value: ModelPreset(
        model="claude-opus-4-1-20250805",  # Claude Opus 强力模型
        context_window=200_000,             # 200K context window
        description="强力模型，适合复杂任务（最强性能、最高成本）",
    ),
}


def get_model_preset(name: str) -> ModelPreset:
    """
    获取指定名称的模型预设

    【参数说明】
    - name: str - 预设名称（"fast" / "balanced" / "powerful"）

    【返回值】
    - ModelPreset: 对应的预设配置（如果名称非法，返回 balanced 的预设）

    【设计目的】
    提供统一的预设查询入口，内部处理非法值回退。

    【示例】
    ```python
    preset = get_model_preset("powerful")
    # preset.model == "claude-opus-4-1-20250805"
    # preset.context_window == 200_000

    preset = get_model_preset("invalid")
    # 回退到 balanced 的预设
    ```
    """
    # 从预设映射中获取配置，如果名称非法则回退到 balanced
    return MODEL_PRESETS.get(name, MODEL_PRESETS[ModelPresetName.BALANCED.value])
