from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from iwan_claude.core.agents.loader import AgentProfile, AgentProfileLoader
from iwan_claude.core.bus.events import SubagentFinishedEvent, SubagentStartedEvent
from iwan_claude.core.context import ExecutionContext
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.events.writer import EventWriter
from iwan_claude.core.loop import AgentLoop
from iwan_claude.core.runs import new_run_id
from iwan_claude.core.subagent.registry import BackgroundTaskRegistry
from iwan_claude.core.tools.base import BaseTool, ToolResult
from iwan_claude.core.tools.builtin.bash import BashTool
from iwan_claude.core.tools.builtin.list_dir import ListDirTool
from iwan_claude.core.tools.builtin.read_file import ReadFileTool
from iwan_claude.core.tools.builtin.task_create import TaskCreateTool
from iwan_claude.core.tools.builtin.task_get import TaskGetTool
from iwan_claude.core.tools.builtin.task_list import TaskListTool
from iwan_claude.core.tools.builtin.task_update import TaskUpdateTool
from iwan_claude.core.tools.builtin.write_file import WriteFileTool
from iwan_claude.core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from iwan_claude.core.llm.base import LLMProvider
    from iwan_claude.core.permissions.manager import PermissionManager

# 全局的 agent profile 加载器（角色配置，如 planner/executor/reviewer）
_profile_loader = AgentProfileLoader()


def _now() -> str:
    return datetime.now(UTC).isoformat()


# spawn_agent 工具的参数模型
# description: 任务描述（用于进度显示）
# prompt: 子 agent 的完整任务描述（子 agent 看不到父对话历史）
# run_in_background: 是否后台并行运行（true=立即返回 run_id，false=阻塞直到完成）
# subagent_type: 角色配置（planner/executor/reviewer），为空使用默认
class SpawnAgentParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str
    prompt: str
    run_in_background: bool = False
    subagent_type: str = ""


# 【s7 核心】在隔离的冷启动上下文中派生子 agent，支持前台阻塞和后台并行两种模式
# 子 agent 特点：
#   1. 独立的 ExecutionContext（冷启动，不继承父对话历史）
#   2. 独立的 EventBus（通过 bridge 桥接到父 bus）
#   3. 独立的 ToolRegistry（可以有不同的工具权限）
#   4. 支持嵌套（最多 2 层）
class SpawnAgentTool(BaseTool):
    name = "spawn_agent"
    description = (
        "Spawn an isolated sub-agent to handle a self-contained sub-task. "
        "The sub-agent starts with a clean context containing only the provided prompt — "
        "it does not inherit the current conversation history. "
        "Use run_in_background=true to run in parallel; retrieve result later with agent_result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "3-5 word task description shown in progress display",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Complete task description including all context the sub-agent needs. "
                    "The sub-agent cannot see the parent conversation, so be explicit."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": "When true, returns immediately with a run_id; use agent_result to poll.",  # noqa: E501
            },
            "subagent_type": {
                "type": "string",
                "description": "Agent role profile (planner/executor/reviewer). Leave empty for default.",  # noqa: E501
            },
        },
        "required": ["description", "prompt"],
    }
    params_model = SpawnAgentParams

    # 构造 SpawnAgentTool；depth=0 表示根 agent，最大允许嵌套深度为 2
    # provider: LLM 提供者（子 agent 共享父 agent 的 provider）
    # parent_bus: 父 agent 的事件总线（用于桥接子事件）
    # parent_run_id: 父 agent 的 run_id（用于事件关联）
    # permission_manager: 权限管理器（子 agent 共享父 agent 的权限检查）
    # max_steps: 最大步骤数
    # task_registry: 后台任务注册表（管理后台运行的子 agent）
    # runs_dir: run 目录（用于写入子 agent 的 events.jsonl）
    # session_id: 会话 ID
    # llm_model_name: 当前配置的模型名（system prompt 身份声明用）
    # depth: 当前嵌套深度（0=根，1=第一层子，2=第二层子，超过2不允许）
    def __init__(
        self,
        provider: LLMProvider,
        parent_bus: EventBus,
        parent_run_id: str,
        permission_manager: PermissionManager | None,
        max_steps: int,
        task_registry: BackgroundTaskRegistry,
        runs_dir: Path,
        session_id: str,
        llm_model_name: str,
        depth: int = 0,
    ) -> None:
        self._provider = provider
        self._parent_bus = parent_bus
        self._parent_run_id = parent_run_id
        self._permission_manager = permission_manager
        self._max_steps = max_steps
        self._task_registry = task_registry
        self._runs_dir = runs_dir
        self._session_id = session_id
        self._llm_model_name = llm_model_name
        self._depth = depth

    # 派生子 agent，前台时阻塞直到完成并返回结果，后台时立即返回 run_id
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SpawnAgentParams.model_validate(params)

        # 【安全检查】嵌套深度限制：最多 2 层（根 → 子 → 孙子）
        # 防止无限递归嵌套导致资源耗尽
        if self._depth >= 2:
            return ToolResult(
                content="Subagent nesting limit (2) reached; cannot spawn further subagents.",
                is_error=True,
                error_type="runtime_error",
            )

        # 【角色配置】如果指定了 subagent_type，加载对应的角色 profile
        # profile 包含：system_prompt（角色提示词）、allowed_tools（允许的工具列表）
        profile: AgentProfile | None = None
        if p.subagent_type:
            profile = _profile_loader.load(p.subagent_type)

        # 【创建子 agent 的上下文】子 agent 是冷启动，不继承父对话历史
        # 只有用户提供的 prompt 作为初始上下文
        child_run_id = new_run_id()
        child_context = ExecutionContext(
            run_id=child_run_id,
            goal=p.prompt,
            max_steps=self._max_steps,
            # 如果有角色配置，用角色的 system_prompt 覆盖默认
            system_prompt_override=profile.system_prompt if profile else None,
        )

        # 【创建子 agent 的事件总线】独立的 bus，不直接使用父 bus
        child_bus = EventBus()

        # 【事件桥接】将子 bus 的所有事件桥接到父 bus
        # 这样 TUI 可以看到子 agent 的进度（step 事件、tool 事件等）
        async def _bridge(event: BaseModel) -> None:
            await self._parent_bus.publish(event)

        child_bus.subscribe(_bridge)

        # 【构建子 agent 的工具注册表】基于角色配置过滤工具
        # 例如：planner 角色可能只允许 read_file、list_dir，不允许 write_file、bash
        child_registry = self._build_child_registry(child_bus, child_run_id, profile)

        # 【创建子 agent 的执行循环】和根 agent 使用相同的 AgentLoop
        child_loop = AgentLoop(
            self._provider,           # 共享 LLM provider
            child_registry,          # 独立的工具注册表
            child_bus,               # 独立的事件总线
            llm_model_name=self._llm_model_name,
            permission_manager=self._permission_manager,  # 共享权限管理器
            session_id=self._session_id,
        )

        # 【发布子 agent 启动事件】TUI 据此渲染嵌套进度
        await self._parent_bus.publish(
            SubagentStartedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                description=p.description,
                ts=_now(),
            )
        )

        # 【创建子 agent 的 run 目录】用于写入 events.jsonl
        child_run_path = self._runs_dir / child_run_id
        child_run_path.mkdir(parents=True, exist_ok=True)

        # 【后台模式】立即返回 run_id，子 agent 在后台运行
        if p.run_in_background:
            # 创建后台任务
            task: asyncio.Task[None] = asyncio.create_task(
                self._run_background(
                    child_loop, child_context, child_bus, child_run_path, child_run_id
                )
            )
            # 注册到任务注册表（供 agent_result 工具查询）
            self._task_registry.register(child_run_id, task, child_context)
            # 立即返回，不等待子 agent 完成
            return ToolResult(
                content=(
                    f"Subagent started in background. run_id={child_run_id}. "
                    f"Use agent_result(run_id='{child_run_id}') to retrieve result."
                )
            )

        # 【前台模式】阻塞直到子 agent 完成
        async with EventWriter(child_run_path / "events.jsonl") as writer:
            writer.subscribe(child_bus)
            await child_loop.run(child_context)

        # 【发布子 agent 完成事件】TUI 更新进度显示
        await self._parent_bus.publish(
            SubagentFinishedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                status=child_context.status,
                ts=_now(),
            )
        )

        # 【返回结果】根据子 agent 的状态返回成功或失败
        if child_context.status == "success":
            return ToolResult(
                content=child_context.result or "Subagent completed with no text output."
            )
        return ToolResult(
            content=(
                child_context.result
                or f"Subagent failed (status={child_context.status}, reason={child_context.reason})"
            ),
            is_error=True,
            error_type="runtime_error",
        )

    # 后台任务协程：写事件文件，运行 loop，发布完成事件
    async def _run_background(
        self,
        loop: AgentLoop,
        context: ExecutionContext,
        bus: EventBus,
        run_path: Path,
        run_id: str,
    ) -> None:
        async with EventWriter(run_path / "events.jsonl") as writer:
            writer.subscribe(bus)
            await loop.run(context)
        await self._parent_bus.publish(
            SubagentFinishedEvent(
                run_id=run_id,
                parent_run_id=self._parent_run_id,
                status=context.status,
                ts=_now(),
            )
        )

    # 构造子 registry；基于角色配置过滤工具，深度允许时注册嵌套 SpawnAgentTool
    def _build_child_registry(
        self,
        child_bus: EventBus,
        child_run_id: str,
        profile: AgentProfile | None,
    ) -> ToolRegistry:
        from iwan_claude.core.task.manager import TaskManager

        # 获取角色配置中的允许工具列表（None 表示允许所有）
        allowed: set[str] | None = (
            set(profile.allowed_tools) if profile and profile.allowed_tools else None
        )

        def _allowed(name: str) -> bool:
            return allowed is None or name in allowed

        # 创建工具注册表
        registry = ToolRegistry()
        # 基础文件工具
        _all_tools = [
            ReadFileTool(),
            BashTool(),
            WriteFileTool(),
            ListDirTool(),
        ]
        for t in _all_tools:
            if _allowed(t.name):
                registry.register(t)

        # 任务管理工具（每个子 agent 有独立的任务管理器）
        child_task_manager = TaskManager(self._runs_dir / child_run_id / ".tasks")
        for t in [
            TaskCreateTool(child_task_manager),
            TaskUpdateTool(child_task_manager),
            TaskListTool(child_task_manager),
            TaskGetTool(child_task_manager),
        ]:
            if _allowed(t.name):
                registry.register(t)

        # 【嵌套支持】如果深度 < 1（根 agent 的子 agent），允许继续嵌套
        # 注册嵌套的 SpawnAgentTool（depth + 1）和 AgentResultTool
        if self._depth < 1:
            nested = SpawnAgentTool(
                provider=self._provider,
                parent_bus=child_bus,
                parent_run_id=child_run_id,
                permission_manager=self._permission_manager,
                max_steps=self._max_steps,
                task_registry=self._task_registry,
                runs_dir=self._runs_dir,
                session_id=self._session_id,
                llm_model_name=self._llm_model_name,
                depth=self._depth + 1,
            )
            if _allowed("spawn_agent"):
                registry.register(nested)
            if _allowed("agent_result"):
                registry.register(AgentResultTool(self._task_registry))

        return registry


# agent_result 工具的参数模型
class AgentResultParams(BaseModel):
    run_id: str


# 查询后台 subagent 的执行状态和最终结果
class AgentResultTool(BaseTool):
    name = "agent_result"
    description = (
        "Retrieve the result of a background sub-agent previously started with spawn_agent. "
        "Returns 'still running' if the sub-agent has not yet completed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The run_id returned by spawn_agent(run_in_background=true)",
            },
        },
        "required": ["run_id"],
    }
    params_model = AgentResultParams

    # 初始化，持有共享的后台任务注册表
    def __init__(self, task_registry: BackgroundTaskRegistry) -> None:
        self._task_registry = task_registry

    # 查询指定 run_id 的后台任务状态，返回结果或错误
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = AgentResultParams.model_validate(params)
        entry = self._task_registry.get(p.run_id)
        if entry is None:
            return ToolResult(
                content=f"Unknown run_id: {p.run_id}. Only background subagents can be queried.",
                is_error=True,
                error_type="runtime_error",
            )
        task, context = entry
        # 任务还在运行中
        if not task.done():
            return ToolResult(content="still running")
        # 任务被取消
        if task.cancelled():
            return ToolResult(
                content="Subagent was cancelled.", is_error=True, error_type="runtime_error"
            )
        # 任务抛出异常
        exc = task.exception()
        if exc is not None:
            return ToolResult(
                content=f"Subagent raised an exception: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        # 任务成功完成
        return ToolResult(content=context.result or "Subagent completed with no text result.")