"""
核心应用模块 - 整个 iwan_claude 系统的主入口

【学习要点】
1. 架构设计：核心应用采用分层架构，包含配置层、事件层、会话层、权限层等
2. 异步编程：使用 asyncio 实现异步 I/O 和并发处理
3. 事件驱动：通过 EventBus 实现模块间的解耦通信
4. 服务端设计：基于 TCP Socket 的 RPC 服务端
5. 生命周期管理：完整的启动、运行、关闭流程

【核心组件】
- CoreApp：核心应用类，管理所有子系统的生命周期
- EventBus：事件总线，实现发布/订阅模式
- SessionManager：会话管理器，管理用户会话
- PermissionManager：权限管理器，控制工具调用权限
- SocketServer：TCP Socket 服务端，处理客户端请求
- IpcEventBroadcaster：IPC 事件广播器，向客户端推送事件

【RPC 命令列表】
- core.ping：检查服务状态
- agent.run：启动一次 agent 任务
- event.subscribe：订阅事件
- session.create：创建会话
- session.send_message：发送消息
- session.get_history：获取会话历史
- session.close：关闭会话
- permission.respond：响应权限请求
- session.compact：压缩会话
- session.checkpoint.list：列出检查点
- session.checkpoint.restore：恢复检查点
"""
from __future__ import annotations

# asyncio：异步 I/O 框架
# datetime：日期时间处理
# fnmatch：文件名匹配（用于事件过滤）
# json：JSON 序列化/反序列化
# logging：日志记录
# os：操作系统相关功能
# signal：信号处理（用于优雅关闭）
# sys：系统相关操作
# time：时间相关功能
# pathlib：路径操作
# typing：类型提示
import asyncio
import datetime
import fnmatch
import json
import logging
import os
import signal
import sys
import time
from datetime import UTC
from pathlib import Path
from typing import Any

# Windows 标记：asyncio.ProactorEventLoop（Windows 默认）不支持 loop.add_signal_handler
# 在 Windows 上需要使用不同的信号处理方式
IS_WINDOWS = sys.platform.startswith("win")

# Pydantic：数据验证和序列化库
from pydantic import BaseModel

# 导入包版本信息
import iwan_claude

# 导入 RPC 命令和响应的数据模型
from iwan_claude.core.bus.commands import (
    AgentRunCommand,                # Agent 运行命令
    AgentRunResult,                 # Agent 运行结果
    CheckpointInfo,                 # 检查点信息
    EventSubscribeCommand,          # 事件订阅命令
    EventSubscribeResult,           # 事件订阅结果
    PermissionRespondCommand,       # 权限响应命令
    PermissionRespondResult,        # 权限响应结果
    PongResult,                     # Ping 响应
    SessionCheckpointListCommand,   # 检查点列表命令
    SessionCheckpointListResult,    # 检查点列表结果
    SessionCheckpointRestoreCommand, # 检查点恢复命令
    SessionCheckpointRestoreResult,  # 检查点恢复结果
    SessionCloseCommand,            # 会话关闭命令
    SessionCloseResult,             # 会话关闭结果
    SessionCompactCommand,          # 会话压缩命令
    SessionCompactResult,           # 会话压缩结果
    SessionCreateCommand,           # 会话创建命令
    SessionCreateResult,            # 会话创建结果
    SessionGetHistoryCommand,       # 获取历史命令
    SessionGetHistoryResult,        # 获取历史结果
    SessionSendMessageCommand,      # 发送消息命令
    SessionSendMessageResult,       # 发送消息结果
    SessionSetAutoModeCommand,      # 设置自动模式命令
    SessionSetAutoModeResult,       # 设置自动模式结果
    SessionSetEffortLevelCommand,   # 设置努力等级命令
    SessionSetEffortLevelResult,    # 设置努力等级结果
    SessionSetModelCommand,         # 设置模型预设命令
    SessionSetModelResult,          # 设置模型预设结果
    SessionSetEngineCommand,        # 设置引擎命令
    SessionSetEngineResult,         # 设置引擎结果
    SessionListCommand,             # 会话列表命令
    SessionListResult,              # 会话列表结果
    SessionInfo,                    # 会话信息
    SessionRenameCommand,           # 重命名会话命令
    SessionRenameResult,            # 重命名会话结果
)
from iwan_claude.core.bus.envelope import EventPushEnvelope  # 事件推送封装

# 导入核心组件
from iwan_claude.core.config import IwanConfig, get_config   # 配置
from iwan_claude.core.events.bus import EventBus             # 事件总线
from iwan_claude.core.llm import create_provider_from_config # LLM 提供者创建
from iwan_claude.core.logging_setup import setup_logging     # 日志初始化
from iwan_claude.core.mcp.server import McpServerManager     # MCP 服务器管理
from iwan_claude.core.permissions.manager import PermissionManager  # 权限管理
from iwan_claude.core.permissions.storage import load_policy_file   # 加载权限策略
from iwan_claude.core.runner import AgentRunner              # Agent 运行器
from iwan_claude.core.runs import events_file, new_run_id    # 运行相关工具
from iwan_claude.core.session import SessionManager, SessionStore  # 会话管理
from iwan_claude.core.trace.record import TraceRecord        # 跟踪记录
from iwan_claude.core.trace.writer import TraceWriter        # 跟踪写入器
from iwan_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster  # IPC 广播器
from iwan_claude.core.transport.socket_server import SocketServer, get_connection_writer  # Socket 服务端

# 获取日志记录器
logger = logging.getLogger(__name__)


# 获取当前时间的 ISO 格式字符串
def _now() -> str:
    """获取当前 UTC 时间的 ISO 8601 格式字符串
    
    返回示例："2024-01-15T15:30:45.123456"
    
    返回：
        str: ISO 格式的时间字符串
    """
    return datetime.datetime.now(UTC).isoformat()


class CoreApp:
    """
    核心应用类 - 管理整个 iwan_claude 系统的生命周期
    
    核心职责：
    1. 初始化所有子系统（配置、日志、事件总线、权限管理等）
    2. 注册 RPC 命令处理器
    3. 启动 TCP Socket 服务端
    4. 处理客户端请求
    5. 管理服务关闭流程
    
    属性说明：
    - _start_time: 服务启动时间（用于计算运行时长）
    - _bus: 事件总线，用于模块间通信
    - _broadcaster: IPC 事件广播器，向客户端推送事件
    - _trace: 跟踪写入器，记录系统运行日志
    - _config: 配置对象
    - _running_runs: 正在运行的任务集合
    - _sessions: 会话管理器
    - _permission_manager: 权限管理器
    - _mcp_manager: MCP 服务器管理器
    - _checkpointer: LangGraph 检查点存储
    - _checkpointer_ctx: 检查点上下文（用于资源清理）
    """
    
    def __init__(self) -> None:
        """初始化核心应用实例"""
        # 服务启动时间（使用 monotonic 时间，不受系统时间调整影响）
        self._start_time = time.monotonic()
        # 事件总线：实现发布/订阅模式，解耦模块间通信
        self._bus = EventBus()
        # IPC 事件广播器：向连接的客户端推送事件
        self._broadcaster: IpcEventBroadcaster | None = None
        # 跟踪写入器：记录系统运行日志
        self._trace: TraceWriter | None = None
        # 配置对象：存储系统配置
        self._config: IwanConfig | None = None
        # 正在运行的任务集合：用于关闭时取消所有任务
        self._running_runs: set[asyncio.Task[Any]] = set()
        # 会话管理器：管理用户会话
        self._sessions: SessionManager | None = None
        # 权限管理器：控制工具调用权限
        self._permission_manager: PermissionManager | None = None
        # MCP 服务器管理器：管理外部 MCP 工具服务器
        self._mcp_manager: McpServerManager | None = None
        # LangGraph 检查点存储：支持状态持久化和回溯
        self._checkpointer: Any | None = None
        # 检查点上下文：用于异步资源的正确关闭
        self._checkpointer_ctx: Any | None = None
        # 跨会话记忆管理器：LongTermMemory + VectorMemory（start 时初始化）
        self._memory: Any | None = None

    # 初始化 LangGraph Checkpointer
    async def _init_checkpointer(self) -> None:
        """
        初始化 LangGraph Checkpointer
        
        根据配置选择不同的存储后端：
        - none: 不使用检查点
        - memory: 内存存储（临时存储，重启后丢失）
        - sqlite: SQLite 持久化存储
        
        注意：SQLite Checkpointer 需要异步上下文管理器来正确初始化和关闭
        """
        assert self._config is not None
        backend = self._config.agent.checkpoint_backend
        
        # 不使用检查点
        if backend == "none":
            self._checkpointer = None
            return
        
        # 内存存储：适合测试和临时使用
        elif backend == "memory":
            from langgraph.checkpoint.memory import InMemorySaver
            self._checkpointer = InMemorySaver()
            logger.info("checkpointer: using memory backend")
        
        # SQLite 持久化存储：适合生产环境
        elif backend == "sqlite":
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            db_path = Path(self._config.agent.checkpoint_db_path)
            # 确保目录存在
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn_str = str(db_path.resolve())
            
            # 创建异步上下文管理器
            # AsyncSqliteSaver.from_conn_string() 返回一个异步上下文管理器
            # 需要使用 await ctx.__aenter__() 来获取实际的 saver 对象
            ctx = AsyncSqliteSaver.from_conn_string(conn_str)
            saver = await ctx.__aenter__()
            
            # 保存上下文和 saver 对象
            self._checkpointer_ctx = ctx
            self._checkpointer = saver
            logger.info("checkpointer: using sqlite backend at %s", conn_str)
        
        # 未知后端
        else:
            logger.warning("Unknown checkpoint_backend=%r, using none", backend)
            self._checkpointer = None

    # 处理 core.ping 请求，返回服务版本、运行时长和接收时间
    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        """
        处理 core.ping RPC 请求
        
        参数：
            params: 请求参数，包含 client 字段（客户端标识）
        
        返回：
            PongResult: 包含服务器版本、运行时长、接收时间
        """
        client = params.get("client", "unknown")
        logger.debug("ping from %s", client)
        
        # 计算运行时长（毫秒）
        uptime_ms = int((time.monotonic() - self._start_time) * 1000)
        
        return PongResult(
            server_version=iwan_claude.__version__,  # 服务器版本
            uptime_ms=uptime_ms,                     # 运行时长（毫秒）
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),  # 接收时间
        )

    # 将 EventBus 事件写入 trace（作为 EventBus 订阅者）
    async def _trace_event_handler(self, event: BaseModel) -> None:
        """
        事件跟踪处理器 - 将所有事件写入 trace 文件
        
        作为 EventBus 的订阅者，每当有事件发布时，此方法会被调用。
        
        参数：
            event: 事件对象（Pydantic BaseModel）
        """
        assert self._trace is not None
        
        # 将事件对象转换为字典
        event_dict = event.model_dump()
        
        # 创建 TraceRecord 并写入 trace 文件
        self._trace.emit(
            TraceRecord(
                ts=_now(),                          # 时间戳
                direction="CORE",                   # 方向：核心服务内部
                layer="event",                      # 层：事件层
                kind="event",                       # 类型：事件
                run_id=event_dict.get("run_id"),    # 关联的 run ID
                data=event_dict,                    # 事件数据
            )
        )

    # 启动一次 agent run：异步创建 AgentRunner 并立即返回 run_id
    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        """
        处理 agent.run RPC 请求 - 启动一次 agent 任务
        
        工作流程：
        1. 创建一个 one_shot 模式的会话
        2. 生成唯一的 run ID
        3. 异步发送消息并执行任务
        4. 立即返回 run_id（不等待任务完成）
        
        参数：
            params: 请求参数，包含 goal 字段（任务目标）
        
        返回：
            AgentRunResult: 包含 run_id
        """
        assert self._sessions is not None
        
        # 验证请求参数
        cmd = AgentRunCommand.model_validate(params)
        
        # 创建 one_shot 模式的会话（一次性任务，完成后自动关闭）
        session = await self._sessions.create(mode="one_shot", title=cmd.goal[:40])
        
        # 生成唯一的 run ID
        run_id = new_run_id()
        
        # 异步发送消息并执行任务
        # 使用 asyncio.create_task() 创建后台任务，不阻塞当前协程
        run_task = asyncio.create_task(
            self._sessions.send_message(session.id, cmd.goal, run_id=run_id)
        )
        
        # 将任务添加到运行中的任务集合
        self._running_runs.add(run_task)
        
        # 任务完成后自动从集合中移除
        run_task.add_done_callback(self._running_runs.discard)
        
        # 立即返回 run_id，不等待任务完成
        return AgentRunResult(run_id=run_id)

    # 创建 chat 或 one_shot session，并返回 session_id
    async def _session_create_handler(self, params: dict[str, Any]) -> SessionCreateResult:
        """
        处理 session.create RPC 请求 - 创建会话
        
        参数：
            params: 请求参数，包含 mode 和 title 字段
        
        返回：
            SessionCreateResult: 包含 session_id 和 status
        """
        assert self._sessions is not None
        
        # 验证请求参数
        cmd = SessionCreateCommand.model_validate(params)
        
        # 创建会话
        session = await self._sessions.create(
            mode=cmd.mode,
            title=cmd.title,
            cwd=cmd.cwd,  # 传递会话绑定的工作目录（沙箱根）
        )

        # 返回会话 ID、状态和当前配置
        auto_mode = self._permission_manager.get_auto_mode() if self._permission_manager is not None else "off"
        effort_level = self._permission_manager.get_effort_level() if self._permission_manager is not None else "medium"
        model_preset = self._permission_manager.get_model_preset() if self._permission_manager is not None else "balanced"
        return SessionCreateResult(session_id=session.id, status=session.status, auto_mode=auto_mode, effort_level=effort_level, model_preset=model_preset)

    # 向 session 发送一条用户消息并同步等待对应 run 完成
    async def _session_send_handler(self, params: dict[str, Any]) -> SessionSendMessageResult:
        """
        处理 session.send_message RPC 请求 - 发送消息到会话
        
        参数：
            params: 请求参数，包含 session_id 和 content 字段
        
        返回：
            SessionSendMessageResult: 包含 run_id
        """
        assert self._sessions is not None
        
        # 验证请求参数
        cmd = SessionSendMessageCommand.model_validate(params)
        
        # 发送消息并等待任务完成
        run_id = await self._sessions.send_message(cmd.session_id, cmd.content)
        
        # 返回 run_id
        return SessionSendMessageResult(run_id=run_id)

    # 返回 session 的完整 Anthropic messages 历史
    async def _session_history_handler(self, params: dict[str, Any]) -> SessionGetHistoryResult:
        """
        处理 session.get_history RPC 请求 - 获取会话历史
        
        参数：
            params: 请求参数，包含 session_id 字段
        
        返回：
            SessionGetHistoryResult: 包含完整的消息历史
        """
        assert self._sessions is not None
        
        # 验证请求参数
        cmd = SessionGetHistoryCommand.model_validate(params)
        
        # 获取会话历史消息
        messages = await self._sessions.get_history(cmd.session_id)
        
        # 返回消息历史
        return SessionGetHistoryResult(messages=messages)

    # 接收客户端权限审批响应，resolve 对应挂起的 Future
    async def _permission_respond_handler(self, params: dict[str, Any]) -> PermissionRespondResult:
        """
        处理 permission.respond RPC 请求 - 响应权限审批
        
        当用户在客户端审批工具调用权限后，客户端发送此命令。
        此方法会调用 PermissionManager 的 respond 方法，
        唤醒等待权限审批的协程。
        
        参数：
            params: 请求参数，包含 tool_use_id 和 decision 字段
        
        返回：
            PermissionRespondResult: 空结果
        """
        # 验证请求参数
        cmd = PermissionRespondCommand.model_validate(params)
        
        logger.info(
            "permission.respond received tool_use_id=%s decision=%s",
            cmd.tool_use_id, cmd.decision,
        )
        
        # 检查权限管理器是否已初始化
        if self._permission_manager is None:
            logger.error("permission.respond: PermissionManager not initialized")
            return PermissionRespondResult()
        
        # 响应权限请求：唤醒等待的协程
        self._permission_manager.respond(cmd.tool_use_id, cmd.decision)
        
        return PermissionRespondResult()

    # 手动压缩 session thread，将摘要持久化写入 thread.jsonl
    async def _session_compact_handler(self, params: dict[str, Any]) -> SessionCompactResult:
        """
        处理 session.compact RPC 请求 - 压缩会话
        
        当会话历史过长时，调用此方法可以：
        1. 使用 LLM 生成会话摘要
        2. 将旧消息替换为摘要
        3. 减少上下文长度
        
        参数：
            params: 请求参数，包含 session_id 和 focus 字段
        
        返回：
            SessionCompactResult: 压缩结果
        """
        assert self._sessions is not None
        
        # 验证请求参数
        cmd = SessionCompactCommand.model_validate(params)
        
        # 执行压缩
        result = await self._sessions.compact(cmd.session_id, cmd.focus)
        
        return result

    # 列出会话的所有检查点
    async def _session_checkpoint_list_handler(self, params: dict[str, Any]) -> SessionCheckpointListResult:
        """
        处理 session.checkpoint.list RPC 请求 - 列出检查点
        
        参数：
            params: 请求参数，包含 session_id 字段
        
        返回：
            SessionCheckpointListResult: 包含检查点列表
        """
        assert self._sessions is not None
        
        # 验证请求参数
        cmd = SessionCheckpointListCommand.model_validate(params)
        
        # 获取检查点列表
        checkpoints = await self._sessions.list_checkpoints(cmd.session_id)
        
        # 将检查点转换为 CheckpointInfo 对象
        return SessionCheckpointListResult(
            thread_id=cmd.session_id,
            checkpoints=[CheckpointInfo(**c) for c in checkpoints],
        )

    # 恢复到指定的检查点
    async def _session_checkpoint_restore_handler(self, params: dict[str, Any]) -> SessionCheckpointRestoreResult:
        """
        处理 session.checkpoint.restore RPC 请求 - 恢复检查点
        
        参数：
            params: 请求参数，包含 session_id 和 checkpoint_id 字段
        
        返回：
            SessionCheckpointRestoreResult: 恢复结果
        """
        assert self._sessions is not None
        
        # 验证请求参数
        cmd = SessionCheckpointRestoreCommand.model_validate(params)
        
        # 尝试恢复检查点
        result = await self._sessions.restore_checkpoint(cmd.session_id, cmd.checkpoint_id)
        
        # 检查恢复是否成功
        if result is None:
            return SessionCheckpointRestoreResult(
                success=False,
                checkpoint_id=cmd.checkpoint_id,
                step=0,
                message="checkpoint not found",
            )
        
        # 恢复成功
        return SessionCheckpointRestoreResult(
            success=True,
            checkpoint_id=cmd.checkpoint_id,
            step=result["step"],
            message=f"restored to step {result['step']}",
        )

    # 关闭 session 并返回 closed 状态
    async def _session_close_handler(self, params: dict[str, Any]) -> SessionCloseResult:
        """
        处理 session.close RPC 请求 - 关闭会话
        
        参数：
            params: 请求参数，包含 session_id 字段
        
        返回：
            SessionCloseResult: 包含关闭状态
        """
        assert self._sessions is not None
        
        # 验证请求参数
        cmd = SessionCloseCommand.model_validate(params)
        
        # 关闭会话
        await self._sessions.close(cmd.session_id)
        
        # 返回关闭状态
        return SessionCloseResult(status="closed")

    # 设置会话的自动模式
    async def _session_set_auto_mode_handler(self, params: dict[str, Any]) -> SessionSetAutoModeResult:
        """
        处理 session.set_auto_mode RPC 请求 - 设置自动模式
        
        参数：
            params: 请求参数，包含 session_id 和 mode 字段
        
        返回：
            SessionSetAutoModeResult: 包含设置后的模式
        """
        assert self._sessions is not None
        assert self._permission_manager is not None
        
        # 验证请求参数
        cmd = SessionSetAutoModeCommand.model_validate(params)
        
        # 确保会话存在
        self._sessions._get_session(cmd.session_id)
        
        # 设置权限管理器的自动模式
        self._permission_manager.set_auto_mode(cmd.mode)
        
        # 发布事件通知客户端模式已变更
        from iwan_claude.core.bus.events import SessionAutoModeChangedEvent
        await self._bus.publish(
            SessionAutoModeChangedEvent(
                session_id=cmd.session_id,
                mode=cmd.mode,
                ts=_now(),
            )
        )
        
        # 返回设置后的模式
        return SessionSetAutoModeResult(mode=cmd.mode)

    # 设置会话的努力等级
    async def _session_set_effort_level_handler(self, params: dict[str, Any]) -> SessionSetEffortLevelResult:
        """
        处理 session.set_effort_level RPC 请求 - 设置努力等级

        参数：
            params: 请求参数，包含 session_id 和 level 字段

        返回：
            SessionSetEffortLevelResult: 包含设置后的等级
        """
        assert self._sessions is not None
        assert self._permission_manager is not None

        # 验证请求参数
        cmd = SessionSetEffortLevelCommand.model_validate(params)

        # 确保会话存在
        self._sessions._get_session(cmd.session_id)

        # 设置权限管理器的努力等级
        self._permission_manager.set_effort_level(cmd.level)

        # 发布事件通知客户端等级已变更
        from iwan_claude.core.bus.events import SessionEffortLevelChangedEvent
        await self._bus.publish(
            SessionEffortLevelChangedEvent(
                session_id=cmd.session_id,
                level=cmd.level,
                ts=_now(),
            )
        )

        # 返回设置后的等级
        return SessionSetEffortLevelResult(level=cmd.level)

    # 设置会话的模型预设
    async def _session_set_model_handler(self, params: dict[str, Any]) -> SessionSetModelResult:
        """
        处理 session.set_model RPC 请求 - 设置模型预设

        参数：
            params: 请求参数，包含 session_id 和 preset 字段

        返回：
            SessionSetModelResult: 包含设置后的预设
        """
        assert self._sessions is not None
        assert self._permission_manager is not None

        # 验证请求参数
        cmd = SessionSetModelCommand.model_validate(params)

        # 确保会话存在
        self._sessions._get_session(cmd.session_id)

        # 设置权限管理器的模型预设（会校验 preset 是否合法）
        self._permission_manager.set_model_preset(cmd.preset)

        # 发布事件通知客户端模型预设已变更
        from iwan_claude.core.bus.events import SessionModelChangedEvent
        from iwan_claude.core.model_presets import get_model_preset
        preset_info = get_model_preset(cmd.preset)
        await self._bus.publish(
            SessionModelChangedEvent(
                session_id=cmd.session_id,
                preset=cmd.preset,
                model=preset_info.model,
                ts=_now(),
            )
        )

        # 返回设置后的预设
        return SessionSetModelResult(preset=cmd.preset)

    # 设置 Agent 引擎（动态切换，无需重启 core）
    async def _session_set_engine_handler(self, params: dict[str, Any]) -> SessionSetEngineResult:
        """
        处理 session.set_engine RPC 请求 - 动态切换 Agent 引擎

        参数：
            params: 请求参数，包含 session_id 和 engine 字段

        返回：
            SessionSetEngineResult: 包含设置后的引擎名称
        """
        assert self._sessions is not None

        # 验证请求参数
        cmd = SessionSetEngineCommand.model_validate(params)

        # 确保会话存在
        self._sessions._get_session(cmd.session_id)

        # 验证引擎名称有效性
        valid_engines = {"legacy", "langgraph", "plan_execute", "debate", "pipeline"}
        if cmd.engine not in valid_engines:
            raise ValueError(
                f"Invalid engine '{cmd.engine}'. Valid engines: {', '.join(sorted(valid_engines))}"
            )

        # 修改配置中的引擎设置（下次 run_and_capture 时生效）
        self._config.agent.engine = cmd.engine

        # 发布事件通知客户端引擎已变更（多客户端状态同步）
        from iwan_claude.core.bus.events import SessionEngineChangedEvent
        await self._bus.publish(
            SessionEngineChangedEvent(
                session_id=cmd.session_id,
                engine=cmd.engine,
                ts=_now(),
            )
        )

        # 返回设置后的引擎名称
        return SessionSetEngineResult(engine=cmd.engine)

    # 列出所有会话
    async def _session_list_handler(self, params: dict[str, Any]) -> SessionListResult:
        """
        处理 session.list RPC 请求 - 列出所有会话

        参数：
            params: 请求参数（无）

        返回：
            SessionListResult: 包含会话列表，按更新时间倒序排列
        """
        assert self._sessions is not None

        # 获取所有会话列表
        sessions = self._sessions.list_sessions()
        # 转换为 SessionInfo 列表
        session_infos = [
            SessionInfo(
                id=s.id,
                title=s.title or "(untitled)",
                status=s.status,
                mode=s.mode,
                updated_at=s.updated_at,
            )
            for s in sessions
        ]
        return SessionListResult(sessions=session_infos)

    # 重命名会话
    async def _session_rename_handler(self, params: dict[str, Any]) -> SessionRenameResult:
        """
        处理 session.rename RPC 请求 - 重命名会话标题

        参数：
            params: 请求参数，包含 session_id 和 title 字段

        返回：
            SessionRenameResult: 包含重命名后的会话信息
        """
        assert self._sessions is not None

        # 验证请求参数
        cmd = SessionRenameCommand.model_validate(params)

        # 确保会话存在并重命名
        session = await self._sessions.rename_session(cmd.session_id, cmd.title)

        # 返回重命名结果
        return SessionRenameResult(session_id=session.id, title=session.title)

    # 获取当前会话使用的引擎信息
    async def _session_engine_info_handler(self, params: dict[str, Any]) -> dict[str, str]:
        """
        处理 session.engine_info RPC 请求 - 获取引擎信息
        
        返回当前使用的 agent 引擎和检查点后端配置。
        
        返回：
            dict: 包含 engine 和 checkpoint_backend
        """
        return {
            "engine": self._config.agent.engine,
            "checkpoint_backend": self._config.agent.checkpoint_backend,
        }

    # 注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流
    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        """
        处理 event.subscribe RPC 请求 - 订阅事件
        
        工作流程：
        1. 如果指定了 replay_from_run，先回放历史事件
        2. 注册事件订阅
        3. 返回订阅 ID 和回放的事件数量
        
        参数：
            params: 请求参数，包含 topics、scope、replay_from_run 字段
        
        返回：
            EventSubscribeResult: 包含 subscription_id 和 replayed_count
        """
        # 验证请求参数
        cmd = EventSubscribeCommand.model_validate(params)
        
        # 获取当前连接的 writer（用于推送事件）
        writer = get_connection_writer()

        # 回放历史事件（如果指定了 replay_from_run）
        replayed_count = 0
        if cmd.replay_from_run is not None:
            replayed_count = await self._replay_events(
                cmd.replay_from_run, writer, cmd.topics
            )

        # 注册事件订阅
        assert self._broadcaster is not None
        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        
        # 返回订阅结果
        return EventSubscribeResult(subscription_id=sub_id, replayed_count=replayed_count)

    # 从 events.jsonl 向 writer 回放匹配 topic 的历史事件，返回已回放条数
    async def _replay_events(
        self,
        run_id: str,
        writer: asyncio.StreamWriter,
        topics: list[str],
    ) -> int:
        """
        回放历史事件到客户端
        
        从 events.jsonl 文件中读取历史事件，
        根据 topics 过滤后推送给客户端。
        
        参数：
            run_id: 要回放的 run ID
            writer: 客户端的 StreamWriter
            topics: 事件过滤模式列表
        
        返回：
            int: 已回放的事件数量
        """
        # 获取事件文件路径
        path = events_file(run_id)
        
        # 如果路径不存在，尝试在默认会话目录中查找
        if not path.exists():
            for candidate in Path("~/.iwan/sessions").expanduser().glob(
                f"*/runs/{run_id}/events.jsonl"
            ):
                path = candidate
                break
        
        # 如果文件仍然不存在，返回 0
        if not path.exists():
            return 0

        count = 0
        # 逐行读取事件文件
        for line in path.read_text().splitlines():
            # 跳过空行
            if not line:
                continue
            
            # 解析 JSON
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            # 获取事件类型
            event_type: str = event.get("type", "")
            
            # 根据 topics 过滤事件
            # fnmatch.fnmatch 支持通配符匹配（如 "run.*"）
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue
            
            # 创建事件推送信封并发送
            envelope = EventPushEnvelope(event=event)
            writer.write(envelope.model_dump_json().encode() + b"\n")
            count += 1

        # 如果有事件发送，刷新缓冲区
        if count:
            await writer.drain()
        
        return count

    # 启动守护进程：加载配置、初始化日志、启动 trace、启动 TCP 服务器，并等待退出信号
    async def run(self) -> None:
        """
        启动核心应用 - 完整的服务启动流程
        
        启动流程：
        1. 记录启动时间
        2. 加载配置
        3. 初始化日志
        4. 初始化 trace（如果启用）
        5. 初始化权限管理器
        6. 初始化事件广播器
        7. 初始化会话存储
        8. 初始化 MCP 服务器（如果配置）
        9. 初始化 Checkpointer（如果配置）
        10. 初始化会话管理器
        11. 创建并启动 Socket 服务器
        12. 注册所有 RPC 命令处理器
        13. 等待退出信号
        14. 优雅关闭所有子系统
        """
        # 记录启动时间（使用 monotonic 时间，不受系统时间调整影响）
        self._start_time = time.monotonic()
        
        # ===== 加载配置 =====
        # 从环境变量和配置文件中加载系统配置
        self._config = get_config()
        
        # ===== 初始化日志 =====
        # 根据配置设置日志级别和输出格式
        setup_logging(self._config)

        # ===== 初始化 Trace =====
        # 如果启用了 trace，创建 TraceWriter 并订阅事件总线
        if self._config.trace.enabled:
            trace_path = Path(self._config.trace.file).expanduser()
            self._trace = TraceWriter(trace_path)
            await self._trace.start()
            # 订阅事件总线：所有事件都会写入 trace 文件
            self._bus.subscribe(self._trace_event_handler)

        # ===== 初始化权限管理器 =====
        # 加载权限策略文件，管理工具调用权限
        policy_file = Path("~/.iwan/policy.toml").expanduser()
        self._permission_manager = PermissionManager(
            policy_file=policy_file,
            timeout_s=self._config.permission.timeout_s,
        )
        logger.info(
            "permission manager: timeout_s=%.1f  persistent=%d entries",
            self._config.permission.timeout_s,
            len(load_policy_file(policy_file)),
        )

        # ===== 初始化事件广播器 =====
        # IpcEventBroadcaster 负责将事件推送给连接的客户端
        self._broadcaster = IpcEventBroadcaster(trace=self._trace)
        # 订阅事件总线：所有事件都会广播给客户端
        self._bus.subscribe(self._broadcaster.handle)
        
        # ===== 初始化会话存储 =====
        # 获取会话根目录：优先使用环境变量 IWAN_SESSIONS_DIR，否则使用默认路径
        sessions_root = Path(os.environ.get("IWAN_SESSIONS_DIR", "~/.iwan/sessions")).expanduser()
        # 确保目录存在
        sessions_root.mkdir(parents=True, exist_ok=True)
        # 创建会话存储（基于文件系统）
        store = SessionStore(sessions_root)
        
        assert self._config is not None
        
        # ===== 创建 Compact Provider =====
        # compact（会话压缩）需要使用 LLM 生成摘要
        # 使用与主 agent 相同的配置选择 Anthropic 或 OpenAI 兼容的 provider
        compact_provider = create_provider_from_config(self._config.llm)

        # ===== 初始化 MCP 服务器管理器 =====
        # MCP（Model Context Protocol）用于集成外部工具服务器
        self._mcp_manager = McpServerManager()
        if self._config.mcp.servers:
            logger.info("mcp: starting %d server(s)", len(self._config.mcp.servers))
            await self._mcp_manager.start_all(self._config.mcp.servers)

        # ===== 初始化 Checkpointer =====
        # 根据配置选择检查点存储后端（none/memory/sqlite）
        await self._init_checkpointer()

        # ===== 初始化跨会话记忆管理器 =====
        # 三层记忆：LongTermMemory（JSONL 持久化）+ VectorMemory（复用 RAG 向量检索）
        # 无 embedding API key 时 VectorMemory 降级为空，长期记忆仍可用
        from iwan_claude.core.memory import (
            LongTermMemory,
            MemoryManager,
            VectorMemory,
        )
        from iwan_claude.core.memory.claude_md import (
            load_claude_md,
            render_claude_md_prompt,
        )
        from iwan_claude.core.rag.embedding import get_embedding_provider
        from iwan_claude.core.rag.vectorstore import get_vector_store

        memory_dir = Path.home() / ".iwan_claude" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        long_term = LongTermMemory(memory_dir / "long_term.jsonl")
        try:
            embedder = get_embedding_provider(self._config.rag, self._config.llm.base_url)
        except Exception:
            logging.getLogger(__name__).exception(
                "embedding provider init failed, vector memory disabled"
            )
            embedder = None
        self._memory = MemoryManager(
            long_term=long_term,
            vector_memory=VectorMemory(
                vector_store=get_vector_store(),
                embedding_provider=embedder,
                index_path=str(memory_dir / "vector_memory.json"),
            ),
            project_context=render_claude_md_prompt(load_claude_md()),
        )
        self._memory.load()
        logging.getLogger(__name__).info("memory manager: initialized (long_term + vector)")

        # ===== 初始化会话管理器 =====
        # SessionManager 负责管理所有用户会话
        # 使用 lambda 作为 runner_factory，确保每个会话都有独立的 AgentRunner
        self._sessions = SessionManager(
            store,
            runner_factory=lambda: AgentRunner(
                self._config,
                bus=self._bus,
                trace=self._trace,
                permission_manager=self._permission_manager,
                mcp_manager=self._mcp_manager,
                checkpointer=self._checkpointer,
                memory_manager=self._memory,
            ),
            bus=self._bus,
            provider=compact_provider,
            memory_manager=self._memory,
        )

        # ===== 创建 Socket 服务器 =====
        # SocketServer 是基于 TCP Socket 的 RPC 服务端
        server = SocketServer(
            self._config.host,      # 绑定地址
            self._config.port,      # 绑定端口
            self._broadcaster,      # 事件广播器
            trace=self._trace,      # 跟踪写入器
        )
        
        # ===== 注册 RPC 命令处理器 =====
        # 将命令名映射到处理方法
        server.register("core.ping", self._ping_handler)
        server.register("agent.run", self._agent_run_handler)
        server.register("event.subscribe", self._subscribe_handler)
        server.register("session.create", self._session_create_handler)
        server.register("session.send_message", self._session_send_handler)
        server.register("session.get_history", self._session_history_handler)
        server.register("session.close", self._session_close_handler)
        server.register("permission.respond", self._permission_respond_handler)
        server.register("session.compact", self._session_compact_handler)
        server.register("session.checkpoint.list", self._session_checkpoint_list_handler)
        server.register("session.checkpoint.restore", self._session_checkpoint_restore_handler)
        server.register("session.set_auto_mode", self._session_set_auto_mode_handler)
        server.register("session.set_effort_level", self._session_set_effort_level_handler)
        server.register("session.set_model", self._session_set_model_handler)
        server.register("session.set_engine", self._session_set_engine_handler)
        server.register("session.list", self._session_list_handler)
        server.register("session.rename", self._session_rename_handler)
        server.register("session.engine_info", self._session_engine_info_handler)

        # ===== 启动服务器 =====
        # start() 方法会启动 TCP 监听并返回绑定的地址
        addr = await server.start()
        logger.info("iwan-core %s listening addr=%s", iwan_claude.__version__, addr)
        logger.info("config: %s", self._config)

        # ===== 设置退出信号处理 =====
        # 获取当前运行的事件循环
        loop = asyncio.get_running_loop()
        
        # 创建关闭事件：用于等待退出信号
        shutdown = asyncio.Event()

        # 设置关闭标志的函数
        def _set_shutdown() -> None:
            shutdown.set()

        # 跨平台信号处理
        if not IS_WINDOWS:
            # Linux / macOS：SelectorEventLoop 原生支持 loop.add_signal_handler
            # SIGINT：Ctrl+C 信号
            # SIGTERM：终止信号（kill 命令发送）
            loop.add_signal_handler(signal.SIGINT, _set_shutdown)
            loop.add_signal_handler(signal.SIGTERM, _set_shutdown)
        else:
            # Windows：默认 ProactorEventLoop 不支持 add_signal_handler
            # 退回到全局 signal.signal() + KeyboardInterrupt 兜底
            def _win_sigint_handler(signum: int, frame: object) -> None:
                try:
                    # 使用 call_soon_threadsafe 确保线程安全
                    loop.call_soon_threadsafe(_set_shutdown)
                except Exception:
                    # 如果事件循环已停止，直接设置
                    _set_shutdown()
            signal.signal(signal.SIGINT, _win_sigint_handler)
            # Windows 没有 SIGTERM 信号，跳过

        # ===== 等待退出信号 =====
        try:
            # 阻塞等待关闭事件
            await shutdown.wait()
        except KeyboardInterrupt:
            # Ctrl+C 直接触发的兜底处理
            pass

        # ===== 优雅关闭 =====
        logger.info("shutting down")
        
        # 取消所有正在运行的任务
        for run_task in list(self._running_runs):
            run_task.cancel()
        
        # 等待所有任务完成（或被取消）
        if self._running_runs:
            await asyncio.gather(*self._running_runs, return_exceptions=True)
        
        # 停止所有 MCP 服务器
        if self._mcp_manager is not None:
            await self._mcp_manager.stop_all()
        
        # 停止 Socket 服务器
        await server.stop()
        
        # 关闭 Checkpointer 上下文
        if self._checkpointer_ctx is not None:
            try:
                if hasattr(self._checkpointer_ctx, "__aexit__"):
                    await self._checkpointer_ctx.__aexit__(None, None, None)
            except Exception:
                logger.exception("Error closing checkpointer context")
        
        # 停止 Trace 写入器
        if self._trace is not None:
            await self._trace.stop()


# 同步入口：启动 CoreApp 事件循环
def run() -> None:
    """
    核心应用的同步入口
    
    使用方式：
    python -m iwan_claude.core
    
    或通过 CLI 命令：
    iwan core start
    
    工作原理：
    asyncio.run() 会：
    1. 创建一个新的事件循环
    2. 运行传入的异步函数（CoreApp().run()）
    3. 函数完成后关闭事件循环
    """
    asyncio.run(CoreApp().run())
