# GUI 前端设计：Codex 桌面版复刻（调研 + 界面规格）

> 读者：项目所有者（决策用）+ 将来的实现者（含 Claude）。
> 状态：调研与设计定稿，未开工。
> 定位：本文是**界面层复刻蓝图**；进程/线程/协议架构见姊妹篇 `desktop-gui-electron.md`（Electron + React 选型定稿，本文只回答"长什么样、每个控件按下后发生什么"）。
> 素材：项目所有者提供的 Codex 桌面版截图（2026-09-22，浅色暖调主题，中文界面，provider 显示 deepseek）。
> 版本沿革：本文初版按 PySide6 写实现层；选型改定 Electron + React 后，§0/§5/§6 相应重写，
> 调研成果（§1-§4、视觉 token、协议映射）全部保留。

---

## 0. 结论先行

1. **Codex 官方前端 = Electron + React**，这本身是 web 技术栈——但它的**架构形状**（薄 UI 壳 + JSON-RPC 守护进程）和 iwanclaude 完全同形，复刻它不需要我们改后端。
2. 选型终版：**Electron + React + TypeScript 壳，Python 后端不动**（决策过程与 PySide6/Tauri 对比存档见姊妹篇 §1）。复刻对象是 Codex 的设计语言与交互模型，而且这次**连技术栈都同构**——它免费的视觉效果我们全免费。
3. v1 复刻范围 = 截图那一屏：左侧栏（新对话/项目分组/最近）+ 中央空态 + 底部悬浮 Composer。截图中**我们后端没有的功能**（定时任务、Pull Request、插件）保留入口但置灰，不假装能用。

---

## 1. 调研 A：Codex 前端技术选型（外部事实存档）

| 层 | 技术 | 证据来源 |
|---|---|---|
| 桌面壳 | **Electron 40**，TypeScript | kitze.io 技术拆解、HN 讨论 |
| 渲染层 | **React**（Vite 构建） | kitze.io |
| 输入编辑器 | **ProseMirror**（富文本 composer） | yuanjiwei 架构分析 |
| 认证 | OAuth2（ChatGPT 账号） | 同上 |
| 智能核心 | **Rust**（`codex-rs`，~208 crate），作为 **子进程 `codex app-server`** 被壳拉起 | InfoQ、zicode、gist 开发者指南 |
| 协议 | **JSON-RPC 2.0 双向**，NDJSON | 官方 App Server 文档 |
| 传输 | stdio(JSONL) / WebSocket / Unix socket 多形态 | zicode、promptfoo 文档 |
| 选 Electron 的理由 | 一套代码 macOS+Windows 双平台；复用 ChatGPT 桌面版经验 | HN 讨论 |
| 生态佐证 | 官方 Python/TS SDK 同样走 JSON-RPC 驱动 app-server → "任何客户端"是官方设计意图 | learn.chatgpt.com/docs/codex-sdk |

截图里为什么出现 "deepseek"：Codex 支持 `~/.codex/config.toml` 自定义 `model_providers`（指向 OpenAI Responses API 兼容端点），CLI 与桌面端通用——左下角账号位显示的是当前活跃 provider，不是官方账号。这也证明 **UI 与模型层解耦**是 Codex 设计的一部分。

**对我们的三点启示**：

1. **同构安心**：`iwan-core`（TCP :7437, JSON-RPC 2.0 over NDJSON）≈ `codex app-server`（stdio/WS, JSON-RPC 2.0 over NDJSON）。官方 UI 壳和我们即将做的 React 壳在协议面前平级。
2. **官方纪律**：智能全在守护进程，壳只做渲染与答复。复刻时保持——**GUI 里不许长任何引擎逻辑**（这条从 TUI 时代就立的规矩，写死在验收标准里）。
3. **同栈红利**：Codex 视觉语言（圆角卡片、渐变、过渡动画）是浏览器送的免费午餐，我们同栈直接继承——初版 Qt 方案里"逐项自绘"的成本清单整体作废。

---

## 2. 调研 B：Codex 桌面版功能面

### 2.1 窗口布局

三区：左侧栏（固定 ~240px）｜中央 thread 视图｜右侧功能区（可切换收起）。新对话时空态居中，有会话后中央变卡片流。

### 2.2 左侧栏（截图逐行对得上）

- 顶部：**新对话**（高亮按钮）、**Pull Request**、**定时任务**、**插件**
- **项目**分组：目录即项目（ClaudeCode / 毛概社会实践 / IwanClaude），展开显示该项目下的会话；会话 hover 出置顶📌/归档图标；正在跑的会话有状态点
- **最近**：不属于任何项目的散会话
- 底部：账号/provider 位（deepseek ⚙）+ 帮助
- 已知坑（社区反馈）：CLI/agent 会话会灌爆侧边栏、无作用域过滤 → 我们的会话列表要预留过滤设计

### 2.3 Composer（输入区控件清单，对照 GitHub issue #26856）

项目选择器（folder 图标"选择项目"）｜模式选择（"随心输入"占位）｜添加文件 ＋｜**审批下拉**（截图="请求批准" chip）｜模型选择器（"DeepSeek-V4-Flash 高"= 模型 + 推理强度）｜发送圆钮。附加交互：`/side` 侧边对话、Appshots（双击 Cmd 截当前窗口进输入框）。

### 2.4 设置界面（Settings 文档 + 中文详解）

分区导航：**常规**（快捷键、启动行为、通知）｜**外观**（基础主题 + 强调色 + 背景/前景色 + UI 字体/代码字体，26.312 起支持自定义主题与主题分享）｜**MCP**｜**Git**（自动化/worktree）｜**智能体**（模型、审批策略、宠物）。→ 抄它的**分区结构**，内容换我们的（§6）。

### 2.5 审批与安全交互

审批模式是 Composer 上的常驻 chip（点开切换，而非每次弹窗）；run 等审批时发系统通知；config.toml 有 `approval_policy` 与 `autoApprove` 类设置。→ 与我们五态权限模式（design/permission-modes.md）语义一一对应。

### 2.6 Codex 有、我们后端没有的功能（明确列账）

Pull Request 集成、定时任务（Automations）、插件目录、Git worktree 并行、Review 面板、云端沙箱执行。v1 处理：侧栏入口置灰 + tooltip"计划中"；不进当前里程碑。

---

## 3. 截图拆解：复刻规格（视觉 token 层）

| 区域 | 内容 | 规格 |
|---|---|---|
| A 标题栏 | 应用名"∨ 文件 编辑 视图 帮助"+ 窗口控制 | Windows 原生标题栏即可（frameless 自绘不做，省成本；Codex 在 Win 也是系统框） |
| B 左侧栏 | §2.2 结构 | 宽 240px 固定；条目高 ~36px，8px 圆角；hover 浅灰底；选中项灰底+文字加粗 |
| C 主空态 | 中央云形 logo 插画 + "我们要构建什么？" | 垂直居中偏上 40%；标题 ~22px 常规字重；插画灰调单色 |
| D Composer | 悬浮卡片（选择项目行 + 输入区 + 工具行） | 宽度约中央区 60% 居中，底部留白 ~40px；纯白卡、大圆角(~14px)、1px 浅描边、极淡投影；聚焦时描边微亮 |
| E 状态 | 顶条暖色渐变带（淡黄→奶油→淡粉紫），左栏米白 | 整窗背景 `linear-gradient`；内容面板半透白 |
| F 右下 | 用户头像圆钮 | 可后置（M4） |

**主题 token 表**（实现为 `:root` CSS 变量，Tailwind 主题层引用，双主题各一份值）：

```
--bg-gradient    = #F6F1E7 → #F7F2EC → #F3EDEA → #EFEAEF   /* 顶→底暖色渐变 */
--sidebar-bg     = #FBF9F5        --card-bg       = #FFFFFF
--text-primary   = #1F1F1F        --text-secondary = #9A9A9A
--item-hover     = #F0EDE6        --item-active   = #E8E4DA
--accent         = #7C6FD0 /* 钉选/归档图标紫灰 */           --status-run = #22C55E /* 绿点 */
--radius-card    = 14px           --radius-item   = 8px
--ui-font        = "Microsoft YaHei UI"   --code-font = "Cascadia Code"
```

深色主题 = 同变量换值（Codex 有"暖色浅色"这件事本身值得学：主题不止黑白两套）。

---

## 4. 控件 → 协议映射（GUI 每个可点的东西实际做什么）

命令名已对照 `src/iwan_claude/core/bus/commands.py` 核实（全部真实存在，除标注★外）：

| UI 元素 | 动作 | 协议调用 |
|---|---|---|
| 新对话 | 建会话进空态 | `session.create{cwd}` → `event.subscribe(topics=session.*,run.*,llm.*,permission.*,trust.*,file.*)` |
| 项目选择器 | 选目录=选项目 | Electron 主进程目录对话框；cwd 随 `session.create` 下发；陌生目录由 daemon 广播 trust.requested → TrustCard 弹出（S9 协议现成） |
| Composer 发送 | 跑一轮 | `session.send_message{sid, content}`；流式渲染吃 `llm.token`（rAF 批量 flush，见姊妹篇 §5） |
| 审批 chip（"请求批准"） | 显示/切换权限模式 | 读会话 meta；切换发 `session.set_permission_mode`；审批卡答复发 `permission.respond` |
| 模型选择器 | 换模型/推理强度 | `session.set_model`；强度 `session.set_effort_level`；引擎 `session.set_engine`（六引擎一个不能少，入口放右栏） |
| 侧栏项目分组列表 | 列会话 | `session.list`（返回含 cwd/title）→ 按 cwd 分组渲染；状态点来自订阅事件 |
| 会话改名 | 双击重命名 | `session.rename`；`session.renamed` 事件反向刷新（daemon 自动标题已上线，GUI 白捡） |
| 停止 | run 中 ⏹ | `run.cancel`；运行中改需求 = steer 输入（TUI 已有交互语义，平移） |
| 文件改动面板 | 查看/回滚 | `files.changes` / `files.restore`；时间旅行 `session.checkpoint.list/restore` |
| 压缩上下文 | 右栏按钮 | `session.compact` |
| 历史恢复 | 打开旧会话 | `session.get_history` + `event.subscribe(replay_from_run)` |
| ★ 置顶 / 归档 | 侧栏 hover 图标 | **协议缺口**：需 daemon 新增 `session.set_pinned` / `session.set_archived`（写 meta，两行 handler + 一个事件）；v1 可先做客户端本地顶替 |
| 定时任务 / PR / 插件 | — | 无协议无后端 → 入口置灰，M 系列之后另立项 |

---

## 5. Web 栈实现对照（初版 Qt 成本清单的平反记录）

| 要复刻的效果 | 实现 | 成本 |
|---|---|---|
| 圆角卡片 + 淡投影 | `border-radius` + `box-shadow` | 免费 |
| 整窗暖色渐变 | 顶层容器 `linear-gradient` | 免费 |
| 侧栏列表 + hover 图标 | flex 列表 + `:hover` 显隐，shadcn sidebar 底座 | 低 |
| Composer 自增高 | textarea `field-sizing: content`（或 20 行 auto-resize hook） | 低 |
| 卡片流时间线 | 虚拟化列表（@tanstack/react-virtual）+ 异构卡片组件注册 | 中，唯一要动脑的一块 |
| Markdown 渲染 | react-markdown + Shiki 高亮 | 近乎免费 |
| 打字机流 | token → Zustand 环形缓冲 → `requestAnimationFrame` 批量 flush；块结束才挂正式卡片 | 设计定死，照做 |
| hover/折叠动画 | CSS transition（Codex 手感原教旨继承） | 免费 |
| 深浅色热切 | `data-theme` 属性换 token 变量组 | 低 |
| 高 DPI/缩放 | 浏览器引擎天然处理 | 免费 |
| 输入法（中文）| contenteditable/textarea 原生支持，无 Qt 那套 IME 坑 | 免费 |

结论：截图那一屏在 web 栈里全部是**常规作业**，没有一块硬骨头；工程难点整体从"视觉复刻"移到了"状态管理与流式渲染的纪律"（§4 映射表 + 节流约定就是为此存在）。

---

## 6. 设置界面设计（借 Codex 分区，装我们内容）

全屏设置视图（路由 `/settings`，左导航 + 右表单，Codex 同款模态形态）：

| 分区 | 项 | 落盘 |
|---|---|---|
| 常规 | 语言（中/英）、开机自启、托盘行为、通知开关 | `~/.iwan/config.toml` [gui] 节（Electron 主进程读写，仅 GUI 消费） |
| 外观 | 基础主题（暖白/深色/跟随系统）、强调色、UI/代码字体与字号 | 同上 |
| 模型 | provider/endpoint/api_key、默认模型、推理强度 | config.toml（与 daemon 共读同一文件，改后需 core restart 的提示要写明） |
| 审批与安全 | 默认权限模式五态、审批超时、已记住规则表（policy.toml `[always]`，可单条删除）、信任目录表（读 `trust.list`，删除走 `trust.revoke`） | ★规则表协议缺口：v1 先只读展示 + 提示用 `iwan policy` CLI 删，M3 补 `policy.list`/`policy.drop` 命令。这两个表**必须做成 GUI 里可见可撤**——"always 记住了什么"不可见是 TUI 时代用户最大困惑 |
| 引擎 | 六引擎状态与默认（只读展示 + 默认选择） | config.toml |
| 关于 | 版本、日志路径、daemon 端口状态灯（探 :7437） | — |

---

## 7. 分期验收（以截图为 M0 的 DoD）

| 期 | 交付 | 验收标准 |
|---|---|---|
| M0 | 空态一屏 | 启动 GUI：侧栏/渐变/空态文案/Composer 与截图并排对比"第一眼是同类产品"；能选项目→发一句话→看到流式回复 |
| M1 | 会话面 | 卡片时间线（chat/stream/tool）、多会话切换、`session.list` 项目分组、最近区、改名 |
| M2 | 安全面 | PermissionCard 队列、TrustCard（含回声：答复后必有一行可见结果——TUI 踩过的坑不再踩）、文件回滚面板、审批 chip 五态、设置页 |
| M3 | 桌面体化 | 深浅主题热切、托盘+通知、electron-builder 安装包、daemon token 鉴权、置顶/归档与规则表协议补全（★项转正） |
| M4 | （可选）web 形态 | iwan-bridge 上线，浏览器打开同一套界面 |

## 8. 明确不抄的

云端任务托管、计算机操作（computer use）、PR 审查面板、宠物系统、主题市场。侧栏只留形，功能不追。

---

## 9. 调研来源

- [kitze.io — A technical breakdown of the Codex desktop app](https://kitze.io/posts/codex-electron-app-technical-breakdown)
- [yuanjiwei — The Architecture Behind OpenAI's Codex Desktop App](https://yuanjiwei.com/20250215-architecture-behind-codex/)
- [HN — The Codex App](https://news.ycombinator.com/item?id=46859054)
- [InfoQ — OpenAI Publishes Codex App Server Architecture](https://www.infoq.com/news/2026/02/opanai-codex-app-server/)
- [oneryalcin — developer's guide to Codex JSON-RPC interface (gist)](https://gist.github.com/oneryalcin/ee2c27e2d8aa040da8fbe7eebcc2ecea)
- [zicode — Codex App Server 实践](https://zicode.com/blog/codex-app-server-practice/)
- [learn.chatgpt.com — Settings 参考](https://learn.chatgpt.com/docs/reference/settings) / [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) / [Scheduled tasks](https://learn.chatgpt.com/docs/automations)
- [OpenAI — Introducing the Codex app](https://openai.com/index/introducing-the-codex-app/)
- [腾讯云开发者 — Codex App 设置详解（常规/MCP/外观/Git/智能体）](https://cloud.tencent.com/developer/article/2690054)
- [知乎 — Codex (APP) 保姆级全攻略](https://zhuanlan.zhihu.com/p/2032921324759277659)
- [runoob — Codex 桌面应用](https://www.runoob.com/codex/codex-app.html)
- [GitHub issue #26856 — Composer controls 组件清单](https://github.com/openai/codex/issues/26856) / [#11073 自定义配色](https://github.com/openai/codex/issues/11073)
- [OpenAI community — CLI 会话灌爆侧边栏反馈](https://community.openai.com/t/codex-cli-and-agent-sessions-flood-the-desktop-app-sidebar-with-no-way-to-scope-them/1387453)
- [ofox — Codex 接入第三方模型/自定义 provider 排查](https://developer.cloud.tencent.com/article/2702724)
