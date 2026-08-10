"""
努力等级模块 - 定义 Agent 执行的深度控制参数

【学习要点】
1. 努力等级（Effort Level）：控制 Agent 在执行任务时的"认真程度"
   - minimal：快速响应，最少读取文件，不验证
   - low：读目标文件就改
   - medium：读相关文件，改完基本验证（默认）
   - high：全面搜索相关代码，改完跑测试，自我审查
   - max：递归搜索整个项目，多轮验证，检查副作用
2. 控制维度：最大文件读取数、验证轮数、最大步数、递归搜索深度、是否自动测试
3. 等级与模型：不同等级可搭配不同模型（如 minimal 用 fast 模型，max 用 powerful 模型）

【核心数据结构】
- EffortLevel：枚举，5 个等级
- EffortParams：数据类，每个等级对应的具体参数
- EFFORT_PRESETS：预设映射，等级 → 参数

【设计理念】
让用户在"快"和"好"之间做选择，避免所有任务都用同一个深度处理。
简单改 typo 用 minimal 秒回，修复杂 bug 用 high 确保不遗漏。
"""
from __future__ import annotations

# dataclasses：数据类装饰器，用于创建简单的数据容器
from dataclasses import dataclass

# enum：枚举类型，用于定义有限的合法值集合
from enum import StrEnum


class EffortLevel(StrEnum):
    """
    努力等级枚举 - 定义 Agent 执行任务的深度

    【学习要点】
    StrEnum 继承自 str + Enum，枚举值本身就是字符串，
    可以直接用于 JSON 序列化和比较。

    【枚举值】
    - MINIMAL: 最快速度，几乎不读上下文，不验证
    - LOW: 读目标文件就改
    - MEDIUM: 读相关文件，改完基本验证（默认）
    - HIGH: 全面搜索，改完跑测试，自我审查
    - MAX: 递归搜索整个项目，多轮验证，检查副作用

    【等级递进关系】
    每提高一级，Agent 会：
    - 读更多文件（了解上下文）
    - 走更多步骤（不急于给出答案）
    - 做更多验证（自检、跑测试）
    - 搜索更深（递归查找相关代码）
    """
    MINIMAL = "minimal"  # 最快速度，最少读取
    LOW = "low"           # 读目标文件就改
    MEDIUM = "medium"    # 默认等级，读相关文件 + 基本验证
    HIGH = "high"        # 全面搜索 + 跑测试 + 自我审查
    MAX = "max"          # 递归搜索 + 多轮验证 + 检查副作用


# 所有合法的努力等级值（用于验证输入）
EFFORT_LEVELS: tuple[str, ...] = tuple(level.value for level in EffortLevel)

# 默认努力等级
_DEFAULT_EFFORT_LEVEL = EffortLevel.MEDIUM.value


@dataclass(frozen=True)
class EffortParams:
    """
    努力等级参数 - 每个等级对应的具体控制参数

    【学习要点】
    frozen=True 使实例不可变，防止运行时被意外修改。
    每个参数控制 Agent 行为的一个维度。

    【字段说明】
    - max_files_read: 单次 run 最多读取的文件数（0 表示不限制）
    - max_verify_rounds: 验证轮数（自我检查、跑测试的次数）
    - max_steps_override: 覆盖 AgentConfig.max_steps（0 表示不覆盖）
    - search_max_depth: 递归搜索文件的最大深度（0 表示不限制）
    - auto_run_tests: 是否在修改代码后自动运行测试
    - read_full_file: 是否读取完整文件内容（False 时只读摘要）
    - verbose_summary: 是否在结束时输出详细总结

    【参数随等级递增的规律】
    等级越高，max_files_read / max_verify_rounds / max_steps_override 越大，
    auto_run_tests 从 False 变 True，search_max_depth 递增。
    """
    # 单次 run 最多读取的文件数（0 = 不限制）
    max_files_read: int = 0
    # 验证轮数（自我检查、跑测试的次数）
    max_verify_rounds: int = 0
    # 覆盖 AgentConfig.max_steps（0 = 不覆盖，使用全局配置）
    max_steps_override: int = 0
    # 递归搜索文件的最大深度（0 = 不限制）
    search_max_depth: int = 0
    # 是否在修改代码后自动运行测试
    auto_run_tests: bool = False
    # 是否读取完整文件内容（False 时只读摘要）
    read_full_file: bool = True
    # 是否在结束时输出详细总结
    verbose_summary: bool = False


# 努力等级预设映射 - 每个等级对应一组预设参数
# 【设计要点】
# - minimal: 几乎不限制，但不做任何验证，最快返回
# - low: 读少量文件，不跑测试
# - medium: 默认等级，读相关文件 + 基本验证
# - high: 全面搜索 + 自动跑测试 + 自我审查
# - max: 递归搜索 + 多轮验证 + 检查副作用 + 详细总结
EFFORT_PRESETS: dict[str, EffortParams] = {
    EffortLevel.MINIMAL.value: EffortParams(
        max_files_read=3,        # 最多读 3 个文件
        max_verify_rounds=0,     # 不验证
        max_steps_override=5,    # 最多 5 步
        search_max_depth=1,      # 搜索深度 1 层
        auto_run_tests=False,    # 不跑测试
        read_full_file=False,    # 只读摘要
        verbose_summary=False,   # 不输出总结
    ),
    EffortLevel.LOW.value: EffortParams(
        max_files_read=10,       # 最多读 10 个文件
        max_verify_rounds=0,     # 不验证
        max_steps_override=10,   # 最多 10 步
        search_max_depth=2,      # 搜索深度 2 层
        auto_run_tests=False,    # 不跑测试
        read_full_file=True,     # 读完整文件
        verbose_summary=False,   # 不输出总结
    ),
    EffortLevel.MEDIUM.value: EffortParams(
        max_files_read=20,       # 最多读 20 个文件
        max_verify_rounds=1,     # 验证 1 轮
        max_steps_override=0,     # 使用全局配置的 max_steps
        search_max_depth=3,      # 搜索深度 3 层
        auto_run_tests=False,    # 不自动跑测试
        read_full_file=True,     # 读完整文件
        verbose_summary=False,   # 不输出总结
    ),
    EffortLevel.HIGH.value: EffortParams(
        max_files_read=50,       # 最多读 50 个文件
        max_verify_rounds=2,     # 验证 2 轮
        max_steps_override=30,   # 最多 30 步（比 medium 多 50%，适合复杂任务）
        search_max_depth=5,      # 搜索深度 5 层
        auto_run_tests=True,     # 自动跑测试
        read_full_file=True,      # 读完整文件
        verbose_summary=True,    # 输出总结
    ),
    EffortLevel.MAX.value: EffortParams(
        max_files_read=0,        # 不限制读取文件数
        max_verify_rounds=3,     # 验证 3 轮
        max_steps_override=40,   # 最多 40 步（适合 RAG 测试等超复杂任务）
        search_max_depth=0,       # 不限制搜索深度
        auto_run_tests=True,     # 自动跑测试
        read_full_file=True,      # 读完整文件
        verbose_summary=True,    # 输出总结
    ),
}


def get_effort_params(level: str) -> EffortParams:
    """
    获取指定努力等级的预设参数

    【参数说明】
    - level: str - 努力等级（"minimal" / "low" / "medium" / "high" / "max"）

    【返回值】
    - EffortParams: 对应等级的参数（如果等级非法，返回 medium 的参数）

    【设计目的】
    提供统一的参数查询入口，内部处理非法值回退。

    【示例】
    ```python
    params = get_effort_params("high")
    # params.max_files_read == 50
    # params.auto_run_tests == True

    params = get_effort_params("invalid")
    # 回退到 medium 的参数
    ```
    """
    # 从预设映射中获取参数，如果等级非法则回退到 medium
    return EFFORT_PRESETS.get(level, EFFORT_PRESETS[EffortLevel.MEDIUM.value])
