# 10 动态模型 · 活动图：从按下 Enter 到 run 结束

**回答的问题**：控制流上到底有几个判定点、每个判定拒绝时会走到哪个出口（这是"过程视角"的一张图，对应 06 的"对象视角"）。

## 图

```mermaid
flowchart TD
    START(["开始：用户提交一条消息"]) --> HAS{"已有活动会话？"}
    HAS -- "否" --> CREATE["session.create<br/>mode=chat, cwd=当前项目"]
    CREATE --> PUSH
    HAS -- "是" --> PUSH["本地先渲染用户气泡<br/>记 pendingClaims"]
    PUSH --> RPC["session.send_message"]

    RPC --> BUSY{"会话锁已被占？"}
    BUSY -- "是" --> REJECT1["返回 SESSION_BUSY<br/>客户端提示'调用太频繁'"]
    BUSY -- "否" --> LOCK["获取 per-session Lock"]
    LOCK --> BIND["set_sandbox_root(cwd)<br/>写 ContextVar"]
    BIND --> SKILL{"命中自动技能？<br/>阈值 2.0 + 命令性门控"}

    SKILL -- "是" --> SM["返回 skill_match<br/>不建 run、不落盘"]
    SM --> CONF{"用户确认用技能？"}
    CONF -- "是" --> RE["重发 send_message<br/>带 skill_name"]
    CONF -- "否" --> SKIP["重发 send_message<br/>带 skip_auto_skill"]
    RE --> RECORD
    SKIP --> RECORD
    SKILL -- "否" --> RECORD["追加用户消息 + 发事件<br/>首条消息起启发式标题"]
    RECORD --> PREP["装配：Engine 选择 + 上下文 + 工具注册表<br/>+ 事件写入器 + 快照写入器"]
    PREP --> STEP["进入 Agent 循环（step += 1）"]

    STEP --> LIMIT{"step ≤ max_steps？"}
    LIMIT -- "否" --> FAIL1["status=failed<br/>reason=exceeded_max_steps"]
    LIMIT -- "是" --> LLM["provider.chat（流式）"]
    LLM --> USAGE["发布 llm.token / llm.usage"]
    USAGE --> TOOLS{"返回里有 tool_use？"}

    TOOLS -- "否" --> DONE["mark_success<br/>result=最后一段文本"]
    TOOLS -- "是" --> PERM["逐个 invoke_tool（并行 gather）"]
    PERM --> DEC{"权限结论"}
    DEC -- "DENY" --> DENY["工具结果为 permission_denied<br/>回喂给模型"]
    DEC -- "ASK" --> WAIT["发布 permission.requested<br/>Future 挂起"]
    WAIT --> ANSWER{"用户答复（或超时）"}
    ANSWER -- "允许" --> RUNTOOL
    ANSWER -- "拒绝/超时" --> DENY
    DEC -- "ALLOW" --> RUNTOOL["shadow.prepare → tool.invoke"]
    RUNTOOL --> TOOLOK{"执行成功？"}
    TOOLOK -- "否，可重试" --> RETRY["按错误类型退避重试"]
    RETRY --> RUNTOOL
    TOOLOK -- "否，不可重试" --> TOOLFAIL["发布 tool.call_failed<br/>错误回喂模型"]
    TOOLFAIL --> STEP
    TOOLOK -- "是" --> COMMIT["shadow.commit + tool.call_finished"]
    DENY --> STEP
    COMMIT --> STEP

    DONE --> SAVE["append_messages 增量<br/>发布 run.finished"]
    FAIL1 --> SAVE
    SAVE --> MODE{"会话模式"}
    MODE -- "one_shot" --> CLOSED["status=closed<br/>发布 session.closed"]
    MODE -- "chat" --> WAITIN["status=waiting_for_input<br/>发布 session.waiting_for_input"]
    CLOSED --> END(["返回 run_id，解锁"])
    WAITIN --> END

    CANCEL(["run.cancel 到达"]) -.-> LIMIT
    CANCEL -.-> CANCELF["mark_failed(cancelled)<br/>发布 run.finished(failed)"]
    CANCELF --> SAVE
```

## 读图要点

1. **两个"软失败"出口不重试**：`exceeded_max_steps` 与 `cancelled` 都是正常收口——前者防死循环，后者是用户意图；都不会让会话卡在 `running`。
2. **拒绝不是抛错，是"回喂"**：权限被拒后工具结果是 `permission_denied` 文本，模型看到后通常自己换方案（`invocation.py:300-315`）——这是 agent 能"遇到拒绝继续想办法"的原因。
3. **技能的确认是"重发"而非"续跑"**：确认后客户端带 `skill_name` 再发一次，daemon 侧第一次调用根本没落盘消息，所以不会出现两条用户气泡（`store.ts:847-877` 用 `echoSuppress` 吞掉 `session.message_received` 回声）。
4. **锁的位置**：`lock.locked()` 预检查在进入 `async with lock` 之前（`manager.py:314-327`），所以"同一会话连点两次"第二次立刻 `SESSION_BUSY`，而不是排队——这是刻意的（排队会让用户以为没反应）。

## 代码锚点

`session/manager.py:255-565`（主流程）、`tools/invocation.py:168-410`（工具分支）、`runner.py:1155-1365`（循环与收尾）、`context.py:227-249`（状态收口）
