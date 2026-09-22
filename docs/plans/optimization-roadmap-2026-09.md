# 项目审阅与优化建议（2026-09）

> 基于四个并行审阅任务：运行模式架构、沙箱与权限、代码结构与 UX、真实 Claude Code 对标。
> 关键结论（invoke_tool 签名不匹配、runner.py 的 `log` NameError、沙箱全局竞态）已在源码中逐一复核确认。

## 〇、Git 状态

`git status` 显示 `runner.py` modified，但 `git diff` 为空、工作区文件 hash 与 index 一致——**没有真实改动**，是 CRLF/mtime 造成的 stat 缓存假象（`core.autocrlf=true`）。执行 `git add` 后 `git reset` 或直接忽略即可，无需提交。

## 一、P0：先修正确性 bug（当前多套引擎实际是坏的）

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| 1 | **三个引擎的工具调用全线失效** | `langgraph_plan_execute.py:445`、`langgraph_debate.py:326`、`langgraph_pipeline.py:432` | 真实签名是 `invoke_tool(registry, tool_call, bus, run_id, ...)`（`invocation.py:167`），调用方却传 `(registry, tc.name, tc.input)` → 每次工具调用 TypeError 被吞成 "Tool X error"。测试全部构造 `tool_calls=None`，从未覆盖此路径 |
| 2 | 工具结果不回喂模型 | 同上三个文件 | 即使修好 #1，结果是拼进答案文本尾部，不生成 tool_use/tool_result 配对消息，Anthropic API 下直接 400 |
| 3 | `engine="auto"` 必抛 NameError | `runner.py:1124` | `log.info` 引用不存在的名字（全文件用 `logging.getLogger(__name__)`），且被 `except Exception` 吞成 llm_error；另外 config 校验白名单里根本没有 `"auto"`，auto 实际不可达 |
| 4 | 沙箱根是模块级全局，并发会话互相踩踏 | `session/manager.py:317` + `sandbox.py:467` | `send_message` 里 `set_sandbox_root(session.cwd)`，两个会话并发时文件校验边界被对方覆写；文件工具仍按进程 CWD 解析相对路径（`sandbox.py:272` vs `bash.py:246`），多项目隔离形同虚设 |
| 5 | langgraph ReAct 无 max_steps/取消守卫 | `langgraph_loop.py:450-500,582-610` | 路由从不比较 step 与 max_steps，chat↔tools 可无限烧钱 |
| 6 | compact 阈值语义冲突 | `langgraph_loop.py:491` | `auto_threshold` 是 0-1 比例，却被拿来和字符数比较；压缩后不复查占用，有 compact↔chat 死循环风险 |
| 7 | debate/pipeline 收了 compactor 从不调用 | `plan_execute.py:146` 等 | 长任务必爆上下文；只有 legacy loop 正确触发压缩 |
| 8 | debate 调权限缺参 | `langgraph_debate.py:316` | `check_and_wait` 缺 `event_emitter` 必抛 TypeError |

## 二、P1：架构方向——向真实 Claude Code 收敛

四套引擎是**并行复制**而非共享底座：`_execute_tools`、`_end_node` 各复制 3 份，同一 bug 修一处漏三处；多 Agent 引擎运行期不回写 messages，plan→executor 之间靠 200/300 字符截断文本传递，信息损耗严重。

**Claude Code 没有 react/plan-execute/debate 多引擎**。它是一个 agentic loop，"模式"只是权限/工具集参数（default→acceptEdits→plan 由 Shift+Tab 切换，plan 态 = 强制只读工具集 + ExitPlanMode 审批闸门）；复杂任务靠 Task 工具派生**子 agent**（独立上下文、只回传摘要、可并行）。

建议路线：
1. **以 langgraph ReAct 单循环为唯一底座**；legacy loop.py 与其近重复，二选一后删另一个。
2. **debate/pipeline 降级为 prompt 策略或 subagent**（reviewer 作为带完整工具回路的子 agent，而不是图上的独立节点），消灭 4 倍维护面。
3. **engine_selector 移除或简化**：单次 `goal[:500]` 分类、无会话上下文、选中即定终身，收益撑不起复杂度。
4. **plan 改成权限模式而非引擎**：只读工具集过滤器 + 计划文件 + 审批事件（协议里加 `plan.approve` 类事件）。
5. compact 学 Claude Code 分层：先做**旧工具结果占位化**（microcompact，把 `[Old tool result content cleared]` 替换进超过最近 N 对的 tool_result，成本远低于摘要），再做全量摘要，且摘要后**重读最近文件、恢复 todo 列表**（状态重建，不是纯丢失）。当前是全量替换成 summary+ack，缺 partial、缺失败退避。

## 三、P2：沙箱与权限——文档宣称的防御与实际不符

现状是**纯进程内检查，零 OS 隔离**：路径 resolve+relative_to 白名单 + 命令正则黑名单，子进程裸 spawn。

关键漏洞（按危险度）：
- policy 里的 Tier 2.5 沙箱强制 ASK 是**死代码**——生产路径只调 `manager.check_and_wait`（`permissions/manager.py:328`），4 层防御实际 3 层；
- 审批缓存按工具名一刀切（`manager.py:571`）："always_allow write_file" 后任何路径不再审；
- `git.py` 无任何路径检查，`git_diff --output=x` 可向沙箱外写盘（`git.py:385`）；
- `http.py` 只查原始 URL 且 `follow_redirects=True`（`http.py:222`）：302 跳回环、169.254.169.254、DNS rebinding 全放行——真实 SSRF；
- `run_python.py` 靠正则扫源码里的字面量 `open(...)`（`run_python.py:95`），f-string/拼接即绕过，网络完全不限；
- `registry.to_langchain_tools` 桥接 `permission_manager=None`（`registry.py:159`），全旁路，休眠炸弹。

改进路线（按性价比）：
- **周内**：把沙箱路径检查真正接入 check_and_wait；审批缓存键改为 tool+参数指纹；git 加路径校验并禁 `--output`；http 改"解析后 IP 校验 + 逐跳重定向校验 + 封私有/回环/链路本地段"；子进程包 Windows **Job Object**（KILL_ON_JOB_CLOSE + 内存/CPU 限额，1-2 天）。
- **月级**：`CreateRestrictedToken` + 降低完整性级别 + 沙箱外目录 ACL → 硬文件系统边界（改造点集中在两处 spawn，约 1 周）；出网走本地代理域名白名单；统一各引擎权限入口，删 to_langchain_tools 旁路；沙箱根改为 per-run 传入而非全局。
- **长期**：AppContainer（FS+网络双隔离，~1 月）或 `docs/plans/S8_sandbox_rag_editor_plan.md` 已规划的 Docker 路径；命令解析从正则黑名单升级为 **AST/可信 argv + fail-closed**（Claude Code 即 default-deny + deny→ask→allow 先匹配先赢的规则序，宽 deny 不可被窄 allow 例外——当前正则黑名单是 best-effort，方向相反）。
- **补 hooks 体系**（Claude Code 拉开差距最隐蔽的机制）：PreToolUse/PostToolUse 子进程钩子，在**任何权限检查之前**触发，exit 2 阻断并把 stderr 回灌给模型——这是模型不可协商的确定性护栏层。

### 落地进展（2026-09-21 更新）

- ✅ **周内清单全部完成**：Tier 2.5 接入 check_and_wait（原死代码）、审批缓存 tool+参数指纹、git 路径校验+禁 `--output`、http SSRF（协议白名单+DNS 解析校验+逐跳重定向+解析失败 fail-closed）、Job Object（bash/run_python 接入；注意受限环境可能创建失败，本机即未验证生效）
- ✅ 月级 2 项提前：沙箱根 per-session（ContextVar）、`to_langchain_tools` 旁路删除
- ✅ 长期部分：`run_python` 命令解析 AST/fail-closed 完成（`analyze_python_writes`）；**bash 仍是配置黑名单+绝对路径正则**，argv 化未做
  - **2026-09-21 更新：已做**。规则引擎 `core/permissions/rules.py`（deny→ask→allow、逐段求值、`:*`/精确/位置通配三形态、exec wrapper 与动态语法 fail-closed）接入 `evaluate_pre/post_cache`；`[permission] deny/ask/allow` 配置启动期校验；legacy allow_patterns 收紧为仅单段命令可命中；42+22 单测全绿
- ✅ hooks 体系（2026-09-21）：`core/hooks/`（spec/runner/registry）+ manager Tier 0 前置闸 + invocation PostToolUse + `hook.evaluated` 事件；退出码协议、多 hook 取最严格、方向不对称（hook ALLOW 不可翻 deny/ask）。设计文档 `docs/design/hooks.md`
- ✅ 权限模式五态对齐（2026-09-21）：`default/acceptEdits/plan/auto/bypassPermissions`，模式只改写 Tier3~6，deny 地板/强制 ASK/hook DENY 在 bypass 下仍生效；per-session 覆盖 + `session.set_permission_mode` RPC + Shift+Tab 循环 + `[permission] mode` 配置（legacy auto_mode 经 AUTO_TO_MODE 折叠兼容）。设计文档 `docs/design/permission-modes.md`
- ⬜ 未动：`CreateRestrictedToken`+ACL 硬边界、出网代理白名单、AppContainer/Docker；hooks 体系 ✅（见上）
- 📌 认知修正（见 `docs/learning/claudecode-sandbox-research-2026-09.md`）：Windows 无官方内核级隔离先例，自研须自带 fail-closed 验证；"沙箱防失控不防策略"是 Claude Code 的分层原则，我们此前路径白名单混用了权限与隔离两层语义；`rm -rf` 类无需硬编码清单，抄"default-ask"即可

## 四、P2：协议与 UX 补齐

TUI 打分 6/10——流式、工具折叠、内联权限、slash 补全、多标签都有，缺的是：

1. **中断**：协议无 `run.cancel` command（`RunFinishedEvent` 有 "cancelled" reason 却无触发路径），且 busy 时 TUI 输入被禁用（`tui/app.py:1415`）→ 整个系统无法打断运行中的任务。这是最高优先级 UX 缺失，做成"取消 API 流但保留已产生 tool_result"。
2. **运行中切权限模式**：Shift+Tab 循环 + `/auto` 被 `not self._busy` 挡住，跑偏时无法现场放行。
3. **diff 预览**：写文件审批只显示参数摘要，无编辑内容 diff——用户无法真正审查改动。
4. **任务事件**：TaskManager/TodoWrite 没有任何 `task.*` 事件，TUI 无法呈现 todo 面板。
5. **契约漂移**：`SessionSetEngineCommand`/`SessionEngineChangedEvent` 定义了却没进 union（`commands.py:691`、`events.py:666`），`engine_info` 返回裸 dict（`app.py:762`），WIRE_PROTOCOL.md 未再生成——typed 契约边界已名存实亡，跑一次 `gen_protocol_doc.py --check` 进 pre-commit。
6. `send_message` 同步阻塞到 run 结束才返回（`app.py:381`→`manager.py:459`），与事件订阅式协议自相矛盾，改为立即返回 run_id。
7. TUI 重连清空所有会话标签（`tui/app.py:2625`），replay 机制存在却不用。
8. Checkpoint/Rewind 升级：Claude Code 每个 prompt 前自动快照、只跟踪 Edit/Write 的文件改动、可恢复代码+对话。已有 snapshot/checkpoint 基础，把"从断点继续"从"发一条中文提示消息"做成真正的状态 resume。

## 五、P3：代码卫生

- 上帝文件：`tui/app.py` 3135、`config.py` 1249、`runner.py` 1243、`editor.py` 1226、`core/app.py` 1152；runner.py 五引擎分支同参数复制 70 行（`runner.py:1126-1197`）——收敛引擎后自然消解。
- core 不反向依赖 cli/tui（方向正确），但单包分发使 core 强制携带 textual——拆 `iwan-core` / `iwan-tui` 两个发行包。
- 教程式大段 docstring 占部分文件近半，稀释真实逻辑；`_now()` 5 处各写一份。
- 测试结构性盲区：引擎测试全部绕开工具调用路径（见 P0#1 为何至今未暴露）。给每个引擎加一条"带真实 tool_call 的最小冒烟用例"。

## 优先级总览

1. 修 P0 #1-#8（一个 bugfix 提交即可覆盖，全部有明确 file:line）
2. 加 `run.cancel` + esc 中断 + 审批 diff 预览（P2 中最影响日常可用性的一组）
3. 沙箱"周内清单"五项 + Job Object（P2）
4. 引擎收敛为单循环 + plan 权限模式化 + microcompact（P1，最大工程量，方向性决策）
5. Restricted Token 硬边界、hooks 体系、AppContainer（P2 长期）
