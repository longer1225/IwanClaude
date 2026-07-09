# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio 库，用于异步 I/O 操作
import asyncio
# 导入 json 库，用于序列化工具参数
import json
# 导入 sys 库，用于异常退出和标准错误输出
import sys
# 导入 time 库，用于计算运行时长
import time

# 导入 BaseModel，用于事件类型判断
from pydantic import BaseModel

# 导入运行过程中产生的各种事件类型
from kama_claude.core.bus.events import (
    LlmTokenEvent,           # LLM 输出的 token 事件
    RunFinishedEvent,        # 运行结束事件
    RunStartedEvent,         # 运行开始事件
    StepFinishedEvent,       # 步骤结束事件
    StepStartedEvent,        # 步骤开始事件
    ToolCallFailedEvent,     # 工具调用失败事件
    ToolCallFinishedEvent,   # 工具调用完成事件
    ToolCallStartedEvent,    # 工具调用开始事件
)
# 导入 KamaConfig 配置类
from kama_claude.core.config import KamaConfig
# 导入 AgentRunner 类，负责执行 agent run
from kama_claude.core.runner import AgentRunner


# StdoutPrinter 类：订阅事件总线并将运行进度格式化打印到终端
class StdoutPrinter:
    # 初始化方法
    def __init__(self) -> None:
        # 是否处于行内输出状态（LLM token 正在逐字输出时为 True）
        self._inline = False
        # 记录运行开始时间（用于计算总耗时）
        self._run_start: float = 0.0

    # 确保输出一个换行符（在需要新行输出之前调用）
    def _ensure_newline(self) -> None:
        # 如果当前正在行内输出（比如 LLM token），先换行
        if self._inline:
            print()
            self._inline = False

    # 事件处理方法：根据事件类型格式化输出
    async def handle(self, event: BaseModel) -> None:
        # 运行开始事件：打印 run_id
        if isinstance(event, RunStartedEvent):
            self._run_start = time.monotonic()
            print(f"[run] {event.run_id}")

        # 步骤开始事件：打印步骤编号
        elif isinstance(event, StepStartedEvent):
            self._ensure_newline()
            print(f"[step {event.step}] planning...")

        # LLM token 事件：逐字输出（不换行），实现流式输出效果
        elif isinstance(event, LlmTokenEvent):
            print(event.token, end="", flush=True)
            self._inline = True

        # 工具调用开始事件：打印工具名称和参数
        elif isinstance(event, ToolCallStartedEvent):
            self._ensure_newline()
            params_str = json.dumps(event.params, ensure_ascii=False)
            print(f"[tool] {event.tool_name} {params_str}")

        # 工具调用完成事件：打印工具名称和耗时
        elif isinstance(event, ToolCallFinishedEvent):
            print(f"[tool] {event.tool_name} ✓  {event.elapsed_ms}ms")

        # 工具调用失败事件：打印工具名称和错误信息（输出到 stderr）
        elif isinstance(event, ToolCallFailedEvent):
            print(
                f"[tool] {event.tool_name} ✗  {event.error_message}",
                file=sys.stderr,
            )

        # 步骤结束事件：打印步骤编号和完成状态
        elif isinstance(event, StepFinishedEvent):
            self._ensure_newline()
            print(f"[step {event.step}] done")

        # 运行结束事件：打印状态、步骤数和总耗时
        elif isinstance(event, RunFinishedEvent):
            self._ensure_newline()
            elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.status}  {event.steps} steps  {elapsed:.1f}s")


# 执行 kama run --goal "..." 命令
# 函数作用：作为 run 命令的同步入口，创建运行器并执行 agent run
# 传参：goal - 用户指定的目标字符串；config - KamaConfig 配置对象
# 返回值：None
def cmd_run(goal: str, config: KamaConfig) -> None:
    # 创建 StdoutPrinter 实例，用于将事件输出到终端
    printer = StdoutPrinter()
    # 创建 AgentRunner 实例，传入配置和事件处理器
    runner = AgentRunner(config, extra_handlers=[printer.handle])
    try:
        # 使用 asyncio.run() 启动异步运行器，执行 agent run
        asyncio.run(runner.run(goal))
    except KeyboardInterrupt:
        # 用户按 Ctrl+C 中断，以退出码 130 退出（SIGINT 的标准退出码）
        sys.exit(130)
