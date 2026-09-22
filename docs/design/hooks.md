# Hooks 体系设计（Batch ②）

> 对齐 Claude Code 的 PreToolUse/PostToolUse 钩子模型，落地为 iwanclaude 的
> 可扩展审批/审计层。状态：✅ 已实现（2026-09-21，core/hooks/ + manager
> Tier 0 + invocation PostToolUse + hook.evaluated 事件；16 项单测全绿）。

## 1. 目标与非目标

**目标**
- 让用户/团队在不改 core 代码的前提下，向工具调用生命周期注入自定义逻辑：
  拦截、审计、通知、格式化后处理。
- 提供比声明式规则更强的表达力：规则只能"形状匹配"，hook 可以执行任意程序
  （查工单系统、跑 lint、校验分支策略）。

**非目标（本期）**
- Notification / Stop / SubagentStop 等其他事件类型（生命周期先只开
  PreToolUse + PostToolUse 两个点，接口留扩展位）。
- hook 间通信、异步长驻 hook 进程（每个 hook 一次性子进程）。
- matcher 的 regex/多值形态（本期只做精确工具名 + `"*"` 通配）。

## 2. 语义模型（对齐官方，来源见 docs/learning/claudecode-sandbox-research-2026-09.md）

1. **PreToolUse 先于一切权限评估**：在六种模式下都会运行，位置在
   `evaluate_pre_cache` 之前——hook 是"规则引擎之前的一道闸"。
2. **方向不对称**（关键安全不变式）：
   - hook 判 DENY → 地板级，连未来的 bypassPermissions 也翻不了；
   - hook 判 ALLOW → 不豁免后续 deny 规则/强制 ASK：只有当后续链没有任何
     更强的声音时，hook ALLOW 才等价于放行。
   - 直觉：hook 是"外部裁判"，可以否决，不能越权特赦。
3. **退出码协议**（stdout 是 JSON 时的细化，先定义再实现）：
   - `exit 0` + 无输出/非 JSON 输出 → 弃权（继续走权限链）
   - `exit 0` + JSON → 读 `permissionDecision`（allow/deny/ask，别名 defer→弃权）
     与可选 `reason`（进审批弹窗与日志）
   - `exit 2` → 硬性 BLOCK：stderr 作为拒绝理由**回灌给模型**（模型能看到并
     改道），stdout/JSON 一律忽略
   - 其他非零退出 / 超时 → **fail-closed：ASK**，把 stderr 摘要展示给用户
     （坏掉的钩子不能变成静默放行通道）
4. **多 hook 聚合**：同一事件多个 hook 时取最严格结果
   （deny > ask(block) > allow > 弃权），任一 hook 的 deny 即时生效。
5. **PostToolUse**：工具执行完成后运行，不能撤销已执行的调用（语义是审计/
   后处理），其输出作为反馈事件推送（本期只推 EventBus，不入对话流）。
   `enabled` 且退出 2 时，把 stderr 作为 tool_result 的补充警告推给模型。

## 3. 配置

```toml
# ~/.iwan/config.toml 或 .iwan/config.toml
[[hooks]]
event   = "PreToolUse"          # PreToolUse | PostToolUse（必填）
matcher = "bash"                # 精确工具名 或 "*"（默认 "*"）
command = "python scripts/guard.py"   # 经 shell=False 拆分后 exec，防注入
timeout_s = 10                  # 可选，默认 10
```

- 启动期校验：event 枚举、matcher 非空、command 非空、timeout>0；
  command 用 `shlex.split`（Windows 回退 `subprocess.list2cmdline` 逆向）解析，
  解析失败 → SystemExit（与 permission 规则同一"笔误当场炸"哲学）。
- **不接受任意 shell 字符串直接跑**：`command` 拆成 argv 后 `shell=False`
  执行——hook 本身不能成为绕过沙箱的 RCE 入口（`command = "guard.py && rm -rf /"`
  会作为找不到该文件名的失败处理，而不是执行 rm）。

## 4. 实现结构

```
core/hooks/
  __init__.py        # 导出 HookRegistry, HookOutcome
  spec.py            # HookSpec dataclass + 配置解析/校验（parse_hook_config）
  runner.py          # 子进程执行器：argv 构造、stdin JSON 载荷、超时、退出码协议
  registry.py        # 按事件+matcher 索引；聚合出最严格 HookOutcome
```

- **载荷（stdin JSON）**：`{"hook_event_name", "tool_name", "tool_input",
  "session_id", "run_id"}`——与官方 schema 对齐，方便已有生态脚本移植。
- **HookOutcome**：`decision(ALLOW/DENY/ASK/NONE)`, `reason`, `from_hook(spec)`,
  `stderr_to_model`（exit 2 路径专用）。
- **接线点**：`PermissionManager.check_and_wait` 最前面加
  `pre = await self._hooks.run_pre(tool_name, params)`；
  - pre DENY → 直接拒绝（reason 回灌，等价 forced）；
  - pre ASK → forced_ask=True 进现有审批链（不查缓存——hook 说要问就是问）；
  - pre ALLOW → 记录标记，继续走链但**跳过指纹缓存**（不重复弹）之外不豁免任何
    deny/强制 ASK 层；链末无更强声音时以 ALLOW 结束。
- **事件**：`HookTriggeredEvent` / `HookDeniedEvent` 加入 bus/events.py，
  TUI 事件流可见"哪条 hook 在什么裁定"（观测性优先 TUI）。
- **runner 工具侧**：PostToolUse 在 registry.execute 成功路径后调用，
  失败/拒绝路径不触发（对齐官方"只有执行成功才 PostToolUse"）。

## 5. 安全边界

- hook 子进程**不受** core 沙箱约束（它是裁判不是选手），但由用户在配置中
  显式声明——信任边界从"模型发的命令"转移到"人写的配置"，与 crontab 同级。
- 超时 kill：Windows 用 Job Object（复用 tools/job_object.py，不可用时退化为
  terminate）；确保挂死的 hook 永不拖死审批链。
- hook 输出进日志前截断（512B），stderr 回灌模型前同样截断——防日志注入。

## 6. 测试计划

- 单测 spec/registry：配置校验矩阵、matcher 匹配、聚合最严格序。
- 单测 runner：用临时 python 脚本当 hook，覆盖 exit0无输出/exit0+JSON
  allow/deny/ask/defer/exit2+stderr/超时/非零退出/argv 解析拒绝 shell 拼接。
- manager 集成：hook DENY 不可被 persistent allow 翻；hook ALLOW 仍被 deny
  规则拦（方向不对称的回归门）；空配置时行为与现在逐字节一致。
- WIRE_PROTOCOL.md 重新生成（新增两个事件模型）。

## 7. 决策记录

- 【设计】hook 失败默认 ASK 而非 DENY/PASS：ASK 把决定权交给人，是 fail-closed
  家族里唯一保留可用性的档位（与 sandbox ContextVar、SSRF 逐跳同一哲学）。
- 【设计】ALLOW 不越权特赦：官方文档确认 "hook allow 不覆盖 deny/ask 规则"，
  若实现成"ALLOW 直接短路放行"，一个被攻陷的 hook 脚本即等于全系统旁路。
- 【设计】argv 化执行：官方允许 shell 字符串是因为它有 OS 沙箱兜底；我们的
  Windows 路线短期没有等价物，所以先收紧。
