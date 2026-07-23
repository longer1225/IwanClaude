# IwanClaude 开发计划书

> 版本：v1.0  
> 日期：2026-07-22  
> 目标：对标 Claude Code 核心体验，分三阶段建设 IwanClaude 的高级功能。

---

## 一、项目现状

IwanClaude 当前是一个本地双进程 AI Agent 系统：

- `iwan-core`：常驻守护进程，负责 LLM 调用、工具执行、会话管理。
- `iwan-tui`：基于 Textual 的终端界面，负责用户交互。
- `iwan`：轻量 CLI，用于脚本化测试。

已具备能力：文件操作、Bash、Git、Python 执行、HTTP、搜索、RAG、Skill、MCP、子 Agent、任务管理、检查点、权限系统、会话压缩、Ctrl+R 历史搜索、@filename 引用等。

---

## 二、差距分析

与 Claude Code 2026 相比，核心差距集中在四个方面：

| 能力 | Claude Code | IwanClaude | 优先级 |
|------|-------------|------------|--------|
| 自动模式 | Auto Mode 自动批准低风险操作 | 每次工具调用都需确认 | 高 |
| 努力等级 | Effort Level 控制读取/验证/步数 | 无控制策略 | 高 |
| 模型切换 | 根据任务切换 Sonnet/Fable 等 | 单一默认模型 | 高 |
| 并行会话 | 多会话/标签页并行 | 单会话 | 中 |
| 工作流 | Workflow 脚本编排多 Agent | 无 | 中 |
| 定时任务 | Routines 定时触发 | 无 | 中 |
| 代码审查 | /ultrareview 五级审查 | 无专用审查 | 中 |
| GitHub 集成 | PR 自动审查、@claude | 无 | 低 |
| Web/桌面 | 多端访问 | 仅 TUI | 低 |

---

## 三、三阶段路线图

### 阶段一：核心体验（预计 2-3 周）

目标：让单会话使用体验接近 Claude Code。

1. **Auto Mode 自动模式**
   - 在 `PermissionManager` 中增加 `auto_mode` 状态机：off / read_only / on。
   - `read_only` 自动批准所有读操作；`on` 额外自动批准白名单内的写操作。
   - TUI 底部状态栏显示当前模式，支持 `/auto [off|read_only|on]` 切换。

2. **Effort Level 努力等级**
   - 定义 5 个等级：minimal / low / medium / high / max。
   - 控制维度：最大文件读取数、验证轮数、最大步数、递归搜索、自动测试。
   - 在 AgentLoop 和 LangGraphAgentLoop 中接入限制逻辑。

3. **模型动态切换**
   - 配置多模型预设：fast / balanced / powerful。
   - 根据 effort_level 自动选择模型。
   - TUI 支持 `/model <preset>` 临时切换。

### 阶段二：并行与编排（预计 3-4 周）

目标：支持复杂多任务场景。

1. **并行会话与多标签**
   - TUI 支持多标签页，每个标签对应一个 session_id。
   - 快捷键：Ctrl+T 新建、Ctrl+W 关闭、Alt+1~9 切换。
   - 支持会话命名（`/name`）和持久化。

2. **Workflow 工作流引擎**
   - YAML 定义工作流，支持串行、并行、条件分支。
   - 支持步骤间变量传递（`{{step.output}}`）。
   - 内置模板：code_review、bug_sweep、refactor。

3. **Routines 定时任务**
   - 数据模型：`prompt` + `repo_path` + `schedule` + `output_target`。
   - 基于 asyncio 的 cron 调度器。
   - CLI 命令：`iwan routine create/list/delete`。

### 阶段三：生态集成（预计 4-6 周）

目标：形成完整工具链。

1. **代码审查增强**
   - 5 级审查：quick / standard / thorough / security / architectural。
   - 输出结构化 Markdown 报告。
   - TUI 命令：`/review [level] [target]`。

2. **GitHub Actions 集成**
   - GitHub App 模板和 Actions workflow。
   - 支持 PR 自动审查、`@claude implement` Issue 处理。
   - CoreApp 增加可选 HTTP webhook 端点。

3. **Web 界面（可选）**
   - 调研 textual-web / FastAPI + React 方案。
   - 实现基础 Web 界面。

---

## 四、Auto Mode 详细设计

### 4.1 目标

减少用户确认打断。对低风险操作自动批准，对高风险操作仍要求确认。

### 4.2 自动模式定义

| 模式 | 说明 |
|------|------|
| `off` | 完全手动，所有 ASK 都弹窗确认（当前行为）。 |
| `read_only` | 自动批准所有只读操作；写入、执行、网络仍需确认。 |
| `on` | 在 `read_only` 基础上，自动批准白名单内的写操作。 |

### 4.3 工具分类

- **只读工具**：`read_file`、`list_dir`、`search`、`git_status`、`git_diff`、`git_log`、`task_get`、`task_list`、`session_get_history` 等。
- **写工具**：`write_file`、`edit_by_search`、`edit_by_lines`、`delete_file`、`copy_file`、`git_commit`、`git_checkout` 等。
- **执行工具**：`bash`、`run_python`。
- **网络工具**：`http_request`。
- **子代理工具**：`spawn_agent`、`spawn_agents`。

### 4.4 涉及文件

- `src/iwan_claude/core/config.py`：增加 `auto_mode` 配置字段。
- `src/iwan_claude/core/permissions/manager.py`：实现 auto_mode 状态机与绕过逻辑。
- `src/iwan_claude/core/permissions/policy.py`：增加 `AUTO_MODE_READ_ONLY_TOOLS` 和 `AUTO_MODE_ALLOW_WRITE_TOOLS` 集合。
- `src/iwan_claude/core/bus/commands.py`：增加 `SetAutoModeCommand` / `SetAutoModeResult`。
- `src/iwan_claude/core/app.py`：注册 `session.set_auto_mode` 命令处理器。
- `src/iwan_claude/core/session/model.py`：Session 中保存当前 auto_mode。
- `src/iwan_claude/core/session/manager.py`：支持设置和查询 auto_mode。
- `src/iwan_claude/tui/app.py`：状态栏显示模式，处理 `/auto` 命令。

### 4.5 验收标准

- [x] `read_only` 模式下，读操作不弹窗确认。
- [x] `on` 模式下，白名单写操作不弹窗确认。
- [x] `off` 模式下，所有 ASK 都弹窗确认。
- [x] TUI 状态栏实时显示当前模式。
- [x] `/auto <mode>` 命令可切换模式。
- [x] 模式切换通过 RPC 同步到 daemon。
- [x] 测试覆盖：manager 单元测试 + TUI 集成测试。

---

## 五、阶段一剩余任务清单

| 编号 | 任务 | 状态 | 优先级 |
|------|------|------|--------|
| P1-1 | Auto Mode 配置层实现 | 已完成 | 高 |
| P1-2 | PermissionManager auto_mode 状态机 | 已完成 | 高 |
| P1-3 | TUI 状态栏与 `/auto` 命令 | 已完成 | 高 |
| P1-4 | RPC `SetAutoModeCommand` | 已完成 | 高 |
| P1-5 | Auto Mode 单元测试 | 已完成 | 高 |
| P1-6 | Effort Level 数据结构 | 已完成 | 高 |
| P1-7 | AgentLoop 接入 effort 限制 | 已完成 | 高 |
| P1-8 | 模型预设配置 | 已完成 | 高 |
| P1-9 | `/model` 命令 | 已完成 | 高 |
| P1-10 | 端到端集成测试 | 已完成 | 高 |

---

## 六、阶段二任务清单

| 编号 | 任务 | 状态 | 优先级 |
|------|------|------|--------|
| P2-1 | 会话列表与重命名 RPC | 已完成 | 高 |
| P2-2 | TUI 多标签页 UI | 已完成 | 高 |
| P2-3 | TUI 会话切换与状态管理 | 已完成 | 高 |
| P2-4 | 快捷键支持（Ctrl+T/W, Alt+1~9） | 已完成 | 高 |
| P2-5 | `/name` 重命名命令 | 已完成 | 中 |
| P2-6 | 启动恢复最近会话 | 待开始 | 中 |
| P2-7 | 单元测试与集成测试 | 待开始 | 中 |

---

## 七、阶段二设计：并行会话与多标签

### 7.1 目标

支持多会话并行，用户可以在 TUI 中通过标签页切换不同会话，每个会话独立运行。

### 7.2 核心功能

1. **会话列表管理**
   - `session.list`：列出所有会话（按更新时间倒序）
   - `session.rename`：重命名会话标题
   - 会话持久化到磁盘，启动时恢复

2. **TUI 多标签页**
   - 顶部标签栏显示所有会话
   - 当前会话高亮显示
   - 点击标签切换会话
   - 快捷键：Ctrl+T 新建、Ctrl+W 关闭、Alt+1~9 切换

3. **会话切换**
   - 切换时保留当前会话的日志视图
   - 切换到新会话时加载该会话的消息历史
   - 后台会话的事件（如 run.finished）仍会更新对应标签状态

### 7.3 数据模型

会话元数据已包含在 `Session` 模型中（id, title, status, created_at, updated_at），
存储在 `sessions/{sid}/meta.json`。

### 7.4 RPC 协议

| 命令 | 参数 | 返回 |
|------|------|------|
| `session.list` | 无 | `{sessions: [{id, title, status, updated_at}]}` |
| `session.rename` | `session_id, title` | `{session_id, title}` |

---

## 八、风险与依赖

1. **权限安全**：Auto Mode 必须确保写工具白名单足够保守，避免误删文件。
2. **配置兼容**：新增配置字段需要默认值，避免旧 `.env` 和 `config.toml` 失效。
3. **测试覆盖**：每个新功能都需要单元测试，尤其是权限相关逻辑。
4. **文档同步**：`WIRE_PROTOCOL.md` 修改命令模型后需要重新生成。

---

## 七、参考资源

- [Claude Code 官方文档](https://docs.anthropic.com/en/docs/claude-code/overview)
- [Claude Code Model and Effort Level 博客](https://claude.com/blog/claude-model-and-effort-level-in-claude-code)
- [Claude Code Workflows](https://docs.anthropic.com/en/docs/claude-code/workflows)
- [Claude Code Routines](https://docs.anthropic.com/en/docs/claude-code/routines)
