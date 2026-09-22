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
    FileChangeInfo,                 # 文件变更条目
    FileChangesListCommand,         # 文件变更清单命令
    FileChangesListResult,          # 文件变更清单结果
    FileRestoreCommand,             # 文件还原命令
    FileRestoreItem,                # 文件还原结果行
    FileRestoreResult,              # 文件还原结果
    PermissionRespondCommand,       # 权限响应命令
    PermissionRespondResult,        # 权限响应结果
    PongResult,                     # Ping 响应
    RunCancelCommand,               # 取消运行命令
    RunCancelResult,                # 取消运行结果
    RunSteerCommand,                # 运行中修正命令
    RunSteerResult,                 # 运行中修正结果
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
    SessionSetPermissionModeCommand,   # 设置五态权限模式命令
    SessionSetPermissionModeResult,    # 设置五态权限模式结果
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
    TrustListCommand,               # 信任列表命令
    TrustListResult,                # 信任列表结果
    TrustRespondCommand,            # 信任响应命令
    TrustRespondResult,             # 信任响应结果
    TrustRevokeCommand,             # 信任撤销命令
    TrustRevokeResult,              # 信任撤销结果
)
from iwan_claude.core.bus.envelope import EventPushEnvelope  # 事件推送封装

# 导入核心组件
from iwan_claude.core.config import (
    IwanConfig,
    get_config,
    resolve_checkpoint_db_path,                            # checkpoint DB 路径解析（锚定会话根）
)   # 配置
from iwan_claude.core.events.bus import EventBus             # 事件总线
from iwan_claude.core.llm import create_provider_from_config # LLM 提供者创建
from iwan_claude.core.logging_setup import setup_logging     # 日志初始化
from iwan_claude.core.mcp.server import McpServerManager     # MCP 服务器管理
from iwan_claude.core.permissions.manager import PermissionManager  # 权限管理
from iwan_claude.core.permissions.storage import load_policy_file   # 加载权限策略
from iwan_claude.core.trust import TrustStore, normalize_dir_key    # Layer 0 信任门
from iwan_claude.core.shadow import ShadowStore                     # Layer 4 文件回滚
from iwan_claude.core.run_registry import add_steer, request_cancel  # 活跃运行注册表：取消/修正
from iwan_claude.core.runner import AgentRunner              # Agent 运行器
from iwan_claude.core.runs import events_file, new_run_id    # 运行相关工具
from iwan_claude.core.session import SessionManager, SessionStore  # 会话管理
# 后台子 Agent 注册表：每 daemon 唯一实例，跨 run 共享
from iwan_claude.core.subagent.registry import BackgroundTaskRegistry
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


# 目录里是否存在会被自动读进系统提示的指令/配置文件（信任弹窗的风险提示依据）
def _has_instruction_files(cwd: str) -> bool:
    base = Path(cwd)
    for name in ("CLAUDE.md", "AGENTS.md", ".claude/settings.json"):
        if (base / name).exists():
            return True
    return False


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
        # 会话根目录（run setup 时按配置刷新）：replay 旁路查找事件文件用
        self._sessions_root: Path = Path("~/.iwan/sessions").expanduser()
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
        # Layer 0 项目信任存储（start 时按 [permission] trust_file 初始化）
        self._trust_store: TrustStore | None = None

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

            from iwan_claude.core.runner import prune_sqlite_checkpoints
            # 相对路径锚定会话根父级（daemon 懒启动 cwd 不定）
            db_path = resolve_checkpoint_db_path(self._config.agent)
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
            # 启动期保留策略：裁剪早于任何客户端连接建立，无并发写风险
            await prune_sqlite_checkpoints(saver, self._config.agent.checkpoint_keep_last)
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
        permission_mode = (
            self._permission_manager.get_permission_mode(session.id)
            if self._permission_manager is not None else "default"
        )
        effort_level = self._permission_manager.get_effort_level() if self._permission_manager is not None else "medium"
        model_preset = self._permission_manager.get_model_preset() if self._permission_manager is not None else "balanced"
        # ===== Layer 0 信任门（S9 Part A）=====
        # 信任判定对准的目录必须与会话实际工作目录一致：cmd.cwd 为空时
        # create() 走 daemon 进程 CWD 兜底，这里用同一判据，否则弹窗问 A 写的是 B
        effective_cwd = cmd.cwd or os.getcwd()
        trust = "ask"
        if self._trust_store is not None and self._permission_manager is not None:
            if cmd.trust_decision in ("allow", "deny"):
                # 客户端先行答复（信任对话框答完再建会话）：直接落持久决定
                trust = cmd.trust_decision
                self._trust_store.set_decision(effective_cwd, trust)
            else:
                trust = self._trust_store.lookup(effective_cwd)
            self._permission_manager.set_trust(session.id, trust)
            if trust == "ask":
                # 未决目录：广播 trust.requested 供在线客户端弹信任对话框。
                # 只通知不等待——会话照常可用（ask=写操作逐次审批的现状语义）
                from iwan_claude.core.bus.events import TrustRequestedEvent
                await self._bus.publish(
                    TrustRequestedEvent(
                        session_id=session.id,
                        cwd=effective_cwd,
                        has_instruction_files=_has_instruction_files(effective_cwd),
                        ts=_now(),
                    )
                )
        return SessionCreateResult(
            session_id=session.id, status=session.status, auto_mode=auto_mode,
            permission_mode=permission_mode, effort_level=effort_level, model_preset=model_preset,
            trust=trust,
        )

    # 向 session 发送一条用户消息并同步等待对应 run 完成
    async def _session_send_handler(self, params: dict[str, Any]) -> SessionSendMessageResult:
        """
        处理 session.send_message RPC 请求 - 发送消息到会话

        参数：
            params: 请求参数，包含 session_id、content、skill_name、skip_auto_skill

        返回：
            SessionSendMessageResult: run_id 或 skill_match（待确认）
        """
        assert self._sessions is not None

        # 验证请求参数
        cmd = SessionSendMessageCommand.model_validate(params)

        # ===== Layer 0 信任复查（覆盖 resume/重启场景）=====
        # 从磁盘恢复的会话不走 session.create，内存信任档是空的；未登记时
        # 按该会话 cwd 查一次 TrustStore——resume 进 deny 目录的会话同样被拦
        if (
            self._trust_store is not None
            and self._permission_manager is not None
            and not self._permission_manager.has_trust(cmd.session_id)
        ):
            sess = self._sessions._get_session(cmd.session_id)
            self._permission_manager.set_trust(
                cmd.session_id, self._trust_store.lookup(sess.cwd or os.getcwd())
            )

        # 发送消息并等待任务完成
        result = await self._sessions.send_message(
            cmd.session_id,
            cmd.content,
            skill_name=cmd.skill_name,
            skip_auto_skill=cmd.skip_auto_skill,
        )

        # 返回结果（run_id 或 skill_match）
        return SessionSendMessageResult(
            run_id=result.run_id,
            skill_match=result.skill_match,
        )

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

    # 处理 trust.respond：登记/撤销某目录的信任决定（不阻塞执行，改的是"以后的规则"）
    async def _trust_respond_handler(self, params: dict[str, Any]) -> TrustRespondResult:
        """
        处理 trust.respond RPC 请求 - 设定会话/目录的信任档

        参数：
            params: 请求参数，含 session_id / cwd（空=会话 cwd）/ decision / persistent

        返回：
            TrustRespondResult: ok + 该会话现在的生效信任档
        """
        assert self._sessions is not None
        assert self._permission_manager is not None
        assert self._trust_store is not None

        cmd = TrustRespondCommand.model_validate(params)
        # 空 session_id = 无会话操作（`iwan trust grant/deny` 的 CLI 场景）：
        # 此时必须显式给 cwd，只改持久层，不碰任何会话内存态
        session = None
        if cmd.session_id:
            # 会话不存在会抛 SessionError → JSON-RPC 错误，不静默吞掉
            session = self._sessions._get_session(cmd.session_id)
        cwd = cmd.cwd or (session.cwd if session is not None else "")
        if not cwd and session is None:
            return TrustRespondResult(ok=False, trust="ask")
        cwd = cwd or os.getcwd()
        if cmd.decision not in ("allow", "deny", "ask"):
            return TrustRespondResult(
                ok=False, trust=self._permission_manager.get_trust(cmd.session_id))
        # 会话内存态立即生效（deny 从下一枚工具调用起拦）
        if session is not None:
            self._permission_manager.set_trust(cmd.session_id, cmd.decision)
        if cmd.persistent:
            if cmd.decision == "ask":
                self._trust_store.revoke(cwd)
            else:
                self._trust_store.set_decision(cwd, cmd.decision)
        # 生效值：持久答复以重查结果为准（inherit 可能让祖先决定继续赢）
        effective = self._trust_store.lookup(cwd) if cmd.persistent else cmd.decision
        from iwan_claude.core.bus.events import TrustChangedEvent
        await self._bus.publish(
            TrustChangedEvent(
                session_id=cmd.session_id,
                cwd=normalize_dir_key(cwd),
                decision=cmd.decision,
                persistent=cmd.persistent,
                ts=_now(),
            )
        )
        return TrustRespondResult(ok=True, trust=effective)

    # 处理 trust.list：全量持久信任条目（CLI/TUI 展示与自查）
    async def _trust_list_handler(self, params: dict[str, Any]) -> TrustListResult:
        """
        处理 trust.list RPC 请求 - 列出 trust.toml 全部条目
        """
        assert self._trust_store is not None
        return TrustListResult(entries=self._trust_store.entries())

    # 处理 trust.revoke：删除某目录的持久决定（回到未决态；deny 撤销后恢复 ask）
    async def _trust_revoke_handler(self, params: dict[str, Any]) -> TrustRevokeResult:
        """
        处理 trust.revoke RPC 请求 - 撤销某目录的持久信任决定

        参数：
            params: 请求参数，含 cwd（与 trust.toml 键做同样归一化后比对）

        返回：
            TrustRevokeResult: ok=是否确实删掉了条目
        """
        assert self._trust_store is not None
        cmd = TrustRevokeCommand.model_validate(params)
        removed = self._trust_store.revoke(cmd.cwd)
        if removed:
            # 广播让在线客户端刷新信任面板；session_id 空串=非会话发起
            from iwan_claude.core.bus.events import TrustChangedEvent
            await self._bus.publish(
                TrustChangedEvent(
                    session_id="",
                    cwd=normalize_dir_key(cmd.cwd),
                    decision="ask",
                    persistent=True,
                    ts=_now(),
                )
            )
        return TrustRevokeResult(ok=removed)

    # 定位指定会话/run 的影子账本；run_id 空 = 按 mtime 取最近一个有账本的 run
    def _shadow_store_for(self, session_id: str, run_id: str) -> tuple[ShadowStore | None, str]:
        """
        组装 ShadowStore（对象目录=会话级 shadow/，账本=该 run 的 file_changes.json）

        参数：
            session_id: 会话 ID（不存在时由调用方的 _get_session 提前拦下）
            run_id: 目标 run；"" = 自动选最近有账本的 run

        返回：
            (ShadowStore | None, 实际选定的 run_id)；无账本时 (None, "")
        """
        assert self._sessions is not None and self._config is not None
        store = self._sessions._store
        sess_dir = store.session_dir(session_id)
        runs_dir = store.runs_dir(session_id)
        max_bytes = self._config.sandbox.shadow_max_file_bytes
        if run_id:
            ledger = runs_dir / run_id / "file_changes.json"
            if not ledger.exists():
                return None, run_id
            return ShadowStore(sess_dir / "shadow", ledger, max_file_bytes=max_bytes), run_id
        # 自动选档：账本文件的存在本身就是"该 run 做过写操作"的证据
        ledgers = sorted(
            runs_dir.glob("*/file_changes.json"),
            key=lambda p: p.stat().st_mtime,
        ) if runs_dir.exists() else []
        if not ledgers:
            return None, ""
        chosen = ledgers[-1]
        return ShadowStore(sess_dir / "shadow", chosen, max_file_bytes=max_bytes), chosen.parent.name

    # 处理 files.changes：列出该 run 被影子快照记录的文件变更（含冲突标记）
    async def _file_changes_list_handler(
        self, params: dict[str, Any]
    ) -> FileChangesListResult:
        """
        处理 files.changes RPC 请求 - 文件变更清单

        参数：
            params: 请求参数，含 session_id 与可选 run_id（空=最近有账本的 run）

        返回：
            FileChangesListResult: 实际 run_id + 按文件归并的最近变更状态
        """
        assert self._sessions is not None
        cmd = FileChangesListCommand.model_validate(params)
        self._sessions._get_session(cmd.session_id)
        shadow, chosen_run = self._shadow_store_for(cmd.session_id, cmd.run_id)
        if shadow is None:
            return FileChangesListResult(run_id=chosen_run, changes=[])
        changes = [FileChangeInfo(**row) for row in shadow.changes_view()]
        return FileChangesListResult(run_id=chosen_run, changes=changes)

    # 处理 files.restore：把选中文件回滚到该 run 变更前（还原自带反向快照，可再撤销）
    async def _file_restore_handler(self, params: dict[str, Any]) -> FileRestoreResult:
        """
        处理 files.restore RPC 请求 - 文件还原

        参数：
            params: 请求参数，含 session_id / run_id（空=最近）/ paths（["*"]=全部）/ force

        返回：
            FileRestoreResult: 逐文件结果；有 skipped/failed 时 ok=False
        """
        assert self._sessions is not None
        cmd = FileRestoreCommand.model_validate(params)
        self._sessions._get_session(cmd.session_id)
        shadow, chosen_run = self._shadow_store_for(cmd.session_id, cmd.run_id)
        if shadow is None:
            return FileRestoreResult(
                ok=False, run_id=chosen_run,
                results=[FileRestoreItem(
                    path="*", status="failed",
                    detail="no shadow ledger found for this session/run",
                )],
            )
        rows = shadow.restore(list(cmd.paths), force=cmd.force)
        items = [FileRestoreItem(**row) for row in rows]
        ok = all(it.status == "restored" for it in items)
        logger.info(
            "files.restore: session=%s run=%s restored=%d/%d force=%s",
            cmd.session_id, chosen_run,
            sum(1 for it in items if it.status == "restored"), len(items), cmd.force,
        )
        return FileRestoreResult(ok=ok, run_id=chosen_run, results=items)

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

        # 旧值取该会话生效模式（legacy 改的是全局默认，per-session 覆盖可能让它不变）
        previous = self._permission_manager.get_permission_mode(cmd.session_id)
        # 设置权限管理器的自动模式（映射进五态的全局默认值）
        self._permission_manager.set_auto_mode(cmd.mode)
        effective = self._permission_manager.get_permission_mode(cmd.session_id)

        # 发布事件通知客户端模式已变更
        from iwan_claude.core.bus.events import (
            SessionAutoModeChangedEvent,
            SessionPermissionModeChangedEvent,
        )
        await self._bus.publish(
            SessionAutoModeChangedEvent(
                session_id=cmd.session_id,
                mode=cmd.mode,
                ts=_now(),
            )
        )
        # 同步广播五态事件：新客户端只认 permission_mode_changed，
        # legacy 入口若不发这条，多端状态栏就会停在过期档位
        if effective != previous:
            await self._bus.publish(
                SessionPermissionModeChangedEvent(
                    session_id=cmd.session_id,
                    mode=effective,
                    previous_mode=previous,
                    ts=_now(),
                )
            )
        
        # 返回设置后的模式
        return SessionSetAutoModeResult(mode=cmd.mode)

    # 切换指定会话的五态权限模式（对齐 Claude Code Shift+Tab；per-session 不伤及他席）
    async def _session_set_permission_mode_handler(
        self, params: dict[str, Any]
    ) -> SessionSetPermissionModeResult:
        """
        处理 session.set_permission_mode RPC 请求 - 设置会话权限模式

        参数：
            params: 请求参数，包含 session_id 和 mode（五态之一）字段

        返回：
            SessionSetPermissionModeResult: 包含 mode 与 previous_mode
        """
        assert self._sessions is not None
        assert self._permission_manager is not None

        # 验证请求参数（非法 mode 在 manager 内抛 ValueError → JSON-RPC INTERNAL_ERROR）
        cmd = SessionSetPermissionModeCommand.model_validate(params)

        # 确保会话存在（不存在的 session_id 直接抛 SessionError）
        self._sessions._get_session(cmd.session_id)

        # 切换并拿到旧值，previous 随事件广播供多端一致刷新
        previous = self._permission_manager.set_permission_mode(cmd.mode, cmd.session_id)

        from iwan_claude.core.bus.events import SessionPermissionModeChangedEvent
        await self._bus.publish(
            SessionPermissionModeChangedEvent(
                session_id=cmd.session_id,
                mode=cmd.mode,
                previous_mode=previous,
                ts=_now(),
            )
        )

        return SessionSetPermissionModeResult(mode=cmd.mode, previous_mode=previous)

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
        # "auto"：runner 每个 run 前用 engine_selector 按任务复杂度自动挑选引擎
        valid_engines = {"legacy", "langgraph", "plan_execute", "debate", "pipeline", "auto"}
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

    # 处理 run.cancel：按 run_id 请求取消活跃运行
    async def _run_cancel_handler(self, params: dict[str, Any]) -> RunCancelResult:
        """
        处理 run.cancel RPC 请求 - 取消正在运行的任务

        【学习要点】
        1. 这里只做"举手示意"：request_cancel 给活跃 run 打标记并 cancel 其
           asyncio.Task，真正的清场（保存轨迹、发 RunFinishedEvent(cancelled)）
           由 runner 捕获 CancelledError 完成——取消语义收敛在一处，RPC 层零逻辑。
        2. 不抛异常而是返回 accepted=False：run 可能刚好自然结束，取消是幂等
           操作，客户端拿到的永远是"有没有命中"而不是错误码。
        3. 与 session.close 的区别：close 是会话级销毁，cancel 只掐当前一次运行，
           会话本身和已写入的历史都保留。

        参数：
            params: 请求参数，包含 run_id 字段

        返回：
            RunCancelResult: accepted 表示是否命中了一个活跃 run
        """
        cmd = RunCancelCommand.model_validate(params)
        accepted = request_cancel(cmd.run_id)
        logger.info("run.cancel run_id=%s accepted=%s", cmd.run_id, accepted)
        return RunCancelResult(accepted=accepted)

    # 处理 run.steer：向活跃运行注入运行中修正消息
    async def _run_steer_handler(self, params: dict[str, Any]) -> RunSteerResult:
        """
        处理 run.steer RPC 请求 - 任务运行中给出修正评论

        【学习要点】
        1. steer 不进 RPC 的请求-响应同步路径：消息只 append 进该 run 的内存
           队列（run_registry），由引擎在下一次调用 LLM 前的回合边界消费。
           所以本方法"入队即成功"，模型何时读到是异步的。
        2. accepted=False 表示 run 已结束/不存在，此时队列里没有这条消息——
           客户端应当改发 session.send_message，避免用户以为修正已生效。
        3. 不阻塞等待模型"看到"：如果等确认，取消/修正这种高频轻量操作会
           被拖成一次完整 turn 的延迟。

        参数：
            params: 请求参数，包含 run_id 和 message 字段

        返回：
            RunSteerResult: accepted 表示是否命中活跃 run，queued 为当前积压条数
        """
        cmd = RunSteerCommand.model_validate(params)
        queued = add_steer(cmd.run_id, cmd.message)
        accepted = queued is not None
        logger.info("run.steer run_id=%s accepted=%s queued=%s", cmd.run_id, accepted, queued)
        return RunSteerResult(accepted=accepted, queued=queued or 0)

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
        
        # 如果路径不存在，尝试在会话目录中查找（one_shot/chat run 的事件落在
        # <sessions_root>/<sid>/runs/ 下；sessions_root 受 IWAN_SESSIONS_DIR 影响，
        # 必须与初始化 SessionStore 时同源，否则测试/自定义目录回放恒为 0）
        if not path.exists():
            for candidate in self._sessions_root.glob(
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
        # 声明式 deny/ask/allow 规则（[permission] 节；全空 = 规则引擎关闭）
        from iwan_claude.core.permissions.rules import PermissionRules
        permission_rules = PermissionRules(
            deny=self._config.permission.deny,
            ask=self._config.permission.ask,
            allow=self._config.permission.allow,
        )
        # [[hooks]] 已在配置加载期校验过，这里解析成可执行规格并接入事件总线
        # （registry 持 bus：每个实际跑过的 hook 广播 hook.evaluated，TUI 可观测）
        from iwan_claude.core.hooks import HookRegistry, parse_hook_entries
        hook_specs = parse_hook_entries(self._config.hooks)
        hook_registry = HookRegistry(hook_specs, bus=self._bus)
        # 启动默认模式：[permission] mode 优先；老配置 [agent] auto_mode 仅在
        # 新键未动过（仍为 default）时经 AUTO_TO_MODE 折进来——新旧键共存时
        # 更具体的 permission 节赢，这条判据写进设计文档 §3
        from iwan_claude.core.permissions.manager import AUTO_TO_MODE
        effective_mode = self._config.permission.mode
        if effective_mode == "default" and self._config.agent.auto_mode != "off":
            effective_mode = AUTO_TO_MODE[self._config.agent.auto_mode]
        self._permission_manager = PermissionManager(
            policy_file=policy_file,
            timeout_s=self._config.permission.timeout_s,
            rules=permission_rules,
            hooks=hook_registry,
            default_mode=effective_mode,
        )

        # ===== 初始化项目信任存储（Layer 0 信任门，S9 Part A）=====
        # 与权限层的关系：信任是上游 AND 门（目录能不能碰），权限是下游逐次裁决
        # （这次碰要不要问）——两者独立评估，更严的一方生效
        trust_path = Path(self._config.permission.trust_file).expanduser()
        self._trust_store = TrustStore(
            trust_path, inherit=self._config.permission.trust_inherit,
        )
        logger.info(
            "trust: store=%s inherit=%s entries=%d",
            trust_path, self._config.permission.trust_inherit,
            len(self._trust_store.entries()),
        )
        logger.info(
            "permission manager: timeout_s=%.1f  default_mode=%s  persistent=%d entries"
            "  rules=%d/%d/%d  hooks=%d",
            self._config.permission.timeout_s,
            effective_mode,
            len(load_policy_file(policy_file)),
            len(permission_rules.deny), len(permission_rules.ask), len(permission_rules.allow),
            len(hook_specs),
        )

        # ===== 初始化事件广播器 =====
        # IpcEventBroadcaster 负责将事件推送给连接的客户端
        self._broadcaster = IpcEventBroadcaster(trace=self._trace)
        # 订阅事件总线：所有事件都会广播给客户端
        self._bus.subscribe(self._broadcaster.handle)
        
        # ===== 初始化会话存储 =====
        # 获取会话根目录：优先使用环境变量 IWAN_SESSIONS_DIR，否则使用默认路径
        sessions_root = Path(os.environ.get("IWAN_SESSIONS_DIR", "~/.iwan/sessions")).expanduser()
        # 保存根目录引用：replay 等旁路读事件时要按同一目录找，不能写死默认路径
        self._sessions_root = sessions_root
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
            # timeout_s 接 [llm] embedding_timeout_s：向量记忆与 RAG 共用同一超时预算
            embedder = get_embedding_provider(
                self._config.rag,
                self._config.llm.base_url,
                timeout_s=self._config.llm.embedding_timeout_s,
            )
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
                # 【设计】index_path 是"目录"不是"文件"（store.save 会 mkdir 后写
                # chunks.json/vectors.json 两个文件）——旧实现传 vector_memory.json，
                # 用户磁盘上出现一个名叫 .json 的目录
                index_path=str(memory_dir / "vector_memory"),
            ),
            project_context=render_claude_md_prompt(load_claude_md()),
        )
        self._memory.load()
        logging.getLogger(__name__).info("memory manager: initialized (long_term + vector)")

        # ===== 初始化会话管理器 =====
        # SessionManager 负责管理所有用户会话
        # 【设计】后台子 Agent 注册表挂在 CoreApp 上、每 daemon 唯一，
        # 注入给每个 AgentRunner：AgentRunner 是每次用户发言新建的，
        # 注册表若跟着 runner 走，上一轮 spawn_agent 的 run_id 下一轮
        # 就查不到、也取消不了（工具描述的"稍后用 agent_result 取"变成假承诺）
        self._subagent_registry = BackgroundTaskRegistry()
        # 使用 lambda 作为 runner_factory，AgentRunner 每 run 新建、共享注册表
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
                task_registry=self._subagent_registry,
            ),
            bus=self._bus,
            provider=compact_provider,
            memory_manager=self._memory,
        )

        # ===== 崩溃恢复：标记上次崩溃时正在运行的会话为 interrupted =====
        # Core 启动时检测 status="running" 的会话（崩溃后状态保留），
        # 将其标记为 "interrupted"，等待 TUI 端用户确认恢复
        recovered = await self._sessions.recover_interrupted_sessions()
        if recovered > 0:
            logger.info("crash recovery: %d interrupted session(s) detected", recovered)

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
        server.register("trust.respond", self._trust_respond_handler)
        server.register("trust.list", self._trust_list_handler)
        server.register("trust.revoke", self._trust_revoke_handler)
        server.register("files.changes", self._file_changes_list_handler)
        server.register("files.restore", self._file_restore_handler)
        server.register("session.compact", self._session_compact_handler)
        server.register("session.checkpoint.list", self._session_checkpoint_list_handler)
        server.register("session.checkpoint.restore", self._session_checkpoint_restore_handler)
        server.register("session.set_auto_mode", self._session_set_auto_mode_handler)
        server.register("session.set_permission_mode", self._session_set_permission_mode_handler)
        server.register("session.set_effort_level", self._session_set_effort_level_handler)
        server.register("session.set_model", self._session_set_model_handler)
        server.register("session.set_engine", self._session_set_engine_handler)
        server.register("session.list", self._session_list_handler)
        server.register("session.rename", self._session_rename_handler)
        server.register("session.engine_info", self._session_engine_info_handler)
        server.register("run.cancel", self._run_cancel_handler)
        server.register("run.steer", self._run_steer_handler)

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

        # 收尸后台子 Agent：只 cancel 不 await 的话，事件循环关闭时
        # 未回收任务会以 "Task was destroyed but it is pending" 收场
        registry = getattr(self, "_subagent_registry", None)
        if registry is not None:
            n_cancelled = await registry.shutdown()
            if n_cancelled:
                logger.info(
                    "subagent registry: cancelled %d background task(s) on shutdown",
                    n_cancelled,
                )
        
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
