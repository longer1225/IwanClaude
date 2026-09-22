# =============================================================================
# 文件: src/iwan_claude/core/subagent/tool.py
# 作用: 子 Agent 工具模块 —— 让主 Agent 能够"生"出子 Agent 来并行处理任务
#
# 包含 5 个工具类:
#   1. SpawnAgentTool       —— 生成单个子 Agent（前台/后台两种模式）
#   2. AgentResultTool      —— 查询后台子 Agent 的执行结果
#   3. SpawnAgentsTool      —— 批量并行生成多个子 Agent（带并发控制）
#   4. BatchResultTool      —— 查询批量任务的整体状态
#   5. CancelAgentTool      —— 取消正在运行的子 Agent 或整个批次
#
# 核心设计思想:
#   - 每个子 Agent 有独立的 EventBus 和执行上下文，实现"隔离"
#   - 子 Agent 的事件通过 _bridge 桥接回父 Agent 的 EventBus
#   - 使用 asyncio.Task 实现后台运行，使用 asyncio.Semaphore 控制并发
#   - BackgroundTaskRegistry 统一管理所有子 Agent 的生命周期
#   - 嵌套深度限制为 2 层，防止无限递归生成子 Agent
# =============================================================================


# ═══════════════════════════════════════════════════════════════════════════════
# 导入部分
# ═══════════════════════════════════════════════════════════════════════════════

# `from __future__ import annotations` —— 允许在类型注解中使用前向引用
# 比如 SpawnAgentTool 的 __init__ 参数里引用了还没定义的类型
# 这是 Python 3.10+ 的特性，在运行时不会立即求值类型注解
from __future__ import annotations

import asyncio    # 异步 I/O 框架：Task、Future、Semaphore、timeout、gather
import json       # JSON 序列化/反序列化（本文件中主要用于调试）
import uuid       # 生成唯一 ID（子 Agent 的 run_id、batch_id）
from dataclasses import dataclass  # 数据类装饰器（类型标注用）
from datetime import UTC, datetime  # 时间戳生成（ISO 格式）
from pathlib import Path  # 路径对象（跨平台文件路径操作）
from typing import TYPE_CHECKING, Any  # 类型标注工具

# pydantic: 数据校验库
# BaseModel —— 所有参数模型的基类，提供 model_validate() 方法
# ConfigDict —— Pydantic v2 的配置容器
# Field —— 为字段添加默认值和约束（如 ge=0.0 表示 >= 0）
from pydantic import BaseModel, ConfigDict, Field

# 项目内部模块导入
from iwan_claude.core.agents.loader import AgentProfile, AgentProfileLoader  # Agent 角色配置
from iwan_claude.core.bus.events import SubagentFinishedEvent, SubagentStartedEvent  # 子 Agent 事件
from iwan_claude.core.context import ExecutionContext  # 执行上下文（状态容器）
from iwan_claude.core.events.bus import EventBus  # 事件总线
from iwan_claude.core.events.writer import EventWriter  # 事件写入器
from iwan_claude.core.loop import AgentLoop  # Agent 执行循环
from iwan_claude.core.runs import new_run_id  # 生成 run_id 的工具函数
from iwan_claude.core.subagent.registry import BackgroundTaskRegistry, BatchStatus  # 后台任务注册表
from iwan_claude.core.tools.base import BaseTool, ToolResult  # 工具基类和结果类
from iwan_claude.core.tools.builtin.bash import BashTool  # bash 命令执行工具
from iwan_claude.core.tools.builtin.list_dir import ListDirTool  # 目录列表工具
from iwan_claude.core.tools.builtin.read_file import ReadFileTool  # 文件读取工具
from iwan_claude.core.tools.builtin.task_create import TaskCreateTool  # 任务创建工具
from iwan_claude.core.tools.builtin.task_get import TaskGetTool  # 任务查询工具
from iwan_claude.core.tools.builtin.task_list import TaskListTool  # 任务列表工具
from iwan_claude.core.tools.builtin.task_update import TaskUpdateTool  # 任务更新工具
from iwan_claude.core.tools.builtin.write_file import WriteFileTool  # 文件写入工具
from iwan_claude.core.tools.registry import ToolRegistry  # 工具注册表

# TYPE_CHECKING 块：仅在类型检查时导入，运行时不导入
# 目的：避免循环导入（这些模块可能也导入了本模块）
if TYPE_CHECKING:
    from iwan_claude.core.llm.base import LLMProvider  # LLM 提供者（类型标注用）
    from iwan_claude.core.permissions.manager import PermissionManager  # 权限管理器（类型标注用）


# 全局单例：AgentProfileLoader
# 负责加载 Agent 角色配置文件（如 planner/executor/reviewer 的 system prompt）
_profile_loader = AgentProfileLoader()


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    """获取当前 UTC 时间的 ISO 格式字符串。

    设计原因：
    - 统一时间戳格式，便于事件排序和调试
    - 使用 UTC 避免时区混乱
    - 私有函数（_前缀），仅在本文件内使用
    """
    return datetime.now(UTC).isoformat()


def _new_batch_id() -> str:
    """生成唯一的批次 ID。

    格式: b_{uuid_hex_12_chars}
    设计原因：
    - b_ 前缀便于在日志中快速识别
    - 取 uuid4().hex 的前 12 位，足够唯一且简洁
    - 与 run_id 的格式区分开（run_id 通常带时间戳前缀）
    """
    return f"b_{uuid.uuid4().hex[:12]}"


# ═══════════════════════════════════════════════════════════════════════════════
# 一、SpawnAgentTool —— 单个子 Agent 生成工具
# ═══════════════════════════════════════════════════════════════════════════════


class SpawnAgentParams(BaseModel):
    """SpawnAgentTool 的参数校验模型。

    设计原因：
    - 使用 Pydantic 自动校验参数类型（如 prompt 必须是 str）
    - Field(default=0.0, ge=0.0) 实现约束：默认值 0，且 >= 0
    - model_config = ConfigDict(extra="ignore") 忽略多余字段，提高容错性

    各参数说明：
    - description: 3-5 个字的短描述，用于进度展示（必填）
    - prompt: 子 Agent 的完整指令，包含所有需要的上下文（必填）
    - run_in_background: 是否后台运行。True 立即返回 run_id，False 阻塞等待
    - subagent_type: 子 Agent 角色类型（planner/executor/reviewer），空则用默认
    - timeout_sec: 子 Agent 超时时间（秒），0 表示使用默认值
    """
    model_config = ConfigDict(extra="ignore")
    description: str
    prompt: str
    run_in_background: bool = False
    subagent_type: str = ""
    timeout_sec: float = Field(default=0.0, ge=0.0)


class SpawnAgentTool(BaseTool):
    """生成单个隔离的子 Agent 来处理独立子任务。

    核心特性：
    1. 隔离性：子 Agent 有独立的 EventBus、上下文、工具集
    2. 两种模式：前台（阻塞等待）和后台（立即返回）
    3. 可嵌套：子 Agent 可以继续生成子 Agent（最多 2 层）
    4. 桥接机制：子 Agent 的事件通过 _bridge 回传到父 Agent 的 EventBus

    使用场景：
    - 主 Agent 遇到独立子任务（如"分析日志" + "生成报告"可并行）
    - 需要隔离上下文避免干扰主对话
    - 长时间运行的任务不想阻塞主流程
    """

    # 工具元数据 —— LLM 根据这些描述决定是否调用本工具
    name = "spawn_agent"
    description = (
        "Spawn an isolated sub-agent to handle a self-contained sub-task. "
        "The sub-agent starts with a clean context containing only the provided prompt — "
        "it does not inherit the current conversation history. "
        "Use run_in_background=true to run in parallel; retrieve result later with agent_result."
    )

    # input_schema —— 给 LLM 看的 JSON Schema 格式的参数描述
    # 设计原因：LLM 更容易理解 JSON Schema 而非 Python 类型注解
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
                "description": "When true, returns immediately with a run_id; use agent_result to poll.",
            },
            "subagent_type": {
                "type": "string",
                "description": "Agent role profile (planner/executor/reviewer). Leave empty for default.",
            },
            "timeout_sec": {
                "type": "number",
                "default": 0,
                "description": "Per-subagent timeout in seconds (0 = use registry default, usually 10 min).",
            },
        },
        "required": ["description", "prompt"],
    }
    params_model = SpawnAgentParams

    def __init__(
        self,
        provider: LLMProvider,                      # LLM 提供者 —— 子 Agent 需要调用 LLM
        parent_bus: EventBus,                       # 父 EventBus —— 子 Agent 事件桥接回父
        parent_run_id: str,                         # 父 run_id —— 建立父子关系
        permission_manager: PermissionManager | None,  # 权限管理器 —— 子 Agent 的工具权限
        max_steps: int,                             # 最大步数 —— 防止子 Agent 无限循环
        task_registry: BackgroundTaskRegistry | None = None,  # 任务注册表 —— 管理后台任务
        runs_dir: Path | None = None,               # 运行目录 —— 子 Agent 的文件输出位置
        session_id: str = "",                       # 会话 ID —— 关联到当前会话
        llm_model_name: str = "",                   # LLM 模型名 —— 使用指定模型
        depth: int = 0,                             # 嵌套深度 —— 防止无限递归
        *,
        batch_id: str | None = None,                # 批次 ID —— 批量任务时标记归属
    ) -> None:
        """初始化 SpawnAgentTool。

        参数设计原因：
        - provider/parent_bus/permission_manager 等：子 Agent 需要继承父 Agent 的核心资源
        - depth: 用计数器而非布尔值，方便日志追踪嵌套层级
        - batch_id: 关键字参数（* 后面），因为它只在批量场景下使用
        - task_registry/runs_dir: 默认值处理为 None 时创建默认实例，简化调用方
        """
        self._provider = provider
        self._parent_bus = parent_bus
        self._parent_run_id = parent_run_id
        self._permission_manager = permission_manager
        self._max_steps = max_steps
        # 使用 or 操作符提供默认值，避免到处写 if None 判断
        self._task_registry = task_registry if task_registry is not None else BackgroundTaskRegistry()
        self._runs_dir = runs_dir if runs_dir is not None else Path.cwd()
        self._session_id = session_id
        self._llm_model_name = llm_model_name
        self._depth = depth
        self._batch_id = batch_id

    # Spawn one subagent. This method is reused both by foreground/background modes and
    # by SpawnAgentsTool when fanning out a batch.
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行子 Agent 生成（核心方法）。

        完整流程：
        1. 参数校验
        2. 嵌套深度检查
        3. 加载子 Agent 角色配置
        4. 创建子 Agent 的执行上下文、EventBus、工具注册表
        5. 创建子 Agent 的执行循环
        6. 桥接事件到父 EventBus
        7. 根据 run_in_background 选择执行模式

        参数:
            params: LLM 生成的参数字典，经 model_validate 校验

        返回:
            ToolResult: 包含 run_id（后台模式）或执行结果（前台模式）
        """
        # Step 1: 用 Pydantic 校验参数，确保类型正确
        p = SpawnAgentParams.model_validate(params)

        # Step 2: 嵌套深度检查
        # 为什么限制 2 层？
        # - 防止 LLM 陷入"生成子 Agent → 子 Agent 再生子 Agent"的无限递归
        # - 每层嵌套都会增加 LLM 调用次数和延迟
        # - 2 层足够覆盖大部分并行场景（主 → 子 → 孙）
        if self._depth >= 2:
            return ToolResult(
                content="Subagent nesting limit (2) reached; cannot spawn further subagents.",
                is_error=True,
                error_type="runtime_error",
            )

        # Step 3: 加载子 Agent 角色配置
        # 角色配置包含 system prompt 和允许的工具列表
        # 如果 subagent_type 为空，profile 保持 None，子 Agent 使用默认配置
        profile: AgentProfile | None = None
        if p.subagent_type:
            try:
                profile = _profile_loader.load(p.subagent_type)
            except Exception as exc:
                return ToolResult(
                    content=f"spawn_agent: unknown subagent_type={p.subagent_type}: {exc}",
                    is_error=True,
                    error_type="runtime_error",
                )

        # Step 4: 创建子 Agent 的执行上下文
        # - 新的 run_id: 每个子 Agent 有唯一标识
        # - goal: 使用 LLM 传入的 prompt 作为目标
        # - system_prompt_override: 如果有角色配置则覆盖默认 system prompt
        child_run_id = new_run_id()
        child_context = ExecutionContext(
            run_id=child_run_id,
            goal=p.prompt,
            max_steps=self._max_steps,
            system_prompt_override=profile.system_prompt if profile else None,
        )

        # Step 5: 创建子 Agent 独立的 EventBus
        # 为什么要独立？
        # - 隔离：子 Agent 的事件不会直接污染父 EventBus
        # - 可控：通过 _bridge 选择性地回传事件
        child_bus = EventBus()

        # Step 6: 事件桥接函数
        # 作用：将子 EventBus 的所有事件转发到父 EventBus
        # 这样 IpcEventBroadcaster 能把子 Agent 的事件也推送给客户端
        async def _bridge(event: BaseModel) -> None:
            await self._parent_bus.publish(event)

        child_bus.subscribe(_bridge)

        # Step 7: 构建子 Agent 的工具注册表
        # 根据 profile 的 allowed_tools 过滤可用工具
        # 嵌套子 Agent 的工具集比父 Agent 少（没有自己的 spawn_agent）
        child_registry = self._build_child_registry(child_bus, child_run_id, profile)

        # Step 8: 创建子 Agent 的执行循环
        # 注意：这里复用了父 Agent 的 provider 和 permission_manager
        # 但使用了独立的 child_bus 和 child_registry
        child_loop = AgentLoop(
            self._provider,
            child_registry,
            child_bus,
            llm_model_name=self._llm_model_name,
            permission_manager=self._permission_manager,
            session_id=self._session_id,
        )

        # Step 9: 发布"子 Agent 启动"事件
        # 客户端 UI 可以据此显示进度条
        await self._parent_bus.publish(
            SubagentStartedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                description=p.description,
                ts=_now(),
            )
        )

        # Step 10: 创建子 Agent 的运行目录
        # 结构: runs/{parent_run_id}/{child_run_id}/events.jsonl
        child_run_path = self._runs_dir / child_run_id
        child_run_path.mkdir(parents=True, exist_ok=True)

        # Step 11: 确定超时时间
        # 优先级：参数指定 > 注册表默认值 > 硬编码 600 秒
        timeout = p.timeout_sec if p.timeout_sec > 0 else self._task_registry.default_timeout_sec
        if timeout <= 0:
            timeout = 600

        # Step 12: 根据 run_in_background 选择执行模式
        # ── 后台模式：立即返回 run_id ──
        if p.run_in_background:
            # 用 asyncio.create_task 把执行包装成后台任务
            task: asyncio.Task[None] = asyncio.create_task(
                self._run_background_wrapped(
                    child_loop,
                    child_context,
                    child_bus,
                    child_run_path,
                    child_run_id,
                    timeout=timeout,
                )
            )
            # 注册到任务注册表，后续可查询结果或取消
            self._task_registry.register(
                child_run_id,
                task,
                child_context,
                description=p.description,
                batch_id=self._batch_id,
            )
            return ToolResult(
                content=(
                    f"Subagent started in background. run_id={child_run_id}. "
                    f"Use agent_result(run_id='{child_run_id}') to retrieve result."
                )
            )

        # ── 前台模式：阻塞等待完成 ──
        # 使用 async with asyncio.timeout 实现超时控制
        # 使用 EventWriter 把子 Agent 的事件写入文件（用于事后调试）
        try:
            async with asyncio.timeout(timeout):
                async with EventWriter(child_run_path / "events.jsonl") as writer:
                    writer.subscribe(child_bus)  # 订阅子 EventBus 的所有事件
                    await child_loop.run(child_context)  # 开始执行，阻塞直到完成
        except TimeoutError:
            child_context.status = "failed"
            child_context.reason = f"timed out after {timeout}s"
            child_context.result = child_context.result or f"Subagent timed out after {timeout}s"

        # 无论成功失败，都发布"子 Agent 完成"事件
        await self._parent_bus.publish(
            SubagentFinishedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                status=child_context.status,
                ts=_now(),
            )
        )

        # Step 13: 返回执行结果
        if child_context.status == "success":
            return ToolResult(
                content=child_context.result or "Subagent completed with no text output."
            )
        # 失败时返回错误信息
        return ToolResult(
            content=(
                child_context.result
                or f"Subagent failed (status={child_context.status}, reason={child_context.reason})"
            ),
            is_error=True,
            error_type="runtime_error",
        )

    # Background wrapper with timeout + mark_finished hook.
    async def _run_background_wrapped(
        self,
        loop: AgentLoop,
        context: ExecutionContext,
        bus: EventBus,
        run_path: Path,
        run_id: str,
        *,
        timeout: int,
    ) -> None:
        """后台执行的包装方法 —— 处理超时、取消、异常等边界情况。

        为什么需要这层包装？
        - 后台任务无法像前台任务那样用 try/except 直接捕获异常
        - 需要统一处理 4 种终止情况：正常完成、超时、取消、异常
        - 每种终止都要更新 context 状态并发布事件通知父 Agent

        处理的 4 种情况：
        1. TimeoutError: 超时，标记为 failed
        2. CancelledError: 被用户取消，标记为 cancelled，重新抛出
        3. Exception: 其他异常，标记为 failed
        4. 正常完成: 不进入任何 except 分支

        无论哪种情况，finally 块都会标记任务为 finished
        """
        try:
            async with asyncio.timeout(timeout):
                await self._run_background(loop, context, bus, run_path, run_id)
        except TimeoutError:
            # 超时处理：更新状态 → 发布事件（用 try/except 防止事件发布本身出错）
            try:
                context.status = "failed"
                context.reason = f"timed out after {timeout}s"
                context.result = context.result or f"Subagent timed out after {timeout}s"
            except Exception:
                pass  # context 可能已被修改，忽略写入失败
            try:
                await self._parent_bus.publish(
                    SubagentFinishedEvent(
                        run_id=run_id,
                        parent_run_id=self._parent_run_id,
                        status="failed",
                        ts=_now(),
                    )
                )
            except Exception:
                pass  # 事件发布失败不影响主流程
        except asyncio.CancelledError:
            # 取消处理：标记为 cancelled，然后重新抛出
            # 为什么要重新抛出？因为 CancelledError 需要传播到 Task 调用方
            try:
                context.status = "cancelled"
                context.reason = context.reason or "cancelled"
            except Exception:
                pass
            try:
                await self._parent_bus.publish(
                    SubagentFinishedEvent(
                        run_id=run_id,
                        parent_run_id=self._parent_run_id,
                        status="cancelled",
                        ts=_now(),
                    )
                )
            except Exception:
                pass
            raise  # 重新抛出 CancelledError，让 Task.done() 能正确检测到取消
        except Exception as exc:
            # 通用异常处理
            try:
                context.status = "failed"
                context.reason = f"exception: {exc}"
                context.result = context.result or str(exc)
            except Exception:
                pass
            try:
                await self._parent_bus.publish(
                    SubagentFinishedEvent(
                        run_id=run_id,
                        parent_run_id=self._parent_run_id,
                        status="failed",
                        ts=_now(),
                    )
                )
            except Exception:
                pass
        finally:
            # 无论成功/失败/取消，都标记任务为已完成
            # mark_finished 清理注册表中的条目
            try:
                self._task_registry.mark_finished(run_id)
            except Exception:
                pass

    async def _run_background(
        self,
        loop: AgentLoop,
        context: ExecutionContext,
        bus: EventBus,
        run_path: Path,
        run_id: str,
    ) -> None:
        """后台执行的实际逻辑 —— 运行 Agent 循环并发布完成事件。

        与前台模式的区别：
        - 前台模式在 invoke() 内直接处理超时和事件发布
        - 后台模式把这些逻辑拆分到 _run_background_wrapped 中
        - 这种拆分使得 invoke() 的前台模式逻辑更简洁
        """
        # 用 EventWriter 记录所有事件到文件
        async with EventWriter(run_path / "events.jsonl") as writer:
            writer.subscribe(bus)
            await loop.run(context)  # 实际执行 Agent 循环
        # 发布完成事件（成功路径）
        await self._parent_bus.publish(
            SubagentFinishedEvent(
                run_id=run_id,
                parent_run_id=self._parent_run_id,
                status=context.status,
                ts=_now(),
            )
        )

    def _build_child_registry(
        self,
        child_bus: EventBus,
        child_run_id: str,
        profile: AgentProfile | None,
    ) -> ToolRegistry:
        """构建子 Agent 的工具注册表。

        核心设计：
        1. 基础工具集：ReadFile、Bash、WriteFile、ListDir（4 个核心文件操作）
        2. 任务工具集：TaskCreate/Update/List/Get（4 个任务管理）
        3. 嵌套工具集：仅在 depth < 1 时添加（防止无限嵌套）
        4. 权限过滤：如果 profile 有 allowed_tools 配置，只注册允许的工具

        参数:
            child_bus: 子 EventBus（嵌套子 Agent 需要引用）
            child_run_id: 子 run_id（任务管理器路径用）
            profile: Agent 角色配置（含 allowed_tools）

        返回:
            配置好的 ToolRegistry 实例
        """
        # 延迟导入 TaskManager 避免循环依赖
        from iwan_claude.core.task.manager import TaskManager

        # 计算允许的工具集合
        # 如果 profile 没有配置 allowed_tools，返回 None 表示全部允许
        allowed: set[str] | None = (
            set(profile.allowed_tools) if profile and profile.allowed_tools else None
        )

        # 过滤函数：检查工具是否在允许列表中
        def _allowed(name: str) -> bool:
            return allowed is None or name in allowed

        registry = ToolRegistry()

        # ── 第一组：基础文件操作工具 ──
        # 所有子 Agent 都默认拥有这些工具
        _all_tools = [
            ReadFileTool(),
            BashTool(),
            WriteFileTool(),
            ListDirTool(),
        ]
        for t in _all_tools:
            if _allowed(t.name):
                registry.register(t)

        # ── 第二组：任务管理工具 ──
        # 每个子 Agent 有独立的 TaskManager，存储在自己的 .tasks/ 目录
        child_task_manager = TaskManager(self._runs_dir / child_run_id / ".tasks")
        for t in [
            TaskCreateTool(child_task_manager),
            TaskUpdateTool(child_task_manager),
            TaskListTool(child_task_manager),
            TaskGetTool(child_task_manager),
        ]:
            if _allowed(t.name):
                registry.register(t)

        # ── 第三组：嵌套子 Agent 工具 ──
        # 仅在 depth < 1 时注册（即当前是第一层子 Agent）
        # 第二层及更深的子 Agent 不能再生成子 Agent
        if self._depth < 1:
            # 创建嵌套的 SpawnAgentTool —— depth + 1
            # 这样下一次调用 invoke() 时 depth 检查会生效
            nested = SpawnAgentTool(
                provider=self._provider,
                parent_bus=child_bus,       # 桥接到子 EventBus 而非父 EventBus
                parent_run_id=child_run_id,
                permission_manager=self._permission_manager,
                max_steps=self._max_steps,
                task_registry=self._task_registry,
                runs_dir=self._runs_dir,
                session_id=self._session_id,
                llm_model_name=self._llm_model_name,
                depth=self._depth + 1,       # 深度 +1
            )
            if _allowed("spawn_agent"):
                registry.register(nested)
            if _allowed("agent_result"):
                registry.register(AgentResultTool(self._task_registry))
            if _allowed("spawn_agents"):
                registry.register(
                    SpawnAgentsTool(
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
                )
            if _allowed("batch_result"):
                registry.register(BatchResultTool(self._task_registry))
            if _allowed("cancel_agent"):
                registry.register(CancelAgentTool(self._task_registry))

        return registry


# =============================================================================
# 二、AgentResultTool —— 查询后台子 Agent 结果
# =============================================================================


class AgentResultParams(BaseModel):
    """AgentResultTool 的参数校验模型。

    极简参数：只需要 run_id
    """
    run_id: str


class AgentResultTool(BaseTool):
    """查询后台子 Agent 的执行结果。

    使用场景：
    - spawn_agent(run_in_background=true) 返回 run_id 后
    - 主 Agent 可以稍后调用 agent_result 查询执行结果

    返回值语义：
    - "still running" —— 子 Agent 还在执行
    - "Subagent was cancelled." —— 被取消
    - "Subagent raised an exception: ..." —— 执行异常
    - 实际结果文本 —— 执行成功
    """

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

    def __init__(self, task_registry: BackgroundTaskRegistry) -> None:
        """初始化。

        参数:
            task_registry: 所有后台任务的注册表（共享同一个实例）
        """
        self._task_registry = task_registry

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """查询子 Agent 结果。

        查询逻辑：
        1. 从注册表获取 (Task, ExecutionContext) 元组
        2. 检查 Task 状态：done → cancelled → exception → success
        3. 从 context.result 获取最终结果
        """
        p = AgentResultParams.model_validate(params)
        entry = self._task_registry.get(p.run_id)
        if entry is None:
            return ToolResult(
                content=f"Unknown run_id: {p.run_id}. Only background subagents can be queried.",
                is_error=True,
                error_type="runtime_error",
            )
        task, context = entry

        # 检查 Task 是否已完成
        # asyncio.Task 的状态检查方法：
        # - done(): Task 是否已结束（完成/失败/取消）
        # - cancelled(): Task 是否被取消
        # - exception(): Task 的异常（如果已完成且有异常）
        if not task.done():
            return ToolResult(content="still running")
        if task.cancelled():
            return ToolResult(
                content="Subagent was cancelled.", is_error=True, error_type="runtime_error"
            )
        exc = task.exception()
        if exc is not None:
            return ToolResult(
                content=f"Subagent raised an exception: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        # 成功：返回 context.result
        return ToolResult(content=context.result or "Subagent completed with no text result.")


# =============================================================================
# 三、SpawnAgentsTool —— 批量并行生成子 Agent
# =============================================================================


class SpawnAgentTask(BaseModel):
    """单个子任务的参数模型（用于 SpawnAgentsTool 的 tasks 列表）。

    与 SpawnAgentParams 的区别：
    - 没有 run_in_background 参数（批量模式下默认都是后台运行）
    - 没有 batch_id（由 SpawnAgentsTool 统一分配）
    - 字段精简，只保留批量场景需要的
    """
    model_config = ConfigDict(extra="ignore")
    description: str
    prompt: str
    subagent_type: str = ""
    timeout_sec: float = Field(default=0.0, ge=0.0)


class SpawnAgentsParams(BaseModel):
    """SpawnAgentsTool 的参数校验模型。

    参数说明：
    - tasks: 子任务列表（至少 1 个）
    - max_concurrency: 最大并发数（1-50，默认 3）
      → 使用 asyncio.Semaphore 控制
      → 为什么限制在 1-50？防止一次性开太多 LLM 调用导致 429 限流
    - wait: 是否阻塞等待所有任务完成
      → True: 返回完整执行结果
      → False: 立即返回 batch_id，后续用 batch_result 查询
    - wait_timeout_sec: wait=true 时的总超时（0 表示不限）
    - batch_description: 批次描述，用于 UI 展示
    """
    model_config = ConfigDict(extra="ignore")
    tasks: list[SpawnAgentTask] = Field(min_length=1)
    max_concurrency: int = Field(default=3, ge=1, le=50)
    wait: bool = True
    wait_timeout_sec: float = Field(default=0.0, ge=0.0)
    batch_description: str = ""


class SpawnAgentsTool(BaseTool):
    """批量并行生成多个子 Agent。

    核心特性：
    1. 并发控制：使用 asyncio.Semaphore 限制同时运行的子 Agent 数量
    2. 两种等待模式：wait=true 阻塞等待 / wait=false 立即返回
    3. 优雅取消：超时或某个任务失败时，取消同批次的其他任务
    4. 结果聚合：按原始顺序返回每个子任务的结果

    并发控制的实现原理：
    - Semaphore 控制的不是"同时 spawn 的数量"，而是"同时运行中的数量"
    - 每个 _run_one 协程获取信号量后，会等待子 Agent 完全执行完才释放
    - 这样 max_concurrency=3 就真的保证同时最多 3 个子 Agent 在运行
    """

    name = "spawn_agents"
    description = (
        "Spawn MULTIPLE isolated sub-agents in parallel to handle a batch of independent tasks. "
        "max_concurrency limits how many sub-agents run at once (prevents rate-limit 429). "
        "wait=true blocks until all complete and returns aggregated results; "
        "wait=false returns immediately with a batch_id; use batch_result to poll later."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "prompt": {"type": "string"},
                        "subagent_type": {"type": "string", "default": ""},
                        "timeout_sec": {"type": "number", "default": 0},
                    },
                    "required": ["description", "prompt"],
                },
                "minItems": 1,
                "description": "List of independent sub-agent tasks.",
            },
            "max_concurrency": {
                "type": "integer",
                "default": 3,
                "minimum": 1,
                "maximum": 50,
                "description": "Max sub-agents running simultaneously (semaphore limit).",
            },
            "wait": {
                "type": "boolean",
                "default": True,
                "description": "True=block until all done & return summary; False=return batch_id immediately.",
            },
            "wait_timeout_sec": {
                "type": "number",
                "default": 0,
                "description": "Total wait timeout when wait=true; 0=unlimited or per-task timeout applies.",
            },
            "batch_description": {
                "type": "string",
                "default": "",
                "description": "Short label shown in progress UI for the whole batch.",
            },
        },
        "required": ["tasks"],
    }
    params_model = SpawnAgentsParams

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
        """初始化。

        参数与 SpawnAgentTool 类似，但 task_registry 和 runs_dir 是必填的
        （批量模式下必须有注册表来管理多个任务）
        """
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

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """批量生成子 Agent 的核心方法。

        完整流程：
        1. 参数校验 + 深度检查
        2. 创建 batch_id 和 SpawnAgentTool 实例（复用单个生成逻辑）
        3. 根据 wait 参数选择后台模式或等待模式
        4. 后台模式：立即生成所有子 Agent，返回 batch_id
        5. 等待模式：用 Semaphore 控制并发，收集所有结果
        """
        p = SpawnAgentsParams.model_validate(params)

        # 深度检查（与 SpawnAgentTool 相同逻辑）
        if self._depth >= 2:
            return ToolResult(
                content="Subagent nesting limit (2) reached; cannot spawn further subagents.",
                is_error=True,
                error_type="runtime_error",
            )
        if not p.tasks:
            return ToolResult(
                content="spawn_agents: tasks must be non-empty",
                is_error=True,
                error_type="schema_error",
            )

        # 创建 batch_id 和复用的 SpawnAgentTool
        # 每个子任务都会通过 per_task_tool.invoke() 来生成
        batch_id = _new_batch_id()
        per_task_tool = SpawnAgentTool(
            provider=self._provider,
            parent_bus=self._parent_bus,
            parent_run_id=self._parent_run_id,
            permission_manager=self._permission_manager,
            max_steps=self._max_steps,
            task_registry=self._task_registry,
            runs_dir=self._runs_dir,
            session_id=self._session_id,
            llm_model_name=self._llm_model_name,
            depth=self._depth,
            batch_id=batch_id,  # 标记所有子 Agent 属于同一批次
        )

        run_ids: list[str] = []
        start_failed = False
        failure_msg = ""

        # ── 分支 1：wait=false 后台批量模式 ──
        # 每个子任务都立即 spawn（不等待完成）
        if not p.wait:
            for task in p.tasks:
                sub_params = {
                    "description": task.description,
                    "prompt": task.prompt,
                    "run_in_background": True,
                    "subagent_type": task.subagent_type,
                    "timeout_sec": task.timeout_sec,
                }
                res = await per_task_tool.invoke(sub_params)
                if res.is_error:
                    start_failed = True
                    failure_msg = res.content
                    break
                # 从返回文本中解析 run_id
                # 返回格式: "Subagent started in background. run_id=abc123. Use agent_result..."
                marker = "run_id="
                start = res.content.find(marker)
                if start == -1:
                    start_failed = True
                    failure_msg = f"unexpected background response: {res.content}"
                    break
                tail = res.content[start + len(marker) :]
                rid = tail.split()[0].rstrip(".")
                run_ids.append(rid)

            # 如果有任务启动失败，取消已启动的任务
            if start_failed:
                for started in run_ids:
                    self._task_registry.cancel(started, reason="sibling failed to start")
                return ToolResult(
                    content=f"spawn_agents: one task failed to start: {failure_msg}",
                    is_error=True,
                    error_type="runtime_error",
                )

            # 注册批次信息，后续可通过 batch_id 查询
            self._task_registry.register_batch(
                batch_id, run_ids, description=p.batch_description or f"batch of {len(run_ids)}"
            )
            return ToolResult(
                content=(
                    f"Spawned {len(run_ids)} sub-agents in background batch. batch_id={batch_id}. "
                    f"Use batch_result(batch_id='{batch_id}') to poll / wait for completion."
                )
            )

        # ── 分支 2：wait=true 等待模式 ──
        # 使用 Semaphore 控制并发，用 gather 收集结果
        sem = asyncio.Semaphore(p.max_concurrency)
        cancelled_any: dict[str, bool] = {"v": False}  # 用 dict 以便内部函数修改

        async def _run_one(i: int, task: SpawnAgentTask) -> tuple[int, str | None]:
            """单个子任务的执行协程。

            关键设计：
            1. 获取信号量 → spawn 子 Agent → 等待完成 → 释放信号量
               → 这保证了 max_concurrency 限制的是"运行中"而非"启动中"
            2. 返回 (索引, run_id) 元组，用于后续按原始顺序排序

            参数:
                i: 任务在列表中的索引（用于结果排序）
                task: 子任务参数

            返回:
                (index, run_id) 或 (index, None) 表示失败
            """
            async with sem:
                # 检查是否有其他任务失败，提前退出
                if cancelled_any["v"]:
                    return (i, None)

                # 构造参数并调用 SpawnAgentTool（后台模式）
                sub_params = {
                    "description": task.description,
                    "prompt": task.prompt,
                    "run_in_background": True,
                    "subagent_type": task.subagent_type,
                    "timeout_sec": task.timeout_sec,
                }
                res = await per_task_tool.invoke(sub_params)
                if res.is_error:
                    return (i, None)

                # 解析 run_id
                marker = "run_id="
                pos = res.content.find(marker)
                if pos == -1:
                    return (i, None)
                tail = res.content[pos + len(marker) :]
                rid = tail.split()[0].rstrip(".")

                # 关键：等待子 Agent 实际完成（此时仍持有信号量）
                # 这保证了并发控制是针对"运行中"而非"启动中"
                entry = self._task_registry.get(rid)
                if entry is not None:
                    t, _ctx = entry
                    try:
                        await t  # 等待 Task 完成
                    except BaseException:
                        pass
                return (i, rid)

        # 构造所有 worker 协程
        worker_coros = [_run_one(i, t) for i, t in enumerate(p.tasks)]

        try:
            # asyncio.gather 并发执行所有 worker
            # return_exceptions=True: 不抛出异常，而是把异常作为结果返回
            if p.wait_timeout_sec > 0:
                async with asyncio.timeout(p.wait_timeout_sec):
                    outcomes = await asyncio.gather(*worker_coros, return_exceptions=True)
            else:
                outcomes = await asyncio.gather(*worker_coros, return_exceptions=True)
        except TimeoutError:
            # 批次超时：取消所有已注册的子 Agent
            cancelled_any["v"] = True
            for rid in run_ids:
                self._task_registry.cancel(rid, reason="batch wait timeout")
            # 额外扫描注册表中属于本批次的任务
            for already_rid in list(self._task_registry._tasks.keys()):
                meta = self._task_registry._task_meta.get(already_rid, {})
                if meta.get("batch_id") == batch_id:
                    self._task_registry.cancel(already_rid, reason="batch wait timeout")
            return ToolResult(
                content=(
                    f"spawn_agents: batch_id={batch_id} timed out after {p.wait_timeout_sec}s; "
                    f"running tasks cancelled. Use batch_result(batch_id='{batch_id}') for partial snapshot."
                ),
                is_error=True,
                error_type="runtime_error",
            )

        # 处理结果：按原始顺序排序
        ordered: list[tuple[int, str]] = []
        any_failed_start = False
        for out in outcomes:
            if isinstance(out, BaseException) or not isinstance(out, tuple):
                any_failed_start = True
                continue
            idx, rid = out
            if rid is None:
                any_failed_start = True
                continue
            ordered.append((idx, rid))

        # 按索引排序，恢复原始顺序
        ordered.sort(key=lambda x: x[0])
        run_ids = [rid for _idx, rid in ordered]

        # 如果有任务启动失败，取消所有已启动的任务
        if any_failed_start or len(run_ids) != len(p.tasks):
            for started in run_ids:
                self._task_registry.cancel(started, reason="sibling failed to start")
            return ToolResult(
                content=(
                    f"spawn_agents: one or more tasks failed to start; "
                    f"succeeded={len(run_ids)}/{len(p.tasks)}."
                ),
                is_error=True,
                error_type="runtime_error",
            )

        # 全部成功：注册批次并返回状态
        self._task_registry.register_batch(
            batch_id, run_ids, description=p.batch_description or f"batch of {len(run_ids)}"
        )
        status = self._task_registry.batch_status(batch_id) or _empty_status(batch_id, run_ids)
        return ToolResult(content=format_batch_status(status, include_results=True))


# ── 辅助函数 ──

def _empty_status(batch_id: str, run_ids: list[str]) -> BatchStatus:
    """创建空的批次状态对象。

    设计原因：当注册表中找不到批次信息时，用此函数构造一个默认状态
    避免在主逻辑中到处做 None 检查
    """
    return BatchStatus(
        batch_id=batch_id,
        total=len(run_ids),
        running=0,
        completed=0,
        success=0,
        failed=0,
        cancelled=0,
        duration_sec=0.0,
        results=[
            {
                "run_id": rid,
                "description": "",
                "status": "unknown",
                "result": None,
                "elapsed_sec": 0.0,
            }
            for rid in run_ids
        ],
    )


def format_batch_status(status: BatchStatus, *, include_results: bool = True) -> str:
    """格式化批次状态为可读字符串。

    输出格式：
    spawn_agents batch_id=b_xxx total=3 running=0 success=2 failed=1 cancelled=0 duration_sec=12.5
    - [success] run_id=abc elapsed=4.2s desc='分析日志' result='日志分析完成...'
    - [failed] run_id=def elapsed=8.3s desc='生成报告' result='超时...'

    参数:
        status: 批次状态对象
        include_results: 是否包含每个任务的详细结果
    """
    # 构建头部摘要
    head = (
        f"spawn_agents batch_id={status.batch_id} "
        f"total={status.total} running={status.running} "
        f"success={status.success} failed={status.failed} "
        f"cancelled={status.cancelled} duration_sec={status.duration_sec}"
    )
    if not include_results:
        return head

    # 构建每个任务的详细结果
    lines = [head, ""]
    for r in status.results:
        snippet = ""
        if r.get("result"):
            text = str(r["result"])
            snippet = text[:200].replace("\n", "\\n")  # 截断到 200 字符
            if len(text) > 200:
                snippet += "…"
        lines.append(
            f"- [{r.get('status','?')}] run_id={r.get('run_id')} "
            f"elapsed={r.get('elapsed_sec', 0)}s "
            f"desc={r.get('description','')!r} result={snippet!r}"
        )
    return "\n".join(lines)


# =============================================================================
# 四、BatchResultTool —— 查询批量任务状态
# =============================================================================


class BatchResultParams(BaseModel):
    """BatchResultTool 的参数校验模型。

    参数说明：
    - batch_id: 批次 ID（必填）
    - wait: 是否阻塞等待批次完成
    - timeout_sec: 等待超时（0 不限）
    - poll_interval_sec: 轮询间隔（0.05-10 秒，默认 0.2）
      → 为什么需要轮询？因为 asyncio 没有原生的"事件通知"机制
      → 替代方案：定期检查状态是否变化
    """
    model_config = ConfigDict(extra="ignore")
    batch_id: str
    wait: bool = False
    timeout_sec: float = Field(default=0.0, ge=0.0)
    poll_interval_sec: float = Field(default=0.2, ge=0.05, le=10.0)


class BatchResultTool(BaseTool):
    """查询批量任务的状态。

    两种使用模式：
    1. 快照模式（wait=false）：立即返回当前状态
    2. 等待模式（wait=true）：轮询直到所有任务完成或超时

    轮询实现：
    while not all_done:
        await asyncio.sleep(interval)
        check_status()
    """

    name = "batch_result"
    description = (
        "Retrieve current status of a batch started by spawn_agents(wait=false). "
        "wait=false returns an immediate snapshot; wait=true blocks until all tasks "
        "complete/fail/cancel or the timeout elapses."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "batch_id": {"type": "string", "description": "Batch id returned by spawn_agents."},
            "wait": {
                "type": "boolean",
                "default": False,
                "description": "True=block until batch terminal; False=snapshot now.",
            },
            "timeout_sec": {
                "type": "number",
                "default": 0,
                "description": "Wait timeout when wait=true. 0=unlimited.",
            },
            "poll_interval_sec": {
                "type": "number",
                "default": 0.2,
                "description": "Polling interval when wait=true (0.05 ~ 10s).",
            },
        },
        "required": ["batch_id"],
    }
    params_model = BatchResultParams

    def __init__(self, task_registry: BackgroundTaskRegistry) -> None:
        self._task_registry = task_registry

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """查询批次状态。

        逻辑分支：
        1. wait=false: 立即返回当前快照
        2. wait=true: 轮询等待所有任务完成
           → 判定标准：running == 0（没有正在运行的任务）
        """
        p = BatchResultParams.model_validate(params)

        status = self._task_registry.batch_status(p.batch_id)
        if status is None:
            return ToolResult(
                content=f"Unknown batch_id: {p.batch_id}",
                is_error=True,
                error_type="runtime_error",
            )

        # 快照模式
        if not p.wait:
            return ToolResult(content=format_batch_status(status, include_results=True))

        # 等待模式：轮询直到所有任务完成
        def _is_terminal(st: BatchStatus) -> bool:
            """判断批次是否终止（没有正在运行的任务）。"""
            return st.running == 0

        try:
            if p.timeout_sec > 0:
                async with asyncio.timeout(p.timeout_sec):
                    while not _is_terminal(status):
                        await asyncio.sleep(p.poll_interval_sec)
                        self._task_registry.prune()  # 清理已完成的任务
                        next_status = self._task_registry.batch_status(p.batch_id)
                        status = next_status if next_status is not None else status
            else:
                # 无超时版本：逻辑相同但没有外层的 timeout
                while not _is_terminal(status):
                    await asyncio.sleep(p.poll_interval_sec)
                    self._task_registry.prune()
                    next_status = self._task_registry.batch_status(p.batch_id)
                    status = next_status if next_status is not None else status
        except TimeoutError:
            return ToolResult(
                content=(
                    f"batch_result: batch_id={p.batch_id} still running after "
                    f"{p.timeout_sec}s; latest={format_batch_status(status, include_results=False)}"
                ),
                is_error=True,
                error_type="runtime_error",
            )

        return ToolResult(content=format_batch_status(status, include_results=True))


# =============================================================================
# 五、CancelAgentTool —— 取消子 Agent
# =============================================================================


class CancelAgentParams(BaseModel):
    """CancelAgentTool 的参数校验模型。

    设计约束：run_id 和 batch_id 必须提供且只能提供一个
    （通过 invoke 中的 bool 异或检查实现）
    """
    model_config = ConfigDict(extra="ignore")
    run_id: str | None = None
    batch_id: str | None = None
    reason: str = "user cancelled"


class CancelAgentTool(BaseTool):
    """取消正在运行的子 Agent 或整个批次。

    使用场景：
    - 主 Agent 决定放弃某个子任务
    - 用户手动取消批量任务

    参数约束：
    - run_id 和 batch_id 二选一（不能同时提供也不能都不提供）
    """

    name = "cancel_agent"
    description = (
        "Cancel a running background sub-agent (run_id) or an entire batch (batch_id). "
        "Exactly one of run_id or batch_id must be provided. "
        "Cancelled tasks return 'cancelled' status from agent_result / batch_result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "Cancel a single background sub-agent by run_id.",
            },
            "batch_id": {
                "type": "string",
                "description": "Cancel all tasks belonging to batch_id.",
            },
            "reason": {
                "type": "string",
                "default": "user cancelled",
                "description": "Short human-readable reason stored with cancelled tasks.",
            },
        },
    }
    params_model = CancelAgentParams

    def __init__(self, task_registry: BackgroundTaskRegistry) -> None:
        self._task_registry = task_registry

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """取消子 Agent。

        逻辑：
        1. 校验：run_id 和 batch_id 必须且只能提供一个
           → 使用 bool(x) 异或：bool(a) ^ bool(b) 为 True 说明只有一个为真
        2. 如果提供了 batch_id，调用 cancel_batch 批量取消
        3. 如果提供了 run_id，调用 cancel 单个取消
        """
        p = CancelAgentParams.model_validate(params)

        # 异或检查：恰好一个为 True
        if bool(p.run_id) == bool(p.batch_id):
            return ToolResult(
                content="cancel_agent: provide exactly one of run_id or batch_id",
                is_error=True,
                error_type="schema_error",
            )

        if p.batch_id:
            # 批量取消
            n = self._task_registry.cancel_batch(p.batch_id, reason=p.reason)
            return ToolResult(
                content=f"cancel_agent: cancelled {n} task(s) in batch_id={p.batch_id}"
            )
        else:
            # 单个取消
            assert p.run_id is not None  # 类型断言（经过异或检查后 p.run_id 必然非 None）
            ok = self._task_registry.cancel(p.run_id, reason=p.reason)
            if not ok:
                return ToolResult(
                    content=(
                        f"cancel_agent: run_id={p.run_id} not found or already completed. "
                        "Use agent_result to check status."
                    ),
                    is_error=True,
                    error_type="runtime_error",
                )
            return ToolResult(content=f"cancel_agent: cancelled run_id={p.run_id}")
