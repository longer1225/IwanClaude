# 05 静态模型 · 类图（客户端：Electron GUI / Textual TUI）

**回答的问题**：两个客户端各自怎么"记住"界面状态、怎么把 daemon 事件翻译成视图。

## 图 A：Electron GUI 的三层与状态归属

```mermaid
classDiagram
    direction TB

    class BrowserWindow {
        +frame: false
        +webPreferences.contextIsolation: true
        +webPreferences.nodeIntegration: false
        +webContents.send(channel, payload)
    }

    class RpcTransport {
        -sock: net.Socket
        -pending: Map~id,Pending~
        -token: str
        +status: ConnStatus
        +start(ensureDaemon)
        +request(method, params): Promise
        +stop()
        -dial()
        -readLoop(sock)
        -dispatch(line)
    }

    class FsBridge {
        +fs:readDir(root, rel)
        +fs:readFile(root, rel)
        +sessions:meta(ids)
        +runs:tasks(sid, runId)
        +config:read()
        +eval:rag()
        +settings:get/set()
        +ui:open-path / ui:pick-file
    }

    class PreloadBridge {
        +request(method, params)
        +onEvent(cb)
        +onStatus(cb)
        +getStatus()
        +readDir / readFile / sessionsMeta / runTasks
        +getSettings / setSettings
        +pickDirectory / pickFile / openPath
    }

    class Store {
        +status: string
        +sessions: SessionMeta[]
        +activeSid: string
        +threads: Record~sid,Msg[]~
        +runningSids: Set~sid~
        +lastRunId: Record~sid,run~
        +notices: Record~sid,int~
        +metaCwd: Record~sid,cwd~
        +sessionAttrs: Record
        +setActive(sid)
        +pushMsg(sid, msg)
        +patchMsg(sid, id, patch)
        +markRunning(sid, bool)
        +setLastRun(sid, runId)
        +upsertSession(meta)
    }

    class GuiStore {
        +theme: light|dark|system
        +projects: string[]
        +sessionProject: Record~sid,cwd~
        +pins: Record~sid,bool~
        +rightTab: files|changes|tasks|preview
        +mainView: chat|pr|git|schedule|mcp|ssh|workflow
        +applyFromDisk()
        +set(patch)
        +addProject(root)
    }

    class App {
        +onConnected()
        +applyStatus(s)
    }

    class Sidebar {
        +projects 分组
        +SessionRow
        +addProject()
        +newTaskIn(root)
    }

    class Composer {
        -sending: bool
        -text: string
        +doSend()
    }

    class ThreadView {
        +ApprovalCard
        +TrustCard
        +SkillCard
        +MsgRow
        +CopyBtn
    }

    class RightPanel {
        +FilesTab / PreviewTab
        +ChangesTab
        +TasksTab
    }

    class ViewPanels {
        +WorkflowView
        +ScheduleView
        +PluginView
        +PrView
        +GitPanel
        +SshView
        +SettingsView
        +TitleBar
    }

    class BusEventReducer {
        <<module>>
        +handleBusEvent(ev)
        +ensureStreamMsg(runId)
        +flushTokens()
        +closeStream()
        +sendMessage(text)
        +respondSkill(sid, msgId, use)
        +steerRun(sid, msg)
        +cancelRun(sid)
        +refreshSessionList()
    }

    class DaemonLauncher {
        <<module>>
        +ensureDaemon()
        +findRepoRoot()
    }

    BrowserWindow --> RpcTransport : 主进程持有
    BrowserWindow --> FsBridge
    DaemonLauncher ..> RpcTransport : 连不上时懒启动
    PreloadBridge ..> RpcTransport : ipc 通道 rpc-request
    PreloadBridge ..> FsBridge : ipc 通道 fs 与 settings
    PreloadBridge ..> BrowserWindow : contextBridge 暴露 window.iwan
    App --> Store
    App --> GuiStore
    App --> Sidebar
    App --> ThreadView
    App --> Composer
    App --> RightPanel
    App --> ViewPanels
    Sidebar --> GuiStore
    Sidebar --> Store
    Composer --> BusEventReducer
    ThreadView --> BusEventReducer
    BusEventReducer --> Store : 事件归约（唯一写入口）
    BusEventReducer --> PreloadBridge : rpc 调用
    Store ..> PreloadBridge : 本窗口内不直接持有 socket
```

## 图 B：Textual TUI 的单 App 多会话

```mermaid
classDiagram
    direction LR

    class TuiApp {
        -_sessions: dict
        -_session_order: list
        -_session_id: str
        -_client: SocketClient
        -_active_run_id: str
        +on_mount()
        +_switch_session(sid)
        +_add_session(sid, title)
        +_save_current_state()
        +_load_session_state(sid)
        +_refresh_tabbar()
        +_update_header(state)
        +on_chat_text_area_submitted(event)
        +action_switch_session(idx)
        +action_cycle_permission_mode()
    }

    class SessionState {
        +session_id: str
        +title: str
        +widgets: list
        +auto_mode: str
        +permission_mode: str
        +trust: str
        +effort_level: str
        +model_preset: str
        +busy: bool
        +last_context_pct: float
        +current_llm: Any
        +pending_tool_blocks: dict
        +pending_permission_blocks: dict
        +subagent_run_ids: dict
        +active_run_id: str
    }

    class SocketClient {
        -_reader: StreamReader
        -_writer: StreamWriter
        +connect()
        +on_event(cb)
        +send_command(method, params)
        +run_event_loop()
        +close()
    }

    class ChatTextArea
    class LLMStreamBlock
    class ToolCallBlock
    class PermissionSelect {
        +_KEY_MAP
        +_pick(decision)
    }
    class SkillConfirm
    class TrustWidget
    class FileChangesPanel
    class SlashComplete

    TuiApp *-- "1..*" SessionState : 每标签一份
    TuiApp --> SocketClient
    TuiApp --> ChatTextArea
    TuiApp --> LLMStreamBlock
    TuiApp --> ToolCallBlock
    TuiApp --> PermissionSelect
    TuiApp --> SkillConfirm
    TuiApp --> TrustWidget
    TuiApp --> FileChangesPanel
    TuiApp --> SlashComplete
    PermissionSelect ..> SocketClient : permission.respond
    SkillConfirm ..> SocketClient : session.send_message(skill_name)
```

## 读图要点（客户端设计的三个共性决策）

1. **daemon 是唯一事实源，客户端只做归约**：`Store` 的每个字段几乎都能对应到一条事件（`runningSids` ← `run.started/finished`，`metaCwd` ← 磁盘 meta + `session.created`，`contextPct` ← `llm.usage`）。GUI 里唯一的"本地真相"是 `useGui`（主题/项目表/置顶），它落 `~/.iwan/gui.json`，daemon 完全不知道——这是 VS Code 的 "settings vs runtime" 双轨。
2. **事件 → 视图要走"归属表"**：`runOwner: Map<run_id, sid>` 把只有 `run_id` 的事件（`llm.token`/`step.*`/`tool.*`）路由到正确会话的线程；`run.started` 现在自带 `session_id`，所以归属不再靠 `goal === 用户原话` 猜（`store.ts:509-531`）。
3. **TUI 的状态是"每标签一份"**：`_SessionState` 把 widgets/busy/pending 审批/子 Agent 计时全挂在会话上（`tui/models/session_state.py`），切标签 = 换一组 widget 指针，不动后台 run——这是它能"后台会话继续跑"的原因。

## 客户端级并发的历史坑（已修，详见 17 号评估）

- 修复前：`Composer.sending` 是**窗口级**布尔（组件只有一份实例），而 `session.send_message` 的响应要等整个 run 结束才返回（daemon 侧 `manager.py:489` `await run_task`），于是**一条 run 未结束前，同一窗口发不出第二条任务**。
- 修复后：`sendingSids: Set<string>` 按会话记账，`sendMessage` 进入置位、整轮结束清零；`Composer` 只读当前会话那一位，A 在跑不影响 B。
- 修复前：流式合并用单一 `activeStream` 槽位 + 无参 `closeStream()`，并发两条 run 时 A 的尾段会被 B 的 `run.finished` 连坐丢掉。
- 修复后：`streams: Map<runId, …>`，`flushTokens` 遍历所有活跃流、同帧一次 `setState`；`closeStream(runId)` 只关自己；断线时 `resetStreams()` 清光标。

## 代码锚点

- GUI 主进程：`gui/src/main/{index.ts,rpc-transport.ts,fs-bridge.ts,daemon.ts}`
- GUI 桥：`gui/src/preload/index.ts`
- GUI 状态与归约：`gui/src/renderer/src/{store.ts,gui.ts,guiHelpers.ts}`
- TUI：`src/iwan_claude/tui/app.py`、`tui/models/session_state.py`、`tui/widgets/*`
