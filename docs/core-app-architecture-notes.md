# CoreApp 架构关系详解笔记

> 解决的核心问题：CoreApp 和 EventBus、SessionManager、PermissionManager、Runner 之间到底是什么关系？

---

## 一、一句话理解各角色

| 角色 | 一句话定位 | 类比 |
| --- | --- | --- |
| **CoreApp** | 系统的「组装中心 + 中控调度器」 | 公司 CEO |
| **AgentRunner** | 一次 Run 的「执行编排器」 | 项目经理（管单次任务） |
| **SessionManager** | 会话的「生命周期管家」 | 前台接待（管用户会话） |
| **PermissionManager** | 权限的「守门员」 | 保安（管工具调用审批） |
| **EventBus** | 事件的「快递中心」 | 公司 OA 系统（管内部通信） |
| **IpcEventBroadcaster** | 事件的「对外广播塔」 | 公司公告栏（推送给客户端） |
| **SocketServer** | TCP 的「门户」 | 公司大门（接收 RPC 请求） |

---

## 二、详细树状组件关系图（一棵树画到底）

```
CoreApp（系统入口 + 中控调度器）
│
├── EventBus（事件总线，全系统共享的通信枢纽）
│   ├── 订阅者 1: IpcEventBroadcaster
│   │   └── _subscriptions: list[Subscription]
│   │       └── [sub_id, writer, topics, scope]
│   │           按 topic (fnmatch glob) 和 scope 过滤 → TCP 推送给客户端
│   │           封装为 EventPushEnvelope → writer.write() + drain()
│   ├── 订阅者 2: _trace_event_handler (CoreApp 方法)
│   │   └── 将所有 EventBus 事件写入 TraceWriter
│   │       → TraceRecord → daemon.jsonl
│   └── 发布者: SessionManager / AgentRunner / PermissionManager
│       在关键生命周期节点发布事件
│
├── PermissionManager（权限管理器，全系统共享的守门员）
│   ├── _policies: dict[str, ToolPolicy]
│   │   └── 每个工具的默认策略 + allow/deny patterns
│   │       例: {"bash": ToolPolicy(default=ASK)}
│   ├── _pending: dict[str, _PendingRequest]
│   │   └── tool_use_id → Future + session_id + tool_name
│   │       存储正在等待用户审批的请求
│   ├── _session_always: dict[tuple, str]
│   │   └── (session_id, tool_name) → "allow"/"deny"
│   │       session 级缓存（进程内存，重启丢失）
│   ├── _persistent_always: dict[str, str]
│   │   └── tool_name → "allow"/"deny"
│   │       持久化缓存（~/.iwan/policy.toml）
│   ├── _auto_mode: "off" / "read_only" / "on"
│   ├── _effort_level: "minimal" ~ "max"
│   └── _model_preset: "fast" / "balanced" / "powerful"
│
├── SessionManager（会话管理器，用户会话的生命周期管家）
│   ├── _sessions: dict[str, Session]
│   │   └── 内存中的活跃会话对象字典
│   │       Session { id, mode, status, title, run_ids, ... }
│   ├── _locks: dict[str, asyncio.Lock]
│   │   └── 每个会话 ID 对应一个锁，保证并发安全
│   ├── _store: SessionStore（文件 I/O）
│   │   ├── _root: Path（根目录，如 ~/.iwan/sessions/）
│   │   ├── session_dir(sid) → root / sid
│   │   │   └── sess-abc123/
│   │   │       ├── meta.json（会话元数据 JSON）
│   │   │       ├── thread.jsonl（消息历史 JSONL）
│   │   │       ├── notes.md（会话笔记）
│   │   │       ├── summary_*.md（压缩摘要）
│   │   │       ├── thread_*.jsonl.bak（压缩/恢复前的备份）
│   │   │       └── runs/（该会话的所有 run 目录）
│   │   │           ├── 20260730-103000-abc123/
│   │   │           │   ├── events.jsonl（该 run 的事件日志）
│   │   │           │   └── .tasks/（该 run 的任务数据）
│   │   │           └── ...
│   │   ├── runs_dir(sid) → session_dir(sid) / "runs"
│   │   ├── write_meta(session) → 序列化写入 meta.json
│   │   ├── read_meta(sid) → 反序列化读取 meta.json
│   │   ├── append_message(sid, role, content, run_id)
│   │   │   └── 追加一行 JSONL 到 thread.jsonl（追加模式保证并发安全）
│   │   ├── append_messages(sid, messages, run_id)
│   │   │   └── 批量追加（循环调用 append_message）
│   │   ├── read_messages(sid) → list[dict]
│   │   │   └── 读取 thread.jsonl 全文件
│   │   │       ├── _trim_orphan_tool_use() ← 移除未配对的 tool_use
│   │   │       └── truncate_tool_results() ← 截断过长工具结果
│   │   ├── write_compacted(sid, messages) → 压缩后覆盖写入
│   │   ├── write_messages(sid, messages) → checkpoint 恢复覆盖写入
│   │   ├── append_note(sid, content, run_id) → 追加到 notes.md
│   │   ├── read_notes(sid) → 读取 notes.md 全文
│   │   └── list_sessions() → list[Session]
│   │       └── 遍历 root 下所有子目录的 meta.json
│   ├── _runner_factory: lambda → AgentRunner（按需创建 Runner，见下）
│   ├── _bus: → EventBus（发布会话生命周期事件）
│   │   ├── SessionCreatedEvent
│   │   ├── SessionMessageReceivedEvent
│   │   ├── SessionWaitingForInputEvent
│   │   └── SessionClosedEvent
│   ├── _provider: → LLMProvider（compact 压缩用）
│   └── _skill_loader: → SkillLoader（Skill 匹配）
│
├── SocketServer（TCP 门户，RPC 请求入口）
│   ├── TCP 监听（host:port）
│   ├── JSON-RPC 协议解析
│   └── RPC 路由 → CoreApp 的 handler 方法
│       "session.send_message" → _session_send_handler()
│       "permission.respond"   → _permission_respond_handler()
│       "agent.run"            → _agent_run_handler()
│       ...
│
├── IpcEventBroadcaster（对外广播塔）
│   └── _subscriptions: list[Subscription]
│       └── [sub_id, writer, topics, scope]
│           按 topic (fnmatch glob) 和 scope 过滤 → TCP 推送
│           封装为 EventPushEnvelope → writer.write() + drain()
│
├── TraceWriter（追踪写入器）
│   ├── _queue: asyncio.Queue[TraceRecord]
│   ├── _drain(): 后台协程循环消费队列写文件
│   └── daemon.jsonl → 全局追踪日志
│
├── McpServerManager（MCP 服务器管理器）
│   └── 管理外部 MCP 工具服务器
│
├── Checkpointer（LangGraph 检查点存储）
│   ├── InMemorySaver（内存后端，进程级共享）
│   └── AsyncSqliteSaver（SQLite 后端，持久化）
│
└── AgentRunner（执行编排器，每次 run 创建新实例）
    ├── 持有引用
    │   ├── _config: IwanConfig（系统配置）
    │   ├── _bus: → EventBus（事件发布）
    │   ├── _trace: → TraceWriter（追踪写入）
    │   ├── _permission_manager: → PermissionManager（权限）
    │   ├── _mcp_manager: → McpServerManager（MCP 工具）
    │   ├── _checkpointer: → Checkpointer（检查点）
    │   ├── _task_registry: BackgroundTaskRegistry（子 Agent）
    │   └── _runs_dir: Path（运行目录）
    ├── run_and_capture(goal, session, store, ...)
    │   ├── Step 1: 初始化
    │   │   ├── 生成 run_id
    │   │   ├── 确定 run_path（session.runs_dir 或 root runs/）
    │   │   ├── 读取历史消息 / 笔记
    │   │   └── 创建 run 目录
    │   ├── Step 2: 加载上下文
    │   │   ├── global_ctx（~/.iwan/context.md）
    │   │   ├── project_ctx（.iwan/context.md）
    │   │   └── claude_md_prompt（CLAUDE.md）
    │   ├── Step 3: 创建核心组件
    │   │   ├── TaskManager（任务管理，存 run_path/.tasks/）
    │   │   ├── EventWriter（事件写入 events.jsonl）
    │   │   └── ExecutionContext（运行时状态容器）
    │   │       ├── messages: list[dict]（消息历史）
    │   │       ├── step: int（当前步骤）
    │   │       ├── status: "running"|"success"|"failed"
    │   │       └── result / reason: str
    │   ├── Step 4: 构建工具注册表
    │   │   _build_registry(task_manager, ...)
    │   │   ├── 第一类: 文件操作工具（18 种）
    │   │   ├── 第二类: Git 工具（5 种）
    │   │   ├── 第三类: 进程/网络工具（2 种）
    │   │   ├── 第四类: 代码质量工具（4 种）
    │   │   ├── 第五类: 依赖管理工具（2 种）
    │   │   ├── 第六类: 文档生成工具（3 种）
    │   │   ├── 第七类: 缓存工具（5 种）
    │   │   ├── 第八类: 角色管理工具（3 种）
    │   │   ├── 第九类: 任务管理工具（4 种）
    │   │   ├── 第十类: Skill 管理工具（5 种）
    │   │   ├── 第十一类: 笔记工具（条件注册）
    │   │   ├── 第十二类: 子 Agent 工具（条件注册）
    │   │   ├── 第十三类: MCP 工具（条件注册）
    │   │   ├── 第十四类: RAG 工具（条件注册）
    │   │   └── 第十五类: Checkpoint 工具（条件注册）
    │   ├── Step 5: 选择执行引擎
    │   │   ├── config.agent.engine == "langgraph" → LangGraphAgentLoop
    │   │   └── 否则 → AgentLoop
    │   ├── Step 6: 执行 Agent 循环 → loop.run(context)
    │   │
    │   │   ┌── AgentLoop（Legacy 引擎，Plan-Act-Observe 循环）
    │   │   │   ├── _provider: → LLMProvider（LLM API 调用）
    │   │   │   ├── _registry: → ToolRegistry（工具查找）
    │   │   │   ├── _bus: → EventBus（发布 StepStarted/StepFinished）
    │   │   │   ├── _permission_manager: → PermissionManager（工具权限）
    │   │   │   ├── _compactor: → Compactor（自动压缩）
    │   │   │   ├── _session_id: str（当前会话 ID）
    │   │   │   └── run(context) 方法
    │   │   │       while not context.is_done():
    │   │   │       ├── [Plan] provider.chat(messages, system) → LLM 响应
    │   │   │       ├── [Observe] 解析响应，更新 context.messages
    │   │   │       ├── [Act] 检测 tool_use → invoke_tool()
    │   │   │       │   ├── registry.get(name) → Tool 实例
    │   │   │       │   ├── permission_manager.check_and_wait() → (allowed, decision)
    │   │   │       │   │   ├── Tier 0-6 权限检查
    │   │   │       │   │   └── ASK 路径: EventBus → IpcBroadcaster → TCP → 客户端审批
    │   │   │       │   └── tool.invoke(params) → ToolResult
    │   │   │       ├── 工具结果追加到 context.messages
    │   │   │       ├── 检查 compact 条件 → compactor.compact_messages()
    │   │   │       └── 检查终止条件（end_turn / max_steps / cancelled）
    │   │   │
    │   │   │   ┌── Compactor（会话压缩器）
    │   │   │   │   ├── compact_messages(messages, provider, focus)
    │   │   │   │   │   ├── 估算 token 数
    │   │   │   │   │   ├── 序列化为文本
    │   │   │   │   │   ├── 调用 LLM 生成摘要
    │   │   │   │   │   └── 返回 CompactionResult(summary, tokens)
    │   │   │   │   └── write summary_*.md 到 session 目录
    │   │   │   │
    │   │   │   ┌── ToolRegistry（工具注册表）
    │   │   │   │   ├── _tools: dict[str, BaseTool]
    │   │   │   │   ├── register(tool) → 注册工具
    │   │   │   │   ├── get(name) → 获取工具实例
    │   │   │   │   └── 支持白名单过滤
    │   │   │   │
    │   │   │   └── LLMProvider（LLM 提供者）
    │   │   │       └── provider.chat(messages, system, **kwargs)
    │   │   │           → LLMResponse(text, usage, ...)
    │   │   │
    │   │   └── LangGraphAgentLoop（LangGraph 引擎，状态图 + Checkpoint）
    │   │       ├── 继承 AgentLoop 所有功能
    │   │       ├── _checkpointer: → Checkpointer（自动保存检查点）
    │   │       ├── _session_id: str（用作 thread_id）
    │   │       └── run(context) 方法
    │   │           ├── LangGraph StateGraph 构建
    │   │           │   ├── 节点 1: LLM 调用
    │   │           │   ├── 节点 2: 工具执行
    │   │           │   ├── 条件边: 是否继续循环
    │   │           │   └── 每步自动 checkpoint.save()
    │   │           └── 执行图 → 支持 checkpoint list/restore
    │   │
    │   └── Step 7: 保存结果
    │       └── store.append_messages() ← 新增消息写回磁盘
    │
    ├── list_checkpoints(thread_id) → list[dict]
    │   └── checkpointer.alist() → 异步迭代解析
    └── restore_checkpoint(thread_id, checkpoint_id) → dict
        └── checkpointer.aget_tuple() → 提取 channel_values
```

---

## 三、依赖注入关系（谁创建谁，谁用谁）

### 3.1 CoreApp 创建所有组件并建立依赖

```python
# CoreApp.run() 中的初始化顺序：

# Step 1: 创建 EventBus（所有组件共享的通信枢纽）
self._bus = EventBus()

# Step 2: 创建 PermissionManager
self._permission_manager = PermissionManager(policy_file=..., timeout_s=...)

# Step 3: 创建 IpcEventBroadcaster，订阅 EventBus
self._broadcaster = IpcEventBroadcaster(trace=self._trace)
self._bus.subscribe(self._broadcaster.handle)

# Step 4: 创建 SessionStore
store = SessionStore(sessions_root)

# Step 5: 创建 Checkpointer
self._init_checkpointer()

# Step 6: 创建 SessionManager（注入所有依赖）
self._sessions = SessionManager(
    store,                                          # 文件 I/O
    runner_factory=lambda: AgentRunner(             # Runner 工厂
        self._config,                               # 配置
        bus=self._bus,                              # EventBus 共享引用
        trace=self._trace,                          # TraceWriter
        permission_manager=self._permission_manager,# PermissionManager 共享引用
        mcp_manager=self._mcp_manager,              # MCP 管理器
        checkpointer=self._checkpointer,            # Checkpointer
    ),
    bus=self._bus,                                  # EventBus 共享引用
    provider=compact_provider,                      # LLM Provider
)

# Step 7: 创建 SocketServer
server = SocketServer(host, port, self._broadcaster, trace=self._trace)
server.register("session.send_message", self._session_send_handler)  # 注册 RPC handler
```

### 3.2 关键依赖传递图

```
                    ┌───────────────────────────────────┐
                    │           CoreApp                 │
                    │                                   │
                    │  创建 + 装配 + 生命周期管理       │
                    │                                   │
                    └─────────┬─────────────────────────┘
                              │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
    ┌──────────────┐ ┌────────────┐ ┌────────────┐
    │  EventBus    │ │Permission  │ │  Session   │
    │  (共享)      │ │ Manager    │ │  Manager   │
    └──────┬───────┘ └─────┬──────┘ └─────┬──────┘
           │               │              │
           │    ┌──────────┤              │
           │    │          │              │
           ▼    ▼          ▼              ▼
    ┌──────────────┐ ┌──────────────┐    │
    │IpcBroadcast  │ │ AgentRunner  │◄───┤  (通过 runner_factory 创建)
    │(订阅 EventBus)│ │  (每次新建)  │    │
    └──────────────┘ └──────┬───────┘    │
                            │            │
                   ┌────────┼────────┐   │
                   ▼        ▼        ▼   │
              ┌────────┐ ┌────────┐ ┌────────┐
              │ Lang   │ │ 权限   │ │ Store  │
              │ Graph  │ │ 检查   │ │(文件) │
              │ Agent  │ │ 调用   │ │        │
              │ Loop   │ │        │ │        │
              └────────┘ └────────┘ └────────┘
```

---

## 四、核心业务流程：一条消息的完整旅程

```
用户在客户端输入: "帮我写一个快速排序"
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. TCP 传输层                                                               │
│                                                                             │
│  客户端 ──JSON-RPC──► SocketServer.listen()                                 │
│                     │                                                       │
│                     ▼                                                       │
│            server 路由: "session.send_message" → _session_send_handler()   │
└─────────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. CoreApp RPC Handler                                                      │
│                                                                             │
│  _session_send_handler() [app.py:357]                                       │
│      └── self._sessions.send_message(session_id, content)                  │
└─────────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. SessionManager.send_message()                                            │
│                                                                             │
│  a. _get_session(sid)  ← 从内存字典查找 Session                             │
│  b. _locks[sid].acquire()  ← 获取会话锁（并发控制）                         │
│  c. _store.append_message(sid, "user", content)  ← 写入 thread.jsonl       │
│  d. 发布 SessionMessageReceivedEvent 到 EventBus                            │
│  e. runner_factory() → 创建 AgentRunner 实例                                │
│  f. runner.run_and_capture(goal, session, store, ...)                      │
└─────────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. AgentRunner.run_and_capture()                                            │
│                                                                             │
│  a. 初始化 LangGraphAgentLoop（含 Checkpointer）                            │
│  b. 构建工具列表（bash, write_file, read_file 等，带权限管理器）            │
│  c. 启动 LangGraphAgentLoop.run(context)                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. LangGraphAgentLoop.run() — Agent 执行循环                                 │
│                                                                             │
│  while not done:                                                            │
│    ├── LLM 调用 → 生成回复 / 工具调用                                       │
│    ├── 遇到工具调用 → PermissionManager.check_and_wait()                   │
│    │       ├── Tier 0-6 权限检查                                            │
│    │       ├── 需要审批 → 发布 PermissionRequestedEvent                    │
│    │       │       → EventBus → IpcEventBroadcaster → TCP 推送给客户端    │
│    │       │       → 客户端显示审批弹窗                                     │
│    │       │       → 用户点击「允许」                                       │
│    │       │       → 客户端 RPC: permission.respond → manager.respond()     │
│    │       │       → Future resolve → 恢复执行                              │
│    │       └── 返回 (allowed, decision)                                     │
│    ├── 执行工具（如写入文件、执行 bash）                                    │
│    ├── 工具结果写回 thread.jsonl                                            │
│    ├── 自动生成 Checkpoint（保存当前状态）                                   │
│    └── 检查是否需要 compact（消息过长时自动压缩）                           │
│                                                                             │
│  结束后:                                                                    │
│    └── 发布 SessionWaitingForInputEvent → EventBus → 推送给客户端          │
└─────────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 6. 客户端收到事件                                                           │
│                                                                             │
│  IpcEventBroadcaster 将事件封装为 EventPushEnvelope → TCP 发送              │
│  客户端 TUI 显示 Agent 的回复，等待用户下一条消息                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 五、CoreApp vs Runner 的区别

| 维度 | CoreApp | AgentRunner |
| --- | --- | --- |
| **定位** | 系统组装中心 + 中控调度器 | 单次 Run 的执行编排器 |
| **生命周期** | 进程级（跟随程序启动/退出） | 任务级（每次对话/任务创建一个） |
| **数量** | 全局唯一（单例） | 每次 run 创建一个，用完即弃 |
| **核心职责** | 装配所有组件、注册 RPC、管理 TCP 服务 | 加载配置、构建工具、编排 Agent 循环、保存结果 |
| **持有资源** | EventBus、PermissionManager、SessionManager、SocketServer | Checkpointer 引用、PermissionManager 引用、TraceWriter 引用 |
| **类比** | 公司 CEO（管整体运营） | 项目经理（管单个项目） |

### 为什么要分两层？

```
如果只有 CoreApp（不分层）:
┌─────────────────────────────────────────────────┐
│  CoreApp（什么都做）                             │
│  ├── 装配组件                                   │
│  ├── 监听 TCP 端口                              │
│  ├── 处理 RPC 请求                              │
│  ├── 创建 Agent 循环                            │
│  ├── 管理 LLM 调用                              │
│  ├── 管理工具调用权限                           │
│  └── ...                                        │
└─────────────────────────────────────────────────┘
→ 问题：CoreApp 太臃肿，难以测试和维护

分层后:
┌──────────────────────┐    ┌──────────────────────┐
│      CoreApp         │    │    AgentRunner       │
│  ┌────────────────┐  │    │  ┌────────────────┐  │
│  │ 装配 + 生命周期 │  │    │  │ 单次任务编排   │  │
│  ├────────────────┤  │    │  ├────────────────┤  │
│  │ RPC 路由       │  │    │  │ AgentLoop 调用 │  │
│  ├────────────────┤  │    │  ├────────────────┤  │
│  │ TCP 服务       │  │    │  │ 工具/权限管理  │  │
│  └────────────────┘  │    │  └────────────────┘  │
└──────────────────────┘    └──────────────────────┘
→ 好处：职责分离，可独立测试，易于替换引擎
```

---

## 六、EventBus 的"共享"设计

EventBus 是**全局共享的单一实例**，所有组件都引用同一个 `self._bus`：

```
         ┌─────────────┐
         │   EventBus   │  ← 唯一实例
         └──────┬──────┘
                │
    ┌───────────┼───────────┐
    │           │           │
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌─────────────┐
│Session │ │Runner  │ │Permission   │
│Manager │ │        │ │Manager      │
└────────┘ └────────┘ └─────────────┘
    │           │           │
    │  发布事件 │           │  发布事件
    │  ───────►│           │  ───────►
    │           │           │
    ▼           ▼           ▼
┌─────────────────────────────────────┐
│        EventBus 调度分发            │
│  ┌─────────────────────────────┐   │
│  │ 订阅者 1: IpcBroadcast       │   │ → 推送给客户端
│  │ 订阅者 2: TraceWriter        │   │ → 写入 trace
│  │ 订阅者 3: (其他)             │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

**为什么要共享？**
- 所有组件发布的事件都能被所有订阅者接收到
- 新增订阅者（如日志、监控）无需修改发布者代码
- 实现模块解耦

---

## 七、组件间的交叉引用一览

| 组件 A | 引用 → 组件 B | 用途 |
| --- | --- | --- |
| CoreApp | → EventBus | 创建并共享给所有子组件 |
| CoreApp | → PermissionManager | 创建并共享给 SessionManager 和 Runner |
| CoreApp | → SessionManager | 创建并持有，RPC handler 调用其方法 |
| CoreApp | → IpcEventBroadcaster | 创建并订阅 EventBus |
| CoreApp | → TraceWriter | 创建并订阅 EventBus |
| CoreApp | → Checkpointer | 创建并共享给 Runner |
| CoreApp | → SocketServer | 创建，RPC 路由到 CoreApp 的 handler |
| SessionManager | → EventBus | 发布会话生命周期事件 |
| SessionManager | → PermissionManager | 通过 runner_factory 间接传递 |
| SessionManager | → SessionStore | 直接持有，文件 I/O |
| SessionManager | → AgentRunner | 通过 runner_factory 按需创建 |
| AgentRunner | → EventBus | 发布运行状态事件 |
| AgentRunner | → PermissionManager | 检查工具调用权限 |
| AgentRunner | → Checkpointer | 保存/恢复检查点 |
| AgentRunner | → TraceWriter | 写入追踪记录 |
| LangGraphAgentLoop | → PermissionManager | 工具调用前检查权限 |
| LangGraphAgentLoop | → Checkpointer | 自动保存检查点 |

---

## 八、总结：CoreApp 的真正价值

### CoreApp = 系统的「大脑皮层」

```
┌─────────────────────────────────────────────────────────────┐
│                         CoreApp                             │
│                                                             │
│  不是"做具体业务"，而是"组织业务"：                        │
│                                                             │
│  1. 启动时：装配所有组件，连接依赖关系                       │
│     ├── 创建 EventBus → 连接所有模块的通信管道              │
│     ├── 创建 PermissionManager → 配置权限守门员             │
│     ├── 创建 SessionManager → 配置会话管家                 │
│     ├── 创建 SocketServer → 开启 TCP 门户                  │
│     └── 注册 RPC handlers → 建立"客户端请求 → 内部调用"路由 │
│                                                             │
│  2. 运行时：作为 RPC 请求的入口点                           │
│     ├── 客户端 → TCP → SocketServer → CoreApp handler      │
│     ├── handler → SessionManager / PermissionManager 等    │
│     └── 组件间通过 EventBus 松耦合通信                     │
│                                                             │
│  3. 关闭时：优雅清理所有资源                                │
│     ├── 取消所有正在运行的 Task                             │
│     ├── 停止 MCP 服务器                                    │
│     ├── 关闭 Socket 服务                                   │
│     ├── 关闭 Checkpointer                                  │
│     └── 关闭 TraceWriter                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 三层架构总结

| 层 | 组件 | 职责 |
| --- | --- | --- |
| **传输层** | SocketServer | TCP 通信、JSON-RPC 解析 |
| **调度层** | CoreApp | RPC 路由、生命周期管理、依赖注入 |
| **业务层** | SessionManager, AgentRunner, PermissionManager | 具体业务逻辑 |
| **通信层** | EventBus, IpcEventBroadcaster | 内部事件通信、外部事件推送 |

### 一句话记住

> **CoreApp 是舞台，Runner 是演员，SessionManager 是场记，PermissionManager 是保安，EventBus 是对讲机。**
>
> 没有 CoreApp，其他组件各自为政，无法协同工作。
