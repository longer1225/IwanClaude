# 权限模式对齐 Claude Code 五态（Batch ③）

> 状态：✅ 已实现（2026-09-21）。落地与设计的差异：Tier6 的 auto 档用只读
> 白名单近似官方 classifier（诚实标注，非分类器）；`/auto` busy 守卫已按设计
> 移除。验证：17 条模式单测 + s4 IPC 五态 round-trip + daemon 启动冒烟
> （TOML/env/legacy 折叠/两键共存/非法值拒启）。调研依据：
> `docs/learning/claudecode-sandbox-research-2026-09.md`（模式语义一节）。

## 1. 现状与差距

- 现有：`auto_mode ∈ {off, read_only, on}`（daemon 全局、session RPC 切换、
  TUI `/auto` + Shift+Tab 但 **busy 时被挡住**——跑偏时无法现场放行，
  roadmap P2#2）。
- 差距：档位太少且语义混（read_only≈官方没有的中间态；"on"≈acceptEdits 但
  连 bash 无关写工具一起放）；没有 plan（探索模式）；没有 bypassPermissions；
  模式是全局的，不是 per-session。

## 2. 五态语义（对齐官方，落到我们的审批链上）

check_and_wait 现有结构：Tier0 hook → Tier1 deny 地板 → Tier2 强制 ASK →
缓存 → Tier5 allow 类 → Tier6 默认/auto 豁免。模式只改写 **Tier3~6**，
永远不动 Tier0/1/2——"deny 是地板，任何模式翻不了"是官方不变式，也是
本设计的核心卖点：bypassPermissions 也不解除 deny 规则/hook DENY/outside-cwd。

| mode | bash | 文件写类工具 | 只读工具 | ask 的去向 |
|---|---|---|---|---|
| `default` | ASK | ASK | ALLOW | 弹窗 |
| `acceptEdits` | ASK | **自动 ALLOW**（白名单） | ALLOW | 弹窗（仅 bash 等） |
| `plan` | **DENY** | **DENY** | ALLOW | 无（只读探索） |
| `auto` | ASK | ALLOW 仅限 AUTO_MODE_READ_ONLY_TOOLS | ALLOW | 弹窗 |
| `bypassPermissions` | ALLOW | ALLOW | ALLOW | 不弹窗 |

- `plan` 的 DENY 带专用理由文本（"plan mode: 只读探索，切换到
  acceptEdits/default 后可执行"），作为 tool_result 回灌模型让它改道。
- `acceptEdits` = 现 `AUTO_MODE_WRITE_ALLOW_TOOLS` 白名单（写文件/编辑/
  changelog…），bash 不在白名单——与官方 "Edits 不解锁 shell" 一致。
- `auto` 官方走分类器模型；我们本期用只读白名单近似（**如实标注为近似**，
  分类器接入留 roadmap，不把启发式吹成 classifier）。
- `bypassPermissions` 的 ALLOW 只豁免"会弹问的"：deny 地板（Tier0/1）、
  强制 ASK（Tier2：outside-cwd/沙箱/规则 ask/hook ask）**全部保留**——
  对齐官方 "deny rules still apply in bypass mode"。

## 3. 与 auto_mode 的兼容映射

`session.set_auto_mode` RPC 与 `[agent] auto_mode` 配置保留一版，映射：

- `off → default`、`read_only → auto`、`on → acceptEdits`
- 反向（事件广播/查询）显示新 mode；TUI 内部全部改用 mode。

## 4. per-session 化

模式从 daemon 全局改为 `PermissionManager._modes: dict[session_id, mode]`，
默认值取 `[permission] mode = "default"`。check_and_wait 已有 session_id，
零签名改动。session 关闭时 `cancel_session` 顺带清 `_modes`（防泄漏）。

## 5. 协议与 UI

- `commands.py`: `SessionSetPermissionModeCommand`（method
  `session.set_permission_mode`, params: session_id, mode）+ Result
- `events.py`: `SessionPermissionModeChangedEvent`（`session.permission_mode_changed`,
  run_id 无关字段：session_id, mode, previous_mode, ts）
- 进 union + `gen_protocol_doc.py` 再生成 WIRE_PROTOCOL.md
- TUI：Shift+Tab 五态循环（**去掉 not busy 守卫**——本批次的 UX 目的就在这）；
  状态栏显示当前 mode；收到 changed 事件时刷新（多端一致）。

## 6. 测试计划

- manager 单测：五态 × {bash / write_file / read_file} 决策矩阵；bypass 下
  deny 规则/hook DENY/outside-cwd 仍不可翻（地板不变式）；plan 的 DENY 文本；
  per-session 隔离（A 切 acceptEdits 不影响 B）；cancel_session 清模式。
- 兼容层：set_auto_mode("on") → mode==acceptEdits；旧测试不碎。
- TUI：键循环顺序与事件驱动刷新（现有 test_tui_app 模式）。
- 集成：set_permission_mode RPC round-trip。

## 7. 决策记录

- 【设计】模式不新增评估层而是"改写 Tier3-6 的输入"——五态若各写一条
  评估链，与规则引擎的组合会产生 5 份要同步维护的 tier 逻辑；把模式收敛成
  check_and_wait 里的一个决策函数，链本身只有一份。
- 【设计】plan 用 DENY 而非 ASK：官方 plan 的 ask 会被自动降级 deny，
  弹窗在只读探索场景里只会制造噪音（用户没在看终端）。
- 【设计】auto 是白名单近似不是分类器：宁可保守一档，也不把"模型判断"
  包装成并不存在的能力。
