"""
Agent Runner 模块 - 管理 Agent 执行的完整生命周期

【学习要点】
1. 依赖注入：通过构造函数注入所有依赖，便于测试和扩展
2. 工具注册：动态构建工具注册表，支持白名单过滤
3. 上下文管理：加载全局、项目和会话级别的上下文
4. 事件驱动：通过 EventBus 发布执行事件
5. 引擎切换：根据配置选择 legacy 或 langgraph 引擎

【核心流程】
1. 初始化 Runner（注入配置、事件总线、权限管理器等）
2. 加载上下文（全局、项目、CLAUDE.md）
3. 构建工具注册表
4. 创建执行上下文（ExecutionContext）
5. 根据配置选择执行引擎（AgentLoop 或 LangGraphAgentLoop）
6. 执行 Agent 循环
7. 保存结果到会话存储
"""
from __future__ import annotations

# asyncio：异步 I/O 框架
# logging：日志记录
# dataclasses：数据类装饰器
# datetime：日期时间处理
# pathlib：路径操作
# typing：类型提示
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 导入事件类型
from iwan_claude.core.bus.events import RunFinishedEvent, RunStartedEvent

# 导入核心组件
from iwan_claude.core.compact.compactor import Compactor       # 会话压缩器
from iwan_claude.core.config import IwanConfig                 # 配置
from iwan_claude.core.context import ExecutionContext           # 执行上下文
from iwan_claude.core.sandbox import init_sandbox              # 沙箱初始化
from iwan_claude.core.events.bus import EventBus, EventHandler # 事件总线
from iwan_claude.core.events.writer import EventWriter         # 事件写入器
from iwan_claude.core.llm.base import LLMProvider              # LLM 提供者接口
from iwan_claude.core.llm import create_provider_from_config   # 创建 LLM 提供者
from iwan_claude.core.loop import AgentLoop                    # Legacy 执行循环
from iwan_claude.core.mcp.server import McpServerManager       # MCP 服务器管理
from iwan_claude.core.memory.loader import load_context_file   # 加载上下文文件
from iwan_claude.core.memory.claude_md import (               # CLAUDE.md 处理
    load_claude_md,
    render_claude_md_prompt,
)
from iwan_claude.core.permissions.manager import PermissionManager  # 权限管理
from iwan_claude.core.runs import RUNS_DIR, new_run_id         # 运行相关工具
from iwan_claude.core.session.model import Session             # 会话模型
from iwan_claude.core.session.store import SessionStore        # 会话存储
from iwan_claude.core.subagent.registry import BackgroundTaskRegistry  # 后台任务注册表
from iwan_claude.core.subagent.tool import (                   # 子 Agent 工具
    AgentResultTool,
    BatchResultTool,
    CancelAgentTool,
    SpawnAgentTool,
    SpawnAgentsTool,
)
from iwan_claude.core.rag.chunker import DocumentChunker       # 文档分块器
from iwan_claude.core.rag.embedding import get_embedding_provider  # 嵌入提供者
from iwan_claude.core.rag.index import KnowledgeIndexManager  # 知识索引管理器
from iwan_claude.core.rag.tools import (                      # RAG 工具
    ForgetKnowledgeTool,
    IndexKnowledgeTool,
    SearchKnowledgeTool,
)
from iwan_claude.core.rag.vectorstore import get_vector_store  # 向量存储
from iwan_claude.core.task.manager import TaskManager          # 任务管理器
from iwan_claude.core.tools.builtin import (                   # 内置工具
    AddContextTool,
    AssignRoleTool,
    BashTool,
    CacheDeleteTool,
    CacheGetTool,
    CacheInvalidateTool,
    CacheSetTool,
    CacheStatsTool,
    ChangelogTool,
    CopyFileTool,
    DeleteFileTool,
    DeleteLinesTool,
    DependencyCheckTool,
    EditByLinesTool,
    EditBySearchTool,
    FileExistsTool,
    FileStatTool,
    FindFilesTool,
    GenerateDocsTool,
    GitCheckoutTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
    GrepSearchTool,
    HttpRequestTool,
    InsertAtLineTool,
    LintCodeTool,
    ListDirTool,
    ListRolesTool,
    MkdirTool,
    NoteSaveTool,
    PipManageTool,
    ProcessListTool,
    ReadFileTool,
    RenameFileTool,
    ReviewCodeTool,
    RunPythonTool,
    SecurityScanTool,
    ShareKnowledgeTool,
    SkillCreateTool,
    SkillDeleteTool,
    SkillInfoTool,
    SkillInstallTool,
    SkillListTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    UpdateReadmeTool,
    ViewFileTool,
    WriteFileTool,
    ListCheckpointsTool,
    RestoreCheckpointTool,
)
from iwan_claude.core.tools.registry import ToolRegistry       # 工具注册表
from iwan_claude.core.trace.provider import TracingProvider    # 跟踪包装器
from iwan_claude.core.trace.writer import TraceWriter          # 跟踪写入器


# 获取当前时间的 ISO 格式字符串
def _now() -> str:
    """
    获取当前 UTC 时间的 ISO 8601 格式字符串
    
    【学习要点】
    1. datetime.now(UTC)：获取当前 UTC 时间，避免时区问题
    2. isoformat()：将 datetime 对象转换为标准 ISO 8601 格式字符串
    3. 统一时间格式：所有事件时间戳使用相同格式，便于日志分析和排序
    
    返回值：
        str: 格式如 "2024-01-01T12:00:00+00:00" 的时间字符串
    """
    return datetime.now(UTC).isoformat()


# 运行结果数据类 - 使用 @dataclass 自动生成 __init__、__repr__、__eq__ 等方法
@dataclass
class RunOutcome:
    """
    运行结果数据类
    
    【学习要点】
    1. @dataclass 装饰器：Python 3.7+ 引入，自动生成常见魔术方法
    2. 类型提示：明确属性类型，便于代码阅读和类型检查
    3. 不可变设计：作为返回值的容器，通常不需要修改属性
    
    属性：
        status: 运行状态（success, failed, cancelled）
        result: 最终结果文本，可能为空字符串
        reason: 失败原因（仅在 failed 状态时有值，其他状态为 None）
    """
    status: str
    result: str
    reason: str | None


class AgentRunner:
    """
    Agent Runner 类 - 管理 Agent 执行的完整生命周期
    
    【学习要点】
    1. 依赖注入模式：通过构造函数注入所有依赖，便于测试和扩展
    2. 可选依赖：大部分参数默认为 None，由调用者决定是否传入
    3. 延迟初始化：某些组件（如 checkpointer）不在构造函数中初始化，而是在需要时延迟初始化
    4. 共享状态：跨 run 共享的组件（如 task_registry、checkpointer）存储在实例属性中
    
    【核心职责】
    - 加载和管理执行上下文
    - 构建工具注册表
    - 创建执行引擎（AgentLoop 或 LangGraphAgentLoop）
    - 执行 Agent 循环
    - 处理运行结果
    """
    
    # 组装所有运行时依赖，准备执行一次完整的 agent run
    def __init__(
        self,
        config: IwanConfig,
        *,
        bus: EventBus | None = None,
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        runs_dir: Path | None = None,
        trace: TraceWriter | None = None,
        permission_manager: PermissionManager | None = None,
        mcp_manager: McpServerManager | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        """
        构造函数 - 注入运行时依赖
        
        参数：
            config: 系统配置，包含 LLM、RAG、Agent 等配置项
            bus: 事件总线，用于发布和订阅系统事件
            provider: LLM 提供者，负责调用 LLM API
            extra_handlers: 额外的事件处理器列表
            runs_dir: 运行记录保存目录
            trace: 跟踪写入器，记录 LLM 调用日志
            permission_manager: 权限管理器，控制工具调用权限
            mcp_manager: MCP 服务器管理器，管理外部 MCP 工具
            checkpointer: LangGraph 检查点管理器，支持状态持久化和回溯
        
        注意：
            所有可选参数默认 None，由调用者根据需要传入
            如果不传入 provider，run_and_capture 会根据 config 创建
            如果不传入 checkpointer，会在需要时根据配置延迟初始化
        """
        # 保存配置
        self._config = config
        
        # 事件总线：用于发布和订阅系统事件（如 RunStartedEvent、RunFinishedEvent）
        self._bus = bus
        
        # LLM 提供者：负责调用 LLM API
        self._provider = provider
        
        # 额外的事件处理器：允许外部订阅系统事件
        self._extra_handlers: list[EventHandler] = extra_handlers or []
        
        # 运行记录目录：保存每次 run 的事件、日志等
        self._runs_dir = runs_dir or RUNS_DIR
        
        # 跟踪写入器：记录 LLM 调用的详细信息
        self._trace = trace
        
        # 权限管理器：控制工具调用的权限（允许、拒绝、询问）
        self._permission_manager = permission_manager
        
        # MCP 服务器管理器：管理外部 MCP 工具的注册和调用
        self._mcp_manager = mcp_manager
        
        # 跨 run 共享的后台 subagent 任务注册表：用于管理异步子 Agent 任务
        self._task_registry = BackgroundTaskRegistry()
        
        # 外部传入的 checkpointer（跨 run 共享），如果没有传入则延迟初始化
        self._checkpointer = checkpointer
        
        # 初始化沙箱配置：设置文件访问的安全限制
        init_sandbox(config.sandbox)
        
        # checkpointer 的上下文管理器（用于正确关闭 sqlite 连接）
        self._checkpointer_ctx: Any = None

    async def _init_checkpointer(self) -> Any:
        """
        延迟初始化 LangGraph Checkpointer
        
        【学习要点】
        1. 延迟初始化模式：只在需要时才创建 checkpointer，节省资源
        2. 多后端支持：根据配置选择不同的后端存储（none、memory、sqlite）
        3. 异步上下文管理器：sqlite 后端需要使用 async with 模式正确管理连接
        4. 单例模式：同一个 AgentRunner 实例只初始化一次 checkpointer
        
        【后端类型说明】
        - "none": 不使用 checkpointer，无法回溯
        - "memory": 内存存储，重启后丢失，适合开发测试
        - "sqlite": SQLite 数据库持久化存储，适合生产环境
        
        返回值：
            Any: LangGraph Checkpointer 实例（InMemorySaver 或 AsyncSqliteSaver），或 None
        
        注意：
            AsyncSqliteSaver.from_conn_string() 返回的是异步上下文管理器，
            需要使用 await ctx.__aenter__() 获取实际的 saver 对象，
            并在 close() 方法中调用 __aexit__() 正确关闭连接
        """
        # 如果已经初始化过，直接返回（单例模式）
        if self._checkpointer is not None:
            return self._checkpointer

        # 根据配置选择后端类型
        backend = self._config.agent.checkpoint_backend
        
        # "none" 后端：不使用 checkpointer
        if backend == "none":
            self._checkpointer = None
            return None
        
        # "memory" 后端：使用内存存储
        elif backend == "memory":
            # 动态导入 InMemorySaver，避免不必要的依赖加载
            from langgraph.checkpoint.memory import InMemorySaver

            self._checkpointer = InMemorySaver()
            return self._checkpointer
        
        # "sqlite" 后端：使用 SQLite 数据库持久化存储
        elif backend == "sqlite":
            # 动态导入 AsyncSqliteSaver
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            # 构建数据库文件路径
            db_path = Path(self._config.agent.checkpoint_db_path)
            # 确保父目录存在（递归创建）
            db_path.parent.mkdir(parents=True, exist_ok=True)
            # 获取绝对路径字符串
            conn_str = str(db_path.resolve())
            
            # 创建异步上下文管理器
            ctx = AsyncSqliteSaver.from_conn_string(conn_str)
            # 进入上下文，获取实际的 saver 对象
            saver = await ctx.__aenter__()
            # 保存上下文管理器引用，用于关闭时清理
            self._checkpointer_ctx = ctx
            self._checkpointer = saver
            return saver
        
        # 未知后端类型：记录警告并使用 none
        else:
            logging.getLogger(__name__).warning(
                "Unknown checkpoint_backend=%r, using none", backend
            )
            self._checkpointer = None
            return None

    async def close(self) -> None:
        """
        关闭资源 - 正确清理 checkpointer 相关资源
        
        【学习要点】
        1. 资源清理顺序：先关闭上下文管理器，再关闭 checkpointer
        2. 异步/同步兼容：同时支持异步和同步的 close 方法
        3. 异常处理：使用 try-except 确保清理失败不会影响其他操作
        4. 属性检查：使用 hasattr() 检查对象是否具有特定方法，增强代码健壮性
        
        【清理流程】
        1. 关闭 checkpointer_ctx（如果存在）：用于正确关闭 sqlite 连接
        2. 关闭 checkpointer（如果存在且没有通过上下文管理器关闭）
        3. 将引用置为 None，便于垃圾回收
        """
        # 第一步：关闭上下文管理器（用于 sqlite 后端）
        if self._checkpointer_ctx is not None:
            try:
                # 检查是否是异步上下文管理器
                if hasattr(self._checkpointer_ctx, "__aexit__"):
                    await self._checkpointer_ctx.__aexit__(None, None, None)
                # 检查是否是同步上下文管理器
                elif hasattr(self._checkpointer_ctx, "__exit__"):
                    self._checkpointer_ctx.__exit__(None, None, None)
            except Exception:
                # 记录异常但不抛出，确保后续清理继续执行
                logging.getLogger(__name__).exception("Error closing checkpointer context")
            # 清理引用
            self._checkpointer_ctx = None
        
        # 第二步：关闭 checkpointer（如果没有通过上下文管理器关闭）
        if self._checkpointer is not None:
            # 检查 checkpointer 是否有 close 方法，且没有通过上下文管理器关闭
            if hasattr(self._checkpointer, "close") and not hasattr(self._checkpointer_ctx, "__exit__"):
                # 检查 close 方法是异步还是同步
                if asyncio.iscoroutinefunction(self._checkpointer.close):
                    await self._checkpointer.close()
                else:
                    self._checkpointer.close()
            # 清理引用
            self._checkpointer = None

    async def list_checkpoints(self, thread_id: str) -> list[dict[str, Any]]:
        """
        列出指定线程的所有 checkpoints
        
        【学习要点】
        1. 线程隔离：每个 thread_id 对应一个独立的对话会话，checkpoints 按线程隔离
        2. 异步迭代：checkpointer.alist() 返回异步生成器，需要使用 async for 遍历
        3. 数据结构解析：LangGraph 的 checkpoint 包含 config、metadata、checkpoint 三个部分
        4. 摘要生成：从 messages 中提取最后一条消息作为摘要，便于用户识别
        
        参数：
            thread_id: 线程 ID，通常使用 session_id
            
        返回值：
            list[dict]: checkpoint 列表，每个元素包含：
                checkpoint_id: 检查点唯一标识
                step: 当前步骤数
                timestamp: 时间戳
                summary: 摘要（最后一条消息内容或 step 信息）
                node: 当前节点（未使用，保留字段）
        
        【数据结构说明】
        LangGraph checkpoint tuple 包含：
        - config: 配置信息，包含 configurable（如 thread_id、checkpoint_id）
        - metadata: 元数据，包含 step 等信息
        - checkpoint: 实际状态，包含 channel_values（如 messages、step、status 等）
        """
        # 确保 checkpointer 已初始化
        if self._checkpointer is None:
            await self._init_checkpointer()
            # 如果初始化失败，返回空列表
            if self._checkpointer is None:
                return []

        result = []
        try:
            # 获取指定 thread_id 的所有 checkpoints
            # alist() 返回异步生成器，需要使用 async for 遍历
            checkpoints_iter = self._checkpointer.alist(
                {"configurable": {"thread_id": thread_id}}
            )
            
            # 异步遍历所有 checkpoints
            async for cp_tuple in checkpoints_iter:
                # 解析配置信息
                configurable = cp_tuple.config.get("configurable", {})
                checkpoint_id = configurable.get("checkpoint_id", "")
                
                # 解析元数据
                step = cp_tuple.metadata.get("step", 0)
                timestamp = cp_tuple.checkpoint.get("ts", "")

                # 解析状态信息（channel_values 存储了工作流的所有状态）
                channel_values = cp_tuple.checkpoint.get("channel_values", {})
                
                # 生成摘要：从 messages 中提取最后一条消息
                if "messages" in channel_values:
                    msgs = channel_values["messages"]
                    if msgs and isinstance(msgs, list) and len(msgs) > 0:
                        last_msg = msgs[-1]
                        content = last_msg.get("content", "")
                        if isinstance(content, str):
                            # 截取前 50 个字符作为摘要
                            summary = content[:50] + "..." if len(content) > 50 else content
                        else:
                            summary = f"step={step}"
                    else:
                        summary = f"step={step}"
                else:
                    summary = f"step={step}"

                # 添加到结果列表
                result.append({
                    "checkpoint_id": checkpoint_id,
                    "step": step,
                    "timestamp": timestamp,
                    "summary": summary,
                    "node": None,
                })
        except Exception:
            # 记录异常但不抛出，确保方法返回空列表而非崩溃
            logging.getLogger(__name__).exception("Failed to list checkpoints")

        # 按步骤数排序，确保顺序正确
        return sorted(result, key=lambda x: x["step"])

    async def restore_checkpoint(self, thread_id: str, checkpoint_id: str) -> dict[str, Any] | None:
        """
        恢复指定的 checkpoint
        
        【学习要点】
        1. 精确查找：通过 thread_id + checkpoint_id 唯一确定一个 checkpoint
        2. 状态提取：从 checkpoint 的 channel_values 中提取需要的状态字段
        3. 容错处理：如果 checkpoint 不存在或解析失败，返回 None
        
        参数：
            thread_id: 线程 ID，通常使用 session_id
            checkpoint_id: checkpoint 的唯一标识
            
        返回值：
            dict | None: 恢复的状态字典，包含：
                messages: 消息历史
                step: 当前步骤数
                status: 当前状态（success, failed, cancelled, running）
                _tool_calls: 待处理的工具调用列表
                _stop_reason: 停止原因
                如果恢复失败，返回 None
        
        【恢复流程】
        1. 使用 aget_tuple() 根据 thread_id 和 checkpoint_id 获取 checkpoint
        2. 从 channel_values 中提取状态字段
        3. 返回状态字典，供 AgentLoop 或 LangGraphAgentLoop 继续执行
        """
        # 确保 checkpointer 已初始化
        if self._checkpointer is None:
            await self._init_checkpointer()
            if self._checkpointer is None:
                return None

        try:
            # 根据 thread_id 和 checkpoint_id 获取 checkpoint
            # aget_tuple() 返回单个 checkpoint tuple
            checkpoint_tuple = await self._checkpointer.aget_tuple(
                {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}
            )
            
            # 如果 checkpoint 不存在，返回 None
            if checkpoint_tuple is None:
                return None

            # 从 checkpoint 中提取状态信息
            state = checkpoint_tuple.checkpoint.get("channel_values", {})
            
            # 返回状态字典，包含 Agent 执行所需的关键信息
            return {
                "messages": state.get("messages", []),      # 消息历史
                "step": state.get("step", 0),               # 当前步骤数
                "status": state.get("status", ""),          # 当前状态
                "_tool_calls": state.get("_tool_calls", []),# 待处理的工具调用
                "_stop_reason": state.get("_stop_reason", ""), # 停止原因
            }
        except Exception:
            # 记录异常但不抛出，确保方法返回 None 而非崩溃
            logging.getLogger(__name__).exception("Failed to restore checkpoint")
            return None

    def _build_registry(
        self,
        task_manager: TaskManager,
        *,
        session: Session | None = None,
        store: SessionStore | None = None,
        run_id: str | None = None,
        provider: LLMProvider | None = None,
        bus: EventBus | None = None,
        child_runs_dir: Path | None = None,
        session_id: str = "",
        tool_whitelist: list[str] | None = None,
        checkpointer: Any | None = None,
    ) -> ToolRegistry:
        """
        构建工具注册表 - 动态注册所有可用工具
        
        【学习要点】
        1. 工具分类：按功能将工具分为多个类别（文件操作、Git、代码审查、任务管理等）
        2. 白名单机制：通过 tool_whitelist 控制哪些工具可以被使用
        3. 条件注册：根据依赖是否存在决定是否注册某些工具
        4. 共享实例：TaskManager 和 TaskRegistry 在多个工具间共享，保持状态一致
        5. Lambda 闭包：使用 lambda 表达式延迟获取 checkpointer 和 session_id，避免参数捕获问题
        
        参数：
            task_manager: 任务管理器，注入到任务相关工具中
            session: 当前会话对象（可选）
            store: 会话存储（可选）
            run_id: 当前运行 ID（可选）
            provider: LLM 提供者，注入到子 Agent 工具中（可选）
            bus: 事件总线，注入到子 Agent 工具中（可选）
            child_runs_dir: 子运行目录（可选）
            session_id: 会话 ID，用于 checkpointer（可选）
            tool_whitelist: 工具白名单，限制可用工具（可选）
            checkpointer: LangGraph Checkpointer，注入到 checkpoint 工具中（可选）
        
        返回值：
            ToolRegistry: 包含所有注册工具的注册表
            
        【工具注册顺序】
        1. 文件操作工具（ReadFileTool, WriteFileTool 等）
        2. Git 工具（GitStatusTool, GitLogTool 等）
        3. 进程和网络工具（ProcessListTool, HttpRequestTool）
        4. 代码质量工具（ReviewCodeTool, LintCodeTool, SecurityScanTool）
        5. 依赖管理工具（PipManageTool, DependencyCheckTool）
        6. 文档生成工具（GenerateDocsTool, UpdateReadmeTool, ChangelogTool）
        7. 缓存工具（CacheGetTool, CacheSetTool 等）
        8. 角色管理工具（AssignRoleTool, ListRolesTool）
        9. 任务管理工具（TaskCreateTool, TaskUpdateTool 等）
        10. Skill 管理工具（SkillListTool, SkillInfoTool 等）
        11. 笔记工具（NoteSaveTool）- 条件注册
        12. 子 Agent 工具（SpawnAgentTool, SpawnAgentsTool 等）- 条件注册
        13. MCP 工具 - 条件注册
        14. RAG 工具（SearchKnowledgeTool 等）- 条件注册
        15. Checkpoint 工具（ListCheckpointsTool, RestoreCheckpointTool）- 条件注册
        """
        # 将白名单转换为集合，便于快速查找
        allowed: set[str] | None = set(tool_whitelist) if tool_whitelist else None

        # 工具名称检查函数：判断工具是否在白名单中
        # 如果白名单为 None，则允许所有工具
        def _ok(name: str) -> bool:
            return allowed is None or name in allowed

        # 创建工具注册表实例
        registry = ToolRegistry()
        
        # ========== 第一类：文件操作工具 ==========
        # 这些工具不需要任何依赖，可以直接实例化
        for t in [
            ReadFileTool(),           # 读取文件
            BashTool(),               # 执行 bash 命令
            WriteFileTool(),          # 写入文件
            ListDirTool(),            # 列出目录
            DeleteFileTool(),         # 删除文件
            RenameFileTool(),         # 重命名文件
            CopyFileTool(),           # 复制文件
            MkdirTool(),              # 创建目录
            FileStatTool(),           # 获取文件状态
            FileExistsTool(),         # 检查文件是否存在
            FindFilesTool(),          # 查找文件
            GrepSearchTool(),         # 文本搜索
            RunPythonTool(),          # 执行 Python 代码
            ViewFileTool(),           # 查看文件内容（分页）
            EditByLinesTool(),        # 按行编辑
            EditBySearchTool(),       # 按搜索编辑
            InsertAtLineTool(),       # 在指定行插入
            DeleteLinesTool(),        # 删除指定行
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第二类：Git 工具 ==========
        for t in [
            GitStatusTool(),          # Git 状态
            GitLogTool(),             # Git 日志
            GitDiffTool(),            # Git 差异
            GitCommitTool(),          # Git 提交
            GitCheckoutTool(),        # Git 切换分支
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第三类：进程和网络工具 ==========
        for t in [
            ProcessListTool(),        # 获取进程列表
            HttpRequestTool(),        # HTTP 请求
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第四类：代码质量工具 ==========
        for t in [
            AddContextTool(),         # 添加上下文
            ReviewCodeTool(),         # 代码审查
            LintCodeTool(),           # 代码检查
            SecurityScanTool(),       # 安全扫描
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第五类：依赖管理工具 ==========
        for t in [
            PipManageTool(),          # Python 包管理
            DependencyCheckTool(),    # 依赖检查
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第六类：文档生成工具 ==========
        for t in [
            GenerateDocsTool(),       # 生成文档
            UpdateReadmeTool(),       # 更新 README
            ChangelogTool(),          # 生成变更日志
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第七类：缓存工具 ==========
        for t in [
            CacheGetTool(),           # 获取缓存
            CacheSetTool(),           # 设置缓存
            CacheDeleteTool(),        # 删除缓存
            CacheInvalidateTool(),    # 失效缓存
            CacheStatsTool(),         # 缓存统计
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第八类：角色管理工具 ==========
        for t in [
            AssignRoleTool(),         # 分配角色
            ListRolesTool(),          # 列出角色
            ShareKnowledgeTool(),     # 共享知识
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第九类：任务管理工具 ==========
        # 所有任务工具共享同一个 TaskManager 实例
        for t in [
            TaskCreateTool(task_manager),
            TaskUpdateTool(task_manager),
            TaskListTool(task_manager),
            TaskGetTool(task_manager),
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第十类：Skill 管理工具 ==========
        # 创建 SkillLoader 实例，注入到相关工具中
        from iwan_claude.core.skills.loader import SkillLoader
        skill_loader = SkillLoader()
        for t in [
            SkillListTool(skill_loader),    # 列出所有技能
            SkillInfoTool(skill_loader),    # 获取技能信息
            SkillInstallTool(skill_loader), # 安装技能
            SkillCreateTool(),              # 创建技能
            SkillDeleteTool(),              # 删除技能
        ]:
            if _ok(t.name):
                registry.register(t)
        
        # ========== 第十一类：笔记工具（条件注册） ==========
        # 需要 session、store 和 run_id 都存在时才能注册
        if session is not None and store is not None and run_id is not None:
            note_tool = NoteSaveTool(store, session.id, run_id)
            if _ok(note_tool.name):
                registry.register(note_tool)
        
        # ========== 第十二类：子 Agent 工具（条件注册） ==========
        # 需要 provider、bus 和 run_id 都存在时才能注册
        if provider is not None and bus is not None and run_id is not None:
            runs_dir = child_runs_dir or self._runs_dir
            
            # 单个子 Agent 启动工具
            if _ok("spawn_agent"):
                registry.register(
                    SpawnAgentTool(
                        provider=provider,
                        parent_bus=bus,
                        parent_run_id=run_id,
                        permission_manager=self._permission_manager,
                        max_steps=self._config.agent.max_steps,
                        task_registry=self._task_registry,
                        runs_dir=runs_dir,
                        session_id=session_id,
                        llm_model_name=self._config.llm.default_model,
                        depth=0,
                    )
                )
            
            # 获取单个子 Agent 结果工具
            if _ok("agent_result"):
                registry.register(AgentResultTool(self._task_registry))
            
            # 批量子 Agent 启动工具
            if _ok("spawn_agents"):
                registry.register(
                    SpawnAgentsTool(
                        provider=provider,
                        parent_bus=bus,
                        parent_run_id=run_id,
                        permission_manager=self._permission_manager,
                        max_steps=self._config.agent.max_steps,
                        task_registry=self._task_registry,
                        runs_dir=runs_dir,
                        session_id=session_id,
                        llm_model_name=self._config.llm.default_model,
                        depth=0,
                    )
                )
            
            # 获取批量子 Agent 结果工具
            if _ok("batch_result"):
                registry.register(BatchResultTool(self._task_registry))
            
            # 取消子 Agent 任务工具
            if _ok("cancel_agent"):
                registry.register(CancelAgentTool(self._task_registry))
        
        # ========== 第十三类：MCP 工具（条件注册） ==========
        # 如果 MCP 管理器存在，注册所有 MCP 工具
        if self._mcp_manager is not None:
            for mcp_tool in self._mcp_manager.get_tools():
                if _ok(mcp_tool.name):
                    registry.register(mcp_tool)
        
        # ========== 第十四类：RAG 工具（条件注册） ==========
        # 如果 RAG 功能启用，注册 RAG 相关工具
        if self._config.rag.enabled:
            try:
                # 创建向量存储
                vector_store = get_vector_store()
                
                # 创建嵌入提供者
                embedding_provider = get_embedding_provider(
                    self._config.rag,
                    self._config.llm.base_url,
                )
                
                # 创建文档分块器
                chunker = DocumentChunker(
                    chunk_size=self._config.rag.max_chunk_size,
                    chunk_overlap=self._config.rag.chunk_overlap,
                )
                
                # 创建知识索引管理器
                index_manager = KnowledgeIndexManager(
                    vector_store=vector_store,
                    embedding_provider=embedding_provider,
                    chunker=chunker,
                    index_path=self._config.rag.index_path,
                )
                
                # 加载索引
                index_manager.load()
                
                # 注册 RAG 工具
                if _ok("search_knowledge"):
                    registry.register(SearchKnowledgeTool(index_manager))
                if _ok("index_knowledge"):
                    registry.register(IndexKnowledgeTool(index_manager))
                if _ok("forget_knowledge"):
                    registry.register(ForgetKnowledgeTool(index_manager))
            except Exception as exc:
                # 如果 RAG 初始化失败，记录警告但不影响其他工具注册
                logging.getLogger(__name__).warning(
                    "Failed to initialize RAG tools: %s", exc
                )
        
        # ========== 第十五类：Checkpoint 工具（条件注册） ==========
        # 如果 checkpointer 存在，注册 checkpoint 相关工具
        if checkpointer is not None:
            # 使用 lambda 表达式延迟获取 checkpointer 和 session_id
            # 这样可以确保在工具实际执行时获取最新值
            if _ok("list_checkpoints"):
                registry.register(ListCheckpointsTool(lambda: checkpointer, lambda: session_id))
            if _ok("restore_checkpoint"):
                registry.register(RestoreCheckpointTool(lambda: checkpointer, lambda: session, lambda: session_id))

        # 返回构建完成的工具注册表
        return registry

    async def run(self, goal: str, *, run_id: str | None = None) -> None:
        """
        执行一次完整的 agent run（简化版本）
        
        【学习要点】
        1. 委托模式：将实际执行逻辑委托给 run_and_capture 方法
        2. 忽略返回值：此方法不关心执行结果，只关心执行过程
        3. 关键字参数：使用 * 强制使用关键字参数，提高代码可读性
        
        参数：
            goal: 用户目标（自然语言描述）
            run_id: 运行 ID（可选，若不传则自动生成）
        """
        await self.run_and_capture(goal, run_id=run_id)

    async def run_and_capture(
        self,
        goal: str,
        *,
        run_id: str | None = None,
        session: Session | None = None,
        store: SessionStore | None = None,
        system_prompt_override: str | None = None,
        tool_whitelist: list[str] | None = None,
    ) -> RunOutcome:
        """
        执行一次完整的 agent run 并返回运行结果
        
        【学习要点】
        1. 执行流程：这是整个 Agent 执行的核心入口，包含完整的执行流程
        2. 上下文加载：按优先级加载全局、项目和会话级别的上下文
        3. 事件驱动：通过 EventBus 发布事件，实现模块间解耦
        4. 引擎选择：根据配置选择 legacy 或 langgraph 执行引擎
        5. 错误处理：捕获异常并正确标记运行状态
        
        参数：
            goal: 用户目标（自然语言描述）
            run_id: 运行 ID（可选，若不传则自动生成）
            session: 当前会话对象（可选）
            store: 会话存储（可选）
            system_prompt_override: 自定义 system prompt（可选，覆盖默认）
            tool_whitelist: 工具白名单（可选，限制可用工具）
        
        返回值：
            RunOutcome: 运行结果，包含状态、结果文本和失败原因
        
        【执行流程详解】
        1. 初始化阶段：生成 run_id，创建运行目录
        2. 上下文加载：加载全局、项目、CLAUDE.md 上下文
        3. 组件创建：创建 TaskManager、EventBus、ExecutionContext
        4. 事件监听：注册事件写入器，发布 RunStartedEvent
        5. 引擎初始化：创建 LLM Provider、工具注册表、Compactor
        6. 引擎选择：根据配置创建 AgentLoop 或 LangGraphAgentLoop
        7. 执行循环：调用 loop.run(context) 执行 Agent 循环
        8. 结果处理：发布 RunFinishedEvent，保存消息到会话存储
        9. 返回结果：返回 RunOutcome 对象
        """
        # ========== 第一步：初始化运行 ID 和目录 ==========
        
        # 如果没有传入 run_id，自动生成一个新的
        run_id = run_id or new_run_id()
        
        # 根据是否有会话，决定运行目录和历史消息来源
        if session is not None and store is not None:
            # 有会话：使用会话的运行目录
            run_path = store.runs_dir(session.id) / run_id
            # 读取会话历史消息
            history = store.read_messages(session.id)
            # 读取会话笔记
            notes = store.read_notes(session.id)
        else:
            # 无会话：使用默认运行目录
            run_path = self._runs_dir / run_id
            # 创建初始消息（用户目标）
            history = [{"role": "user", "content": goal}]
            notes = ""
        
        # 创建运行目录（递归创建，已存在则忽略）
        run_path.mkdir(parents=True, exist_ok=True)

        # ========== 第二步：加载上下文 ==========
        
        # 加载全局上下文（用户目录下的 .iwan/context.md）
        global_ctx = load_context_file(Path("~/.iwan/context.md").expanduser())
        
        # 加载项目上下文（当前目录下的 .iwan/context.md）
        project_ctx = load_context_file(Path(".iwan/context.md"))
        
        # 加载 CLAUDE.md 配置并渲染为 prompt
        claude_md_config = load_claude_md()
        claude_md_prompt = render_claude_md_prompt(claude_md_config)

        # ========== 第三步：创建核心组件 ==========
        
        # 创建任务管理器（存储在运行目录下的 .tasks 目录）
        task_manager = TaskManager(run_path / ".tasks")

        # 获取事件总线（如果没有传入则创建新的）
        bus = self._bus if self._bus is not None else EventBus()
        
        # 注册额外的事件处理器
        for h in self._extra_handlers:
            bus.subscribe(h)

        # 创建执行上下文（封装所有运行时状态）
        context = ExecutionContext(
            run_id=run_id,
            goal=goal,
            max_steps=self._config.agent.max_steps,
            prefill_messages=history,        # 历史消息
            session_notes=notes,             # 会话笔记
            global_context=global_ctx,       # 全局上下文
            project_context=project_ctx,     # 项目上下文
            claude_md_context=claude_md_prompt, # CLAUDE.md 上下文
            system_prompt_override=system_prompt_override, # 自定义 system prompt
        )
        
        # 记录预填充消息的数量，用于后续保存时跳过已存在的消息
        prefill_len = len(history)

        # ========== 第四步：事件监听和执行 ==========
        
        # 使用上下文管理器创建事件写入器，自动处理文件打开和关闭
        async with EventWriter(run_path / "events.jsonl") as writer:
            # 订阅事件总线，将所有事件写入文件
            writer.subscribe(bus)
            
            # 发布运行开始事件
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))

            # 标记是否被取消
            cancelled = False
            
            try:
                # 创建或获取 LLM Provider
                provider: LLMProvider = self._provider or create_provider_from_config(
                    self._config.llm
                )
                
                # 如果启用了跟踪，包装 provider
                if self._trace is not None:
                    provider = TracingProvider(
                        provider,
                        self._trace,
                        include_payload=self._config.trace.include_llm_payload,
                    )
                
                # 获取会话 ID（用于 checkpointer）
                session_id_str = session.id if session is not None else ""
                
                # 确定子运行目录
                child_runs_dir = (
                    store.runs_dir(session.id)
                    if session is not None and store is not None
                    else self._runs_dir
                )
                
                # 初始化 checkpointer（如果尚未初始化）
                checkpointer = await self._init_checkpointer()
                
                # 构建工具注册表
                registry = self._build_registry(
                    task_manager,
                    session=session,
                    store=store,
                    run_id=run_id,
                    provider=provider,
                    bus=bus,
                    child_runs_dir=child_runs_dir,
                    session_id=session_id_str,
                    tool_whitelist=tool_whitelist,
                    checkpointer=checkpointer,
                )
                
                # 确定会话目录（用于压缩）
                session_dir = (
                    store.session_dir(session.id)
                    if session is not None and store is not None
                    else run_path
                )
                
                # 创建会话压缩器（用于自动压缩过长的会话历史）
                compactor = Compactor(bus, session_dir, session_id_str)
                
                # 检查是否启用了 RAG
                has_rag = self._config.rag.enabled and registry.get("search_knowledge") is not None
                
                # ========== 第五步：选择执行引擎 ==========
                
                if self._config.agent.engine == "langgraph":
                    # 使用 LangGraph 引擎（支持 checkpoint 和状态管理）
                    from iwan_claude.core.langgraph_loop import LangGraphAgentLoop

                    loop = LangGraphAgentLoop(
                        provider, registry, bus,
                        llm_model_name=self._config.llm.default_model,
                        permission_manager=self._permission_manager,
                        compactor=compactor,
                        compact_threshold=self._config.compaction.auto_threshold,
                        session_id=session_id_str,
                        checkpointer=checkpointer,
                        has_rag=has_rag,
                    )
                else:
                    # 使用 Legacy 引擎（简单的循环实现）
                    loop = AgentLoop(
                        provider, registry, bus,
                        llm_model_name=self._config.llm.default_model,
                        permission_manager=self._permission_manager,
                        compactor=compactor,
                        compact_threshold=self._config.compaction.auto_threshold,
                        session_id=session_id_str,
                        has_rag=has_rag,
                    )
                
                # ========== 第六步：执行 Agent 循环 ==========
                await loop.run(context)
            
            except asyncio.CancelledError:
                # 用户取消操作
                cancelled = True
                if not context.is_done():
                    context.mark_failed("cancelled")
            
            except Exception:
                # 其他异常（如 LLM API 错误）
                logging.getLogger(__name__).exception(
                    "agent run failed run_id=%s step=%d", run_id, context.step
                )
                if not context.is_done():
                    context.mark_failed("llm_error")

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

        # ========== 第七步：保存结果到会话存储 ==========
        
        # 如果有会话和存储，保存新增的消息
        if session is not None and store is not None:
            # 只保存新增的消息（跳过预填充的历史消息）
            store.append_messages(session.id, context.messages[prefill_len:], run_id=run_id)

        # 如果被取消，重新抛出异常
        if cancelled:
            raise asyncio.CancelledError()

        # 返回运行结果
        return RunOutcome(
            status=context.status,
            result=context.result,
            reason=context.reason,
        )
