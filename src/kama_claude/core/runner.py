# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入 asyncio 库，用于异步 I/O 操作和任务取消
import asyncio
# 导入 datetime 模块，用于获取当前 UTC 时间
from datetime import UTC, datetime
# 导入 Path 类，用于文件路径操作
from pathlib import Path

# 导入运行开始和结束事件模型
from kama_claude.core.bus.events import RunFinishedEvent, RunStartedEvent
# 导入 KamaConfig 配置类
from kama_claude.core.config import KamaConfig
# 导入 ExecutionContext 执行上下文类，用于跟踪运行状态
from kama_claude.core.context import ExecutionContext
# 导入 EventBus 和 EventHandler，用于事件发布/订阅
from kama_claude.core.events.bus import EventBus, EventHandler
# 导入 EventWriter，用于将事件写入文件
from kama_claude.core.events.writer import EventWriter
# 导入 LLMProvider 基类，定义 LLM 提供者接口
from kama_claude.core.llm.base import LLMProvider
# 导入 AnthropicProvider，用于调用 Anthropic 的 LLM API
from kama_claude.core.llm.provider import AnthropicProvider
# 导入 AgentLoop 类，负责驱动 agent 的执行循环
from kama_claude.core.loop import AgentLoop
# 导入 RUNS_DIR（运行记录目录）和 new_run_id（生成唯一运行 ID）
from kama_claude.core.runs import RUNS_DIR, new_run_id
# 导入 ReadFileTool，一个内置工具（读取文件）
from kama_claude.core.tools.builtin.read_file import ReadFileTool
# 导入 ToolRegistry，用于注册和管理工具
from kama_claude.core.tools.registry import ToolRegistry


# 获取当前 UTC 时间的 ISO 8601 格式字符串
def _now() -> str:
    return datetime.now(UTC).isoformat()


# AgentRunner 类：组装所有运行时依赖，准备执行一次完整的 agent run
class AgentRunner:
    # 初始化方法：组装运行所需的所有依赖
    # 函数作用：创建 AgentRunner 实例，保存配置和可选依赖
    # 传参：
    #   config - KamaConfig 配置对象，包含 LLM 模型、最大步骤数等配置
    #   provider - 可选的 LLMProvider 实例，默认为 None（使用默认的 AnthropicProvider）
    #   extra_handlers - 可选的事件处理器列表，用于接收运行过程中的事件
    #   runs_dir - 可选的运行记录目录，默认为 RUNS_DIR
    # 返回值：None
    def __init__(
        self,
        config: KamaConfig,
        *,
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        runs_dir: Path | None = None,
    ) -> None:
        # 保存配置对象
        self._config = config
        # 保存 LLM 提供者（可为 None，运行时创建）
        self._provider = provider
        # 保存额外的事件处理器列表，默认为空列表
        self._extra_handlers: list[EventHandler] = extra_handlers or []
        # 保存运行记录目录，默认为 RUNS_DIR
        self._runs_dir = runs_dir or RUNS_DIR

    # 执行一次完整的 agent run：生成 run_id、接线事件总线、驱动 AgentLoop
    # 函数作用：执行一次完整的 agent run，从开始到结束
    # 传参：goal - 用户指定的目标字符串
    # 返回值：None
    async def run(self, goal: str) -> None:
        # 生成唯一的运行 ID（格式如 20260511-161020-abc123）
        run_id = new_run_id()
        # 构建运行记录目录路径（runs/<run_id>）
        run_path = self._runs_dir / run_id
        # 创建运行记录目录（如果不存在），parents=True 表示创建所有父目录
        run_path.mkdir(parents=True, exist_ok=True)

        # 创建事件总线实例，用于发布和订阅事件
        bus = EventBus()
        # 将所有额外的事件处理器注册到事件总线
        for h in self._extra_handlers:
            bus.subscribe(h)

        # 获取 LLM 提供者：如果已传入则使用，否则创建默认的 AnthropicProvider
        # 使用配置中的默认模型名称（如 claude-3-sonnet-20240229）
        provider = self._provider or AnthropicProvider(self._config.llm.default_model)
        # 创建工具注册表实例
        registry = ToolRegistry()
        # 注册内置的 ReadFileTool 工具
        registry.register(ReadFileTool())
        # 创建 AgentLoop 实例，传入 LLM 提供者、工具注册表和事件总线
        loop = AgentLoop(provider, registry, bus)

        # 创建执行上下文，保存运行状态
        context = ExecutionContext(
            run_id=run_id,              # 运行 ID
            goal=goal,                  # 用户目标
            max_steps=self._config.agent.max_steps,  # 最大步骤数（防止无限循环）
        )

        # 使用 async with 创建 EventWriter，自动管理文件写入和关闭
        # 将事件写入到 runs/<run_id>/events.jsonl 文件
        async with EventWriter(run_path / "events.jsonl") as writer:
            # 将 EventWriter 注册到事件总线，所有事件都会被写入文件
            writer.subscribe(bus)
            # 发布运行开始事件，通知所有订阅者
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))

            # 标记是否被取消
            cancelled = False
            try:
                # 启动 AgentLoop 执行，传入执行上下文
                await loop.run(context)
            except asyncio.CancelledError:
                # 如果收到取消信号（如用户按 Ctrl+C），标记为已取消
                cancelled = True
                # 如果运行尚未完成，标记为失败
                if not context.is_done():
                    context.mark_failed("cancelled")

            # 发布运行结束事件，通知所有订阅者
            await bus.publish(
                RunFinishedEvent(
                    run_id=run_id,      # 运行 ID
                    status=context.status,  # 运行状态（success/failed/cancelled）
                    reason=context.reason,  # 失败原因（如果有）
                    steps=context.step,     # 执行的步骤数
                    ts=_now(),          # 结束时间戳
                )
            )

        # 如果运行被取消，重新抛出 CancelledError，让上层处理
        if cancelled:
            raise asyncio.CancelledError()
