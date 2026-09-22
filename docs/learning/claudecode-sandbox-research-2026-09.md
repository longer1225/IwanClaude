# Claude Code 权限与沙箱模型 —— 官方实现调研存档

> 调研日期：2026-09-21。基于 Anthropic 官方文档（code.claude.com/docs）与官方博客，
> 由 claude-code-guide agent 逐项核实并标注来源。本文是 CLAUDE.md
> "Security model" 小节的完整依据，改动权限/沙箱代码前先读这里。
> ⚠ 方法学注记：调研环境 WebFetch 对官方域被网络策略拦截，事实来自 WebSearch
> 对官方页的引用摘要，未逐字校对原文；版本号/时间线以官方 changelog 为准。

## 1. 权限规则模型

评估顺序：**deny → ask → allow，先匹配先赢**。任一层级的 deny 不能被任何层级
或其他参数（`--allowedTools`、bypass 模式）的 allow 掉。

- **ask 盖过 allow**——预批准 ≠ 免确认
- 规则是**工具级**（`Bash` 盖过整个工具）+ 可选内容级限定符
- 未匹配任何规则的调用默认**弹窗 ASK**（"default ask"）。官方无 `defaultPolicy` 设置；`rm -rf /`、`git push --force` 之类**没有找到硬编码必确认规则**的证据，靠 default-ask + 模型保守性兜底

specifier 匹配语义（修正版）：

| 形态 | 语义 |
|---|---|
| `git commit`（无 `*`） | **整串精确匹配**，不是前缀 |
| `git commit:*` | **遗留前缀语法**，等价于 `git commit *`（`:*` ≡ 尾部 ` *`） |
| `*` | 匹配任意文本**含空格**，可放任意位置；但**通配符前必须有空格**（`git*` 不合法，`git *` 合法） |
| `Bash(run_in_background:true)` | 存在参数匹配形式 |
| `Read(./.env)`、`Read(./.env.*)` | **文件类工具用 gitignore 语法**，不是前缀语法 |
| `WebFetch(domain:x)` | 域名限定；`WebFetch(domain:*)` ≠ 裸 `WebFetch` |

工具名 allow glob（`*`、`B*`、`mcp__*`）被跳过并告警——只有 deny/ask 允许宽 glob。

**官方未明确**：引号/变量展开/重定向/`$( )`/brace 扩展在拆解时如何规范化——社区实测
这些形态能绕过静态前缀规则（formal.ai、dylancaponi）。**我们的实现选择更保守：
含命令替换/重定向/brace 的段一律不享受 allow（fail-closed）。**

来源：[permissions](https://code.claude.com/docs/en/permissions) ·
[settings-reference](https://code.claude.com/docs/en/settings-reference)

## 2. 权限模式

| 模式 | 语义 |
|---|---|
| `default` | 每次工具调用做权限决策 |
| `acceptEdits` | 项目目录内编辑/创建免批，**还自动批准常见文件系统 bash**（mkdir/touch/rm/rmdir/mv/cp/sed，仅限 cwd 或 additionalDirectories 内路径；`git stash` 不在列）；不绕 ask 规则 |
| `plan` | "权限覆盖层"：仅允许命中 allow 规则的动作 + 内置只读 Bash 集（ls/cat/echo/pwd/head/tail/grep/find/wc、git status/log/diff）；**文件编辑永不自动批准，即使 allow 规则命中**；ask 规则在 plan 下**直接拒绝**而非弹问；v2.1.212 起沙箱内只读命令也要弹问 |
| `auto` | **独立 classifier 模型逐动作审查**（2026-03 引入，约 8 月成默认）：两阶段（单 token 粗筛 → CoT 裁定），裁定只看危害、不考虑用户意图；拦"超出请求的升级/不属你的基础设施/不可逆/疑似外泄"；反复被拦回落人工确认；误拦良性命令约 0.4%。`--enable-auto-mode` 已移除，Shift+Tab 菜单切换 |
| `dontAsk` | 不是常规模式（SDK/headless 另有） |
| `bypassPermissions` | 跳过全部弹问（含写 `.git`/`.claude`），**但 deny 规则仍生效**；`rm`/`rmdir` 命中关键路径仍回落确认；**root/sudo 下拒绝启动** |

切换：Shift+Tab 循环 default→acceptEdits→plan（bypass 不进默认循环）；`--permission-mode X`；`--dangerously-skip-permissions`；settings `defaultMode`。官方推荐 `/plan` 起手再切回。
**官方未明确**：plan 的只读限制是提示层还是工具层硬拦（issue #19874、#39201、#40324 主张非硬阻）。

来源：[permission-modes](https://code.claude.com/docs/en/permission-modes) ·
[auto-mode-config](https://code.claude.com/docs/en/auto-mode-config) ·
[auto 模式博客](https://claude.com/blog/auto-mode) ·
[auto 工程博客](https://www.anthropic.com/engineering/claude-code-auto-mode)

## 3. 锁死与不可覆盖

- 显式 deny/ask 规则**优先于所有模式**，连 `--dangerously-skip-permissions` 都跳不过（"cannot be overridden"）
- managed 层可**强制** ask 规则（"Even `--dangerously-skip-permissions` cannot override a managed ask rule"）
- managed 独占：`autoMode`、`sandbox` 的 FS/网络 allow 列表、`allowedMcpServers`/`deniedMcpServers`（只能 managed 设）、`forceLoginMethod`/`forceLoginOrgUUID`、`requireLogin`、`allowedChannelConfigurators`、`disableSideloadableCredits`
- 不可覆盖的例外：`CLAUDE.md` 等记忆文件"加载但不遵从"可被 managed 锁
- `allowManaged*Only` 系列把对应集合限制为仅 managed 项

来源：[permissions](https://code.claude.com/docs/en/permissions.mdx) ·
[settings](https://code.claude.com/docs/en/settings.mdx)

## 4. OS 级沙箱

`Bash(sandbox)` 工具走 OS 沙箱：**macOS 系统 `sandbox-exec`（Seatbelt）；Linux/WSL2 用 bubblewrap（需装 bubblewrap+socat）；原生 Windows 不支持，WSL1 也不支持**（issue #46740 开放），官方指引 Windows 用户走 WSL2——**这对以 Windows 为主栈的我们是最大的差异点**。沙箱能力已抽成独立包 `@anthropic-ai/sandbox-runtime`。

**默认 FS 写 = cwd + 会话临时目录**（`sandbox.filesystem.allowWrite` 增补）；读默认全盘（除 deny 目录，`~/.ssh`、`~/.aws` 屏蔽）。**网络默认全拒**，域名须进 `sandbox.network.allowedDomains`；本地代理按 **hostname** 决策、**不解 TLS**；忽略代理环境变量的工具落到内核层（Seatbelt/bwrap）阻断——域不在 allowlist 时是**结构上不可达**（代理永不授权 + 内核层阻断），不是"可达但会弹问"（"unreachable by policy" 短语本身出自第三方文章，官方未用此措辞）。

与批准耦合的开关（设计我们自己的模式联动时可借鉴）：`autoAllowBashIfSandboxed`（可沙箱化的命令不弹问直接跑）；`allowUnsandboxedCommands: false` 关掉 `dangerouslyDisableSandbox` 逃逸口（默认允许沙箱失败后请求非沙箱重试，实测可被模型自行重试）；`failIfUnavailable` 把"沙箱不可用"从警告变硬失败。

**后台命令绕过沙箱网络限制是官方设计行为**。Windows 上 Anthropic 的硬化候选（restricted token + FS broker + AppContainer）仍是探索方向——我们在 Windows 做 `CreateRestrictedToken` 属自研，无官方先例，必须自带 fail-closed 验证。

来源：[sandboxing](https://code.claude.com/docs/en/sandboxing) ·
[sandbox-environments](https://code.claude.com/docs/en/sandbox-environments) ·
[沙箱工程博客](https://www.anthropic.com/engineering/claude-code-sandboxing) ·
[Windows 沙箱缺口 issue #46740](https://github.com/anthropics/claude-code/issues/46740)

## 5. Hooks

- PreToolUse 在任何权限评估**前**运行；**exit 2 = 强制阻断 + stderr 回灌模型（此时 stdout/JSON 一律被忽略，是唯一翻不了的结果）**；无论 allow 规则
- 权限决策可走 JSON：`permissionDecision` allow/deny/ask/**defer**（defer → `/queue` 恢复继续跑）、`updatedInput` 改写入参、`updatedPermissions` 持久化规则、`additionalContext`；**多 hook 并存取最严结果**
- **方向不对称**：hook 的 deny 连 `bypassPermissions`/`--dangerously-skip-permissions` 都能拦；hook 的 **allow 只跳过交互弹问，不覆盖 deny/ask 规则**（曾出现 allow 盖 deny 的回归，官方按 bug 处理，issue #52822）
- `disableAllHooks` 跳不过 enterprise 策略钩子（managed 层 `policy_managed: true`），managed 可 `writable:false` 锁钩子自身；企业钩子以 root 运行，仅 Windows
- PreToolUse 期间弹窗被抑制，`PermissionRequest` hook 可代答弹窗
- `canEscalateSandbox` / `isNativeToolUseDecision` / hook 的 `canPreRemediate`：控制是否允许绕过沙箱/用户审批，默认拒绝
- `sandboxOverride` 只在 `suggestions` 里（持久化沙箱例外用）

来源：[hooks-guide](https://code.claude.com/docs/en/hooks-guide.mdx) ·
[settings](https://code.claude.com/docs/en/settings.mdx) ·
[沙箱笔记](https://code.claude.com/docs/en/sandboxing-notes.mdx)

## 6. 复合命令与后台的权限语义

- `&&`/`||`/`;`/`|` 复合命令**拆成子命令，每个都必须各自被规则覆盖**才放行；"Yes, don't ask again" **为每个需批准的子命令各存一条规则**，而不是整串（issue #29491）
- **前缀规则永远不能自动批准 exec wrapper**：`watch`、`setsid`、`ionice`、`flock`、`nice`、`nohup` 等与 `find -exec`/`find -delete`，Manual 模式必弹
- `(...)` 分组被拒；`if`/`for`/`while`/`case` 体内有危险命令则整体标 risky 强制弹窗
- 重定向 `>`/`>>` 视为**写操作**：`echo x >> ~/.bashrc` 被 deny 拦，而 `echo 'export …' >> ~/.bashrc` 放行（文档矛盾处，标注）
- `timeout 10 command` 的权限检查看的是内层 `command`；`xargs` 展开出的命令**不**过权限
- 反斜杠续行：单 token 续行可匹配规则，跨运算符续行弹原始多行形式
- 子进程内部再 exec、脚本内容检查：**官方未明确**（静态分析只到命令行层）——formal.ai 论证"允许某前缀 ≈ 允许任意代码执行"
- Monitor 工具**复用 Bash 同一套权限规则**并计入同一每会话命令配额；插件 monitor 跑在沙箱外；后台 agent 需要交互批准的工具会**静默失败**（issue #30264）

来源：[sandbox-config](https://code.claude.com/docs/en/sandbox-config.mdx)

## 7. 设置层级合并

优先级：**managed > CLI flag > local > project > user**。

| 文件 | 角色 |
|---|---|
| `managed-settings.json` | 企业 IT，覆盖一切，不可被任何层覆盖 |
| `--settings` CLI 层 | 在 project 与 local 之间（"both take precedence over …"） |
| `.claude/settings.json` | 项目提交进仓库 |
| `.claude/settings.local.json` | 个人，自动 gitignore |
| `~/.claude/settings.json` | 用户全局 |

- `dropIns`：目录内多片段按文件名排序合并；`strict` 冲突报错
- settings 损坏时 managed 文件被整体忽略（含 drop-ins），非 managed 跳过坏文件
- 合并语义：**逐 key 深合并，但 deny 在任何层级都压过任何层级的 allow**（不是"高层全赢"）；`/permissions` 面板显示每条规则来源
- 部分安全敏感 key 只从 managed/`--settings`/user 读取，**项目与 local 文件里被忽略**（防克隆仓库自提权）
- Windows managed 路径：`C:\Program Files\ClaudeCode\managed-settings.json` + 注册表 `HKLM\SOFTWARE\Policies\ClaudeCode`；Linux `/etc/claude-code/`；macOS `/Library/Application Support/ClaudeCode/`
- managed 独占键见第 3 节；`forceRemoteSettingsRefresh` 仅 managed

来源：[settings](https://code.claude.com/docs/en/settings) ·
[server-managed-settings](https://code.claude.com/docs/en/server-managed-settings) ·
[admin-setup](https://code.claude.com/docs/en/admin-setup)

---

## 对 iwanclaude 现有假设的 4 条纠正

（对照我们 docs/ 里此前的描述——这些误传要改）

1. **Windows 没有现成的"restricted token 三件套"官方先例**——Anthropic 自己还在探索（token/broker/AppContainer 三选一），且承认"可被绕过"。我们的 CreateRestrictedToken 路线是自研，必须自带 fail-closed 验证。
2. **deny 覆盖一切 allow**，ask 也覆盖 allow——我们 roadmap 里写的方向一致，但 policy.py 实现时注意 `Bash(npm run test:*)` 是**字符串前缀**，不是"命令的第一个词"。
3. **`rm -rf /`、`git push --force` 官方无硬编码确认**——靠 default-ask 兜底。我们不需要抄"内置危险命令清单"，该抄的是"未匹配即 ASK"。
4. **沙箱默认允许一切读写**——Anthropic 明确：沙箱防失控不防策略，策略仍归权限系统。两套机制正交。我们此前把路径白名单当"权限"用是混了层——正确形态：权限层管"该不该做"，沙箱层管"失控了能碰到什么"。
