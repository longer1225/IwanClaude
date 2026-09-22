# S9 设计方案：项目信任 + OS 级强制 + 文件回滚

> 本文是设计方案（未实现部分均标注阶段）。前置阅读：
> `docs/architecture/sandbox-deep-dive.md`（现有 4 层防御链）、`docs/learning/claudecode-sandbox-research-2026-09.md`（对标调研存档）、
> `docs/design/permission-modes.md`、`docs/design/hooks.md`。
> 回溯部分基于 2026-09-22 的 checkpointer 评审结论 + 外部调研（LangGraph / Claude Code shadow copy / aider / OpenHands / Cursor）。

---

## 0. 要解决的三个问题

| # | 问题 | 现状 | 后果 |
|---|------|------|------|
| 1 | "在任意文件夹安全操作"缺信任起点 | session.cwd 即沙箱根，但**进入新目录无人过问** | 第一次打开恶意仓库 = 未经确认就在其内部给 agent 全套文件权限 |
| 2 | 防线全是进程内的，bash 面可绕过 | 路径检查靠字符串扫描 + 命令黑名单（`_check_bash_paths` 是启发式） | 编码/拼接/间接调用的命令绕过路径检查；与 Claude Code 的 OS 级强制差一整层 |
| 3 | 回溯只回对话不回文件 | checkpoint 默认 `none`（休眠）；restore 只重写 thread.jsonl | agent 改坏了 20 个文件，回滚会话后文件还是坏的 |

三者是一件事：**"敢开、开了敢放权、放权后敢反悔"**。所以合并为同一轮设计。

## 1. 目标 / 非目标

**目标**
- G1 首次进入目录有信任决策点，决定持久化、可撤销，fail-closed（没决定 = 不放开写）。
- G2 OS 级写边界：被 agent 拉起的子进程（尤其 bash/powershell），在操作系统层就写不到沙箱外，黑名单不再是唯一防线。
- G3 文件回滚：对**受控工具**（write/edit/delete 等）产生的文件变更可预览、可一键还原；与对话回溯解耦但 UI 同屏。
- G4 checkpointer 从休眠到可用：默认开启、run 归属可见、有保留上限。

**非目标**
- 不做容器/VM 级隔离（官方也没做；成本失配）。
- 不承诺 bash 副作用可回滚（Claude Code 同样不承诺——对齐此边界，但提供事后 diff 报告）。
- 不实现 macOS Seatbelt / Linux bwrap 本体，只留后端抽象接口（本项目主战场是 Windows）。
- 不做 Streamable HTTP MCP 传输（独立债务，见 MCP 遗留清单）。

## 2. 总览：防御链从 4 层升为 5 层

```
Layer 0  项目信任门（新增）     进入目录 → trust 决策 → 决定后面 4 层能否武装
Layer 1  权限系统               deny→ask→allow 规则序 + default-ask（已有）
Layer 2  沙箱路径检查           resolve 判界 + symlink 防逃逸（已有，保留为第一道）
Layer 3  OS 级强制（新增）      RestrictedToken + DACL：进程写不到界外（Windows 自研）
Layer 4  审计 + 快照（扩展）    审计日志（已有）+ ShadowStore 文件快照（新增，支撑回滚）
```

原则不变：宽 deny 永不可被窄 allow 例外；**新增层的失败语义一律 fail-closed**（建 token 失败 = 拒绝执行命令，而不是退回无沙箱执行）。

---

## 3. Part A：项目信任（Layer 0）

### A.1 信任模型

一个决策问题：**"允许 iwan 在当前目录工作吗？"**。三值答案，独立于权限模式：

| 决定 | 含义 |
|------|------|
| `allow` | 武装会话：cwd 成为沙箱根，文件工具可写，bash 可执行（仍受 Layer1 审批） |
| `deny` | 只读模式：写类工具与 bash 在权限层直接 DENY（forced），仅 Read/Glob/Grep 类放行 |
| `ask`（默认）| 未决定 = 每次写操作走 ask（现状行为），并在 session.create 时弹一次性信任对话框 |

要点：**信任 ≠ 免审批**。trust=allow 只把 default-ask 的"这个目录能不能进来"这层前置解决掉；目录内每条危险命令仍按 deny→ask→allow 序评估。对齐官方：Claude Code 的 trust prompt 也不改变后续审批。

### A.2 TrustStore 持久化

- 新文件 `src/iwan_claude/core/trust/store.py`。
- 存储位置：`~/.iwan/trust.toml`（与 policy_file 平级），键 = resolve 后的目录绝对路径（Windows 不区分大小写：入库前 `lower()`，与 rules.py 的跨平台策略同法）。
- 条目：`[trust."d:/repo/foo"] decision = "allow" | "deny" | "ask"`，附 `updated_at`、可选 `pattern_note`。
- **继承规则（fail-closed）**：查询 cwd 时，若无精确条目，向上找最近祖先的 allow/deny；**子目录默认继承父目录的 trust，但反过来不成立**（trust 了 `~/code` 即 trust 其全部仓库——UI 必须明说这一点，并提供"仅信任精确目录"开关 `trust_inherit = false`）。无任何命中 → `ask`。
- 撤销：CLI `iwan trust list|revoke <path>` + TUI 面板。

### A.3 协议与 UI 流程

- `SessionCreateCommand` 增加 `trust_decision: str | None = None` 字段；daemon 在 `create()` 时查 TrustStore：
  - 命中 allow/deny → 直接按结论武装/锁死，不发问；
  - 未命中 → 发布新事件 `TrustRequestedEvent {session_id, cwd, options:["allow","allow_once","deny"]}`（与 PermissionRequestedEvent 同构，复用现有"事件推送→客户端回包"通路），对应新命令 `TrustRespondCommand`。
- TUI：模态对话框，展示完整绝对路径 + 目录内 `CLAUDE.md/AGENTS.md/.claude/settings.json` 存在性（这些文件本身就是 prompt-injection 面，值得一并提示）。
- 无人应答（CLI 脚本场景）：超时/无订阅者 → 按 `ask` 默认处理，即不武装写权限，**不阻塞创建**。

### A.4 与权限模式的交互

| 模式 × trust | 行为 |
|---|---|
| default × allow | 现状语义（写文件免弹窗由规则决定，bash 仍 ask） |
| acceptEdits × allow | 官方语义：项目内编辑免批，但 **ask 规则仍强制** |
| acceptEdits × deny | 编辑仍锁死——trust 是权限模式的**上游与门**，两者取更严 |
| bypassPermissions × * | 跳 Layer1 规则，但 **Layer0 deny 与 Layer3 OS 边界仍生效**（对齐 managed 层跳不过的哲学） |

## 4. Part B：OS 级强制（Layer 3，Windows 自研）

> 官方无 Windows 先例（`sandbox-exec` 是 macOS、bwrap 是 Linux，issue #46740 开放）。本部分是本项目自研路线，**风险最高、价值也最高**，故单列分级放量。

### B.1 威胁模型修正

default-ask 的假设是"模型保守 + 人看命令"。被注入的模型会写出*看起来无害*的命令（`python -c` 里藏 `open(os.path.expanduser('~/.ssh/id_rsa'),'w')`），人眼审不出来。**Layer3 的假想敌就是通过了 Layer1/2 的调用**。因此必须内核级。

### B.2 SandboxBackend 抽象

```
core/sandbox_backend/__init__.py   # Protocol: async wrap_spawn(argv, cwd, allow_write_dirs) -> Popen-equivalent | Refused
core/sandbox_backend/passthrough.py  # 现状行为（无 OS 强制），默认，行为零变化
core/sandbox_backend/windows_token.py # RestrictedToken 实现
core/sandbox_backend/bwrap.py / seatbelt.py  # 占位 NotSupported（明确 raise，不静默降级）
```

`sandbox.py` 增加 config 键 `os_enforcement: "off" | "auto" | "required"`（映射官方 `failIfUnavailable`：required 下建不成本地隔离 → 命令拒绝执行并回错误文本给模型）。

### B.3 RestrictedToken 设计（Windows 具体方案）

子进程创建链：`CreateRestrictedToken` → 得到受限令牌 → `CreateProcessAsUser`（经 `startupinfo.lpAttributeList` 的 `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` 或直接 ctypes 起进程；`asyncio.create_subprocess_exec` 无法携带令牌，**bash.py 需换成自研 spawner**，这是本 Part 最大的代码改动点）。

令牌内容：
- 禁用特权列表（`SeDebugPrivilege`、`SeTakeOwnershipPrivilege` 等全部 privileges 用 AND-删除）；
- 写权限只保留：`sandbox_root`、会话 tmp 目录、`%TEMP%\iwan-<run_id>`；
- 实现方式 = **对目标目录树设显式 DACL（grant 受限 SID），其余盘符继承 deny-write**。令牌里 `DeleteAllPrivileges + 受限组(SIDs for capability)`；文件边界靠 NTFS ACL，而非内核 filter——这是 Windows 上唯一无驱动可行路径（写 minifilter 驱动是明确非目标）。
- 读：默认**不限制**（对齐官方默认：写窄读宽；`~/.ssh` 泄露风险靠 Layer1 ask 规则 + env_scrub 兜底，读限制列入 backlog）。

已知代价：DACL 修改是**目录级持久副作用**——实现上改为给受限 SID 在沙箱根上 `grant-write`（可逆，随会话撤销 ACE），避免整盘 deny；撤销 ACE 失败必须告警进审计日志。

### B.4 网络

官方模型：默认全拒 + `allowedDomains`；本地代理按 hostname 决策。Windows 自研等价物：
- spawn 前强制注入 `HTTP(S)_PROXY=http://127.0.0.1:<daemon_proxy_port>` + `NO_PROXY` 置空，daemon 起一个最小 CONNECT 代理按规则放行；
- **如实记录**：不吃代理的工具（curl --noproxy、raw socket）可绕过，官方对此的答案是内核层，我们没有内核层——所以本方案是"提高绕过成本 + 审计可见"，不是"结构性不可达"。文档与告警文案不得声称 OS 级网络隔离。
- 后台进程绕网络限制是官方设计行为，不作为缺陷对待（对齐认知）。

### B.5 三开关对齐（官方语义直接继承）

| 开关（新配置键） | 默认 | 语义 |
|---|---|---|
| `auto_allow_bash_if_sandboxed` | false | Layer3 生效时，bash 类 ask 降级为"已沙箱=免弹问"（仅当 trust=allow 且命令未命中 deny/ask 规则） |
| `allow_unsandboxed_commands` | false | `dangerouslyDisableSandbox` 逃逸口默认关闭（对齐官方收紧方向） |
| `os_enforcement` = required | off（阶段1）| 沙箱建不起来 = 拒绝执行，不降级 |

### B.6 分级放量

- 阶段1：`windows_token` 后端实现 + 测试 + `off` 默认（只给显式配置的用户）。验收：受限进程 `echo x > ../../pwn.txt` 在 OS 层失败且错误可读。
- 阶段2：`auto` 在 trust=allow 的新会话默认尝试、失败回退 passthrough + 审计警告。
- 阶段3：评估后默认 `required`。每阶段独立可回退，不合并发布。

## 5. Part C：文件回滚 + checkpoint 激活（Layer 4 扩展 + 回溯层）

### C.1 三层回溯模型（解耦，对齐调研结论）

```
对话回溯   thread.jsonl + LangGraph checkpoint      ← 已有（本轮修好 3 个 bug）
文件回滚   ShadowStore（内容寻址文件快照）           ← 新增
崩溃恢复   snapshot.json 双轨                        ← 已有，不动
```

UI 上合成同一个"时间旅行"面板，底层是**两个独立操作**：回滚对话 ≠ 还原文件（用户明确选择，避免官方"undo 语义模糊"的坑）。

### C.2 ShadowStore

- 新模块 `src/iwan_claude/core/shadow/store.py`。
- 位置：`<sessions_root>/<sid>/shadow/`（内容寻址 blob：`objects/<sha256>`），**绝不 git-init 用户仓库**（调研 5 决策之一；Cursor 的 shadow git 放 `~/.cursor` 同理）。
- 写路径钩子：所有文件变更工具（`write_file`/`edit_by_lines`/`edit_by_search`/`multi_edit`/`insert_at_line`/`delete_lines`/`delete_file`/`rename_file`/`copy_file`/`mkdir`）在 `validate_path()` 之后、执行写之前，调 `shadow.capture(rel_path)` 存**修改前**内容（新建文件存 `tombstone` 标记）。实现方式：不加 10 处散调用——在 `BaseTool` 增加 `FILE_MUTATING = False` 类标记，由 `ToolRegistry.invoke` 统一前置钩子（唯一咽喉点，天然覆盖未来新工具）。
- 记账：`<run_id>/file_changes.json`——`[{rel_path, blob_before, was_new}]`，`file_changes` 追踪从 snapshot.json 的"报告用"升级为"回滚用"。
- bash 写的文件不承诺回滚（官方同边界）；给事后 diff 报告（mtime 扫描沙箱根）。

### C.3 还原协议

- 新命令：`FileChangesListCommand {session_id, run_id}` → 变更清单 + 每文件 before/after diff 预览（after 现读磁盘，标记"已被外部修改"冲突）。
- `FileRestoreCommand {session_id, run_id, paths: [..] | "*"}` → 还原前**再把当前内容存一个反向快照**（还原本身可撤销），逐文件写回。冲突文件默认跳过需 `force`。
- 与对话回溯的组合动作"回滚这次运行"（run 级）：restore_checkpoint(该 run 起点 checkpoint) + FileRestore(run) 一键双发，分两个确认。

### C.4 checkpoint↔run 交叉索引 + 激活（小改动，先行）

- `AgentState` 增加 `run_id: str` 通道（无 reducer → 每次 ainvoke 全量覆盖，正好让**最新 checkpoint 天然标注所属 run**）；`list_checkpoints` 输出 `run_id`，restore 面板按 run 分组。
- 默认激活：`checkpoint_backend` 默认 `"none"` → `"memory"`（零磁盘风险的第一档）；sqlite 档保留给 daemon 常开用户。`checkpoint_db_path` 相对路径解析从 **daemon cwd** 改为 **sessions_root 下**（消除懒启动导致的 DB 漂移）。
- 保留策略：sqlite 每 thread 只保留最近 `checkpoint_keep_last = 50` 个（超出在 run 结束后裁剪）；session close 不删（尊重"close≠delete"），新增 `iwan session purge <sid>` 硬删 + `adelete_thread`。

## 6. 测试与验收

- 信任：新目录 create → 弹事件；respond allow 后写工具放行 / deny 后写工具 forced-deny；祖先继承与 `trust_inherit=false` 各有正反用例；`~/.iwan/trust.toml` 损坏 = 全部回落 ask（fail-closed 用例）。
- OS 强制：受限 powershell 写界外**必须 OS 层报错**（非我们拦截文案）；写界内成功；`os_enforcement=required` 时 spawner 自检失败 → 拒绝执行路径有测试。
- ShadowStore：每个 FILE_MUTATING 工具逐一验证"写失败不留快照/写成功必有快照"；还原→再还原=原样（反向快照链）；冲突检测（外部改过→默认跳过）。
- checkpoint：run_id 标注出现在 list_checkpoints；第 51 个 checkpoint 触发裁剪；DB 落在 sessions_root。
- 回归锁：本轮已加的 3 条（共享 checkpointer 关闭 / run_ids 截断 / replay 目录）必须保持绿。

## 7. 实施排期（每阶段独立可交付、可回退）

| 阶段 | 内容 | 预估 | 依赖 |
|---|---|---|---|
| R1 | C.4 全部（run_id 索引 + 默认 memory + DB 路径 + 裁剪） | 0.5 天 | 无——**✅ 已完成 2026-09-22**（tests/unit/test_checkpoint_activation.py；run_id 索引暂仅 ReAct 引擎，plan/debate/pipeline 三引擎状态通道未加） |
| C1 | ShadowStore + Registry 咽喉点 + file_changes 记账 | 1-2 天 | 无——**✅ 已完成 2026-09-22**（core/shadow/，tests/unit/test_shadow.py 含 invoke_tool 挂点集成） |
| C2 | 还原协议 + CLI/TUI 双确认面板 | 1-2 天 | C1——**✅ 全部完成 2026-09-22**（files.changes/files.restore 协议 + CLI `/files` + TUI F7/`/files` 回滚面板 FileChangesSelect：勾选/全选/强制档/esc 收起、还原后重查账本、空账撤面板；tests/unit/test_tui_app.py 覆盖键路由与挂载路径） |
| A1 | TrustStore + trust 事件/命令 + TUI 对话框 | 1-2 天 | 无——**✅ 已完成 2026-09-22**（core/trust/ + PermissionManager 信任地板 + TrustSelect + `iwan trust` CLI + chat y/n 答复；tests/unit/test_trust.py 13 例含写工具登记守护） |
| B1 | SandboxBackend 抽象 + passthrough 化（bash spawner 收口） | 1 天 | 无 |
| B2 | windows_token 实现（ctypes 链路）+ 阶段1 off 交付 | 3-5 天 | B1 |
| B3 | 本地 CONNECT 代理 + 审计 | 2 天 | B2 |
| 合计 | | ~11-14 天 | |

顺序建议：R1 → A1 → C1 → C2 → B1 → B2 → B3（先给用户可感知的"敢开敢反悔"，最硬的 OS 层最后但独立）。

> **2026-09-22 决策更新**：Part B 整体搁置（用户确认）。理由：官方在原生 Windows 上同样无 OS 层（指引为 WSL2），
> 不做 B 对齐的是官方 Windows 现状而非落后；RestrictedToken 自研链成本最高且保护的场景（注入+骗审批）尚无真实样本。
> 保留两个将来接口：① B1 的 spawner 收口在 A1/C1 中顺手做（bash 执行路径统一）；② bwrap 后端占位（WSL2 用户借力官方等价物）。

## 8. 决策记录

1. **信任与授权分离**：trust 是与 permission_mode 正交的上游与门，取更严者——拒绝"trust=全局免审"的省事方案，因为那是把 default-ask 原则掏空。
2. **Windows 用 RestrictedToken+NTFS ACL，不承诺网络结构性不可达**——无内核层可依赖，诚实声明边界好过过度承诺（文档、错误文案同此）。
3. **文件快照钩子放 ToolRegistry 咽喉点而非各工具**——10 个写工具逐个加调用 = 第 11 个必漏；类标记 + 统一前置是唯一可持续形状。
4. **对话回溯与文件回滚是两个操作、一个面板**——官方调研里"undo 语义模糊"是真实用户抱怨的主源。
5. **默认值激进程度分级**：checkpoint 默认 memory（无害档）先上；OS enforcement 默认 off 到阶段3 才议 required。fail-closed 指"建立失败不降级"，不指"默认全开"。
6. **shadow 存储进 sessions 目录、绝不碰用户仓库**（不 git init、不建 .iwan/ 于 cwd）——避免污染用户 VCS 状态，与 Cursor/aider 的教训一致。

## 9. 风险

- **ctypes 令牌链是全新代码面**：失败模式复杂（UAC 环境、非 NTFS 卷、OneDrive 目录），用 `os_enforcement=off` 默认 + 自检前置吸收；阶段1 灰度期收集真实失败形态再定阶段2。
- **ShadowStore 磁盘增长**：内容寻址 + 同 blob 去重缓解，但大文件仓库仍是问题 → `shadow_max_file_bytes`（复用 sandbox `max_file_size`）超限只记账不存体，回滚该文件时明确报"未捕获"。
- **协议加事件/命令 = WIRE_PROTOCOL 重生成**（机制已有，成本零）。
- TUI 是主前端的约定不变：A1/C2 的 UI 先落 TUI；GUI（S10 计划书）是消费同协议的另一个壳。
