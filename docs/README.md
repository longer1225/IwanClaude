# docs 导航（项目复盘索引）

> 分类原则：**设计思路**（为什么这么做）／**模块深潜**（代码怎么运作）／
> **计划与路线**（接下来做什么、做到哪了）／**调研学习**（外部事实与教学材料）／**待办**（挂起事项）。
> 复盘一个功能时：先看 plans/ 里对应阶段书（状态账），再看 design/ 里的设计决策，
> 想懂实现细节再进 architecture/ 或源码。

## design/ — 设计思路与决策记录

| 文档 | 一句话说明 |
|---|---|
| [hooks.md](design/hooks.md) | PreToolUse/PostToolUse 钩子体系设计：退出码协议、多 hook 取最严、方向不对称（hook ALLOW 不可翻 deny） |
| [permission-modes.md](design/permission-modes.md) | 权限模式对齐 Claude Code 五态（default/acceptEdits/plan/auto/bypassPermissions），deny 地板任何模式不可穿 |
| [rag.md](design/rag.md) | RAG 子系统全面评审后的设计复盘：索引、检索、嵌入链路的取舍 |
| [run-cancel-steer.md](design/run-cancel-steer.md) | 运行中任务取消（run.cancel）与运行中修正（run.steer）的设计（实现中） |
| [subagent.md](design/subagent.md) | 子 Agent 子系统 P0–P2 评审复盘：隔离、递归、资源边界 |

## architecture/ — 核心模块深潜笔记

| 文档 | 一句话说明 |
|---|---|
| [core-app-notes.md](architecture/core-app-notes.md) | CoreApp 与 EventBus/SessionManager/PermissionManager/Runner 的装配关系 |
| [permission-manager-notes.md](architecture/permission-manager-notes.md) | PermissionManager 分层评估链详解（Tier0 hooks → … → 兜底） |
| [session-manager-notes.md](architecture/session-manager-notes.md) | SessionManager 会话生命周期与状态持久化详解 |
| [sandbox-deep-dive.md](architecture/sandbox-deep-dive.md) | 现有沙箱纵深防御详解（威胁模型 → 进程内强化），S9 五层模型的前身 |

## plans/ — 阶段计划与路线图（状态账本）

| 文档 | 一句话说明 |
|---|---|
| [development_roadmap.md](plans/development_roadmap.md) | S0–S8 总路线图（各阶段目标与依赖） |
| [S8_sandbox_rag_editor_plan.md](plans/S8_sandbox_rag_editor_plan.md) | S8：沙箱 + RAG + 精修编辑器 + 工具补全计划书 |
| [S9_security_rollback_plan.md](plans/S9_security_rollback_plan.md) | S9：项目信任 + OS 级强制 + 文件回滚。§7 是状态账：**A、C 已完成（2026-09-22），B 搁置有理由记录**，决策记录见 §8 |
| [S10_gui_console_plan.md](plans/S10_gui_console_plan.md) | S10：图形控制台计划书，S9 协议的消费方 |
| [optimization-roadmap-2026-09.md](plans/optimization-roadmap-2026-09.md) | 2026-09 全面审阅后的优化清单（已完成/进行中/挂起三态账） |

## learning/ — 外部调研存档与教学材料

| 文档 | 一句话说明 |
|---|---|
| [claudecode-sandbox-research-2026-09.md](learning/claudecode-sandbox-research-2026-09.md) | Claude Code 权限/沙箱官方事实存档——**改安全代码前先查这里**（CLAUDE.md 引用此路径，勿移动） |
| [asyncio_create_task.md](learning/asyncio_create_task.md) | asyncio.create_task 学习笔记 |
| tool_annotated_part1.py / part2.py / tool_zh_annotated.py | 工具协议（MCP）逐行中文教学注释样例 |

## todo/ — 挂起事项

| 文档 | 一句话说明 |
|---|---|
| [memory-integration.md](todo/memory-integration.md) | 三层记忆系统集成的后续待办（集成本身已完成 2026-08-08） |
| [project-showcase.md](todo/project-showcase.md) | 简历/面试导向的数据化内容清单；新想法先记这里再排期 |

## 根目录

- `index.md` — 早期自动生成的 API 列表，**已过时**，无引用；确认无用可直接删除。

## 推荐复盘路线

1. **安全线**（当前主线）：`plans/S9_security_rollback_plan.md` → `design/permission-modes.md` → `design/hooks.md` → `architecture/sandbox-deep-dive.md` → `architecture/permission-manager-notes.md`，外部事实对照 `learning/claudecode-sandbox-research-2026-09.md`。
2. **引擎线**：`design/run-cancel-steer.md` → `design/subagent.md` → `architecture/core-app-notes.md`。
3. **知识线**：`design/rag.md` → `todo/memory-integration.md`。
