# IwanClaude TUI 知识手册

> 面向 TUI 前端开发者的架构说明与概念入门指南

---

## 1. 术语表 (Glossary)

### Textual

**是什么**：一个用于构建终端用户界面（TUI）的 Python 框架。类似于 Web 开发中的 React，但运行在终端中。

**核心机制**：
- **Widget 树**：所有 UI 元素组成一棵组件树（类似 DOM），`Screen` 是根节点
- **响应式（Reactive）**：属性变化时自动触发 UI 刷新，无需手动调用 `update()`
- **消息泵（Message Pump）**：Textual 的事件循环机制，所有事件（键盘、鼠标、定时器等）都通过消息泵分发

```python
# Textual 的基本 App 结构
from textual.app import App, ComposeResult

class MyApp(App):
    def compose(self) -> ComposeResult:
        yield Label("Hello")  # compose 定义子 widget

    def on_mount(self) -> None:
        self.query_one(Label).update("World")  # mount 后操作子 widget
```

### Widget

**是什么**：可复用的 UI 组件，类似 React 中的组件。每个 Widget 封装了自己的状态、样式和行为。

在 Textual 中，Widget 是所有 UI 元素的基类：

```python
class MyWidget(Widget):
    DEFAULT_CSS = "MyWidget { height: 5; }"

    def compose(self) -> ComposeResult:
        yield Static("内容")
```

本项目中的核心 Widget：
| Widget | 文件 | 作用 |
|--------|------|------|
| `LLMStreamBlock` | `widgets/llm_stream.py` | LLM 流式输出，逐 token 渲染 |
| `ToolCallBlock` | `widgets/tool_call.py` | 可折叠的工具调用展示 |
| `PermissionSelect` | `widgets/permission.py` | 内联权限审批交互 |
| `PermissionBlock` | `widgets/permission.py` | 权限审批状态摘要 |
| `SlashCompleteWidget` | `widgets/slash_complete.py` | 斜杠命令自动补全 |
| `ChatTextArea` | `widgets/chat_input.py` | 聊天输入框 |

### Reactive 属性

**是什么**：一种特殊属性，当其值改变时，Textual 框架会自动通知相关 Widget 刷新界面。

```python
from textual.reactive import reactive

class MyWidget(Widget):
    count = reactive(0)  # reactive 属性

    def watch_count(self, value: int) -> None:
        """当 count 变化时自动调用"""
        self.refresh()  # 触发重绘
```

本项目中 `IwanTuiApp` 使用 reactive 属性管理会话状态（`_busy`、`_auto_mode` 等）。

### Message / Message Pump

**是什么**：Textual 的事件通信机制。Widget 通过定义 `Message` 子类来通知宿主（父级 Widget）发生了某事。

```python
# 定义消息
class MyWidget(Widget):
    class ValueChanged(Message):
        def __init__(self, value: str):
            self.value = value
            super().__init__()

    def some_action(self) -> None:
        self.post_message(self.ValueChanged("hello"))

# 宿主监听消息（命名约定：on_小写类名）
class ParentWidget(Widget):
    def on_my_widget_value_changed(self, event: MyWidget.ValueChanged) -> None:
        print(f"收到值: {event.value}")
```

本项目中 `ChatTextArea.Submitted`、`PermissionSelect.Decided` 等都是 Message 的典型用法。

### Compose

**是什么**：Widget 的「渲染」方法，定义该 Widget 包含哪些子 Widget。类似 React 的 `render()` 方法。

```python
class ToolCallBlock(Widget):
    def compose(self) -> ComposeResult:
        yield Static(self._summary(), classes="summary")
        yield Static("", classes="detail")
```

关键点：`compose()` 只在 Widget 首次挂载时调用一次，用于初始化子 Widget 结构。后续通过 `update()`、`add_class()`、`remove_class()` 等方法动态修改。

### CSS in Textual

**是什么**：Textual 使用类 CSS 语法定义 Widget 样式，支持：

- **类选择器**：`.summary`、`.detail`、`.expanded`
- **ID 选择器**：`#log-view`、`#prompt`
- **伪类**：`:focus`（聚焦状态）
- **响应式**：通过 CSS 类控制显示/隐藏，实现动态布局

```python
class ToolCallBlock(Widget):
    DEFAULT_CSS = """
    ToolCallBlock > .detail { display: none; }           /* 默认隐藏详情 */
    ToolCallBlock.expanded > .detail { display: block; } /* expanded 时显示 */
    """
```

本项目在 `app.py` 的 `IwanTuiApp.CSS` 中定义全局样式，在各 Widget 的 `DEFAULT_CSS` 中定义局部样式。

### Worker

**是什么**：Textual 中运行异步后台任务的机制，不会阻塞 UI 渲染。

```python
# 启动后台任务
self.run_worker(self._socket_loop(), exclusive=True, name="socket")
```

- `exclusive=True`：同一时间只有一个该名称的 worker 在运行
- Worker 在后台异步执行，不阻塞 Textual 的消息泵
- 适合用于长时间运行的任务（如 socket 连接循环）

### Socket

**是什么**：客户端（TUI）与服务端（CoreApp）之间的 TCP 连接，用于双向通信。

本项目使用 `SocketClient` 封装异步 TCP 连接：

```python
client = SocketClient("127.0.0.1", 7437)
await client.connect()
# 发送命令
result = await client.send_command("session.send_message", {"session_id": sid, "content": msg})
# 接收事件
client.on_event(lambda event: self._handle_event(event))
```

### 事件总线 (EventBus)

**是什么**：发布-订阅（Pub/Sub）模式的事件通信机制。服务端产生事件，所有订阅的客户端都会收到。

```python
# 服务端：广播事件
broadcaster.broadcast(event, topics=["run.*"], scope="global")

# 客户端：订阅事件
await client.send_command("event.subscribe", {"topics": ["llm.token", "tool.*"], "scope": "global"})
```

### IPC (Inter-Process Communication)

**是什么**：进程间通信。TUI 客户端和 CoreApp 服务端运行在不同的进程中，通过 TCP Socket 交换 JSON-RPC 消息进行通信。

```
┌─────────────┐    TCP/JSON-RPC     ┌─────────────┐
│  TUI 进程    │ ◄────────────────► │ CoreApp 进程 │
│  (客户端)    │                    │  (服务端)    │
└─────────────┘                    └─────────────┘
```

### RPC (Remote Procedure Call)

**是什么**：远程过程调用。像调用本地函数一样调用远程进程的方法。

```python
# 看起来像本地函数调用
result = await client.send_command("session.send_message", {"session_id": sid, "content": "hello"})

# 实际底层流程：
# 1. 序列化为 JSON-RPC 请求
# 2. 通过 TCP 发送到服务端
# 3. 服务端执行对应 handler
# 4. 结果通过 TCP 返回
# 5. 反序列化为 Python 对象
```

本项目使用 JSON-RPC 2.0 协议，支持命令（请求-响应）和事件（推送）两种模式。

### 回调 (Callback)

**是什么**：作为参数传递给其他函数的函数，稍后被调用。

```python
# 注册事件回调
def on_event(event: dict[str, Any]) -> None:
    self._handle_event(event)

client.on_event(on_event)  # 注册回调，事件到达时自动调用
```

### 刷新 (Refresh/Re-render)

**是什么**：重新绘制 UI 界面。

- **响应式刷新**：reactive 属性变化时 Textual 自动刷新
- **手动刷新**：调用 `widget.refresh()` 或 `widget.update()` 强制刷新
- **选择性刷新**：`widget.update(new_content)` 更新内容，`widget.add_class()` 更新样式

```python
# LLMStreamBlock 中的逐 token 刷新
def append_token(self, token: str) -> None:
    self._text += token
    self.update(self._text)  # 手动刷新显示最新文本
```

### 流式输出 (Streaming)

**是什么**：逐 token 接收和显示 LLM 输出，而非等待完整回复后一次显示。

```python
# 收到 token 事件时
elif t == "llm.token":
    token = event.get("token", "")
    if self._current_llm is None:
        llm_block = LLMStreamBlock()
        self._append(llm_block)
        self._current_llm = llm_block
    self._current_llm.append_token(token)  # 每个 token 追加并刷新
```

### Markdown 渲染

**是什么**：将纯文本转换为带格式的显示（代码高亮、标题、列表等）。

本项目使用 Rich 的 `Markdown` 类：

```python
# 流式接收时显示纯文本
block.append_token("Hello **world**")

# 流式结束后渲染为 Markdown
block.finalize_markdown()
# 使用 Rich Markdown 渲染，支持 monokai 代码主题
self.update(Markdown(self._text, code_theme="monokai"))
```

**为什么只在 finalize 时渲染？** 因为 Markdown 渲染涉及大量计算（解析、语法高亮），每收到一个 token 就重新渲染会严重影响性能。

### Tab 切换

**是什么**：在多个会话（标签页）之间切换。每个会话独立运行、独立显示。

```python
# 切换会话
self._switch_session(session_id)

# 流程：
# 1. 保存当前会话的 widgets 到 _SessionState
# 2. 将目标会话移到 _session_order 头部
# 3. 清空 #log-view 并加载目标会话的 widgets
# 4. 更新标签栏
```

快捷键：`Ctrl+T` 新建会话、`Ctrl+W` 关闭会话、`Alt+1~9` 切换会话。

### 权限审批

**是什么**：用户审批 Agent 的工具调用权限。采用内联设计（非弹窗），在日志流中直接操作。

```python
# 收到权限请求事件
elif t == "permission.requested":
    perm_block = PermissionBlock(tool_use_id, tool_name, param_preview)
    select = PermissionSelect(tool_use_id)
    self._mount_permission_select(select)  # 挂载到 #prompt 之前
```

用户可通过 `y/a/n/d` 或方向键快速选择：Allow once / Always allow / Deny / Always deny。

### 斜杠命令 (Slash Commands)

**是什么**：以 `/` 开头的命令，触发特定操作。支持自动补全。

```python
# 输入 / 时显示补全弹窗
class ChatTextArea(TextArea):
    def on_text_area_changed(self, event):
        text = self.text
        if text.startswith("/") and " " not in text:
            self.post_message(ChatTextArea.SlashChanged(query=text[1:]))
```

可用命令：`/help`、`/auto`、`/compact`、`/checkpoint`、`/history`、`/close` 等。

### 子 Agent (Subagent)

**是什么**：由主 Agent 派生的并行任务单元，用于分解复杂任务。

```python
# 子 Agent 开始
elif t == "subagent.started":
    self._subagent_run_ids[run_id] = description
    self._subagent_start_times[run_id] = time.monotonic()

# 子 Agent 完成
elif t == "subagent.finished":
    # 显示耗时和结果
```

子 Agent 的工具调用和 LLM 输出会使用缩进（padding-left: 6）和 `┌─/└─` 标记来区分层级。

---

## 2. 架构图 (Architecture)

### 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                    TUI 客户端进程                            │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              IwanTuiApp (App 主类)                  │    │
│  │                                                     │    │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────────┐  │    │
│  │  │ #tabbar   │  │ #header   │  │ #prompt       │  │    │
│  │  │ (会话标签) │  │ (状态栏)  │  │ (聊天输入框)  │  │    │
│  │  └───────────┘  └───────────┘  └───────────────┘  │    │
│  │                                                     │    │
│  │  ┌─────────────────────────────────────────────┐   │    │
│  │  │            #log-view (日志滚动区)           │   │    │
│  │  │                                             │   │    │
│  │  │  ┌──────────────┐  ┌──────────────────┐    │   │    │
│  │  │  │LLMStreamBlock│  │ ToolCallBlock    │    │   │    │
│  │  │  │(LLM 流式输出)│  │ (工具调用展示)   │    │   │    │
│  │  │  └──────────────┘  └──────────────────┘    │   │    │
│  │  │                                             │   │    │
│  │  │  ┌──────────────┐  ┌──────────────────┐    │   │    │
│  │  │  │PermissionBlock│ │PermissionSelect │    │   │    │
│  │  │  │(权限审批摘要) │ │ (权限审批交互)  │    │   │    │
│  │  │  └──────────────┘  └──────────────────┘    │   │    │
│  │  └─────────────────────────────────────────────┘   │    │
│  │                                                     │    │
│  │  ┌─────────────────────────────────────────────┐   │    │
│  │  │          _socket_loop (后台连接)             │   │    │
│  │  │                                             │   │    │
│  │  │  SocketClient                               │   │    │
│  │  │  ├── connect()  建立 TCP 连接               │   │    │
│  │  │  ├── send_command()  发送 JSON-RPC 命令     │   │    │
│  │  │  ├── run_event_loop()  持续读取事件         │   │    │
│  │  │  └── on_event()  注册事件回调               │   │    │
│  │  └─────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              IPC 协议层 (JSON-RPC 2.0)             │    │
│  │  ┌─────────────┐    ┌──────────────┐              │    │
│  │  │  请求/响应   │    │  事件推送     │              │    │
│  │  │  send_command│    │  event.subscribe│             │    │
│  │  └─────────────┘    └──────────────┘              │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ TCP Socket (localhost:7437)
                          │ JSON Lines (每行一条 JSON)
┌─────────────────────────────────────────────────────────────┐
│                   CoreApp 服务端进程                         │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              SocketServer (TCP 服务器)              │    │
│  │  ├── session.*  会话管理                            │    │
│  │  ├── run.*  运行控制                                │    │
│  │  ├── tool.*  工具调用                               │    │
│  │  ├── llm.*  LLM 流式输出                            │    │
│  │  └── permission.*  权限审批                         │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │           IpcEventBroadcaster (事件广播)            │    │
│  │  ├── topic glob 匹配 (如 "llm.token")              │    │
│  │  ├── scope 过滤 (global / 特定 run)                │    │
│  │  └── 推送到所有订阅客户端                           │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Agent 引擎 (LangGraph/Legacy)          │    │
│  │  ├── 执行推理循环 (Think → Act → Observe)          │    │
│  │  ├── 调用工具 → 产生 tool.* 事件                   │    │
│  │  ├── LLM 输出 → 产生 llm.token 事件                │    │
│  │  └── 权限需求 → 产生 permission.requested 事件     │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 组件关系树

```
IwanTuiApp (Screen 根节点)
│
├── #tabbar (Horizontal)
│   ├── .tab (Label) - 会话1
│   ├── .tab.active (Label) - 会话2
│   └── ...
│
├── #header (Label) - 状态栏
│
├── #log-view (VerticalScroll) - 日志滚动区
│   │
│   ├── Static.user-turn - 用户消息显示
│   │
│   ├── LLMStreamBlock - LLM 流式输出块
│   │   └── (纯文本 → Markdown 渲染)
│   │
│   ├── ToolCallBlock - 可折叠工具调用块
│   │   ├── Static.summary (摘要行)
│   │   └── Static.detail (详情，默认隐藏)
│   │
│   ├── PermissionBlock - 权限审批摘要
│   │   └── (待审批 → 已解决 ✓/✗)
│   │
│   ├── PermissionSelect - 权限审批交互控件
│   │   └── (↑↓ 导航 / y/a/n/d 选择)
│   │
│   └── Static.log-line - 日志行、步骤分隔线等
│
├── SlashCompleteWidget (仅在输入 / 时出现)
│   └── (命令列表弹窗，can_focus=False)
│
└── #prompt (ChatTextArea)
    └── TextArea 基类
```

### 数据流图

```
用户输入 "帮我分析代码"
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│ 1. ChatTextArea._on_key()                                        │
│    - 检测到 Enter 键                                             │
│    - 发布 Submitted(self) 消息                                    │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ 2. IwanTuiApp.on_chat_text_area_submitted()                      │
│    - 解析命令或普通消息                                          │
│    - 设置 _busy=True，更新输入框状态                              │
│    - 调用 run_worker(_do_send_message(content))                   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ 3. IwanTuiApp._do_send_message()                                │
│    - client.send_command("session.send_message", {...})          │
│    - 序列化为 JSON-RPC 请求                                       │
│    - 通过 TCP Socket 发送到 CoreApp                               │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ 4. CoreApp 处理请求                                              │
│    - SocketServer 解析 JSON-RPC 请求                             │
│    - 调用 session.send_message handler                            │
│    - Agent 开始执行：推理 → 调用工具 → LLM 生成回复              │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ 5. CoreApp 产生事件并广播                                        │
│    - agent 调用工具 → 广播 tool.call_started 事件                │
│    - agent 完成工具 → 广播 tool.call_finished 事件               │
│    - LLM 生成 token → 每 token 广播 llm.token 事件               │
│    - 运行结束 → 广播 run.finished 事件                            │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ 6. SocketClient._dispatch() 接收事件                             │
│    - 解析 JSON 行 → 识别为 event 类型                             │
│    - 调用注册的事件回调 on_event(event_data)                      │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ 7. IwanTuiApp._handle_event() → _handle_event_inner()            │
│    - 根据 event["type"] 路由到不同处理逻辑                       │
│    - llm.token → append_token() 追加到 LLMStreamBlock             │
│    - tool.call_started → 创建 ToolCallBlock                      │
│    - tool.call_finished → 更新 ToolCallBlock 结果                │
│    - permission.requested → 创建 PermissionSelect                │
│    - run.finished → 结束 LLM 流式块 + 显示完成状态               │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ 8. Widget 更新 → Textual 重绘                                    │
│    - LLMStreamBlock.update(text) → 屏幕刷新                      │
│    - ToolCallBlock.set_result() → 摘要更新                       │
│    - PermissionSelect.update(ui) → 选项列表刷新                  │
│    - ScrollView.scroll_end() → 自动滚动到底部                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. 文件结构 (File Structure)

```
tui/
├── __init__.py              # 包入口，导出 run() 函数
├── __main__.py              # python -m 入口，解析参数+启动 TUI
├── app.py                   # IwanTuiApp 主类 + run() 启动函数
├── knowledge_manual.md     # 本手册
├── formatters.py            # 文本格式化工具函数(_preview, _params_str, _param_summary)
├── models/
│   ├── __init__.py
│   └── session_state.py    # _SessionState 数据类，保存会话 UI 状态
├── widgets/
│   ├── __init__.py         # 统一导出所有 Widget 类
│   ├── llm_stream.py       # LLMStreamBlock：LLM 流式输出，逐 token 渲染
│   ├── tool_call.py        # ToolCallBlock：可折叠工具调用展示
│   ├── permission.py       # PermissionSelect/Block：内联权限审批
│   ├── slash_complete.py   # SlashCompleteWidget：斜杠命令自动补全弹窗
│   └── chat_input.py       # ChatTextArea：聊天输入框(Enter提交/斜杠命令)
└── app/
    └── __init__.py         # 预留扩展位
```

**各模块职责**：

| 模块 | 核心职责 | 关键类/函数 |
|------|----------|-------------|
| `__init__.py` | 包导出 | `run()` |
| `__main__.py` | CLI 入口 | `main()` |
| `app.py` | 主应用类 | `IwanTuiApp`, `run()` |
| `formatters.py` | 文本格式化 | `_preview()`, `_params_str()`, `_param_summary()` |
| `session_state.py` | 会话状态 | `_SessionState` |
| `llm_stream.py` | LLM 输出 | `LLMStreamBlock` |
| `tool_call.py` | 工具调用 | `ToolCallBlock` |
| `permission.py` | 权限审批 | `PermissionSelect`, `PermissionBlock` |
| `slash_complete.py` | 命令补全 | `SlashCompleteWidget` |
| `chat_input.py` | 聊天输入 | `ChatTextArea` |

---

## 4. 核心流程 (Core Flow)

### 4.1 启动流程

```
run(config)
  │
  ▼
IwanTuiApp(host, port)      # app.py:3128
  │                         创建 App 实例，保存配置
  ▼
app.run()                    # Textual 启动事件循环
  │
  ▼
on_mount()                   # app.py:1573
  │
  ├── _build_slash_items()   # 构建斜杠命令列表
  ├── _append(BANNER)        # 显示欢迎横幅
  ├── run_worker(_socket_loop(), exclusive=True)  # 启动 socket 连接
  └── prompt.disabled=True   # 禁用输入框

_socket_loop()               # app.py:2704
  │
  ├── client.connect()       # TCP 连接到 CoreApp
  ├── client.send_command("event.subscribe", {...})  # 订阅事件
  ├── client.send_command("session.create", {...})  # 创建会话
  ├── _add_session(sid, title)  # 添加会话到状态管理
  ├── client.run_event_loop()  # 持续接收事件
  └── prompt.disabled=False  # 连接成功，启用输入框
```

### 4.2 发送消息流程

```
用户输入 "你好" + Enter
  │
  ▼
ChatTextArea._on_key()       # chat_input.py:151
  │
  ├── key == "enter"
  ├── popup 不存在或无选中项
  └── post_message(Submitted(self))

IwanTuiApp.on_chat_text_area_submitted()  # app.py:1923
  │
  ├── content.strip()
  ├── 检测特殊命令(/compact, /help 等)
  ├── _busy = True
  ├── prompt.text = ""      # 清空输入框
  ├── prompt.disabled=True  # 禁用输入框
  ├── _append(user-turn)    # 显示用户消息
  ├── _update_header("running")
  └── run_worker(_do_send_message(content))

_do_send_message(content)    # app.py:2457
  │
  └── client.send_command("session.send_message",
        {"session_id": sid, "content": content})
      序列化为 JSON-RPC → TCP → CoreApp
```

### 4.3 接收事件流程

```
CoreApp 产生事件（如 tool.call_started）
  │
  ▼
SocketClient._dispatch()     # socket_client.py
  │
  ├── 解析 JSON 行
  ├── 识别为 event 类型
  └── 调用 on_event(event_data)

IwanTuiApp._handle_event()   # app.py:2828
  │
  ├── try-except 包裹（防止单事件崩溃）
  └── _handle_event_inner(event)  # app.py:2847
        │
        ├── t == "llm.token" → 追加 token
        ├── t == "tool.call_started" → 创建 ToolCallBlock
        ├── t == "tool.call_finished" → 更新结果
        ├── t == "permission.requested" → 创建 PermissionSelect
        ├── t == "run.finished" → 结束 LLM 块 + 显示完成
        └── ... 其他事件类型
```

### 4.4 流式输出流程

```
收到 llm.token 事件
  │
  ▼
_handle_event_inner()       # app.py:2896
  │
  ├── if _current_llm is None:
  │     llm_block = LLMStreamBlock()
  │     _append(llm_block)  # 挂载到 #log-view
  │     _current_llm = llm_block
  │
  └── _current_llm.append_token(token)  # llm_stream.py:56
        │
        ├── self._text += token    # 追加到累积文本
        └── self.update(self._text)  # 刷新显示

收到非 llm.token 事件（如 tool.call_started）
  │
  ▼
_break_llm()                # app.py:2559
  │
  ├── _current_llm.finalize_markdown()  # 渲染 Markdown
  │     └── self.update(Markdown(self._text, code_theme="monokai"))
  └── _current_llm = None
```

### 4.5 权限审批流程

```
收到 permission.requested 事件
  │
  ▼
_handle_event_inner()       # app.py:3075
  │
  ├── 创建 PermissionBlock 摘要块
  ├── 创建 PermissionSelect 交互控件
  ├── _append(perm_block)
  ├── _mount_permission_select(select)  # 挂载到 #prompt 之前
  └── prompt.disabled=True

用户按 y/a/n/d 或方向键
  │
  ▼
PermissionSelect.on_key()   # permission.py:219
  │
  ├── 处理按键事件
  ├── event.stop()  # 阻止冒泡
  └── _pick(decision) → post_message(Decided(self, tool_use_id, decision))

IwanTuiApp.on_permission_select_decided()  # app.py:2491
  │
  ├── select.remove()       # 移除交互控件
  ├── perm_block._resolve(decision)  # 更新摘要为已解决
  ├── client.send_command("permission.respond", {...})  # 回复 CoreApp
  └── 若无待处理权限 → 恢复输入框
```

### 4.6 多会话流程

```
Ctrl+T 新建会话
  │
  ▼
action_new_session()         # app.py:1839
  │
  └── run_worker(_do_new_session())

_do_new_session()            # app.py:1882
  │
  ├── client.send_command("session.create", {...})
  ├── _save_current_state()  # 保存当前会话状态
  ├── _add_session(sid, title)  # 添加新会话
  ├── _load_session_state(sid)  # 加载新会话到 UI
  └── _refresh_tabbar()

Alt+2 切换到会话2
  │
  ▼
action_switch_session("2")   # app.py:1863
  │
  └── _switch_session(session_id)  # app.py:1391
        │
        ├── _save_current_state()  # 保存当前会话 widgets
        ├── _session_order 调整   # 目标会话移到头部
        ├── _load_session_state(sid)  # 清空 log-view + 加载目标会话
        └── _refresh_tabbar()
```

---

## 5. 回调机制详解

### 5.1 Message 机制

Textual 的 Message 机制是 Widget 之间通信的核心方式。遵循「发布-订阅」模式：

```python
# 第一步：定义 Message 子类（在子 Widget 中）
class ChatTextArea(TextArea):
    class Submitted(Message):
        def __init__(self, area: ChatTextArea):
            self.text_area = area
            self.value = area.text
            super().__init__()

# 第二步：在适当的时机发布消息
def _on_key(self, event: events.Key) -> None:
    if key == "enter":
        self.post_message(self.Submitted(self))

# 第三步：宿主监听消息（命名约定：on_{消息类名的小写}）
class IwanTuiApp(App):
    def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted):
        content = event.value
        # 处理提交...
```

**命名约定**：`on_` 前缀 + 消息类名（驼峰转下划线）。Textual 会自动查找并调用对应的方法。

### 5.2 事件路由层级

```
                    Textual Message Pump
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
     键盘/鼠标事件              Widget.post_message()
              │                         │
              ▼                         ▼
     Widget.on_key()            宿主的 on_{message}()
     (如 ChatTextArea)          (如 IwanTuiApp.on_chat_text_area_submitted)
```

**关键规则**：
1. 事件从焦点 Widget 开始，沿 DOM 树向上冒泡
2. `event.stop()` 可阻止继续冒泡
3. `event.prevent_default()` 可阻止默认行为
4. Message 只能向上冒泡（子 → 父），不能向下

### 5.3 Socket 异步回调

Socket 事件通过异步回调处理，与 Textual 消息泵并行运行：

```python
async def _socket_loop(self) -> None:
    client = SocketClient(host, port)
    await client.connect()

    # 注册异步事件回调
    async def on_event(event: dict[str, Any]) -> None:
        self._handle_event(event)  # 这是普通方法，不是 async

    client.on_event(on_event)
    await client.run_event_loop()
```

**关键点**：`_handle_event()` 虽然是同步方法，但它通过 Textual 的 Widget API（`update()`、`mount()` 等）操作界面。这些 API 在 Textual 事件循环中是线程安全的，因为 Socket 回调通过 `run_worker()` 启动的 asyncio 任务运行，与 Textual 的事件循环共享同一个线程。

### 5.4 完整回调链示例

以用户提交消息为例：

```
[用户按 Enter]
    │
    ▼
ChatTextArea._on_key(event)
    │  检测到 Enter
    │  post_message(Submitted(self))
    ▼
[Textual 消息泵分发]
    │
    ▼
IwanTuiApp.on_chat_text_area_submitted(event)
    │  _busy = True
    │  run_worker(_do_send_message(content))
    ▼
[Worker 异步执行]
    │
    ▼
_do_send_message(content)
    │  client.send_command("session.send_message", ...)
    │  ← JSON-RPC 响应
    ▼
[Socket 事件循环]
    │  收到 llm.token / tool.call_started 等事件
    │  on_event(event_data)
    ▼
IwanTuiApp._handle_event(event)
    │  _handle_event_inner(event)
    │  根据 type 路由处理
    ▼
[Widget 更新]
    │  LLMStreamBlock.update(text)
    │  ToolCallBlock.set_result(output, elapsed_ms)
    ▼
[Textual 自动重绘]
```

---

## 6. 刷新与渲染

### 6.1 响应式刷新

Textual 的 reactive 属性在值改变时自动触发 Widget 刷新：

```python
class IwanTuiApp(App):
    @property
    def _busy(self) -> bool:
        return self._state.busy if self._state else False

    @_busy.setter
    def _busy(self, value: bool) -> None:
        if self._state:
            self._state.busy = value
            self._refresh_tabbar()  # 手动触发标签栏刷新
```

本项目主要通过手动刷新而非 reactive，因为状态管理较复杂（多会话、异步事件驱动）。

### 6.2 手动刷新方式

| 方法 | 用途 | 示例 |
|------|------|------|
| `widget.refresh()` | 强制重绘（内容不变） | 样式变化后刷新 |
| `widget.update(new_content)` | 更新内容并刷新 | `block.update(text)` |
| `widget.add_class("expanded")` | 添加 CSS 类 → 样式变化 → 重绘 | `tool_block.add_class("expanded")` |
| `widget.remove_class("expanded")` | 移除 CSS 类 | `tool_block.remove_class("expanded")` |
| `self._refresh_tabbar()` | 重建标签栏 | 会话切换时 |
| `self._load_session_state(sid)` | 清空并加载 widgets | 会话切换时 |

### 6.3 流式 Token 刷新策略

`LLMStreamBlock` 采用「追加 + 立即刷新」策略：

```python
class LLMStreamBlock(Static):
    def append_token(self, token: str) -> None:
        if self._finalized:
            return
        self._text += token           # 1. 追加 token 到累积文本
        self.update(self._text)       # 2. 用纯文本刷新显示
```

**为什么用纯文本而非 Markdown？**
- Markdown 渲染需要完整文本才能正确解析（如代码块的闭合标记）
- 流式接收时 Markdown 可能解析出错误结构（如不完整的代码块）
- 纯文本刷新速度快（无需语法高亮计算）

**性能优化**：`update()` 仅重绘当前 Widget，不会触发全屏重绘。

### 6.4 Markdown 延迟渲染

```python
def finalize_markdown(self) -> None:
    if self._finalized:
        return
    self._finalized = True          # 标记已完成
    if self._text.strip():
        self.update(Markdown(self._text, code_theme="monokai"))
```

**为什么延迟到 finalize 才渲染 Markdown？**

1. **正确性**：流式接收期间，文本可能包含不完整的 Markdown 语法（如未闭合的 ````python` 代码块），渲染会出错
2. **性能**：Rich Markdown 涉及解析 + 语法高亮，每个 token 都重新渲染会导致严重卡顿
3. **用户体验**：纯文本逐字显示已经足够流畅，最终再渲染为带格式的版本

**渲染时机**：在 `_break_llm()` 中触发，当收到非 `llm.token` 事件时（如工具调用、运行结束等）：

```python
def _break_llm(self) -> None:
    if self._current_llm is not None:
        self._current_llm.finalize_markdown()
    self._current_llm = None
```

### 6.5 滚动优化

```python
def _append(self, widget: Widget) -> None:
    log_view = self.query_one("#log-view", VerticalScroll)
    log_view.mount(widget)
    log_view.scroll_end(animate=False)  # 禁用动画，直接滚动到底部
```

使用 `animate=False` 避免在高频事件（如流式 token）时产生滚动动画导致的性能问题。

### 6.6 会话状态保存与恢复

切换会话时，所有 Widget 被完整保存和恢复：

```python
def _save_current_state(self) -> None:
    state = self._state
    log_view = self.query_one("#log-view", VerticalScroll)
    state.widgets = list(log_view.children)  # 保存所有子 widget 引用

def _load_session_state(self, session_id: str) -> None:
    state = self._sessions.get(session_id)
    log_view = self.query_one("#log-view", VerticalScroll)
    log_view.remove_children()               # 清空当前内容
    for widget in state.widgets:
        log_view.mount(widget)               # 挂载目标会话的 widget
    log_view.scroll_end(animate=False)
```

**关键设计**：Widget 对象本身被保存在 `_SessionState.widgets` 列表中，切换时重新挂载到 DOM。这避免了重新创建 Widget 导致的闪烁和状态丢失。

---

## 附录：事件类型速查表

| 事件类型 | 触发时机 | TUI 处理 |
|----------|----------|----------|
| `llm.token` | LLM 生成每个 token | `LLMStreamBlock.append_token()` |
| `llm.usage` | LLM 回复完成 | 显示 token 统计 + 上下文进度条 |
| `run.started` | Agent 开始新运行 | 显示运行头部（run_id + goal） |
| `run.finished` | Agent 运行结束 | 结束 LLM 块 + 显示完成/失败状态 |
| `step.started` | Agent 进入新步骤 | 显示步骤分隔线 |
| `tool.call_started` | 开始调用工具 | 创建 `ToolCallBlock` |
| `tool.call_finished` | 工具调用成功 | 更新 `ToolCallBlock` 结果 |
| `tool.call_failed` | 工具调用失败 | 更新 `ToolCallBlock` 错误状态 |
| `permission.requested` | Agent 需要权限审批 | 创建 `PermissionBlock` + `PermissionSelect` |
| `permission.denied` | 权限被拒绝（超时/断连） | 更新状态 + 恢复输入框 |
| `session.waiting_for_input` | Agent 等待用户输入 | 恢复输入框、设置 `_busy=False` |
| `session.closed` | 会话被关闭 | 禁用输入框、设置状态 |
| `session.renamed` | 会话标题变更 | 更新标签栏 |
| `skill.invoked` | Skill 被调用 | 显示 Skill 名称 |
| `subagent.started` | 子 Agent 开始执行 | 记录 run_id + 显示开始标记 |
| `subagent.finished` | 子 Agent 完成 | 显示结果 + 耗时 |
| `context.compacted` | 上下文压缩完成 | 显示压缩结果 |
| `log.line` | 日志事件 | 显示日志行（带级别颜色） |
