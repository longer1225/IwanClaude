# PermissionManager 权限管理器详解笔记

> 基于 `d:\IwanClaude\src\iwan_claude\core\permissions\manager.py`、`policy.py`、`storage.py` 的学习总结

---

## 一、核心概念解答

### 1. 什么是 deny_patterns "bash only，不可被缓存绕过"？

**含义**：`deny_patterns` 只对 `bash` 工具生效（通过 `command` 变量判断），且一旦命中，**直接返回 DENY**，不检查任何缓存（session_always 或 persistent_always）。

```python
# Tier 1: deny_patterns（bash only，不可被缓存绕过）
if command and policy:
    for pat in policy.deny_patterns:
        if re.search(pat, command):
            return False, "auto_deny"   # 直接拒绝，跳过所有缓存检查
```

**设计意图**：某些高危 bash 命令（如 `rm -rf /`）必须被无条件拒绝，即使用户之前设置了 `always_allow bash`，deny_pattern 依然优先。这是安全底线。

---

### 2. Tier 1-6 优先级说明

是的，**Tier 1 最高，Tier 6 最低**。这是一个典型的「短路求值」链式判断：

| Tier | 规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| **0** | Auto Mode 自动批准 | 快速通道 | 非 bash 工具 + auto_mode 允许 → 直接放行 |
| **1** | deny_patterns | 硬拒绝 | bash only，命中直接 DENY，**不可被缓存绕过** |
| **2** | OUTSIDE_CWD_HEURISTICS | 强制 ASK | bash only，命中强制 ASK，**不可被任何缓存绕过** |
| **3** | session always 缓存 | 内存缓存 | `(session_id, tool_name)` 命中 → 直接返回 |
| **4** | persistent always | 持久缓存 | `tool_name` 命中 → 直接返回（跨 session） |
| **5** | allow_patterns | 硬允许 | bash only，命中直接 ALLOW |
| **6** | tool default | 默认策略 | 工具的默认决策（ALLOW/DENY/ASK） |

> 注意 Tier 0 是我补充的代码中实际存在的自动批准逻辑（在 Tier 1 之前检查），源码注释中未标记为独立 Tier。

---

### 3. Model Preset 是什么？

`model_preset` 控制 Agent 使用哪个 LLM 模型。三个预设值：

| 预设值 | 含义 | 典型用途 |
| --- | --- | --- |
| `fast` | 快速模型 | 简单查询、快速响应 |
| `balanced` | 平衡模型（默认） | 日常对话、代码编写 |
| `powerful` | 强大模型 | 复杂推理、代码审查、架构设计 |

**切换机制**：运行时通过 `set_model_preset()` 动态切换，下一次 Agent run 会使用新预设对应的模型。在 `Runner.run_and_capture()` 中读取预设并覆盖 `config.llm.default_model`。

---

### 4. 状态机：auto_mode / effort_level / model_preset

你理解得完全正确！这些本质上就是**状态机**：

- **定义**：一组有限的合法值 + 根据不同值执行不同行为的设计模式
- **auto_mode**：`("off", "read_only", "on")` → 控制哪些工具可以自动批准
- **effort_level**：`("minimal", "low", "medium", "high", "max")` → 控制 Agent 执行深度
- **model_preset**：`("fast", "balanced", "powerful")` → 控制使用哪个 LLM

```python
# 状态机校验示例
if mode not in AUTO_MODES:           # 非法值拒绝
    raise ValueError(...)
self._auto_mode = mode               # 设置状态
```

> 你说的「就是一种字段，有几种只可以填的值，根据不同的值来进行不同的行为」——这就是状态机的核心思想，在业务代码中非常常见。

---

### 5. pending requests 是什么？

**是的，`_pending` 就是用来存储待审批请求的数据载体**。

数据结构：
```python
self._pending: dict[str, _PendingRequest] = {}
# 键: tool_use_id (如 "call_abc123")
# 值: _PendingRequest(future, session_id, tool_name)
```

`_PendingRequest` 数据类：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `future` | `asyncio.Future[str]` | 异步 Future，等待审批结果 |
| `session_id` | `str` | 所属 session |
| `tool_name` | `str` | 工具名称 |

**写入时机**：`check_and_wait()` 进入 ASK 路径时
**删除时机**：`respond()` 被调用时（`pop`）、超时、或 `cancel_session()` 时

---

### 6. policy 和 policy_file 是什么？

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `policies` | `dict[str, ToolPolicy] \| None` | 工具策略映射，默认使用 `DEFAULT_POLICIES` |
| `policy_file` | `Path \| None` | 策略文件路径，用于持久化缓存的读写 |

**policies 字典的内容示例**（来自 `policy.py` DEFAULT_POLICIES）：

```python
{
    "bash":               ToolPolicy(default=PermissionDecision.ASK),
    "write_file":         ToolPolicy(default=PermissionDecision.ASK),
    "read_file":          ToolPolicy(default=PermissionDecision.ALLOW),
    "list_dir":           ToolPolicy(default=PermissionDecision.ALLOW),
    "note_save":          ToolPolicy(default=PermissionDecision.ALLOW),
    "list_checkpoints":   ToolPolicy(default=PermissionDecision.ALLOW),
    "restore_checkpoint": ToolPolicy(default=PermissionDecision.ALLOW),
}
```

`ToolPolicy` 数据类：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `default` | `PermissionDecision` | 默认决策（ALLOW/DENY/ASK） |
| `allow_patterns` | `list[str]` | 允许的正则模式（bash only） |
| `deny_patterns` | `list[str]` | 拒绝的正则模式（bash only） |

**policy_file**：持久化缓存的 TOML 文件，默认路径 `~/.iwan/policy.toml`

---

### 7. 持久化缓存存在哪？

两个层级的缓存：

| 缓存 | 存储位置 | 生命周期 | 写入时机 |
| --- | --- | --- | --- |
| `_session_always` | 内存字典 | 进程级，重启丢失 | 用户选择 `always_allow` / `always_deny` |
| `_persistent_always` | `~/.iwan/policy.toml` | 永久（跨 session） | 用户选择 `always_allow` / `always_deny` |

**policy.toml 文件格式**：
```toml
# ~/.iwan/policy.toml
# 由 iwan-core 自动管理，手动编辑生效但格式须正确

[always]
bash = "allow"
write_file = "deny"
```

---

## 二、6 层权限检查流程详解

### 完整流程图

```
check_and_wait(tool_use_id, tool_name, params, session_id, event_emitter)
│
├── Tier 0: Auto Mode 快速通道
│   └── 非 bash 工具 + auto_mode_allows(tool_name)? → auto_allow
│
├── Tier 1: deny_patterns（bash only）
│   └── 命中拒绝模式? → auto_deny（直接返回，不可被缓存绕过）
│
├── Tier 2: OUTSIDE_CWD_HEURISTICS（bash only）
│   └── 命中危险路径? → 强制 ASK（不可被任何缓存绕过）
│
├── Tier 3: session_always 缓存
│   └── (session_id, tool_name) 命中? → auto_allow / auto_deny
│
├── Tier 4: persistent_always 缓存
│   └── tool_name 命中? → auto_allow / auto_deny
│
├── Tier 5: allow_patterns（bash only）
│   └── 命中允许模式? → auto_allow
│
├── Tier 6: tool default
│   ├── policy.default == ALLOW? → auto_allow
│   ├── policy.default == DENY? → auto_deny
│   └── policy.default == ASK? → 检查 Auto Mode 快速通道
│       ├── auto_mode_allows? → auto_allow
│       └── 否 → 进入 ASK 路径 ↓
│
└── ASK 路径（向客户端请求审批）
    ├── 1. 创建 asyncio.Future
    ├── 2. 存入 _pending[tool_use_id]
    ├── 3. 发送 permission.requested 事件
    ├── 4. await Future（带超时）
    ├── 5. 超时? → 返回 (False, "timeout")
    └── 6. _apply_response → 返回最终结果
```

---

### _auto_mode_allows() 的三重 if 解释

```python
def _auto_mode_allows(self, tool_name: str) -> bool:
    if self._auto_mode == "off":              # 第一关：auto_mode 关了？
        return False                           # → 不自动批准

    if tool_name in AUTO_MODE_READ_ONLY_TOOLS: # 第二关：只读工具？
        return True                            # → read_only 和 on 模式都批准

    if self._auto_mode == "on" and tool_name in AUTO_MODE_WRITE_ALLOW_TOOLS:  # 第三关
        return True                            # → 只有 on 模式才批准白名单写工具

    return False                               # → 其他情况不自动批准
```

| 条件 | 结果 | 说明 |
| --- | --- | --- |
| auto_mode == "off" | 不自动批准 | 关闭状态，所有工具需审批 |
| 只读工具 + auto_mode in ("read_only", "on") | 自动批准 | 只读工具始终安全 |
| 写工具 + auto_mode == "on" + 在白名单中 | 自动批准 | 最宽松模式下才放行写工具 |
| 写工具 + auto_mode == "read_only" | 不自动批准 | read_only 模式不放行写操作 |

---

## 三、Future / Loop / Runner 的关系

### 三者对比表

| 概念 | 类型 | 作用 | 生命周期 |
| --- | --- | --- | --- |
| `asyncio.Future` | awaitable | 异步占位符，代表一个尚未完成的结果 | 临时（审批期间） |
| `asyncio.Loop` | 事件循环 | 调度所有协程和 Future 的执行 | 进程级（单例） |
| `AgentRunner` | 业务类 | 编排 Agent 完整执行生命周期 | 一次 run |
| `AgentLoop` / `LangGraphAgentLoop` | 业务类 | 实际执行 Agent 循环的引擎 | 一次 run |

### Loop 在 PermissionManager 中的用途

```python
# 获取当前事件循环
loop = asyncio.get_event_loop()
# 创建一个 Future 占位符
future: asyncio.Future[str] = loop.create_future()
# 存入 _pending 等待客户端响应
self._pending[tool_use_id] = _PendingRequest(future=future, ...)
# 挂起协程，等待 Future 被 resolve
raw = await asyncio.wait_for(future, timeout=self._timeout_s)
```

**流程**：
1. `check_and_wait()` 需要用户审批时，不阻塞线程，而是创建一个 `Future`
2. 把 Future 存入 `_pending`，然后 `await` 它（协程挂起，让出控制权）
3. 客户端返回决策 → `respond()` 调用 `future.set_result(decision)`
4. Future 完成 → `await` 恢复 → 继续执行

**为什么用 Loop 创建？**
- `asyncio.Future` 必须绑定到一个事件循环
- `loop.create_future()` 确保 Future 属于当前运行的事件循环
- 这是 `asyncio` 的标准用法

### Runner vs Loop

| 维度 | AgentRunner | AgentLoop / LangGraphAgentLoop |
| --- | --- | --- |
| 层次 | 业务编排层 | 执行引擎层 |
| 职责 | 加载配置、构建工具、选择引擎、保存结果 | 实际执行 Agent 循环（LLM 调用 + 工具调用） |
| 对应关系 | Runner 「包含」Loop | Loop 是 Runner 的一个组件 |
| 类比 | 导演 | 演员 |

```python
# Runner 选择和创建 Loop
if self._config.agent.engine == "langgraph":
    loop = LangGraphAgentLoop(...)   # LangGraph 引擎
else:
    loop = AgentLoop(...)            # Legacy 引擎

await loop.run(context)  # Runner 调用 Loop 执行
```

---

## 四、权限请求的 IPC 通信流程

### 完整通信链路

```
Agent 调用工具 → PermissionManager.check_and_wait()
│
├── 1. 本地判断需要 ASK
├── 2. 创建 Future，存入 _pending
├── 3. 调用 event_emitter({type: "permission.requested", ...})
│      │
│      └──→ EventBus.publish(PermissionRequestedEvent)
│              │
│              └──→ IpcEventBroadcaster.handle() 订阅者
│                      │
│                      └──→ 封装为 EventPushEnvelope
│                              │
│                              └──→ TCP 发送给客户端
│                                      │
│                                      └──→ 客户端 UI 显示审批界面
│                                              │
│                                              └──→ 用户点击「允许一次」/「始终允许」等
│                                                      │
│                                                      └──→ 客户端发送 TCP 响应
│                                                              │
│                                                              └──→ 服务端 RPC 接收
│                                                                      │
│                                                                      └──→ PermissionManager.respond(tool_use_id, decision)
│                                                                              │
│                                                                              └──→ future.set_result(decision)
│                                                                                      │
│                                                                                      └──→ check_and_wait() 的 await 恢复执行
│                                                                                              │
│                                                                                              └──→ _apply_response() 更新缓存
│
└── 4. 返回 (allowed, decision_str)
```

**核心机制**：
- **服务端 → 客户端**：通过 `event_emitter` → EventBus → IpcEventBroadcaster → TCP 推送
- **客户端 → 服务端**：通过 RPC 调用 `permission.respond` → `manager.respond()`

---

## 五、respond() 与 _apply_response() 的配合

### 职责分离

| 方法 | 方向 | 职责 |
| --- | --- | --- |
| `respond()` | 客户端 → 服务端 | 接收审批决策，resolve Future（唤醒等待的协程） |
| `_apply_response()` | 服务端内部 | 根据决策更新缓存，返回最终 allow/deny |

### 协作流程

```python
# ===== check_and_wait() 中的 ASK 路径 =====

# Step 1: 创建 Future，发送事件
future = loop.create_future()
self._pending[tool_use_id] = _PendingRequest(future=future, ...)
await event_emitter({type: "permission.requested", ...})

# Step 2: 挂起等待客户端响应
raw = await asyncio.wait_for(future, timeout=60)

# Step 3: Future 被 resolve 后，调用 _apply_response 更新缓存
allowed = self._apply_response(raw, session_id, tool_name)
return allowed, raw
```

```python
# ===== respond() 被 RPC 调用时 =====
def respond(self, tool_use_id, decision):
    req = self._pending.pop(tool_use_id)  # 取出并删除 pending
    if not req.future.done():
        req.future.set_result(decision)   # resolve Future，唤醒 check_and_wait
```

**关键点**：`respond()` 不调用 `_apply_response()`，因为它只负责「把决策传过去」。`_apply_response()` 在 `check_and_wait()` 内部调用，负责「根据决策更新缓存」。两者是**生产者-消费者**关系：
- `respond()` 是生产者（写入 Future）
- `check_and_wait()` 是消费者（等待 Future，然后调用 `_apply_response()`）

---

## 六、_apply_response 的四种决策处理

### 决策处理表

| 决策 | 含义 | 更新 session 缓存 | 更新 persistent 缓存 | 写 policy.toml | 返回值 |
| --- | --- | --- | --- | --- | --- |
| `allow_once` | 允许一次 | ❌ | ❌ | ❌ | `True` |
| `always_allow` | 始终允许 | ✅ `(sid, tool)→"allow"` | ✅ `tool→"allow"` | ✅ | `True` |
| `deny_once` | 拒绝一次 | ❌ | ❌ | ❌ | `False` |
| `always_deny` | 始终拒绝 | ✅ `(sid, tool)→"deny"` | ✅ `tool→"deny"` | ✅ | `False` |

### 与 6 层检查的关系

`_apply_response` 的缓存更新是**单向补充**，与 6 层检查是两个不同阶段：

| 阶段 | 逻辑 | 作用 |
| --- | --- | --- |
| **检查阶段**（check_and_wait） | 6 层判断 | 决定需不需要 ASK |
| **响应阶段**（_apply_response） | 根据决策更新缓存 | 让下次检查直接命中缓存，不再 ASK |

流程：
1. 第一次调用 bash → Tier 6 default=ASK → 进入 ASK 路径
2. 用户选择 `always_allow` → `_apply_response` 更新两个缓存
3. 第二次调用同样的 bash → Tier 3 session_always 命中 → 直接 auto_allow，不再 ASK

---

## 七、_pending 的完整生命周期

### 写入时机

**仅在 `check_and_wait()` 进入 ASK 路径时写入**：

```python
# check_and_wait() 末尾
self._pending[tool_use_id] = _PendingRequest(
    future=future,
    session_id=session_id,
    tool_name=tool_name,
)
await event_emitter(...)
raw = await asyncio.wait_for(future, timeout=self._timeout_s)
```

### 删除时机（三种）

| 时机 | 代码位置 | 行为 |
| --- | --- | --- |
| 客户端正常响应 | `respond()` → `self._pending.pop(tool_use_id)` | Future 被 set_result |
| 超时 | `check_and_wait()` 的 `asyncio.TimeoutError` → `self._pending.pop(tool_use_id, None)` | Future 被取消 |
| 客户端断连 | `cancel_session()` → 遍历 session 下所有 pending，逐个 pop | Future 被 set_result("deny_once") |

### 完整生命周期图

```
check_and_wait() 进入 ASK 路径
    │
    ├── _pending[tool_use_id] = PendingRequest(...)  ← 写入
    │
    ├── await Future（挂起）
    │
    ├── 客户端响应 → respond()
    │   └── _pending.pop(tool_use_id)  ← 删除
    │       └── future.set_result(decision)
    │
    ├── 或：超时
    │   └── _pending.pop(tool_use_id, None)  ← 删除
    │       └── return (False, "timeout")
    │
    └── 或：cancel_session()（客户端断连）
        └── 遍历 pop 所有 pending for this session  ← 批量删除
            └── future.set_result("deny_once")
```

---

## 八、核心字段总结

| 字段 | 类型 | 说明 | 示例 |
| --- | --- | --- | --- |
| `_policies` | `dict[str, ToolPolicy]` | 工具策略映射 | `{"bash": ToolPolicy(default=ASK)}` |
| `_pending` | `dict[str, _PendingRequest]` | 待审批请求映射 | `{"call_001": PendingRequest(...)}` |
| `_session_always` | `dict[tuple[str,str], str]` | session 级缓存 | `{("sess-1", "bash"): "allow"}` |
| `_persistent_always` | `dict[str, str]` | 持久化缓存 | `{"bash": "allow", "write_file": "deny"}` |
| `_policy_file` | `Path \| None` | 策略文件路径 | `Path("~/.iwan/policy.toml")` |
| `_timeout_s` | `float` | 审批超时（秒） | `60.0` |
| `_auto_mode` | `str` | 自动模式 | `"off"` / `"read_only"` / `"on"` |
| `_effort_level` | `str` | 努力等级 | `"minimal"` - `"max"` |
| `_model_preset` | `str` | 模型预设 | `"fast"` / `"balanced"` / `"powerful"` |

---

## 九、关键代码文件索引

| 文件 | 作用 |
| --- | --- |
| `core/permissions/manager.py` | PermissionManager 主类（权限检查 + 审批流程） |
| `core/permissions/policy.py` | 策略定义（ToolPolicy、DEFAULT_POLICIES、evaluate） |
| `core/permissions/storage.py` | 持久化存储（policy.toml 读写） |
