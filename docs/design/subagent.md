# iwanclaude 子 Agent（subagent）子系统设计复盘文档

> 写作背景：2026-09-21 对 subagent 子系统做了一次 P0–P2 分级评审并全部落地修复，
> 随后补了本地性能基准（`scripts/bench_subagent.py`）。
> 本文档记录**每个组件为什么存在、为什么选这条路线、好处和代价各是什么**，
> 供以后复盘时回答"当初为什么这么写"而不是重新读代码倒推。
> 配套回归测试：`tests/unit/test_subagent_review_fixes.py`（16 条钉死修复行为）
> + 既有 `test_spawn_agent_tool.py` / `test_agent_concurrency.py` / `test_background_task_registry.py`（42 条）。

---

## 0. 全景：组件与数据流

```
CoreApp（daemon 持有唯一 BackgroundTaskRegistry）
   └─ AgentRunner 工厂按 run 注入 registry ──► ToolRegistry 挂 5 个工具
        spawn_agent      spawn_agents      agent_result   batch_result   cancel_agent
             │                │                 │             │              │
             ▼                ▼                 └──────┬──────┴──────────────┘
      _prepare_child ──► _ChildHandle（run_id/loop/bus/context/run_path/timeout）   查询/取消走 registry
             │
   ┌─────────┴──────────┐
   │ 前台：await loop    │ 后台：create_task → register(run_id, task, ctx, run_dir)
   │  阻塞直到完成       │        │
   │                    │        ▼
   │   asyncio.timeout  │  _run_background_wrapped（可选 gate 排队 → asyncio.timeout → loop.run）
   │   EventWriter      │        │  CancelledError/TimeoutError/Exception 三分支收口
   └────────────────────┘        ▼
                     child EventBus ──_bridge──► parent EventBus ──► TUI / IPC 广播 / EventWriter
                     每子 Agent 目录 runs/<run_id>/events.jsonl
```

依赖方向：`tool.py`（编排）→ `registry.py`（账本）→ 什么都不依赖。
registry 不认识 AgentLoop、不认识事件——它只存 `(Task, ExecutionContext)` 和元数据，
这是它能被前台/后台/批量三条路径共用、也能被独立单测的前提。

---

## 1. 注册表所有权：为什么在 CoreApp 而不是 AgentRunner

**这是本轮最大的一个结构性修复（P0-1/P0-2）。** 旧设计中 registry 跟着 `AgentRunner`
（每次 prompt run 新建一个），意味着：

- 用户在 run B 里问 run A 起的后台子 Agent 结果 → run_id 查无此人（run A 的 registry 已死）；
- run 结束后没人再持有 Task → 后台 LLM 循环变成**没有任何入口可以取消的孤儿**；
- `cancel_all()` 全项目零调用点 → daemon 退出时"Task was destroyed but it is pending"。

**新设计**：`CoreApp.__init__` 建唯一实例，经 runner 工厂注入每个 `AgentRunner`；
优雅关闭路径调 `registry.shutdown()`（cancel 全部 + gather 收尸）。

| | 好处 | 代价 |
|---|---|---|
| daemon 级单例 | run_id 在 daemon 生命周期内全局有效；取消入口始终存在；退出有序 | 多会话共享一本账（当前单用户本地场景成立）；将来要按 session 隔离时 registry 需加 owner 维度 |
| shutdown 收尸 | EventWriter 句柄正常关闭、无悬挂 Task 告警 | 退出多等一轮 gather——子 Agent 卡死会拖慢 daemon 关闭（可接受：任务本身有超时） |

`AgentRunner` 构造参数 `task_registry=None` 时仍会自建实例——这是给单测和
"独立跑一个 runner"的开发场景留的缝，生产路径（app.py 工厂）永远注入共享实例。

---

## 2. spawn 的三条路：前台阻塞 / 后台 run_id / 程序化入口

`invoke()` 面向模型（参数来自 tool call），`spawn_background()` 面向代码（返回
`(run_id, 错误)` 二元组）。

**为什么要有 `spawn_background`（P0-3）**：旧版批量工具从 `ToolResult.content` 里
`split("run_id=")` 提取 id——展示文案是给人/模型看的，**把它当 API 用**，文案一改批量
就静默丢任务。程序化入口返回结构化结果，展示文案随便改。

| 路径 | 适用 | 好处 | 代价 |
|---|---|---|---|
| 前台阻塞 | 单任务、结果马上要用 | 语义简单，父上下文直接拿结果 | 父循环停摆；超时后子 Task **并未被取消**（`asyncio.timeout` 只掐 `await` 点，loop.run 内部的 sleep 结束仍会跑完）——已知债务 |
| 后台 run_id | 并行、长任务 | 立即返回、可轮询、可取消 | 结果要第二次工具调用取；模型要学会"发起-轮询"节奏 |
| 批量 wait=true | 一批独立任务 | 一次调用聚合结果 | 占闸期间父 run 整体挂起 |
| 批量 wait=false | 后台批 | 立即拿 batch_id | 排队不可见（只能靠 batch_result 猜进度） |

`_prepare_child` 把三条路共用的材料（深度检查 → profile 加载 → context/bus/bridge/
child_registry/loop 组装 → Started 事件 → mkdir run 目录 → 超时计算）收敛成一次调用，
`_ChildHandle` dataclass 是它们共同的中间表示——**新增第四条路只需接 handle**，
这是本轮重构里最值钱的一个形状。

---

## 3. 并发闸（Semaphore）：三个决定和它们的代价

1. **闸持有整个子任务生命周期，不是只持有 spawn 调用**（wait=true 路径）。
   `max_concurrency` 的语义是"同时在飞的子 Agent 数"，如果只框住 spawn 那一瞬间，
   N 个循环照样同时跑，限流形同虚设——这是 description 对模型的承诺。
   代价：**队头阻塞**——一个 9 分钟的长任务占住闸位，后面的短任务排队；没有优先级、
   没有老化提权。本地单机场景接受（正确性>公平性）。

2. **后台批同样设闸**（旧版只给 wait=true 设）。旧 description 宣称防 429，
   实际 wait=false 时 N 个全量起飞——恰恰在批量场景最疼。现在两条路一个闸语义。
   代价：排队中任务的 started 事件延迟发出，TUI 上"批起了但没动"可能让用户以为卡死
   （缓解：agent_result 的 "still running" 现在带 elapsed，能看出在排队还是真在跑）。

3. **排队时间不计入任务超时**（gate 包在 `asyncio.timeout` 外面）。
   超时的语义是"这个子 Agent 干活干了多久"，如果把排队也算进去，
   `max_concurrency=1` + 队列稍长就会把第 2、3 个任务**误杀成超时**。
   代价：daemon 最坏延迟 = 排队 + 超时，总时长无上界（批量侧的 `wait_timeout_sec` 是兜底）。

---

## 4. 取消语义：asyncio 取消契约是这个系统的地基

- `registry.cancel(rid)`：对活 Task `task.cancel(msg)` + ctx.status="cancelled"。
  旧版 ctx 赋值包在 `try/except Exception: pass` 里——ExecutionContext 是普通可写
  dataclass，这个 try 只会把真 bug 吞成静默，已删（P2）。
- `register` 撞 run_id：**先 cancel 活旧任务再覆盖**（P2 防御）。旧版静默换血，
  旧 Task 失联成没人能取消的后台循环。
- `_run_background_wrapped` 三分支收口：`TimeoutError`（发布 failed Finished）/
  `CancelledError`（置 cancelled、发布事件、**上抛**——吞掉它就违反取消契约）/
  `Exception`（转 failed，**不上抛**——后台任务炸不能带走注册表）。finally 里 mark_finished。
- 前台 `invoke` 的 `except Exception` 分支（P1 新增）：子循环裸异常转成
  is_error 结果 + Finished 事件，与后台包装器对称。旧版异常直接炸穿父循环。
- 批量 `_run_one` 的**取消来源分辨**（P1 核心修复）：

  ```python
  except asyncio.CancelledError:
      if t.cancelled() and (cur is None or cur.cancelling() == 0):
          pass   # 子任务自己被 cancel_agent 取消：正常结局，继续交付 rid
      else:
          raise  # 冲本协程来的取消（超时/关闸/shutdown）：必须原样上抛
  ```

  旧版 `except BaseException: pass` 把两种混吞。`cancelling()`（3.11+）是唯一的
  判别式——CancelledError 本身不携带"取消的是谁"。
- 批量启动失败**拉闸**（`cancelled_any`）：任一任务起不来，未开跑的兄弟直接跳过，
  不再"先起后杀"白烧已启动的；错误聚合进返回文案 `First failures: ...`。

| 好处 | 代价 |
|---|---|
| cancel_agent 与批量等待可以共存（用户取消单个成员不炸整批） | 判别逻辑依赖 `cancelling()` 语义，Python < 3.11 不可移植（项目锁 3.12，无碍） |
| daemon 关闭时真正等得到所有子任务退出 | 极端情况：子 Agent 内部 `shield` 了取消则 shutdown 的 gather 会等满它的超时 |

---

## 5. 事件桥与可观测性：child bus + _bridge + run 目录

每个子 Agent 一个**私有 EventBus**，`_bridge` 订阅它并无条件转发到 parent bus。

- **为什么不让子 loop 直接用 parent bus**：子 Agent 的中间事件（工具调用、token 流）
  混进父会话流会把 TUI 和上下文搅成粥；私有 bus + 显式桥让"哪些事件要冒泡"
  成为可设计的点（将来做事件过滤/降级只需要动 `_bridge` 一处）。
- 桥成本实测 **~0.2µs/事件**（§10 [3]），完全不值得为省它而共享 bus。
- `EventWriter` 给每个子 Agent 落 `runs/<run_id>/events.jsonl`——崩溃后可事后取证。
- 代价：事件多一跳内存拷贝（引用传递，忽略不计）；子 bus 上没有 parent 侧的
  会话级订阅者（如 IPC broadcaster），**所有 parent 侧消费者只能看到桥过来的事件**，
  将来若要在 parent 侧按"来源 run"分流，得给事件加 parent_run_id 标注——已有字段但
  并非所有事件都填，接这个需求前先补齐。

---

## 6. Profile、深度与权限收窄

- `subagent_type` → `AgentProfileLoader`（项目 > 用户 > 内建三级搜索）。
  `allowed_tools` 是**只收窄白名单**：profile 只能减工具不能加——子 Agent 权限上限
  永远不超过父 run，与 CLAUDE.md 安全模型（默认拒绝、deny 盖 allow）同构。
- **未知 profile 现在响亮报错**（本轮测试顺手抓出的 P1 新发现）：loader 对查无此名
  返回 None 而非 raise，旧代码的 except 分支是死路——显式指定的角色被静默降级成默认
  角色，模型以为子 Agent 带着 planner 提示词在跑，实际什么都没有。角色承诺落空
  属于 fail-closed 违例，必须报错。
- 深度上限 2（父=0，子=1，孙=2 拒绝）：挡失控递归（每层一个 LLM 循环，成本乘法增长）；
  代价是合法的"编排者分派专家"三层结构用不了，真实需求出现时把上限做成配置而非拍数字。

---

## 7. 生命周期清理：prune 是唯一防泄漏闸口

`prune()` 的完整职责（旧版只管 `_tasks`/`_task_meta` 两个字典，"防内存泄漏"机制
在自己的另外两个字典上原地挖洞）：

1. 扫过期 done 任务 → 删 `_tasks`/`_task_meta` + **rmtree 其 run_dir**；
2. 批次收缩：`_batches[batch_id]` 剔除死成员，全空则连 `_batch_meta` 一起删
   （否则任务被清后 batch_status 冒一排 "unknown" 僵尸行）。

- **run_dir 删除只对后台任务**：目录是注册表自己创建的，随任务同生命周期；
  前台 run 目录属于会话调试产物，归会话清理管（现状：没人管 → §11 债务，
  §10 [5] 的"磁盘残留目录=100"就是这份债的实测数字）。
- TTL 优先级 cancelled_at > finished_at > created_at；`ttl_after_done_sec` 默认 1h。
- 代价：prune 是 O(n) 全表扫描、由调用方驱动——**现在没有任何自动定时调用点**，
  账本只随工具调用顺带清。单用户本地量级（几百条）无所谓，常驻 daemon + 高频
  后台任务要补一个 app 层周期任务。

---

## 8. 成本模型（谁在花钱）

| 动作 | 框架开销（实测） | LLM 开销 |
|---|---|---|
| spawn 一个子 Agent（后台） | ~4ms + 一个 run 目录 | ≥1 次 chat |
| 一次子 Agent 完整 run | + EventWriter 写盘 | steps × chat（干净上下文从头喂 prompt） |
| agent_result 轮询 | µs 级 | 父 run 多一次 tool call 往返（**token 成本在父上下文**） |
| prune（含目录删除） | ~2ms/条（Windows 删除目录贵） | 0 |

子 Agent 的**真金白银大头永远是"干净上下文"**：它看不到父对话，prompt 必须自带全部
背景，所以父 Agent 写 prompt 会倾向复制大量上下文，且子 Agent 常把父已经查过的东西
再查一遍。省 token 的正道是拆小任务、多后台并行，而不是加深嵌套。

---

## 9. 2026-09-21 评审修复记录（问题 → 根因 → 修复）

| # | 级别 | 症状 | 根因 | 修复 | 遗留风险 |
|---|---|---|---|---|---|
| 1 | P0 | 后台 run_id 跨 prompt run 即失效；孤儿循环无法取消 | registry 随 AgentRunner 生灭 | CoreApp 持有唯一实例并注入 | 将来多租户需加 owner 维度 |
| 2 | P0 | daemon 退出 "Task was destroyed but it is pending" | `cancel_all()` 零调用点 | `registry.shutdown()` + app 退出路径接线 | 卡死子任务会拖慢关闭 |
| 3 | P0 | 批量工具从展示文案 parse run_id | 文案当 API 用 | `spawn_background()` 结构化返回 | — |
| 4 | P1 | `_batches`/`_batch_meta` 永增长；批次摘要冒 unknown 僵尸行 | prune 只清两个字典 | 批次收缩 + 整批删除 | 轮询 batch_result 拿 None 时调用方需处理 |
| 5 | P1 | wait=false 批量不设闸，429 承诺失效 | 只给 wait=true 设闸 | 两条路同闸 | 排队不可见（靠 elapsed 缓解） |
| 6 | P1 | 排队时间冒充超时误杀任务 | timeout 包住了闸 | gate 外、timeout 内分层 | 批总时长无上界 |
| 7 | P1 | `_run_one` 吞掉一切取消 | `except BaseException: pass` | `cancelling()` 分辨来源，外部取消上抛 | — |
| 8 | P1 | 启动失败"先起后杀"白烧兄弟 | 失败不拉闸 | `cancelled_any` 跳过 + 错误聚合 | — |
| 9 | P1 | 未知 profile 静默降级成默认角色 | loader 返回 None，except 分支是死路 | None → 显式报错 | — |
| 10 | P2 | `runs_dir` 默认 cwd 埋产物 | 缺参不报错 | 构造期 ValueError | — |
| 11 | P2 | cancel 中 ctx 赋值吞异常 | 无谓 try/except-pass | 删除 | — |
| 12 | P2 | register 撞 id 静默换血失联 | 无覆盖防御 | 先 cancel 活旧任务 | 概率极低，防御性 |
| 13 | P2 | "still running" 无信息量 | 只回四个词 | 带 elapsed + description | 前缀契约：消费方只可依赖 `startswith` |
| 14 | P2 | prune 后台任务磁盘目录无人清 | 只清内存账 | rmtree run_dir | 前台目录无人管（§11） |

---

## 10. 性能实测（scripts/bench_subagent.py，2026-09-21，Windows 原生）

> 运行方式：`uv run python scripts/bench_subagent.py`
> 口径声明：LLM 全部用 mock provider。量的是**框架自身成本**；真实部署墙钟大头是
> API 往返。另 Windows 事件循环 sleep 粒度 ~15.6ms，低 cap 场景的"理论下限"本身不准
> （60ms sleep 实际 ~78ms），所以低 cap 效率百分比要打折看，横向对比才有意义。

```
[1] 注册表规模        n=1000: register 4.2µs/条  batch_status(100) 0.11ms  prune-all 0.5ms
                     n=5000: register 5.7µs/条  batch_status(100) 0.13ms  prune-all 3.1ms
[2] 并发扩展(12×60ms) cap=1: 938ms(理论720)  cap=3: 329ms(240)  cap=6: 158ms(120)  cap=12: 94ms(60)
[3] 事件桥           直发 0.16µs/事件  经桥 0.37µs/事件  净增 0.2µs/事件
[4] spawn 开销        后台 spawn+跑完 4.1ms/次  前台完整 run 3.8ms/次(含写盘)
[5] 稳态翻搅          500 任务 1.9s；prune 500 条 969ms(~2ms/条，几乎全是 Windows 目录删除)
                     prune 后注册表 0 条——无泄漏；磁盘残留 100 个 = 前台 run 目录(债务 #14 的另一半)
```

**解读**：
- 框架不是瓶颈：4ms/spawn 对上 ≥1 次真实 API 往返（百 ms~秒级），占比 <5%；
  注册表 µs 级、桥接 0.2µs 级、batch_status 百 µs 级，千条规模内全部免检。
- [2] 呈现近线性扩展（cap 1→12 墙钟 938→94ms ≈ 10×）——闸语义兑现；
  高 cap 端偏离理论值的 ~34ms 就是 12 路并发同时付的 spawn 框架成本，与 [4] 互相印证。
- 随规模唯一值得盯的曲线是 prune：目录删除 ~2ms/条在 Windows 上是删除成本的
  1000 倍于内存操作。若将来后台任务量上千，批量删目录要挪到后台任务里跑，别卡事件循环。

---

## 11. 已知债务清单（下次动 subagent 先看这里）

1. **前台 run 目录无人清理**：不进注册表、prune 不管，实测一次 bench 留 100 个目录。
   方向：会话级 runs 目录随 session 清理策略走，或让前台路径也登记一个"仅记账不取消"的轻量条目。
2. **前台超时不取消子任务**：`asyncio.timeout` 到点后 `loop.run` 的 Task 继续跑完，
   白烧 token。方向：前台也走 create_task + 超时 cancel 的包装。
3. **prune 无自动触发**：只在工具调用顺带跑。常驻 daemon 要加 app 层周期任务
   （事件循环里删目录会卡循环，量大时 `asyncio.to_thread`）。
4. **队头阻塞**：闸按 FIFO，长任务占位；无优先级/老化机制。真实抱怨出现前不动。
5. **wait=false 批量排队不可见**：batch_result 只能看到 running，看不出"在排队"还是"在跑"。
   方向：注册表给任务加 gating/acquired 状态位。
6. **事件冒泡无过滤全量转发**：子 Agent 的每个工具事件都进 parent bus，
   12 路并发时 TUI 可能被刷屏。方向：`_bridge` 按事件类型白名单冒泡。
7. **多用户/多租户**：全局单例 registry 无 owner 维度，session 隔离时要加过滤。
8. **深度上限 2 是硬编码**：需求确认时做成配置项。
9. **`cancel_agent` 与 LLM 调用中点的取消粒度**：chat 请求已发出的那一次往返 token 花掉
   不退——取消只保证之后不再继续，这是所有 LLM 编排系统的共性，写在这里防止当成 bug 再查一遍。
