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
    GitBranchesCommand,             # git 分支列表命令
    GitBranchesResult,              # git 分支列表结果
    GitCheckoutCommand,             # git 切分支命令
    GitCommitCommand,               # git 提交命令
    GitLogCommand,                  # git 提交历史命令
    GitLogResult,                   # git 提交历史结果
    GitOpResult,                    # git 写操作统一结果
    GitPathsCommand,                # git 按路径操作命令（stage/unstage/discard）
    GitPullCommand,                 # git 拉取命令
    GitPushCommand,                 # git 推送命令
    GitStatusCommand,               # git 状态查询命令
    GitStatusResult,                # git 状态查询结果
    McpServerStatus,                # MCP 服务器运行时状态行
    McpStatusCommand,               # MCP 状态查询命令
    McpStatusResult,                # MCP 状态查询结果
    PermissionRespondCommand,       # 权限响应命令
    PermissionRespondResult,        # 权限响应结果
    PongResult,                     # Ping 响应
    PrContextCommand,               # PR 上下文命令（本地 git 解析）
    PrContextResult,                # PR 上下文结果
    PrCreateCommand,                # PR 创建命令（push + 建 PR）
    PrCreateResult,                 # PR 创建结果
    PrListCommand,                  # PR 列表命令
    PrListResult,                   # PR 列表结果
    PrReviewCommand,                # PR 评审命令（拉 diff + 起评审会话）
    PrReviewResult,                 # PR 评审结果
    RunCancelCommand,               # 取消运行命令
    RunCancelResult,                # 取消运行结果
    RunSteerCommand,                # 运行中修正命令
    RunSteerResult,                 # 运行中修正结果
    ScheduleCreateCommand,          # 定时任务创建命令
    ScheduleDeleteCommand,          # 定时任务删除命令
    ScheduleListCommand,            # 定时任务列表命令
    ScheduleListResult,             # 定时任务列表结果
    ScheduleOpResult,               # 定时任务写操作统一结果
    ScheduleRunNowCommand,          # 定时任务立即执行命令
    ScheduleTaskInfo,               # 定时任务行
    ScheduleUpdateCommand,          # 定时任务更新命令
    SpeechTranscribeCommand,        # 语音转写命令
    SpeechTranscribeResult,         # 语音转写结果
    SshConnAddCommand,              # SSH 连接新增命令
    SshConnDeleteCommand,           # SSH 连接删除命令
    SshConnInfo,                    # SSH 连接行
    SshConnListCommand,             # SSH 连接列表命令
    SshConnListResult,              # SSH 连接列表结果
    SshConnOpResult,                # SSH 连接写操作统一结果
    SshConnUpdateCommand,           # SSH 连接更新命令
    SshHostTrustCommand,            # SSH 主机信任命令
    SshKeyGenerateCommand,          # SSH 密钥生成命令
    SshKeyOpResult,                 # SSH 密钥操作结果
    SshKeyStatusCommand,            # SSH 密钥状态查询命令
    SshKeyStatusResult,             # SSH 密钥状态结果
    SshTermCloseCommand,            # SSH 终端关闭会话命令
    SshTermOpenCommand,             # SSH 终端开会话命令
    SshTermOpenResult,              # SSH 终端开会话结果
    SshTermOpResult,                # SSH 终端操作统一结果
    SshTermResizeCommand,           # SSH 终端尺寸调整命令
    SshTermWriteCommand,            # SSH 终端输入命令
    SshTrustResult,                 # SSH 主机信任结果
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
    WorkflowCancelCommand,          # 工作流取消命令
    WorkflowDeleteCommand,          # 工作流删除命令
    WorkflowGetCommand,             # 工作流详情命令
    WorkflowGetResult,              # 工作流详情结果
    WorkflowInfo,                   # 工作流定义行
    WorkflowListCommand,            # 工作流列表命令
    WorkflowListResult,             # 工作流列表结果
    WorkflowNodeRunInfo,            # 工作流节点运行态行
    WorkflowOpResult,               # 工作流写操作统一结果
    WorkflowRunCommand,             # 工作流运行命令
    WorkflowRunInfo,                # 工作流运行行
    WorkflowRunsCommand,            # 工作流运行历史命令
    WorkflowRunsResult,             # 工作流运行历史结果
    WorkflowSaveCommand,            # 工作流保存命令
)
from iwan_claude.core.bus.envelope import EventPushEnvelope  # 事件推送封装

# 导入核心组件
from iwan_claude.core.config import (
    IwanConfig,
    get_config,
    resolve_checkpoint_db_path,                            # checkpoint DB 路径解析（锚定会话根）
    resolve_sessions_root,                                 # 会话根解析（工作流 run 子目录锚定）
)   # 配置
from iwan_claude.core import gitpanel as core_git  # noqa: E402  本地 git 面板支撑（status/stage/commit/分支/log/pull/push）
from iwan_claude.core import pr as core_pr  # noqa: E402  PR 面板支撑（git 解析 + GitHub API）
from iwan_claude.core.events.bus import EventBus             # 事件总线
from iwan_claude.core.llm import create_provider_from_config # LLM 提供者创建
from iwan_claude.core.logging_setup import setup_logging     # 日志初始化
from iwan_claude.core.mcp.server import McpServerManager     # MCP 服务器管理
from iwan_claude.core.permissions.manager import PermissionManager  # 权限管理
from iwan_claude.core.permissions.storage import load_policy_file   # 加载权限策略
from iwan_claude.core.schedule import ScheduleStore, Scheduler  # noqa: E402  定时任务（存储 + 调度循环）
from iwan_claude.core.speech import SpeechTranscriber  # noqa: E402  本地语音转写器（V1）
from iwan_claude.core.ssh import keys as ssh_keys  # noqa: E402  SSH 密钥与主机信任（M4a）
from iwan_claude.core.ssh.connections import SshConnStore  # noqa: E402  SSH 连接表存储（M4a）
from iwan_claude.core.ssh.session import SshSessionManager  # noqa: E402  SSH 终端会话池（M4c）
from iwan_claude.core.subagent.tool import SpawnAgentTool  # noqa: E402  工作流节点=后台子 Agent（W1）
from iwan_claude.core.workflow.engine import (  # noqa: E402  工作流引擎装配（W1）
    NodeLaunchCtx,
    NodeTransition,
    RunTransition,
    WorkflowEngine,
)
from iwan_claude.core.workflow.store import WorkflowStore  # noqa: E402  工作流定义/运行存储（W1）
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
        # M2 定时任务：存储 + 调度器（run() 里创建并启动；None=未启动）
        self._schedule_store: ScheduleStore | None = None
        self._scheduler: Scheduler | None = None
        # 最近一次触发的 (run_id, session_id) 映射：schedule.fired 事件补字段用
        self._sched_last_fire: dict[str, tuple[str, str]] = {}
        # ④ PR 自动评审：已处理过的 PR 号（手动评审与轮询共享，防重复烧 LLM）
        # + 首轮 seed 标记（上线即囤：存量 open PR 登记但不评审，只跟新号）
        # + 轮询任务句柄（config.github.auto_review=False 时永远是 None）
        self._pr_seen: set[int] = set()
        self._pr_seen_seeded: bool = False
        self._pr_poller_task: asyncio.Task[None] | None = None
        # M4a SSH 连接库（run() 里创建；懒载入，handler 访问时才读盘）
        self._ssh_store: SshConnStore | None = None
        # M4c SSH 终端会话池（run() 里创建；发射器绑 bus）
        self._ssh_mgr: SshSessionManager | None = None
        # W1 工作流：定义/运行存储 + 运行引擎（run() 里创建并清算孤儿；None=未启动）
        self._workflow_store: WorkflowStore | None = None
        self._workflow_engine: WorkflowEngine | None = None
        # V1 语音转写器（run() 里创建；模型本身懒加载，这里只是挂个空壳）
        self._speech: SpeechTranscriber | None = None
        # run 级懒建缓存：workflow run_id → SpawnAgentTool / one_shot 会话 id；收尾时清空
        self._wf_tools: dict[str, SpawnAgentTool] = {}
        self._wf_sessions: dict[str, str] = {}
        # 懒建会话的互斥锁：同层兄弟并发时防止双开会话（见 _workflow_launch）
        self._wf_sess_lock = asyncio.Lock()

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

    # ==================== M2 功能面：MCP 实况 / Pull Request / 定时任务 ====================
    # 【设计】以下 handler 全部是"薄壳"：参数校验 → 调 core_pr / ScheduleStore / Scheduler，
    # 结果模型 model_validate 收口。业务逻辑放模块里才有独立单测的可能，
    # app.py 只负责把 RPC 线翻译成函数调用。

    # 查询各 MCP 服务器运行时实况（纯内存快照，不发探测包）
    async def _mcp_status_handler(self, params: dict[str, Any]) -> McpStatusResult:
        McpStatusCommand.model_validate(params)
        assert self._mcp_manager is not None
        rows = self._mcp_manager.status()
        return McpStatusResult(servers=[McpServerStatus.model_validate(r) for r in rows])

    # 解析本地 git/GitHub 坐标（pr.context：owner/repo/branch/ahead，无网络）
    async def _pr_context_handler(self, params: dict[str, Any]) -> PrContextResult:
        cmd = PrContextCommand.model_validate(params)
        data = await core_pr.collect_context(cmd.cwd or os.getcwd())
        return PrContextResult.model_validate(data)

    # 拉 GitHub PR 列表（pr.list：解析坐标 → httpx 请求；无 token 时公开库只读）
    async def _pr_list_handler(self, params: dict[str, Any]) -> PrListResult:
        cmd = PrListCommand.model_validate(params)
        assert self._config is not None
        data = await core_pr.list_pulls_for_cwd(
            cmd.cwd or os.getcwd(), cmd.state, cmd.page,
            self._config.github.base_url, self._config.github.token,
        )
        return PrListResult.model_validate(data)

    # 创建 PR（pr.create：push + POST）——改变共享状态的动作，GUI 必须先二次确认；此处落审计日志
    async def _pr_create_handler(self, params: dict[str, Any]) -> PrCreateResult:
        cmd = PrCreateCommand.model_validate(params)
        assert self._config is not None
        logger.info(
            "audit pr.create: cwd=%s title=%r base=%r head=%r draft=%s",
            cmd.cwd, cmd.title, cmd.base, cmd.head, cmd.draft,
        )
        data = await core_pr.create_pull_for_cwd(
            cmd.cwd or os.getcwd(), cmd.title, cmd.body, cmd.base, cmd.head, cmd.draft,
            self._config.github.base_url, self._config.github.token,
        )
        return PrCreateResult.model_validate(data)

    # 【学习要点】_start_pr_review 是"一键评审"与"后台轮询评审"的唯一动会话点：
    # 两条触发路径（RPC 按钮 / 轮询新号）在这里汇流，pr.reviewed 事件也只在这一
    # 处广播——成败都发（对齐 schedule.fired 的"后台干活必留打卡"先例），GUI 订阅
    # 一个事件就能看到全部评审动作，不用分别监听 RPC 回包和轮询日志。
    # 失败不删 seen 登记（调用方 pr_new_numbers 已登记）：网络失败的 PR 不自动
    # 重试，宁可用户手动再点，也不让轮询变成烧 token 的重试机器。
    async def _start_pr_review(
        self, cwd: str, number: int,
    ) -> tuple[bool, str, str, str]:
        """发起一次 PR 评审会话，返回 (ok, session_id, 标题, 错误文案)"""
        from iwan_claude.core.bus.events import PrReviewedEvent
        assert self._config is not None
        assert self._sessions is not None
        gh = self._config.github
        data = await core_pr.fetch_pr_for_review(cwd, number, gh.base_url, gh.token)
        title = str(data.get("title", ""))
        if not data.get("ok"):
            err = str(data.get("error", "")) or "拉取 PR 失败"
            await self._bus.publish(PrReviewedEvent(
                pr_number=number, title=title, session_id="", ok=False, error=err, ts=_now(),
            ))
            return False, "", title, err
        goal = (
            f"请评审 GitHub PR #{number}（标题：{title}）。以下是 PR 描述与完整 diff。\n\n"
            f"【PR 描述】\n{str(data.get('body', ''))[:2000]}\n\n"
            f"【diff】\n{data['diff']}\n\n"
            "评审要求：这是只读评审，不要修改任何文件；"
            "按 🔴严重 / 🟡建议 / 🟢可选 三级输出意见，每条注明涉及文件与位置。"
        )
        try:
            session = await self._sessions.create(
                mode="one_shot", title=f"评审·PR#{number} {title}"[:40], cwd=cwd,
            )
        except Exception as e:
            err = f"创建评审会话失败：{type(e).__name__}: {e}"
            await self._bus.publish(PrReviewedEvent(
                pr_number=number, title=title, session_id="", ok=False, error=err, ts=_now(),
            ))
            return False, "", title, err
        # 即发即返：评审内容在会话里慢慢跑，RPC 不等 LLM（_schedule_fire 同套路）
        run_task = asyncio.create_task(
            self._sessions.send_message(session.id, goal, run_id=new_run_id())
        )
        self._running_runs.add(run_task)
        run_task.add_done_callback(self._running_runs.discard)
        self._pr_seen.add(number)  # 手动评审也登记：轮询器下轮不再重复评审同号
        await self._bus.publish(PrReviewedEvent(
            pr_number=number, title=title, session_id=session.id, ok=True, error="", ts=_now(),
        ))
        return True, session.id, title, ""

    # pr.review RPC：GUI「评审」按钮的一键入口；发起即烧 LLM，属授权动作，落审计
    async def _pr_review_handler(self, params: dict[str, Any]) -> PrReviewResult:
        cmd = PrReviewCommand.model_validate(params)
        cwd = cmd.cwd or os.getcwd()
        logger.info("audit pr.review: cwd=%s pr_number=%s", cwd, cmd.pr_number)
        ok, sid, _title, err = await self._start_pr_review(cwd, cmd.pr_number)
        return PrReviewResult(ok=ok, session_id=sid, error=err)

    # PR 自动评审轮询（config [github] auto_review opt-in）：周期拉 open 列表，
    # 新号走 _start_pr_review。首轮只 seed 不评审——daemon 重启不该把存量
    # 历史 PR 全部回灌成评审会话；一切异常 log 后等下轮，轮询器永不因失败退出
    async def _pr_review_poller(self) -> None:
        assert self._config is not None
        gh = self._config.github
        interval = max(60, gh.auto_review_interval_min * 60)
        cwd = gh.auto_review_cwd or os.getcwd()
        while True:
            await asyncio.sleep(interval)
            try:
                data = await core_pr.list_pulls_for_cwd(
                    cwd, "open", 1, gh.base_url, gh.token,
                )
                if not data["ok"]:
                    logger.warning("pr 自动评审：拉取列表失败（%s），下轮重试", data["error"])
                    continue
                rows = data["pulls"]
                if not self._pr_seen_seeded:
                    core_pr.pr_new_numbers(rows, self._pr_seen)
                    self._pr_seen_seeded = True
                    logger.info("pr 自动评审：已囤积 %d 个存量 PR，只跟新号", len(self._pr_seen))
                    continue
                for number in core_pr.pr_new_numbers(rows, self._pr_seen):
                    ok, _sid, _title, err = await self._start_pr_review(cwd, number)
                    logger.info(
                        "pr 自动评审：#%d %s", number, "已起评审会话" if ok else f"失败：{err}",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("pr 自动评审：轮询异常 %s: %s", type(e).__name__, e)

    # 定时任务列表（schedule.list：直读存储，存储层每次变更已落盘）
    async def _schedule_list_handler(self, params: dict[str, Any]) -> ScheduleListResult:
        ScheduleListCommand.model_validate(params)
        assert self._schedule_store is not None
        rows = self._schedule_store.list()
        return ScheduleListResult(tasks=[ScheduleTaskInfo.model_validate(t) for t in rows])

    # 新建定时任务（schedule.create：kind/spec 服务端校验，成功即排期写盘）
    async def _schedule_create_handler(self, params: dict[str, Any]) -> ScheduleOpResult:
        cmd = ScheduleCreateCommand.model_validate(params)
        assert self._schedule_store is not None
        try:
            row = self._schedule_store.create(cmd.name, cmd.cwd, cmd.prompt, cmd.kind, cmd.spec)
        except ValueError as e:
            return ScheduleOpResult(ok=False, error=str(e))
        return ScheduleOpResult(ok=True, id=str(row["id"]))

    # 更新定时任务（schedule.update：None 字段不动；改节奏/重新启用会重排 next_due）
    async def _schedule_update_handler(self, params: dict[str, Any]) -> ScheduleOpResult:
        cmd = ScheduleUpdateCommand.model_validate(params)
        assert self._schedule_store is not None
        fields: dict[str, Any] = {}
        for k in ("name", "cwd", "prompt", "kind", "spec", "enabled"):
            v = getattr(cmd, k)
            if v is not None:
                fields[k] = v
        try:
            row = self._schedule_store.update(cmd.id, fields)
        except ValueError as e:
            return ScheduleOpResult(ok=False, error=str(e))
        if row is None:
            return ScheduleOpResult(ok=False, error=f"任务不存在：{cmd.id}")
        return ScheduleOpResult(ok=True, id=cmd.id)

    # 删除定时任务（schedule.delete：未命中返回 ok=False，幂等）
    async def _schedule_delete_handler(self, params: dict[str, Any]) -> ScheduleOpResult:
        cmd = ScheduleDeleteCommand.model_validate(params)
        assert self._schedule_store is not None
        if not self._schedule_store.delete(cmd.id):
            return ScheduleOpResult(ok=False, error=f"任务不存在：{cmd.id}")
        return ScheduleOpResult(ok=True, id=cmd.id)

    # 立即手动触发一次（schedule.run_now：与到点触发同路径，但不动排期与记账）
    async def _schedule_run_now_handler(self, params: dict[str, Any]) -> ScheduleOpResult:
        cmd = ScheduleRunNowCommand.model_validate(params)
        assert self._scheduler is not None
        res = await self._scheduler.run_now(cmd.id)
        return ScheduleOpResult(
            ok=bool(res["ok"]), id=cmd.id, run_id=str(res["run_id"]), error=str(res["error"]),
        )

    # ==================== M3 功能面：本地 Git 面板 ====================
    # 【设计】与 M2 同构的薄壳：参数校验 → core_git 编排 → model_validate 收口。
    # 会改仓库/碰网络的动作（discard/commit/checkout/pull/push）全部落审计日志——
    # 本地 git 没有 GitHub 那样的服务端历史可查，daemon 日志就是唯一的事后线索。

    # 查询工作区状态（git.status：分支 + ahead/behind + 文件三态表，无网络）
    async def _git_status_handler(self, params: dict[str, Any]) -> GitStatusResult:
        cmd = GitStatusCommand.model_validate(params)
        data = await core_git.status(cmd.cwd)
        return GitStatusResult.model_validate(data)

    # 查询本地分支表（git.branches：当前分支置顶，无网络）
    async def _git_branches_handler(self, params: dict[str, Any]) -> GitBranchesResult:
        cmd = GitBranchesCommand.model_validate(params)
        data = await core_git.branches(cmd.cwd)
        return GitBranchesResult.model_validate(data)

    # 查询提交历史（git.log：limit 由服务端钳位，无网络）
    async def _git_log_handler(self, params: dict[str, Any]) -> GitLogResult:
        cmd = GitLogCommand.model_validate(params)
        data = await core_git.log(cmd.cwd, cmd.limit)
        return GitLogResult.model_validate(data)

    # 暂存/退暂存（git.stage / git.unstage：paths 白名单进 git add / reset HEAD）
    # GitPathsCommand 的 type 无默认值（一类三判别值，任何默认都可能选错动作），
    # 由注册方法名注入——方法名是权威，模型只做形状收口
    async def _git_stage_handler(self, params: dict[str, Any]) -> GitOpResult:
        return await self._git_paths_op(params, "git.stage")

    # 退暂存（git.unstage：与 stage 同壳不同 kind）
    async def _git_unstage_handler(self, params: dict[str, Any]) -> GitOpResult:
        return await self._git_paths_op(params, "git.unstage")

    # 按路径三操作共用的收口壳：注入 type → 校验 → 分派 core_git 对应编排
    async def _git_paths_op(self, params: dict[str, Any], kind: str) -> GitOpResult:
        cmd = GitPathsCommand.model_validate({**params, "type": kind})
        fn = {"git.stage": core_git.stage, "git.unstage": core_git.unstage}[kind]
        data = await fn(cmd.cwd, cmd.paths)
        return GitOpResult.model_validate(data)

    # 丢弃工作区改动（git.discard：破坏性，GUI 双确认兜底，这里落审计含路径清单）
    async def _git_discard_handler(self, params: dict[str, Any]) -> GitOpResult:
        cmd = GitPathsCommand.model_validate({**params, "type": "git.discard"})
        logger.info("audit git.discard: cwd=%s paths=%r", cmd.cwd, cmd.paths)
        data = await core_git.discard(cmd.cwd, cmd.paths)
        return GitOpResult.model_validate(data)

    # 提交暂存区（git.commit：message 原文入审计——提交信息本身就是最好的日志载荷）
    async def _git_commit_handler(self, params: dict[str, Any]) -> GitOpResult:
        cmd = GitCommitCommand.model_validate(params)
        logger.info("audit git.commit: cwd=%s message=%r", cmd.cwd, cmd.message)
        data = await core_git.commit(cmd.cwd, cmd.message)
        return GitOpResult.model_validate(data)

    # 切换分支（git.checkout：脏树被 git 拒时 error 原文回显，不预检不加工）
    async def _git_checkout_handler(self, params: dict[str, Any]) -> GitOpResult:
        cmd = GitCheckoutCommand.model_validate(params)
        logger.info("audit git.checkout: cwd=%s name=%r", cmd.cwd, cmd.name)
        data = await core_git.checkout(cmd.cwd, cmd.name)
        return GitOpResult.model_validate(data)

    # 拉取（git.pull：--ff-only 确定性策略；网络动作，120s 超时在编排层兜着）
    async def _git_pull_handler(self, params: dict[str, Any]) -> GitOpResult:
        cmd = GitPullCommand.model_validate(params)
        logger.info("audit git.pull: cwd=%s", cmd.cwd)
        data = await core_git.pull(cmd.cwd)
        return GitOpResult.model_validate(data)

    # 推送（git.push：-u origin HEAD；与 pr.create 的推送同级审计）
    async def _git_push_handler(self, params: dict[str, Any]) -> GitOpResult:
        cmd = GitPushCommand.model_validate(params)
        logger.info("audit git.push: cwd=%s", cmd.cwd)
        data = await core_git.push(cmd.cwd)
        return GitOpResult.model_validate(data)

    # ==================== M4a 功能面：SSH 连接库与密钥 ====================
    # 【设计】连接表校验失败走 ValueError → ok=False 文案（业务性拒绝不是
    # 协议错误，不该让 GUI 去 catch JSON-RPC 异常）；改动连接登记、生成密钥、
    # 信任主机全部落审计——这三类都是"以后连不上要找原因"的高频嫌疑人。

    # 列出全部 SSH 连接（ssh.conn_list：读缓存表，按名称排序）
    async def _ssh_conn_list_handler(self, params: dict[str, Any]) -> SshConnListResult:
        assert self._ssh_store is not None
        SshConnListCommand.model_validate(params)
        return SshConnListResult(connections=[
            SshConnInfo.model_validate(c) for c in self._ssh_store.list()
        ])

    # 新建连接（ssh.conn_add：服务端校验 + 查重；不做连通性探测）
    async def _ssh_conn_add_handler(self, params: dict[str, Any]) -> SshConnOpResult:
        assert self._ssh_store is not None
        cmd = SshConnAddCommand.model_validate(params)
        try:
            row = self._ssh_store.add(cmd.name, cmd.host, cmd.user, cmd.port, cmd.key_file)
        except ValueError as e:
            return SshConnOpResult(ok=False, error=str(e))
        logger.info("audit ssh.conn_add: id=%s name=%r host=%s user=%s port=%d",
                    row["id"], row["name"], row["host"], row["user"], row["port"])
        return SshConnOpResult(id=str(row["id"]))

    # 更新连接（ssh.conn_update：None 字段不动；未知 id → ok=False）
    async def _ssh_conn_update_handler(self, params: dict[str, Any]) -> SshConnOpResult:
        assert self._ssh_store is not None
        cmd = SshConnUpdateCommand.model_validate(params)
        try:
            row = self._ssh_store.update(cmd.id, {
                "name": cmd.name, "host": cmd.host, "user": cmd.user,
                "port": cmd.port, "key_file": cmd.key_file,
            })
        except ValueError as e:
            return SshConnOpResult(ok=False, id=cmd.id, error=str(e))
        if row is None:
            return SshConnOpResult(ok=False, id=cmd.id, error=f"连接不存在：{cmd.id}")
        logger.info("audit ssh.conn_update: id=%s", cmd.id)
        return SshConnOpResult(id=cmd.id)

    # 删除连接（ssh.conn_delete：只删登记，不碰密钥与 known_hosts）
    async def _ssh_conn_delete_handler(self, params: dict[str, Any]) -> SshConnOpResult:
        assert self._ssh_store is not None
        cmd = SshConnDeleteCommand.model_validate(params)
        if not self._ssh_store.delete(cmd.id):
            return SshConnOpResult(ok=False, id=cmd.id, error=f"连接不存在：{cmd.id}")
        logger.info("audit ssh.conn_delete: id=%s", cmd.id)
        return SshConnOpResult(id=cmd.id)

    # 查询本机密钥现状（ssh.key_status：纯本地检查，无网络）
    async def _ssh_key_status_handler(self, params: dict[str, Any]) -> SshKeyStatusResult:
        SshKeyStatusCommand.model_validate(params)
        data = await ssh_keys.key_status()
        return SshKeyStatusResult.model_validate(data)

    # 生成密钥对（ssh.key_generate：已存在拒绝不覆盖；失败原因原文回显）
    async def _ssh_key_generate_handler(self, params: dict[str, Any]) -> SshKeyOpResult:
        SshKeyGenerateCommand.model_validate(params)
        data = await ssh_keys.key_generate()
        if data["ok"]:
            logger.info("audit ssh.key_generate: fingerprint=%s", data["fingerprint"])
        return SshKeyOpResult.model_validate(data)

    # 信任主机（ssh.host_trust：keyscan + 指纹回显；追加 known_hosts 落审计）
    async def _ssh_host_trust_handler(self, params: dict[str, Any]) -> SshTrustResult:
        cmd = SshHostTrustCommand.model_validate(params)
        logger.info("audit ssh.host_trust: host=%s port=%d", cmd.host, cmd.port)
        data = await ssh_keys.host_trust(cmd.host, cmd.port)
        return SshTrustResult.model_validate(data)

    # ==================== M4c 功能面：SSH 终端会话 ====================
    # 【设计】终端 RPC 是"会话寻址"的一组：open 返回 session_id，之后
    # write/resize/close 都以它为键。会话不存在/已死的错误统一文案——
    # GUI 拿 ok=False 就标页签断开，不需要区分"从来不存在"和"刚死"。

    # 开终端会话（ssh.term_open：目的地锁连接库 id；审计记录目标主机）
    async def _ssh_term_open_handler(self, params: dict[str, Any]) -> SshTermOpenResult:
        assert self._ssh_mgr is not None and self._ssh_store is not None
        cmd = SshTermOpenCommand.model_validate(params)
        conn = self._ssh_store.get(cmd.conn_id)
        if conn is None:
            return SshTermOpenResult(ok=False, error=f"未知 SSH 连接：{cmd.conn_id}")
        sid, err = await self._ssh_mgr.open(conn, cmd.rows, cmd.cols)
        if err:
            return SshTermOpenResult(ok=False, error=err)
        logger.info("audit ssh.term_open: sid=%s conn=%s host=%s %dx%d",
                    sid, cmd.conn_id, conn.get("host"), cmd.cols, cmd.rows)
        return SshTermOpenResult(session_id=sid)

    # 终端输入（ssh.term_write：b64 解码后原样进远端 pty）
    async def _ssh_term_write_handler(self, params: dict[str, Any]) -> SshTermOpResult:
        assert self._ssh_mgr is not None
        cmd = SshTermWriteCommand.model_validate(params)
        import base64 as _b64
        try:
            data = _b64.b64decode(cmd.data_b64, validate=True)
        except Exception:
            return SshTermOpResult(ok=False, error="data_b64 不是合法 base64")
        s = self._ssh_mgr.get(cmd.session_id)
        if s is None:
            return SshTermOpResult(ok=False, error="会话不存在或已结束")
        err = await s.write(data)
        return SshTermOpResult(ok=err is None, error=err or "")

    # 尺寸调整（ssh.term_resize：v1 只确认收到，不向远端传播——协议占位）
    async def _ssh_term_resize_handler(self, params: dict[str, Any]) -> SshTermOpResult:
        assert self._ssh_mgr is not None
        cmd = SshTermResizeCommand.model_validate(params)
        if self._ssh_mgr.get(cmd.session_id) is None:
            return SshTermOpResult(ok=False, error="会话不存在或已结束")
        return SshTermOpResult()

    # 关闭会话（ssh.term_close：用户主动关页签；幂等）
    async def _ssh_term_close_handler(self, params: dict[str, Any]) -> SshTermOpResult:
        assert self._ssh_mgr is not None
        cmd = SshTermCloseCommand.model_validate(params)
        if not await self._ssh_mgr.close(cmd.session_id, "用户关闭"):
            return SshTermOpResult(ok=False, error="会话不存在或已结束")
        logger.info("audit ssh.term_close: sid=%s", cmd.session_id)
        return SshTermOpResult()

    # ==================== W1 功能面：工作流（DAG 编排） ====================
    # 【设计】与 M2/M3 同构的薄壳：参数校验 → WorkflowStore/WorkflowEngine →
    # 结果模型收口。唯一的适配点是 store 的 nodes 记账用哈希表（就地更新 O(1)）、
    # 线上传输用数组（GUI 直接 map 渲染），_wf_run_wire 是两种形态的翻译官。

    # store run 行（nodes 为 dict）→ 线上模型 WorkflowRunInfo（nodes 为 list）
    @staticmethod
    def _wf_run_wire(row: dict[str, Any]) -> WorkflowRunInfo:
        data = dict(row)
        nodes = data.pop("nodes", None) or {}
        data["nodes"] = [
            WorkflowNodeRunInfo.model_validate({"node": k, **v}) for k, v in nodes.items()
        ]
        return WorkflowRunInfo.model_validate(data)

    # 工作流定义列表（workflow.list：定义行自带服务端现算的 layers 分层）
    async def _workflow_list_handler(self, params: dict[str, Any]) -> WorkflowListResult:
        WorkflowListCommand.model_validate(params)
        assert self._workflow_store is not None
        rows = self._workflow_store.list_defs()
        return WorkflowListResult(workflows=[WorkflowInfo.model_validate(r) for r in rows])

    # 工作流详情（workflow.get：定义+最近 20 次运行一次拉齐，详情页免往返）
    async def _workflow_get_handler(self, params: dict[str, Any]) -> WorkflowGetResult:
        cmd = WorkflowGetCommand.model_validate(params)
        assert self._workflow_store is not None
        row = self._workflow_store.get(cmd.id)
        if row is None:
            return WorkflowGetResult(ok=False, error=f"工作流不存在：{cmd.id}")
        runs = self._workflow_store.list_runs(cmd.id, 20)
        return WorkflowGetResult(
            workflow=WorkflowInfo.model_validate(row),
            runs=[self._wf_run_wire(r) for r in runs],
        )

    # 保存工作流（workflow.save：id 空新建/非空整图覆盖；坏图 ok=False 绝不落盘）
    async def _workflow_save_handler(self, params: dict[str, Any]) -> WorkflowOpResult:
        cmd = WorkflowSaveCommand.model_validate(params)
        assert self._workflow_store is not None
        try:
            row = self._workflow_store.upsert(
                cmd.id, cmd.name, cmd.description, [t.model_dump() for t in cmd.tasks],
            )
        except ValueError as e:
            return WorkflowOpResult(ok=False, error=str(e))
        wf_id = str(row["id"])
        logger.info(
            "audit workflow.save: id=%s name=%r nodes=%d", wf_id, row["name"], len(cmd.tasks),
        )
        return WorkflowOpResult(ok=True, id=wf_id)

    # 删除工作流定义（workflow.delete：运行历史保留不级联；未命中 ok=False，幂等）
    async def _workflow_delete_handler(self, params: dict[str, Any]) -> WorkflowOpResult:
        cmd = WorkflowDeleteCommand.model_validate(params)
        assert self._workflow_store is not None
        if not self._workflow_store.delete(cmd.id):
            return WorkflowOpResult(ok=False, error=f"工作流不存在：{cmd.id}")
        logger.info("audit workflow.delete: id=%s", cmd.id)
        return WorkflowOpResult(ok=True, id=cmd.id)

    # 启动一次运行（workflow.run：GUI 显式点击即授权，同 schedule.run_now；即发即返）
    async def _workflow_run_handler(self, params: dict[str, Any]) -> WorkflowOpResult:
        cmd = WorkflowRunCommand.model_validate(params)
        assert self._workflow_engine is not None
        run_id, err = self._workflow_engine.start_run(cmd.id)
        if err:
            return WorkflowOpResult(ok=False, id=cmd.id, error=err)
        logger.info("audit workflow.run: id=%s run_id=%s", cmd.id, run_id)
        return WorkflowOpResult(ok=True, id=cmd.id, run_id=run_id)

    # 取消进行中的运行（workflow.cancel：显式点击=授权，同 workflow.run；id=工作流 id，
    # 与 workflow.run 同口径经 active_run_for 解析——坐等到 cancelled 终态才回包，
    # GUI 拿到 ok 时事件必已广播完，不必再靠轮询对账）
    async def _workflow_cancel_handler(self, params: dict[str, Any]) -> WorkflowOpResult:
        cmd = WorkflowCancelCommand.model_validate(params)
        assert self._workflow_engine is not None
        run_id = self._workflow_engine.active_run_for(cmd.id)
        if not run_id:
            return WorkflowOpResult(ok=False, id=cmd.id, error="该工作流没有进行中的运行，无需取消")
        err = await self._workflow_engine.cancel_run(run_id)
        if err:
            return WorkflowOpResult(ok=False, id=cmd.id, error=err)
        logger.info("audit workflow.cancel: id=%s run_id=%s", cmd.id, run_id)
        return WorkflowOpResult(ok=True, id=cmd.id, run_id=run_id)

    # 运行历史（workflow.runs：id 空=全部工作流混排，新→旧）
    async def _workflow_runs_handler(self, params: dict[str, Any]) -> WorkflowRunsResult:
        cmd = WorkflowRunsCommand.model_validate(params)
        assert self._workflow_store is not None
        rows = self._workflow_store.list_runs(cmd.id, cmd.limit)
        return WorkflowRunsResult(runs=[self._wf_run_wire(r) for r in rows])

    # 语音转文字（speech.transcribe）：模型加载+推理全在 worker 线程，循环不停摆
    async def _speech_transcribe_handler(self, params: dict[str, Any]) -> SpeechTranscribeResult:
        cmd = SpeechTranscribeCommand.model_validate(params)
        assert self._speech is not None
        try:
            # to_thread 的动机：base 模型 int8 推理一段语音要数秒~十几秒，压在主
            # 循环上会卡死同期所有审批卡/心跳/广播——线程可以牺牲，循环是大家的
            text = await asyncio.to_thread(
                self._speech.transcribe_sync, cmd.audio_b64, cmd.sample_rate
            )
        except (ValueError, RuntimeError) as e:
            return SpeechTranscribeResult(ok=False, error=str(e))
        return SpeechTranscribeResult(ok=True, text=text)

    # 引擎 launch 回调：懒建本 run 共享的 one_shot 会话与 SpawnAgentTool，后台启动节点子 Agent
    async def _workflow_launch(self, ctx: NodeLaunchCtx) -> tuple[str, str]:
        assert self._sessions is not None and self._config is not None
        assert self._workflow_store is not None and self._subagent_registry is not None
        run_row = self._workflow_store.get_run(ctx.run_id)
        if run_row is None:
            return "", "运行记录不存在（可能已被裁剪）"
        # 【学习要点】双检锁：同层兄弟节点并发 launch，都不持锁先查表 →
        # 双双看到会话还没建 → 开出两条 one_shot 会话，第二次 attach 覆盖
        # 第一条，落在孤儿会话里的审批卡没人能批（黑洞卡=节点挂死到超时）。
        # 一把全局锁就够：贵的是 create()，每个 run 只发生一次，不必按 run 分锁。
        session_id = self._wf_sessions.get(ctx.run_id, "")
        if not session_id:
            async with self._wf_sess_lock:
                session_id = self._wf_sessions.get(ctx.run_id, "")
                if not session_id:
                    try:
                        session = await self._sessions.create(
                            mode="one_shot",
                            title=f"工作流·{run_row.get('workflow_name', '')}"[:40],
                            cwd=os.getcwd(),
                        )
                    except Exception as e:
                        return "", f"创建会话失败：{type(e).__name__}: {e}"
                    session_id = session.id
                    self._wf_sessions[ctx.run_id] = session_id
                    self._workflow_store.attach_session(ctx.run_id, session_id)
        tool = self._wf_tools.get(ctx.run_id)
        if tool is None:
            # 构造参数照抄 runner.py 的子 Agent 注册处；parent_run_id 用工作流 run id，
            # 子 Agent 的 started/finished 事件挂在它下面，审计链不断
            tool = SpawnAgentTool(
                provider=create_provider_from_config(self._config.llm),
                parent_bus=self._bus,
                parent_run_id=ctx.run_id,
                permission_manager=self._permission_manager,
                max_steps=self._config.agent.max_steps,
                task_registry=self._subagent_registry,
                runs_dir=resolve_sessions_root().parent / "workflow_runs",
                session_id=session_id,
                llm_model_name=self._config.llm.default_model,
                depth=0,
            )
            self._wf_tools[ctx.run_id] = tool
        child, err = await tool.spawn_background(
            description=ctx.node, prompt=ctx.prompt, subagent_type="",
            timeout_sec=0.0, gate=None,
        )
        if child is None:
            return "", str(err or "spawn failed")
        return child, ""

    # 引擎 await_child 回调：等注册表里的子任务收尾，读后台包装器写好的终态
    async def _workflow_await(self, child_run_id: str) -> tuple[bool, str]:
        assert self._subagent_registry is not None
        entry = self._subagent_registry.get(child_run_id)
        if entry is None:
            return False, f"子任务不存在或已被回收：{child_run_id}"
        task, context = entry
        try:
            await task
        except asyncio.CancelledError:
            # 【学习要点】分辨这个 CancelledError 是谁的（subagent/tool.py 批量
            # await 同款契约）：子 task 自己被 registry.cancel 杀掉且没人冲本协程
            # 来 → 收口成失败结果；冲本协程（引擎取消传导）来的必须原样上抛，
            # 否则 workflow.cancel 会被吸收成"节点执行失败"而非 cancelled 终态
            cur = asyncio.current_task()
            if task.cancelled() and (cur is None or cur.cancelling() == 0):
                return False, "子 Agent 被取消"
            raise
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        if context.status == "success":
            return True, context.result or "（子 Agent 无文本输出）"
        return False, context.reason or f"子 Agent 状态={context.status}"

    # 引擎 on_node 回调：节点状态上总线（GUI 详情页即时上色的唯一驱动源）
    async def _workflow_on_node(self, tr: NodeTransition) -> None:
        from iwan_claude.core.bus.events import WorkflowNodeEvent
        await self._bus.publish(WorkflowNodeEvent(
            run_id=tr.run_id, workflow_id=tr.workflow_id, node=tr.node,
            status=tr.status, child_run_id=tr.child_run_id, detail=tr.detail, ts=_now(),
        ))

    # 引擎 on_run_finished 回调：定义行记账 + run 级缓存清退 + 终局事件上总线
    async def _workflow_on_finished(self, tr: RunTransition) -> None:
        from iwan_claude.core.bus.events import WorkflowRunFinishedEvent
        assert self._workflow_store is not None
        self._workflow_store.touch_last_run(tr.workflow_id, tr.run_id, tr.status)
        self._wf_tools.pop(tr.run_id, None)
        self._wf_sessions.pop(tr.run_id, None)
        await self._bus.publish(WorkflowRunFinishedEvent(
            run_id=tr.run_id, workflow_id=tr.workflow_id, workflow_name=tr.workflow_name,
            session_id=tr.session_id, status=tr.status, error=tr.error,
            finished_at=tr.finished_at, ts=_now(),
        ))

    # 调度器的触发入口：复刻 agent.run 的即发即返路径，返回 "run_id|说明" 或 "|失败原因"
    async def _schedule_fire(self, task: dict[str, Any]) -> tuple[bool, str]:
        assert self._sessions is not None
        try:
            session = await self._sessions.create(
                mode="one_shot",
                title=f"定时·{task.get('name', '')}"[:40],
                cwd=str(task.get("cwd", "")),
            )
        except Exception as e:
            return False, f"|创建会话失败：{type(e).__name__}: {e}"
        run_id = new_run_id()
        run_task = asyncio.create_task(
            self._sessions.send_message(session.id, str(task.get("prompt", "")), run_id=run_id)
        )
        self._running_runs.add(run_task)
        run_task.add_done_callback(self._running_runs.discard)
        self._sched_last_fire[str(task.get("id", ""))] = (run_id, session.id)
        return True, f"{run_id}|已触发（会话 {session.id}）"

    # 调度器记账后的广播：发 schedule.fired，GUI 借此刷新任务表并发现新会话
    async def _schedule_notified(
        self, task: dict[str, Any], ok: bool, run_id: str, detail: str,
    ) -> None:
        from iwan_claude.core.bus.events import ScheduleFiredEvent
        sid = self._sched_last_fire.get(str(task.get("id", "")), ("", ""))[1]
        await self._bus.publish(
            ScheduleFiredEvent(
                task_id=str(task.get("id", "")), run_id=run_id, session_id=sid,
                ok=ok, detail=detail, ts=_now(),
            )
        )

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

        # ===== 初始化定时任务调度器（M2）=====
        # 存储落在会话根的父目录（~/.iwan/scheduled.json）：与信任/影子快照同一驻址
        self._schedule_store = ScheduleStore()
        self._scheduler = Scheduler(
            self._schedule_store,
            fire=self._schedule_fire,
            on_fired=self._schedule_notified,
        )
        # start() 内部先做"补跑跳过"（过期排期推到下一班）再挂 30s tick 循环；
        # 放在 server.start() 前：客户端一连上就能 schedule.list 到完整任务表
        await self._scheduler.start()

        # ===== PR 自动评审轮询（④ opt-in）=====
        # 默认关=零 token 消耗、零网络周期请求；显式打开 config 才算授权后台起会话。
        # 任务句柄存 self，关停路径里 cancel——不接受"起了就停不掉"的后台进程
        if self._config.github.auto_review:
            self._pr_poller_task = asyncio.create_task(self._pr_review_poller())
            logger.info(
                "pr 自动评审已启用：间隔 %d 分钟，仓库目录 %s",
                self._config.github.auto_review_interval_min,
                self._config.github.auto_review_cwd or os.getcwd(),
            )

        # ===== 初始化 SSH 连接库（M4a）=====
        # 存储路径 ~/.iwan/ssh/connections.json 由 store 自己锚定；构造不读盘，
        # 首个 ssh.conn_list 才懒载入——目录不存在不该在启动期制造任何噪音
        self._ssh_store = SshConnStore()

        # ===== 初始化 SSH 终端会话池（M4c）=====
        # 发射器是会话→bus 的唯一出口：输出帧与关闭通知都从这里走
        async def _ssh_emit_output(session_id: str, data_b64: str) -> None:
            from iwan_claude.core.bus.events import SshOutputEvent
            await self._bus.publish(
                SshOutputEvent(session_id=session_id, data_b64=data_b64, ts=_now())
            )

        async def _ssh_emit_closed(session_id: str, exit_code: int, reason: str) -> None:
            from iwan_claude.core.bus.events import SshClosedEvent
            await self._bus.publish(
                SshClosedEvent(session_id=session_id, exit_code=exit_code, reason=reason, ts=_now())
            )

        self._ssh_mgr = SshSessionManager(_ssh_emit_output, _ssh_emit_closed)

        # ===== 初始化工作流（W1）=====
        # 定义/流水两份 JSON 与 scheduled.json 同址；引擎四个协作者全走
        # CoreApp 方法注入（launch/await 桥 subagent，on_* 桥总线），装配层
        # 与调度层就此解耦——engine.py 单测给 fake 回调即可，不碰这套真实现。
        # 孤儿清算必须在 server.start 前：客户端一连上 workflow.runs 就该看到
        # 上次进程死亡留下的 running 行已变成 interrupted，而不是永远转圈
        self._workflow_store = WorkflowStore()
        self._workflow_store.mark_orphans_interrupted()
        self._workflow_engine = WorkflowEngine(
            self._workflow_store,
            launch=self._workflow_launch,
            await_child=self._workflow_await,
            on_node=self._workflow_on_node,
            on_run_finished=self._workflow_on_finished,
            # 取消传导第五线：引擎 task 被 cancel 只解开 await，注册表里的子
            # Agent 是独立 task——不显式击杀就白烧 LLM 到自然终
            cancel_child=self._subagent_registry.cancel,
        )

        # ===== 初始化语音转写（V1）=====
        # 构造零成本（不 import faster-whisper、不碰模型文件），放在装配区只是
        # 让"daemon 具备哪些能力"在一处列全；真正的重活首次 RPC 才发生
        self._speech = SpeechTranscriber()

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
        server.register("mcp.status", self._mcp_status_handler)
        server.register("pr.context", self._pr_context_handler)
        server.register("pr.list", self._pr_list_handler)
        server.register("pr.create", self._pr_create_handler)
        server.register("pr.review", self._pr_review_handler)
        server.register("schedule.list", self._schedule_list_handler)
        server.register("schedule.create", self._schedule_create_handler)
        server.register("schedule.update", self._schedule_update_handler)
        server.register("schedule.delete", self._schedule_delete_handler)
        server.register("schedule.run_now", self._schedule_run_now_handler)
        server.register("git.status", self._git_status_handler)
        server.register("git.branches", self._git_branches_handler)
        server.register("git.log", self._git_log_handler)
        server.register("git.stage", self._git_stage_handler)
        server.register("git.unstage", self._git_unstage_handler)
        server.register("git.discard", self._git_discard_handler)
        server.register("git.commit", self._git_commit_handler)
        server.register("git.checkout", self._git_checkout_handler)
        server.register("git.pull", self._git_pull_handler)
        server.register("git.push", self._git_push_handler)
        # M4a：SSH 连接库与密钥（7 个方法；终端 RPC 在 M4c 另开一段）
        server.register("ssh.conn_list", self._ssh_conn_list_handler)
        server.register("ssh.conn_add", self._ssh_conn_add_handler)
        server.register("ssh.conn_update", self._ssh_conn_update_handler)
        server.register("ssh.conn_delete", self._ssh_conn_delete_handler)
        server.register("ssh.key_status", self._ssh_key_status_handler)
        server.register("ssh.key_generate", self._ssh_key_generate_handler)
        server.register("ssh.host_trust", self._ssh_host_trust_handler)
        # M4c：SSH 终端会话（输入/输出经 b64，输出与关闭走事件流）
        server.register("ssh.term_open", self._ssh_term_open_handler)
        server.register("ssh.term_write", self._ssh_term_write_handler)
        server.register("ssh.term_resize", self._ssh_term_resize_handler)
        server.register("ssh.term_close", self._ssh_term_close_handler)
        # W1：工作流（定义 CRUD + 即发即返 run + 运行历史）
        server.register("workflow.list", self._workflow_list_handler)
        server.register("workflow.get", self._workflow_get_handler)
        server.register("workflow.save", self._workflow_save_handler)
        server.register("workflow.delete", self._workflow_delete_handler)
        server.register("workflow.run", self._workflow_run_handler)
        server.register("workflow.cancel", self._workflow_cancel_handler)
        server.register("workflow.runs", self._workflow_runs_handler)
        server.register("speech.transcribe", self._speech_transcribe_handler)

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

        # 先停调度器：避免关停半途还有任务被触发、抓着半拆好的子系统不放
        if self._scheduler is not None:
            await self._scheduler.stop()

        # 再掐 PR 轮询：它和调度器同属"会自己起会话的手"，必须在子系统开拆前断源
        if self._pr_poller_task is not None:
            self._pr_poller_task.cancel()
            self._pr_poller_task = None

        # 停工作流引擎：cancel 活跃 run 协程并等收口（_execute 里各自标
        # interrupted）；放在 registry.shutdown 前——run 协程还在 await 子任务，
        # 先把调度侧解散，子 Agent 再统一回收
        if self._workflow_engine is not None:
            await self._workflow_engine.shutdown()

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

        # 击杀全部 SSH 终端会话（M4c）：Job Object 连坐 ssh.exe 进程树，
        # 放在 server.stop 前——订阅者还活着时把 ssh.closed 发出去
        if self._ssh_mgr is not None:
            await self._ssh_mgr.kill_all()
        
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
