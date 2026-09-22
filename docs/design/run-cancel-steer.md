# 设计中：运行中任务取消（run.cancel）与运行中修正（run.steer）

> 状态：实现中 · 2026-09-21
> 对标能力：真实 Claude Code 的 Esc 中断当前 turn、运行中直接打字插入指令。

## 1. 目标

用户在 TUI 里能看到并操作一个**正在运行**的任务：

1. **取消**：按 Esc 中止当前 run，保留已产生的工具轨迹，会话可以马上继续用。
2. **修正（steering）**：任务跑偏时直接输入一句话（"方向错了，改用 asyncio"），
   不打断当前正在执行的工具，在**下一次调用模型前**注入对话历史生效。

## 2. 为什么不能做成"发一条普通消息"

daemon 是双进程架构，一次 run 由 `session.send_message` 的 handler 协程持有
（`await runner.run_and_capture(...)`）。RPC 是请求-响应模型：

- 同一条连接上，handler 正在 await 时读不到下一条命令 → 必须用**另一条连接**
  或复用多连接发 `run.cancel` / `run.steer`；
- 所以需要一个进程内索引，把 run_id 映射到"活着的 asyncio.Task + 修正队列"，
  这就是 `run_registry`。

## 3. 架构

```
TUI (Esc / 运行中输入)
  │ JSON-RPC: run.cancel {run_id} / run.steer {run_id, message}
  ▼
SocketServer ──► CoreApp._run_cancel_handler / _run_steer_handler
                     │                        │
                     ▼                        ▼
             run_registry.request_cancel   run_registry.add_steer
              （打标记 + task.cancel()）    （消息入该 run 的 deque）
                     │                        │
        CancelledError 注入 run 协程          │（不阻塞，入队即返回）
                     ▼                        ▼
        AgentRunner 优雅收尾          引擎在"下一次 LLM 调用前" pop_steers
        · 保存轨迹到会话历史           · run_tool_turn 循环顶部
        · 发 RunFinishedEvent          · langgraph _chat_node / legacy loop
          (cancelled=True)             · 包装成 user 消息注入 messages
```

### 关键决策

| 决策 | 选择 | 为什么 |
|---|---|---|
| 取消粒度 | `task.cancel()` + 协作式收尾 | 硬杀线程会丢轨迹、留下脏会话状态；CancelledError 让 runner 的既有 except 分支（保存 messages + 发 RunFinishedEvent）原样复用 |
| steer 生效时机 | 只在**回合边界**（调 LLM 前）消费 | 工具执行中途改 messages 会产生非法的 tool_use/tool_result 交错；Claude Code 同样不打断进行中的工具 |
| steer 载体 | 独立 user 消息，前缀 "User Steering" | 与原始 goal 区分，重放历史时能看出用户何时改向；不篡改原消息 |
| run 不在了怎么办 | `accepted=False` 返回，不报错 | run 可能刚好自然结束；客户端据此改发 `session.send_message`，消息不丢 |
| 取消来源区分 | `cancel_requested` 标志 | 客户端断线也会 CancelledError；只有用户主动取消才走"优雅停住"路径，断线仍走原清理逻辑 |

## 4. 协议变更（WIRE_PROTOCOL.md 需重新生成）

- `run.cancel` → `RunCancelCommand(run_id)` / `RunCancelResult(accepted)`
- `run.steer` → `RunSteerCommand(run_id, message)` / `RunSteerResult(accepted, queued)`
- `RunFinishedEvent` 已有 `reason` 字段（取消时为 `"cancelled"`），runner 的
  CancelledError 分支已保存轨迹并发布该事件，无需改动
- 补漏：`SessionEngineChangedEvent` 之前定义了但没进 `Event` union，已修

## 5. 实现进度

- [x] `run_registry.py`：ActiveRun / register / cancel / steer 队列
- [x] `bus/commands.py` + `events.py`：两个命令 + union 修复
- [x] `session/manager.py`：send_message 任务化（create_task + 登记 + 取消分支）
- [x] `app.py`：`run.cancel` / `run.steer` handler 与注册
- [x] 引擎消费点：`tool_turn.run_tool_turn`、`langgraph_loop._chat_node`、`loop.py`（legacy）
- [ ] TUI：Esc 取消、运行中输入 → steer；跟踪当前 run_id
- [ ] `iwan` CLI：`iwan cancel <run_id>` / `iwan steer <run_id> "..."` 调试命令
- [ ] 测试：单元（registry/handler）+ 集成（真实 run 取消后历史完整）

## 6. 边界与已知限制

- **工具执行不可中断**：cancel 会等当前 `invoke_tool` 完成点生效（bash 长命令
  由 invoke_tool 内部超时兜底；沙箱 Job Object 加固后杀子进程会更干净）。
- **多会话并行**：注册表按 run_id 全局索引，天然支持；但 TUI 需要维护
  per-session 的"当前活跃 run_id"（从 RunStartedEvent 记录）。
- **steer 积压**：同一 run 连发多条会全部注入；上限暂不设（deque 内存极小）。
- **崩溃恢复**：run 中途 daemon 挂掉，注册表是进程内的，重启后自然清空——
  与 crash recovery 的 recovery_context 机制正交，不需要额外处理。
