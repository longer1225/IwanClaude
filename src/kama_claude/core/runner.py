# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：用于异步编程
import asyncio
# 导入 dataclasses：用于定义数据类
from dataclasses import dataclass
# 导入 datetime：用于生成时间戳
from datetime import UTC, datetime
# 导入 Path：用于文件路径操作
from pathlib import Path

# 导入事件类：用于发布运行开始和结束事件
from kama_claude.core.bus.events import RunFinishedEvent, RunStartedEvent
# 导入配置类：用于获取配置
from kama_claude.core.config import KamaConfig
# 导入执行上下文：用于存储运行状态
from kama_claude.core.context import ExecutionContext
# 导入事件总线：用于发布/订阅事件
from kama_claude.core.events.bus import EventBus, EventHandler
# 导入事件写入器：用于将事件写入文件
from kama_claude.core.events.writer import EventWriter
# 导入 LLM 提供者接口：定义统一的接口
from kama_claude.core.llm.base import LLMProvider
# 导入 Anthropic 提供者：真正的 LLM API 调用实现
from kama_claude.core.llm.provider import AnthropicProvider
# 导入 AgentLoop：核心的 agent 循环逻辑
from kama_claude.core.loop import AgentLoop
# 导入运行相关的常量和函数
from kama_claude.core.runs import RUNS_DIR, new_run_id
# 导入任务管理器：用于管理任务
from kama_claude.core.task.manager import TaskManager
# 导入内置工具
from kama_claude.core.tools.builtin import (
    BashTool,
    ListDirTool,
    ReadFileTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    WriteFileTool,
)
# 导入工具注册表：用于注册和管理工具
from kama_claude.core.tools.registry import ToolRegistry
# 导入追踪提供者：包装 LLMProvider 添加追踪功能
from kama_claude.core.trace.provider import TracingProvider
# 导入追踪写入器：用于写入追踪记录
from kama_claude.core.trace.writer import TraceWriter


# 返回当前时间的 ISO 格式字符串（UTC 时区）
def _now() -> str:
    return datetime.now(UTC).isoformat()


# RunOutcome：运行结果的数据类
# 什么是数据类？就是只包含数据的类，用于存储结构化信息
@dataclass
class RunOutcome:
    # 运行状态（success / failed / cancelled）
    status: str
    # 运行结果（最终文本）
    result: str
    # 原因（失败或取消的原因）
    reason: str | None


# AgentRunner 类：协调所有组件，执行一次完整的 agent run
# 什么是协调器？就是把各个组件（LLM、工具、事件总线）组装起来，让它们协同工作
class AgentRunner:
    # 组装所有运行时依赖，准备执行一次完整的 agent run
    # config：配置对象
    # bus：事件总线（可选，外部传入以便共享）
    # provider：LLM 提供者（可选，外部传入以便复用或测试）
    # extra_handlers：额外的事件处理器（可选）
    # runs_dir：运行记录目录（可选）
    # trace：追踪写入器（可选）
    def __init__(
        self,
        config: KamaConfig,
        *,
        bus: EventBus | None = None,
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        runs_dir: Path | None = None,
        trace: TraceWriter | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._provider = provider
        self._extra_handlers: list[EventHandler] = extra_handlers or []
        self._runs_dir = runs_dir or RUNS_DIR
        self._trace = trace

    # 构建工具注册表，注入 TaskManager（任务工具共享同一实例）
    # 什么是依赖注入？就是把组件需要的依赖从外部传入，而不是自己创建
    def _build_registry(self, task_manager: TaskManager) -> ToolRegistry:
        registry = ToolRegistry()
        # 文件操作工具（4 个）
        registry.register(ReadFileTool())   # 读取文件
        registry.register(BashTool())       # 执行命令
        registry.register(WriteFileTool())  # 写入文件
        registry.register(ListDirTool())    # 列出目录
        # 任务管理工具（4 个）
        registry.register(TaskCreateTool(task_manager))  # 创建任务
        registry.register(TaskUpdateTool(task_manager))  # 更新任务
        registry.register(TaskListTool(task_manager))    # 列出任务
        registry.register(TaskGetTool(task_manager))     # 获取任务详情
        return registry

    # 执行一次完整的 agent run（委托给 run_and_capture，忽略返回值）
    async def run(self, goal: str, *, run_id: str | None = None) -> None:
        await self.run_and_capture(goal, run_id=run_id)

    # 执行 agent run 并返回 RunOutcome（含最终文字结果）
    async def run_and_capture(
        self, goal: str, *, run_id: str | None = None
    ) -> RunOutcome:
        # 如果没有指定 run_id，生成一个新的
        run_id = run_id or new_run_id()
        # 创建运行目录：runs/{run_id}
        run_path = self._runs_dir / run_id
        run_path.mkdir(parents=True, exist_ok=True)

        # 创建任务管理器，任务文件存储在 runs/{run_id}/.tasks 目录
        task_manager = TaskManager(run_path / ".tasks")

        # 如果没有外部传入的事件总线，创建一个新的
        bus = self._bus if self._bus is not None else EventBus()
        # 注册额外的事件处理器
        for h in self._extra_handlers:
            bus.subscribe(h)

        # 创建执行上下文：存储运行状态（run_id、goal、最大步骤等）
        context = ExecutionContext(
            run_id=run_id,
            goal=goal,
            max_steps=self._config.agent.max_steps,
        )

        # 使用 EventWriter 将事件写入 events.jsonl 文件（上下文管理器）
        # 什么是上下文管理器？就是用 async with 包裹，自动处理打开和关闭
        async with EventWriter(run_path / "events.jsonl") as writer:
            # 订阅事件总线：所有事件都会被写入文件
            writer.subscribe(bus)
            # 发布运行开始事件
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))

            # 创建 LLM 提供者：如果外部没有传入，创建默认的 AnthropicProvider
            provider: LLMProvider = self._provider or AnthropicProvider(
                self._config.llm.default_model
            )
            
            # 如果启用了追踪，将 provider 包装成 TracingProvider
            if self._trace is not None:
                provider = TracingProvider(
                    provider,
                    self._trace,
                    include_payload=self._config.trace.include_llm_payload,
                )
            
            # 构建工具注册表
            registry = self._build_registry(task_manager)
            
            # 创建 AgentLoop：核心循环，负责与 LLM 和工具交互
            loop = AgentLoop(provider, registry, bus)

            # 标记是否被取消
            cancelled = False
            try:
                # 执行核心循环
                await loop.run(context)
            except asyncio.CancelledError:
                # 如果任务被取消
                cancelled = True
                # 如果还没有完成，标记为失败
                if not context.is_done():
                    context.mark_failed("cancelled")

            # 发布运行结束事件
            await bus.publish(
                RunFinishedEvent(
                    run_id=run_id,
                    status=context.status,
                    reason=context.reason,
                    steps=context.step,
                    ts=_now(),
                )
            )

        # 如果被取消，重新抛出异常
        if cancelled:
            raise asyncio.CancelledError()

        # 返回运行结果
        return RunOutcome(
            status=context.status,
            result=context.result,
            reason=context.reason,
        )
