# SessionManager 组件详解笔记

> 基于 `d:\IwanClaude\src\iwan_claude\core\session\manager.py` 及相关模块的学习总结

---

## 一、SessionManager 核心组件介绍

### 1. `_store` — SessionStore（会话存储管理器）

**职责**：负责会话数据的**文件系统持久化**，是 SessionManager 与磁盘之间的桥梁。

- **文件读写**：以 JSON/JSONL 格式存储会话元数据和消息历史
- **自动建目录**：操作前自动创建目录结构，无需手动准备
- **消息追加**：使用追加模式写入 `thread.jsonl`，天然支持并发写入安全
- **工具配对修复**：`_trim_orphan_tool_use()` 移除尾部未配对的 tool_use，防止传给 LLM 时报错

**关键方法**：

| 方法 | 功能 |
| --- | --- |
| `write_meta(session)` | 写入 `meta.json`（会话元数据） |
| `read_meta(sid)` | 从 `meta.json` 读取会话元数据 |
| `append_message(sid, role, content)` | 追加一条消息到 `thread.jsonl` |
| `read_messages(sid)` | 读取完整消息历史，返回可直接传给 LLM 的 messages 列表 |
| `write_compacted(sid, messages)` | 压缩后覆盖写入 `thread.jsonl`（自动备份原文件） |
| `write_messages(sid, messages)` | checkpoint 恢复后覆盖写入 `thread.jsonl` |
| `append_note(sid, content, run_id)` | 追加笔记到 `notes.md` |
| `list_sessions()` | 列出所有已存储会话（按 updated_at 降序） |

---

### 2. `_runner_factory` — AgentRunner 工厂函数

**职责**：一个 `Callable[[], AgentRunner]` 工厂函数，用于**按需创建 AgentRunner 实例**。

- **为什么用工厂而不是直接持有 Runner？**
  - SessionManager 不需要长期持有 Runner（Runner 是有生命周期的重型对象）
  - 每次需要执行或查询时，通过工厂创建新的 Runner，用完关闭
  - 避免了在 SessionManager 中管理 Runner 的生命周期（初始化/关闭/资源清理）
- **典型使用场景**：
  - `send_message()` → 创建 Runner 执行 Agent 运行
  - `list_checkpoints()` → 创建 Runner 查询检查点列表
  - `restore_checkpoint()` → 创建 Runner 获取检查点数据

---

### 3. `_bus` — EventBus（事件总线）

**职责**：发布 Session 相关事件，实现模块解耦。

SessionManager 在关键生命周期节点发布事件：

| 事件 | 触发时机 |
| --- | --- |
| `SessionCreatedEvent` | 创建会话后 |
| `SessionMessageReceivedEvent` | 收到用户消息后 |
| `SessionResumedEvent` | 从 waiting_for_input 恢复时 |
| `SessionWaitingForInputEvent` | chat 模式下 Agent 运行结束后 |
| `SessionClosedEvent` | 会话关闭后 |
| `SessionRenamedEvent` | 重命名会话后 |
| `SkillInvokedEvent` | Skill 被触发时（手动/自动） |

其他模块（如 TUI、IPC 广播器）通过订阅这些事件实现联动。

---

### 4. `_sessions` — 内存会话字典（`dict[str, Session]`）

**职责**：内存中保存当前活跃的会话对象。

- **键**：会话 ID（如 `sess-abc123`）
- **值**：`Session` 对象（包含 id、mode、status、title、时间戳、run_ids 等）
- **作用**：
  1. 快速查找会话（O(1) 查找，比读文件快）
  2. 缓存最新状态（内存中状态可能比磁盘更新）
  3. 通过 `list_sessions()` 将内存状态与磁盘状态合并

**与磁盘的关系**：
- 内存是"权威数据源"，每次修改都会同步写回磁盘
- 进程重启后，从磁盘重新加载（`store.list_sessions()`）

---

### 5. `_locks` — 会话锁字典（`dict[str, asyncio.Lock]`）

**职责**：确保**同一会话的并发安全**。

- 每个会话 ID 对应一个 `asyncio.Lock`
- 防止同一会话同时处理多条消息（避免竞态条件）
- 用法：在 `send_message()`、`close()`、`compact()`、`restore_checkpoint()` 等方法中，先获取锁再操作
- 如果会话正在处理中，其他请求会抛出 `SESSION_BUSY` 错误

---

### 6. `_skill_loader` — SkillLoader（Skill 加载器）

**职责**：管理 Skill 的加载、解析和匹配。

- 解析 `/skill_name args` 形式的手动触发
- 根据消息内容自动匹配最合适的 Skill
- 将 Skill 渲染为 system prompt 覆盖和工具白名单

---

### 7. `_provider` — LLMProvider（LLM 提供商，可选）

**职责**：用于 compact（消息压缩）功能。

- 只有在 compact 时才需要
- 如果未设置 provider，compact 会抛出错误

---

## 二、为什么 checkpoint list / restore 要通过 Runner 实现？

### 核心原因

1. **Checkpointer 在 Runner 中初始化**
   - Checkpointer 的生命周期与 Runner 绑定（在 `AgentRunner._init_checkpointer()` 中延迟初始化）
   - `SessionManager` 不直接持有 Checkpointer，而是通过 Runner 间接访问

2. **Runner 封装了 LangGraph 引擎的交互逻辑**
   - `AgentRunner.list_checkpoints(thread_id)` 内部处理了 `checkpointer.alist()` 的异步迭代
   - `AgentRunner.restore_checkpoint(thread_id, checkpoint_id)` 内部处理了 `checkpointer.aget_tuple()` 和状态提取
   - 这些逻辑涉及 LangGraph 特定 API，封装在 Runner 中更合理

3. **设计模式**：SessionManager 是"门面"层，Runner 是"引擎"层
   - SessionManager 负责会话生命周期管理（创建/关闭/并发控制）
   - Runner 负责与 Agent 引擎（LangGraph/Legacy）的交互
   - 这种分层使得替换引擎实现更容易（只需修改 Runner）

### 流程示意

```
用户请求 → SessionManager.list_checkpoints(sid)
         → runner_factory() 创建 AgentRunner
         → runner.list_checkpoints(sid)  ← 内部操作 Checkpointer
         → runner.close() 关闭清理资源
         → 返回结果
```

---

## 三、Session 元数据包含什么？

对应 `Session` 数据类（`model.py`），序列化为 `meta.json`：

| 字段 | 类型 | 说明 | 示例 |
| --- | --- | --- | --- |
| `id` | `str` | 会话唯一标识 | `"sess-abc123def456"` |
| `mode` | `Literal["one_shot", "chat"]` | 会话模式 | `"chat"` |
| `status` | `Literal["active", "waiting_for_input", "closed"]` | 当前状态 | `"waiting_for_input"` |
| `title` | `str` | 会话标题 | `"代码审查"` |
| `created_at` | `str` | 创建时间（ISO 8601 UTC） | `"2026-07-25T10:30:00+00:00"` |
| `updated_at` | `str` | 最后更新时间 | `"2026-07-25T10:35:00+00:00"` |
| `run_ids` | `list[str]` | 该会话的所有运行 ID 列表 | `["run-20260725-103000-abc123"]` |

**状态值说明**：
- `active`：刚创建，等待第一条消息
- `waiting_for_input`：chat 模式下 Agent 运行结束，等待用户继续
- `closed`：已关闭，不可再发送消息

---

## 四、运行时文件结构详解

### 4.1 顶层目录结构

```
项目根目录/
└── ~/.iwan/
    ├── sessions/                   # 会话存储根目录（可通过 IWAN_SESSIONS_DIR 配置）
    │   ├── sess-abc123def456/      # 每个会话一个目录
    │   │   ├── meta.json           # 会话元数据
    │   │   ├── thread.jsonl        # 消息历史（JSONL 格式）
    │   │   ├── notes.md            # 会话笔记
    │   │   ├── summary_*.md        # 压缩摘要（每次 compact 生成一个）
    │   │   ├── thread_*.jsonl.bak  # 压缩/恢复前的备份
    │   │   └── runs/               # 会话级运行记录（★ 主要路径）
    │   │       ├── 20260725-103000-abc123/
    │   │       │   ├── events.jsonl
    │   │       │   └── .tasks/
    │   │       └── ...
    │   └── sess-xyz789.../
    │
    ├── runs/                       # 全局运行记录（仅无 session 时使用的回退路径）
    │   └── 20260725-103000-abc123/
    │       ├── events.jsonl
    │       └── .tasks/
    │
    ├── traces/                     # 追踪日志
    │   └── daemon.jsonl            # 全局 trace 文件（可配置路径）
    │
    ├── checkpoints.db              # LangGraph 检查点数据库（sqlite 后端时）
    │
    ├── context.md                  # 全局上下文
    │
    └── skills/                     # 用户级 Skill
        └── {skill-name}/
            └── SKILL.md
```

> **两个 `runs/` 目录的区别**：
> - **session 下的 `runs/`**：★ 主要路径。通过 SessionManager 发起的所有 run 都存放在这里，与对应会话绑定。
> - **根目录下的 `runs/`**：回退路径。仅在没有关联 session 的独立 run（如 one-shot 模式、直接 API 调用）时使用。
>
> 代码逻辑（`runner.py` `run_and_capture()`）：
> ```python
> if session is not None and store is not None:
>     run_path = store.runs_dir(session.id) / run_id  # session 级 runs/
> else:
>     run_path = self._runs_dir / run_id              # 根目录 runs/
> ```

### 4.2 各文件内容说明

| 文件 | 格式 | 内容说明 |
| --- | --- | --- |
| `meta.json` | JSON | 会话元数据（id/mode/status/title/timestamps/run_ids） |
| `thread.jsonl` | JSONL（每行一个 JSON） | 消息历史，每条包含 `ts`、`role`、`content`、`run_id` |
| `notes.md` | Markdown | 会话笔记，按 `## Note (timestamp, run_id)` 格式追加 |
| `events.jsonl` | JSONL | 单次 run 的事件日志（所有 EventBus 事件） |
| `daemon.jsonl` | JSONL | 全局 trace 文件，记录 LLM 调用、事件等追踪信息 |
| `summary_*.md` | Markdown | compact 生成的摘要文件，按时间戳命名 |
| `checkpoints.db` | SQLite | LangGraph 检查点持久化存储（仅 sqlite 后端） |

### 4.3 thread.jsonl 格式示例

```jsonl
{"ts": "2026-07-25T10:30:00+00:00", "role": "user", "content": "你好，帮我写个排序函数", "run_id": "20260725-103000-abc123"}
{"ts": "2026-07-25T10:30:05+00:00", "role": "assistant", "content": "好的，我来帮你实现...", "run_id": "20260725-103000-abc123"}
{"ts": "2026-07-25T10:30:10+00:00", "role": "assistant", "content": [{"type": "tool_use", "name": "WriteFile", "id": "toolu_123", "input": {...}}], "run_id": "20260725-103000-abc123"}
{"ts": "2026-07-25T10:30:11+00:00", "role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_123", "content": "File written"}], "run_id": "20260725-103000-abc123"}
```

### 4.4 路径配置

| 配置项 | 默认值 | 环境变量 |
| --- | --- | --- |
| 会话目录 | `~/.iwan/sessions` | `IWAN_SESSIONS_DIR` |
| trace 文件 | `~/.iwan/traces/daemon.jsonl` | `IWAN_TRACE_FILE` |
| checkpoints DB | `.iwan/checkpoints.db` | `IWAN_AGENT_CHECKPOINT_DB_PATH` |

---

## 五、Compact（消息压缩）机制

### 5.1 调用时机

- **手动 compact**：用户通过 RPC 调用 `session.compact(sid, focus)` 时触发
- **auto compact**：在 Agent 运行过程中，当消息数超过 `auto_threshold` 时由 LangGraphAgentLoop 自动触发

### 5.2 压缩流程

```
Compactor.compact_messages(messages, provider, focus)
    │
    ├── 1. 估算原始 token 数（字符数 / 4）
    ├── 2. 将消息列表序列化为纯文本（_messages_to_text）
    ├── 3. 构建压缩 prompt + 历史文本
    ├── 4. 调用 LLM 生成摘要
    ├── 5. 估算摘要 token 数（使用 usage.output_tokens）
    │
    └── 返回 CompactionResult(summary_text, original_token_estimate, summary_tokens)
```

### 5.3 "无收益则报错" 的实现分析

#### 你的疑问是对的！

查看 `compactor.py` 中的 `compact_messages()` 方法：

```python
# 当前代码：只在 LLM 调用失败时返回 None
try:
    response = await provider.chat(...)
except Exception:
    logger.exception("compactor: LLM call failed, skipping compaction")
    return None

summary_text = response.text.strip()
if not summary_text:
    logger.warning("compactor: LLM returned empty summary, skipping compaction")
    return None
```

**实际情况**：
- ✅ 检查了 LLM 调用是否成功
- ✅ 检查了摘要是否为空
- ❌ **没有检查** "压缩后 token 数是否小于原始 token 数"

#### 在 SessionManager.compact() 中的处理

```python
result = await compactor.compact_messages(messages, self._provider, focus=focus)
# 只检查 result 是否为 None（即 LLM 调用是否成功）
if result is None:
    raise HandlerError(-32021, "compaction failed or not beneficial")
```

`result is None` 只在**失败**时触发，并不代表"没有收益"。变量名 `-32021` 虽然叫 "not beneficial"，但实际检查的是 LLM 是否返回了有效摘要。

#### 结论

**当前代码确实没有真正实现"无收益检查"**。正确的做法应该是：

```python
# 理想实现（未实际存在）
if result.summary_tokens >= result.original_token_estimate:
    return None  # 压缩后反而更大或一样大
```

这是一个可以考虑补充的优化点。

### 5.4 压缩后的写入

压缩成功后，`SessionManager.compact()` 会：
1. 生成两条新消息（摘要 user 消息 + assistant 确认消息）
2. 调用 `store.write_compacted()` 覆盖 `thread.jsonl`
3. 原文件被重命名为 `thread_<timestamp>.jsonl.bak` 作为备份

---

## 六、Checkpoint 机制详解

### 6.1 Checkpoint 保留了什么

Checkpoint 存储在 LangGraph Checkpointer 中，每个 checkpoint tuple 包含三部分：

| 部分 | 内容 |
| --- | --- |
| `config` | 配置信息，包含 `configurable.thread_id` 和 `configurable.checkpoint_id` |
| `metadata` | 元数据，包含 `step`（当前步骤数） |
| `checkpoint` | 实际状态数据 |

`checkpoint` 部分的结构：

| 字段 | 说明 |
| --- | --- |
| `ts` | 检查点创建时间戳 |
| `channel_values` | 工作流的所有通道值 |

`channel_values` 中的关键字段：

| 字段 | 说明 |
| --- | --- |
| `messages` | 完整的消息历史列表（每条包含 role、content） |
| `step` | 当前步骤编号 |
| `status` | 运行状态（success/failed/cancelled） |
| `_tool_calls` | 待处理的工具调用列表 |
| `_stop_reason` | 停止原因 |

### 6.2 Checkpoint 保存在哪里

**这取决于配置的后端**：

| 后端 | 存储位置 | 特点 |
| --- | --- | --- |
| `none` | 不存储 | 无法使用 checkpoint 功能 |
| `memory` | 内存（`InMemorySaver`） | 进程重启后丢失，适合开发测试 |
| `sqlite` | SQLite 数据库文件 | 持久化存储，默认路径为 `.iwan/checkpoints.db` |

**初始化位置**：`AgentRunner._init_checkpointer()` 中根据 `config.agent.checkpoint_backend` 创建。

### 6.3 Checkpoint 是如何生成的

LangGraph 引擎在每次状态更新时**自动创建检查点**：
- 每当 Agent 完成一个步骤（如 LLM 响应、工具调用），LangGraph 会自动保存当前状态
- 通过 `thread_id`（即 `session_id`）关联到具体会话
- 一次 run 可能产生多个 checkpoint（每个步骤一个）

### 6.4 Restore Checkpoint 流程

```
用户请求 → SessionManager.restore_checkpoint(sid, checkpoint_id)
         │
         ├── 1. 创建 AgentRunner 实例
         ├── 2. runner.restore_checkpoint(sid, checkpoint_id)
         │       └── checkpointer.aget_tuple({thread_id, checkpoint_id})
         │           → 获取 checkpoint tuple
         │           → 从 channel_values 提取 messages、step 等
         ├── 3. runner.close() 关闭清理
         │
         ├── 4. 从状态中提取 messages 和 step
         ├── 5. store.write_messages(sid, messages)  ← 覆盖 thread.jsonl
         ├── 6. session.run_ids 截断到 step
         ├── 7. 更新 meta.json
         │
         └── 返回 {"checkpoint_id", "step", "messages": len}
```

**关键注意**：恢复操作会**覆盖当前会话的所有消息历史**，原始 thread.jsonl 会被备份。

---

## 七、内存与会话字典总结

### 7.1 内存中的两套字典

| 字典 | 键 | 值 | 生命周期 |
| --- | --- | --- | --- |
| `_sessions` | session_id | `Session` 对象 | 随 SessionManager 存在 |
| `_locks` | session_id | `asyncio.Lock` | 随 SessionManager 存在 |

### 7.2 `_sessions` 字典的作用

- **快速查找**：`_get_session(sid)` 直接从内存查找，无需读文件
- **状态缓存**：内存中的状态比磁盘新（修改时先改内存再写磁盘）
- **合并来源**：`list_sessions()` 会合并磁盘数据和内存数据

### 7.3 `_locks` 字典的作用

- **并发控制**：每个会话的操作（发送消息、关闭、压缩等）都通过 `asyncio.Lock` 串行化
- **防止竞态**：避免同一会话同时处理多条消息导致数据不一致

### 7.4 磁盘 vs 内存的数据流向

```
创建会话 → 内存（_sessions）+ 磁盘（meta.json）同步写入
         ↓
修改会话 → 先改内存对象 → 再 write_meta 到磁盘
         ↓
读取会话 → 优先从内存查找（_get_session）
         ↓
重启恢复 → 从磁盘（meta.json）重新加载到内存
```

---

## 八、总结：组件协作关系

```
                    ┌─────────────────────────────────────────┐
                    │           SessionManager                │
                    │                                         │
                    │  _sessions (内存字典)                    │
                    │  ┌────────────────────────────────┐     │
                    │  │  sid → Session 对象            │     │
                    │  └────────────────────────────────┘     │
                    │                                         │
                    │  _locks (锁字典)                         │
                    │  ┌────────────────────────────────┐     │
                    │  │  sid → asyncio.Lock            │     │
                    │  └────────────────────────────────┘     │
                    │                                         │
                    │  依赖注入：                              │
                    │  ├── _store → SessionStore（磁盘 I/O）  │
                    │  ├── _runner_factory → AgentRunner 工厂 │
                    │  ├── _bus → EventBus（事件发布）        │
                    │  ├── _provider → LLMProvider（压缩用）   │
                    │  └── _skill_loader → SkillLoader        │
                    └─────────────────────────────────────────┘
```

**数据流图**：

```
用户 RPC → SessionManager.send_message(sid, content)
         │
         ├── _get_session(sid)  ← 从内存字典查找
         ├── _locks[sid] 加锁
         ├── _store.append_message(sid, "user", content)
         ├── 解析 @filename 和 /skill 语法
         ├── runner_factory() → AgentRunner
         ├── runner.run_and_capture(goal, session, store, ...)
         │       ├── 创建 LangGraphAgentLoop（含 Checkpointer）
         │       ├── Agent 执行循环（LLM 调用 + 工具调用）
         │       ├── 自动生成 Checkpoint
         │       └── 消息追加到 store
         ├── 更新 session 状态
         └── _bus.publish(SessionWaitingForInputEvent)
```

---

## 九、关键代码文件索引

| 文件 | 作用 |
| --- | --- |
| `core/session/manager.py` | SessionManager 主类（会话生命周期管理） |
| `core/session/store.py` | SessionStore（文件 I/O 层） |
| `core/session/model.py` | Session 数据模型定义 |
| `core/tools/builtin/checkpoint.py` | ListCheckpointsTool / RestoreCheckpointTool |
| `core/runner.py` | AgentRunner（Agent 执行引擎封装 + Checkpoint 操作） |
| `core/compact/compactor.py` | Compactor（消息压缩实现） |
| `core/runs.py` | 运行 ID 生成和运行目录管理 |
| `core/trace/writer.py` | TraceWriter（异步追踪写入） |
| `core/config.py` | 配置定义（含 trace、checkpoint 等路径） |
| `core/app.py` | CoreApp（组装 SessionManager 和所有依赖） |
