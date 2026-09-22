# 桌面 GUI 架构篇：Electron + React 客户端（技术选型定稿）

> 读者：项目所有者（决策用）+ 将来的实现者（含 Claude）。
> 状态：定稿，待开工。
> 版本沿革：本文**取代** `desktop-gui-pyside6.md`（该文因"脱离 web"约束选了 Qt 路线；
> 所有者了解 Codex 本体即 Electron 后撤销该约束，选型重定为 Electron + React，旧文档删除，
> 对比决策在本文 §1 完整存档，防止将来反复）。对 `docs/plans/S10_gui_console_plan.md` 而言，
> 本文回到其"Web 前端"大方向，但把壳从"浏览器 + bridge 起步"调整为"Electron 桌面优先、web 形态后置"。
> 界面长什么样、每个控件按下发生什么：见姊妹篇 `gui-frontend-codex-replica.md`（调研/截图复刻规格/视觉 token 均在那篇，本文只管骨架）。

---

## 0. 决策一句话

**后端 `iwan-core`（Python daemon）一行不改；新增 `gui/` 子工程：Electron + React + TypeScript 客户端，桌面版由 Electron 主进程直连现有 TCP JSON-RPC；同一套 React 代码将来可单独打包成浏览器 web 版（届时加一层可选 bridge）。CLI 与 TUI 两个既有前端全部保留。**

## 1. 选型终版与决策过程存档

| 方案 | 判定 | 理由 |
|---|---|---|
| **Electron + React + TS** | ✅ **定选** | 复刻目标（Codex 界面）官方同栈，抄设计不翻译；一套代码白送 web 形态；组件生态与 AI 辅助编码熟练度最高；VS Code/Discord/Codex 验证过的"应用感" |
| PySide6 原生 | ️ 降为备选 | 曾按"脱离 web"约束定选；约束撤销后其唯一优势（不含浏览器）不再是需求，而它在复刻成本上的劣势（圆角/渐变/动画逐项自绘、Markdown 渲染弱）是真实的。协议映射、里程碑骨架、设置页设计全部平移进本方案 |
| Tauri | ❌ 不采用 | 壳更轻（~10MB），但 bridge/文件系统逻辑要用 Rust 写，Windows 开发体验与调试链路成本高。**若将来要瘦身体积，迁移路径存在**（渲染层零改动，只换主进程） |
| C# WPF / WinUI3 | ❌ | 第二语言工程，协议模型要手写一份，单人项目不划算 |

**接受的代价**（写死，免得日后反悔时又当新发现）：桌面安装包 ~150-200MB（内置 Chromium）；常驻内存 ~200MB 级；电脑要装 Node.js 工具链（M0 实际用 npm，非 pnpm），仓库里多一个前端子工程。

## 2. 形态矩阵（这是本方案相对 Qt 路线买到的东西）

| 形态 | 载体 | 到 daemon 的通路 | 期数 |
|---|---|---|---|
| 桌面应用 | Electron 打包 exe | **主进程 Node 直连 TCP :7437**（无需 bridge） | M0-M3 |
| 浏览器 web | `vite build` 产物 + 静态服务 | bridge（WS↔TCP 透传，§6） | M4 可选 |
| 终端 TUI | 现有 iwan-tui | TCP（不动） | 已存在 |
| CLI | 现有 iwan | TCP（不动） | 已存在 |

关键工程手段：渲染进程的传输层做成**一个接口两个实现**——`Transport { request(method, params), onEvent(cb) }`，桌面走 `ipcRenderer`（主进程代发 TCP），web 走 `WebSocket`。界面代码对"自己跑在哪种壳里"零感知。

## 3. 进程模型

```
┌ iwan-desktop (Electron) ────────────────────────────┐         ┌ iwan-core (Python, 不改) ─┐
│ 主进程 (Node)                                        │  TCP    │  :7437 JSON-RPC 2.0/NDJSON │
│  ├─ DaemonSupervisor: 探端口→不通则懒启动 daemon      │────────▶│   24 Command / 30 Event    │
│  ├─ RpcBridge: TCP NDJSON ⇄ ipcMain 通道             │         │   EventBus                 │
│  └─ 系统能力: 托盘/通知/文件对话框/设置文件读写        │         └────────────────────────────┘
│ 渲染进程 (React)                                     │
│  ├─ transport/ipc.ts ──(桌面)──┐                     │
│  ├─ transport/ws.ts ───(web)───┤→ store (Zustand)    │
│  └─ components: Sidebar / Composer / 卡片流 / 设置页 │
└──────────────────────────────────────────────────────┘
```

安全纪律（Electron 官方红线，照做）：渲染进程 `nodeIntegration: false` + `contextIsolation: true`；网络 I/O 全部在主进程；`contextBridge` 只暴露两个白名单通道（`rpc.request` 双向调用、`rpc.event` 事件推送），不把裸 ipcRenderer 漏给页面。

## 4. 协议复用：TS 类型是生成物，不是手抄本

前端绝不手写消息定义。新增 `scripts/gen_ts_types.py`：读 `core/bus/commands.py` 与 `events.py` 的 pydantic 模型，输出 `gui/src/protocol/types.ts`（判别联合原样映射）。与 `gen_protocol_doc.py` 同源思路，纳入 `--check` 校验：**协议改一个字，前端 CI 就红**，防止双端漂移。这是本项目的架构生命线，优先级等同"deny→ask→allow 评估序"。

## 5. 前端技术栈（最小主流清单）

| 层 | 选型 | 备注 |
|---|---|---|
| 语言/框架 | TypeScript + React 19 | Vite 构建，electron-vite 管壳 |
| 样式 | TailwindCSS v4 + CSS 变量 | 复刻篇 §3 的视觉 token 表**原样**做成 `:root` 变量，双主题热切 |
| 组件底座 | shadcn/ui（复制进仓库，非依赖） | 侧栏/弹层/按钮/对话框现成，源码可读可改 |
| 状态 | Zustand | 会话表/时间线/审批队列三块 store |
| Markdown | react-markdown + Shiki | 代码高亮不糊弄 |
| 打包 | electron-builder → NSIS 安装包 + portable exe | 自动更新（M4 后评估） |

**流式渲染节流**（界面再好看也怕这个）：`llm.token` 进 store 的环形缓冲，`requestAnimationFrame` 批量 flush（60fps 上限），块结束才挂载正式卡片。这条约束从 Qt 时代继承，实现更简单。

## 6. web bridge（M4 才做，设计先立此）

独立进程 `iwan-bridge`（Python，aiohttp/websockets）：浏览器 WS 收到的 NDJSON 原样转发 TCP :7437，反向同理——**它不理解协议，纯透传**，所以 daemon 与前端都零改动。仅监听 127.0.0.1，Origin 白名单 + 启动时打印一次性配对码。不进 daemon 进程：守护进程的事件循环不背 web 栈依赖。量级 ~1 周，排在桌面版全部落地之后。

## 7. 安全边界

- 不新增网络监听面：桌面形态下 :7437 仍是唯一 loopback 口，bridge 默认不启动。
- **token 鉴权转正**（原 Qt 篇 M3 待办，照搬）：daemon 启动生成 `~/.iwan/daemon.token`，客户端连接握手校验。桌面化放大"本机任意进程可驱动 agent"风险面，此项在 M3 内完成，不再挂账。
- 审批/信任语义 GUI 只做渲染与答复透传（`permission.respond` / `trust.respond`），**任何判定逻辑不进前端**——与 Codex "智能全在核心"的官方纪律一致。
- 已记住规则（policy.toml `[always]`）与信任目录（trust.toml）在设置页可见可撤，界面规格见复刻篇 §6。

## 8. 仓库工程结构

```
iwanclaude/
├─ src/iwan_claude/…        # Python：core daemon + CLI + TUI，不动
├─ gui/                     # 新增：pnpm 工程（Electron + React）
│   ├─ src/main/            # 主进程（DaemonSupervisor, RpcBridge, 托盘）
│   ├─ src/renderer/        # React 界面
│   └─ src/protocol/        # ← 生成物，勿手改
└─ scripts/gen_ts_types.py  # 新增生成脚本
```

开发命令：`uv run iwan-core`（后台）+ `pnpm -C gui dev`。uv 管 Python、pnpm 管前端，两套锁文件互不越界；lint 各跑各的（ruff/mypy 基线不变，前端 eslint/tsc 独立）。

## 9. 里程碑（每期末都是能用的一版；界面 DoD 见复刻篇 §7）

| 期 | 交付 | 量级 |
|---|---|---|
| M0 ✅ | Electron 骨架 + `gen_ts_types.py` + 截图空态一屏：连接、建会话、发一句话、流式回复。**2026-09-22 完成**：`gui/` 子工程，dev 启动 → daemon 连接 → 注入消息 → llm.token 流式渲染 → run 完成标记，capturePage 截图验证通过 | ~4 天 |
| M1 ✅ | 卡片时间线 + 侧栏项目分组/最近/改名/停止与 steer。**2026-09-22 完成**：Markdown 渲染（react-markdown+gfm）、工具卡含详情折叠、审批/信任/技能/压缩/子任务事件归约、`session.get_history` 历史回放、流式段切分、行内改名（原地替换不跳位）、运行中行 ⚡转向/■停止（run.steer/run.cancel）、项目分组+最近区；devtest 链（注入→历史加载→改名→切换）全绿零 console 错误。事件严格 run 归属 + "待认领"机制修复跨会话串线与"运行中"残留（详见 store.ts 学习要点） | ~1 周 |
| M1.5 ✅ | 前端大修（用户验收反馈"不好看/功能不齐"后的定向补课）。**2026-09-22 完成**：设置弹窗六分区（外观：主题/字号/缩放写 gui.json；项目登记；MCP 只读卡片+原文折叠；审批与信任：deny→ask→allow 说明+信任表实时撤权；引擎与模型：session.engine_info+config.toml 表格，全局作用域如实标注；关于：core.ping 版本/路径）；右栏三页签（懒加载文件树——展开才 readdir、路径越界 jail、256KB 截断、GBK 兜底、点文件开关、文件预览浮层 / 变更页签接 files.changes+files.restore 逐文件回滚 / 任务页签读 `runs/<run_id>/.tasks`）；Composer 四选择器（审批 5 档、模型 3 档+解析名、努力 5 档、引擎 6 档一个不少）+上下文水位条+压缩、附件对话框（ui:pick-file）、运行中 Enter=转向/■停止；线程内审批卡（四决策按钮）/信任卡（本次/总是/拒绝）；深色主题整组 token 切换；Popover 视口向上翻（devtest 抓出的真 bug：贴底工具栏菜单跑出视口）。devtest v2 链（一步一注入+错峰截图）四轮迭代全绿；抓到并修复：树根 rel 前导斜杠致 safeJoin 误判越界、树根空标签。零后端改动约束保持 | ~1 周 |
| M2 | 审批卡队列强化（多请求排队 UI）+ 设置页规则表**可编辑**（需 ★ 协议补全，单独请示） | ~1 周 |
| M3 | 桌面体化：托盘/通知 + electron-builder 安装包 + daemon token 鉴权（深浅主题已在 M1.5 完成） | ~1 周 |
| M4 | （可选）iwan-bridge + web 形态上线 | ~1 周 |

## 10. 风险账（新栈新账）

1. **npm 供应链**：lockfile 提交进库 + 新增依赖须经决定（不装来路不明的包）。
2. **Electron 升级节奏**：它背 Chromium 安全公告，大版本每年至少跟一次（Codex 也在背，这是同栈同命）。
3. **Windows 中文路径**：daemon 已有 `normalize_dir_key` 教训，前端所有路径过 `path.normalize` 且大小写敏感逻辑交给后端，不重蹈覆辙。
4. **双栈维护**：单人项目多一个工具箱是真实负担——缓解：前端代码 AI 生成+人审，协议生成物锁死一致性，接口薄（§2 Transport）。
