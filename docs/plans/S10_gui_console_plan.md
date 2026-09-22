# S10 计划书：可视化界面（对标 Codex 的图形控制台）

> 前置：`docs/plans/S9_security_rollback_plan.md`（信任对话框、审批、文件回滚的协议都在 S9 定稿，本计划书是它们的**消费方**）。
> 状态：计划书（未开工）。目标读者：决定要不要做、按什么路线做、分几期做。
> 修订 2026-09-22（二）：**§3 技术选型定稿**——曾短暂转向 PySide6 原生（"脱离 web"约束），
> 项目所有者了解 Codex 本体即 Electron 后撤销该约束，终版 = **Electron + React 桌面优先、web 形态后置**。
> 设计定稿见 `docs/design/desktop-gui-electron.md`（架构）+ `docs/design/gui-frontend-codex-replica.md`（界面复刻规格）。
> §1 对标分析与 §4 信息架构仍然有效并被继承。

---

## 1. 我们要对标的到底是什么

Codex 的界面（CLI + IDE 扩展 + 云端控制台三形态）值得抄的不是像素，是四个信息架构决策：

1. **任务列表是主语，聊天是详情**：一屏先看"N 个 run 在跑/等审批/完成"，点进去才是对话流。我们的多会话/子 Agent 已经天然是这个形状，但 TUI 把会话藏在标签页里。
2. **工具调用渲染成卡片，不是文本流**：命令 + 状态 + 耗时 + 可折叠输出 + diff 内联预览。我们 30 种事件里信息全有，缺的是呈现结构。
3. **审批是界面对象，不是终端问答**：PermissionRequestedEvent 在 UI 上是一张带"允许/拒绝/总是允许"按钮和完整上下文的卡片，可以排着队等用户，而不是阻塞式 y/n。
4. **回溯是一等公民**：时间点可跳、diff 可看可撤——正好接 S9 的时间旅行面板。

明确**不对标**的：Codex 云端的沙箱托管执行（我们的 daemon 是本地常驻进程，模型对等物是本地 GUI，不是云任务）。

## 2. 我们手里已有的资产（这是本计划最有利的一点）

```
iwan-core daemon（TCP 127.0.0.1:7437, JSON-RPC 2.0 over NDJSON）
  ├─ 22 个 Command（含 checkpoint list/restore、permission respond、run cancel/steer）
  ├─ 30 种 Event（含 LlmTokenEvent 流式、Permission*、Session*、Subagent*、Hook*）
  └─ event.subscribe + replay_from_run（断线重放已经是对的）
```

**协议层零缺口**支撑一个只读观察台；交互指令除 S9 新增的 trust/file-restore 外全部就绪。GUI 本质上是"第三个客户端"，与 iwan CLI、iwan-tui 平级——架构不动，只加壳。这是与"从零做界面"的项目相比最大的成本优势，必须在计划书里说清楚。

## 3. 技术选型

浏览器不能直连 TCP socket，所以无论选哪个壳，daemon 侧都要加一个 **bridge 层**（新组件，~1 周内的量）：

```
iwan-core ── TCP NDJSON ── bridge（进程内嵌或独立进程）── WebSocket/HTTP ── 前端
```

bridge 首选**嵌进 daemon**（aiohttp/websockets 起在同一 event loop，`host` 默认仍 127.0.0.1，鉴权复用"仅本机 + token"），独立进程为回退选项。

前端四选一：

| 方案 | 栈 | 优 | 劣 | 判定 |
|---|---|---|---|---|
| A. 纯 Web + 浏览器 | React/Vue + Vite | 零打包负担、迭代最快、Windows 无环境坑 | 无桌面集成（通知/托盘要 PWA 凑） | ✅ **P0-P2 采用** |
| B. Tauri 壳 Web | Rust 壳 + 同 A 的前端 | 桌面体验完整、体积小 | 引入 Rust 工具链，违背纯 Python 项目现状 | P3 打包期再议 |
| C. Electron | Node 壳 | 生态最全 | 200MB 起步、与 A 相比无额外收益 | ❌ |
| D. PySide6 + QWebEngine | 全 Python | 无前端语言、无新工具链 | WebEngine 体积大、HTML 渲染在 Qt 壳里调试别扭、UI 迭代反而慢 | 备选（若拒绝引入 JS 栈）|

**推荐路线：A 起步（前端 React + TypeScript），bridge 用 Python（daemon 内 aiohttp），到 P3 用 Tauri 把同一份前端装进桌面壳。** 唯一新增的技术栈是前端语言——这是该项目目前完全没有的能力面，是计划书里必须诚实声明的最大风险（见 §8）。

## 4. 界面信息架构

```
┌────────────┬──────────────────────────────────┬───────────────┐
│ 侧栏        │ 主面板（选中会话/运行）             │ 右栏（上下文）  │
│            │                                  │               │
│ ● 状态灯    │  事件时间线（卡片流）：             │ 文件变更列表    │
│ 会话列表    │   ▸ 用户消息                      │  +diff 预览   │
│  (状态徽章:  ▸ assistant 流式文本               │ 还原按钮(S9-C2)│
│   跑/等审批/ │ ▸ 工具卡片 [name|args|耗时|状态]   │               │
│   完成)     │     └ 展开: 输出/错误/diff         │ 审批队列       │
│            │   ▸ ⚠ 审批卡片                     │ (PermissionReq│
│ [新建会话]  │     [允许][拒绝][总是允许]          │  .listed 卡片) │
│            │   ▸ checkpoint 时间轴(S9-C4)      │               │
│ 设置/信任   │                                  │ Token/费用统计 │
│ 面板(S9-A) │                                  │               │
└────────────┴──────────────────────────────────┴───────────────┘
```

关键约束：
- **右栏是审批队列不是弹窗**：ask 事件可积压、可稍后处理（对齐 Claude Code 审批 UI 的"不可见即丢失"反面）；daemon 侧超时语义不变。
- **工具卡片必须区分 error_type**（S5/MCP 修完后 ToolResult 已带语义分类：timeout ≠ server_offline ≠ tool_error，图标与重试提示不同）。
- **diff 预览数据源是 S9 ShadowStore**，不是现读磁盘对拍——P2 之前先以"文件路径 + 字节数变化"降级显示。
- 流式：LlmTokenEvent 频率高，前端做 50ms 合帧渲染，bridge 可选二进制帧压缩。

## 5. 功能矩阵（界面元素 → 协议映射，验证"有没有协议支撑"）

| 界面元素 | 事件/命令 | 状态 |
|---|---|---|
| 会话列表/切换/新建 | session.list / session.create / 事件流 | ✅ 已有 |
| 事件时间线 | event.subscribe + replay_from_run | ✅ 已有 |
| 发消息 | session.send_message | ✅ 已有 |
| 取消/转向 | run.cancel / run.steer | ✅ 已有 |
| 审批队列与按钮 | PermissionRequested/Granted/Denied + permission.respond | ✅ 已有 |
| 权限模式/引擎/模型切换 | session.set_permission_mode / set_engine / set_model | ✅ 已有 |
| 子 Agent 树 | SubagentStarted/Finished | ✅ 已有 |
| Token/费用 | LlmUsageEvent | ✅ 已有 |
| 压缩指示 | ContextCompactedEvent | ✅ 已有 |
| 信任对话框 | TrustRequestedEvent + trust.respond | 🚧 S9-A1 |
| checkpoint 时间旅行 | session.checkpoint.list/restore（+run 标注） | 🚧 S9-C4（命令已有，索引待加） |
| 文件 diff/还原 | file_changes.list / file_restore | 🚧 S9-C2 |
| 全局搜索历史 | 无 | ❌ 新增命令，P3 |

P0-P1 能做的全部有现成协议——**不依赖 S9**；S9 依赖项只挡住 P2 的三个面板。

## 6. 分期

| 期 | 交付 | 判据 | 预估 |
|---|---|---|---|
| **P0 桥与观察台** | bridge（WS）、前端骨架、事件时间线只读渲染、会话列表 | 浏览器打开 `http://127.0.0.1:<port>` 能看到 TUI 里正在发生的完整事件流 | 3-4 天 |
| **P1 交互** | 发消息、审批队列（含三按钮）、取消/转向、模式切换 | GUI 成为可用工作面：跑完一个真实任务全程不碰 TUI | 4-5 天 |
| **P2 回溯面板** | checkpoint 时间轴、文件变更 + diff、还原双确认（依赖 S9-C1/C2/C4 完成） | "回滚这次运行（对话+文件）"一键可用 | 4-5 天 |
| **P3 外壳与打磨** | 浏览器通知/PWA、托盘、设置面板、历史搜索、（评估 Tauri 打包） | 日常默认界面切到 GUI | 按需 |

每期结束 TUI 与 GUI 并行可用，**不存在"界面断档"窗口期**。

## 7. 与 TUI 的定位关系（需要修订 CLAUDE.md 的一条）

CLAUDE.md 现行条款："iwan-tui 是 primary frontend，所有用户侧工作先为 TUI 设计验证。"本计划书落地 P1 后，实际主张改为：

- **协议与语义**仍先在 TUI 验证（TUI 是协议的"最小可用消费端"，回归价值高）；
- **交互密度高的面板**（审批队列、diff 审阅、时间旅行）以 GUI 为主设计目标，TUI 保留能用的最简形态；
- CLI（`iwan`）维持调试定位不变。

修订动作：P1 验收时同步改 CLAUDE.md 措辞，避免规范与现实互相打脸。

## 8. 风险与缓解

| 风险 | 评级 | 缓解 |
|---|---|---|
| 前端栈（TS/React）是项目零基础，我（Claude）写、你无法 review 前端代码 | 高 | P0 起前端保持最小依赖（React+Vite 两件套）；每个组件配截图验收；核心逻辑（合帧、diff 计算）放 Python/bridge 侧，前端只做渲染 |
| scope creep：GUI 项目天然吸引"再加个功能" | 高 | 每期验收判据写死在本计划书；新想法一律进 `docs/todo/project-showcase.md` |
| bridge 进 daemon 引入 aiohttp 依赖与新端口面 | 中 | 默认只绑 127.0.0.1 + 启动期 token（与 IWAN_PORT 同法配置化）；`ui_bridge=false` 默认关，不影响纯 TUI 用户 |
| 双端事件渲染漂移（TUI/GUI 对同一事件显示不一致） | 中 | 事件→展示语义抽成 Python 侧单一格式化模块，两端共用 |
| Windows 终端用户直接开浏览器的体验（找不到 URL/端口） | 低 | `iwan ui` 子命令：确保 daemon 在、打印/直接唤起默认浏览器 |

## 9. 明确不做（防歧义）

- 不做云端部署/多租户（本机工具，协议带 token 即可）；
- 不做移动端；
- 不自研 diff 算法（jsdiff 级别）；
- P0-P2 不引入任何状态管理全家桶（事件流是单一事实源，UI 状态 = 事件 reducer，不需要 Redux 级架构）。

## 10. 开工顺序建议

S9-R1（半天）→ S9-A1 → S9-C1 → **GUI P0**（可与 S9-C2 并行，互不阻塞）→ S9-B 线（OS 沙箱，长周期独立推进）→ GUI P1 → S9-C2 完成 → GUI P2。理由：信任/快照先行给 GUI 提供"值得展示的新数据"；OS 沙箱周期最长且无 UI 依赖，放最后并行不悖。
