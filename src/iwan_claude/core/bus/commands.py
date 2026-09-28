"""
命令定义模块 - 定义客户端向服务器发送的命令类型

【学习要点】
1. 命令类型：定义客户端向服务器发送的命令
2. 响应类型：定义服务器返回的响应
3. 判别联合：使用 Pydantic 的 Discriminator 实现多态类型
4. 命令分类：心跳检测、Agent 运行、事件订阅、会话管理、权限响应、上下文压缩、检查点管理

【命令分类】
- 心跳检测：PingCommand
- Agent 运行：AgentRunCommand
- 事件订阅：EventSubscribeCommand
- 会话管理：SessionCreateCommand, SessionSendMessageCommand, SessionGetHistoryCommand, SessionCloseCommand
- 权限响应：PermissionRespondCommand
- PR 协作：PrContextCommand, PrListCommand, PrCreateCommand, PrReviewCommand
- 模式设置：SessionSetAutoModeCommand（legacy 三态）, SessionSetPermissionModeCommand（五态）
- 上下文压缩：SessionCompactCommand
- 检查点管理：SessionCheckpointListCommand, SessionCheckpointRestoreCommand

【判别联合】
使用 Pydantic 的 Discriminator("type") 实现多态类型，
根据 type 字段自动推断命令类型。

【设计目的】
提供统一的命令定义，
便于客户端向服务器发送命令和服务器处理命令。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator

from iwan_claude.core.session.model import SessionMode, SessionStatus


class PingCommand(BaseModel):
    """
    心跳检测命令 - 客户端向服务器发送心跳检测

    【字段说明】
    - type: Literal["core.ping"] - 命令类型
    - client: str - 客户端标识

    【设计目的】
    检测服务器是否在线，获取服务器版本和运行时间。

    【响应】
    PongResult - 包含服务器版本、运行时间和接收时间
    """
    type: Literal["core.ping"] = "core.ping"
    client: str


class PongResult(BaseModel):
    """
    心跳检测响应 - 服务器返回的心跳响应

    【字段说明】
    - server_version: str - 服务器版本
    - uptime_ms: int - 服务器运行时间（毫秒）
    - received_at: str - 请求接收时间（ISO 8601）

    【设计目的】
    返回服务器状态信息，用于客户端健康检查。
    """
    server_version: str
    uptime_ms: int
    received_at: str


class AgentRunCommand(BaseModel):
    """
    Agent 运行命令 - 客户端请求运行 Agent

    【字段说明】
    - type: Literal["agent.run"] - 命令类型
    - goal: str - 运行目标

    【设计目的】
    请求服务器运行 Agent，执行指定目标。

    【响应】
    AgentRunResult - 包含运行 ID
    """
    type: Literal["agent.run"] = "agent.run"
    goal: str


class AgentRunResult(BaseModel):
    """
    Agent 运行响应 - 服务器返回的运行响应

    【字段说明】
    - run_id: str - 运行 ID

    【设计目的】
    返回运行 ID，用于后续查询运行状态和事件。
    """
    run_id: str


class EventSubscribeCommand(BaseModel):
    """
    事件订阅命令 - 客户端订阅服务器事件

    【字段说明】
    - type: Literal["event.subscribe"] - 命令类型
    - topics: list[str] - 订阅主题列表（fnmatch 模式，如 ["step.*", "tool.*"]）
    - scope: str - 订阅范围（"global" | "run:<run_id>"，默认 "global"）
    - replay_from_run: str | None - 回放起始运行 ID（设置则先从 events.jsonl 回放历史再接实时流）

    【设计目的】
    订阅服务器事件，实现实时事件推送和历史事件回放。

    【响应】
    EventSubscribeResult - 包含订阅 ID 和回放事件数
    """
    type: Literal["event.subscribe"] = "event.subscribe"
    topics: list[str]
    scope: str = "global"
    replay_from_run: str | None = None


class EventSubscribeResult(BaseModel):
    """
    事件订阅响应 - 服务器返回的订阅响应

    【字段说明】
    - subscription_id: str - 订阅 ID
    - replayed_count: int - 回放事件数（默认 0）

    【设计目的】
    返回订阅 ID 和回放事件数，用于客户端管理订阅。
    """
    subscription_id: str
    replayed_count: int = 0


class SessionCreateCommand(BaseModel):
    """
    会话创建命令 - 客户端请求创建会话

    【字段说明】
    - type: Literal["session.create"] - 命令类型
    - mode: SessionMode - 会话模式（默认 "chat"）
    - title: str - 会话标题（默认空字符串）
    - cwd: str - 会话绑定的工作目录（沙箱根），实现多项目隔离
    - trust_decision: str - 客户端对信任询问的先行答复（"allow"/"deny"/"ask"，
      ""=未表态走服务端流程）。CLI 信任对话框答复后重连创建会话时携带

    【cwd 的作用】
    每个会话可绑定独立的项目目录（类似 VS Code 的 workspace），
    Agent 的文件操作被限制在此目录内。
    - 在 D:/project-a 启动 TUI → 会话 A 的 cwd = D:/project-a
    - 在 E:/project-b 启动 TUI → 会话 B 的 cwd = E:/project-b
    - cwd 为空时使用 Core 启动时的 CWD 作为兜底

    【设计目的】
    创建新的会话，设置会话模式、标题和工作目录。

    【响应】
    SessionCreateResult - 包含会话 ID 和状态
    """
    type: Literal["session.create"] = "session.create"
    mode: SessionMode = "chat"
    title: str = ""
    cwd: str = ""
    trust_decision: str = ""


class SessionCreateResult(BaseModel):
    """
    会话创建响应 - 服务器返回的会话创建响应

    【字段说明】
    - session_id: str - 会话 ID
    - status: SessionStatus - 会话状态
    - auto_mode: str - 当前自动模式（"off" / "read_only" / "on"，legacy 三态）
    - permission_mode: str - 当前权限模式（五态），新客户端应以此为准
    - effort_level: str - 当前努力等级（"minimal" / "low" / "medium" / "high" / "max"）
    - model_preset: str - 当前模型预设（"fast" / "balanced" / "powerful"）
    - trust: str - 该会话 cwd 生效的信任档（"allow"/"deny"/"ask"）

    【设计目的】
    返回会话 ID、状态和当前配置，用于客户端管理会话。
    """
    session_id: str
    status: SessionStatus
    auto_mode: str = "off"
    permission_mode: str = "default"
    effort_level: str = "medium"
    model_preset: str = "balanced"
    trust: str = "ask"


class SessionSendMessageCommand(BaseModel):
    """
    发送消息命令 - 客户端向会话发送消息

    【字段说明】
    - type: Literal["session.send_message"] - 命令类型
    - session_id: str - 会话 ID
    - content: str - 消息内容
    - skill_name: str - 手动指定技能名称（TUI 确认后回传，空=不指定）
    - skip_auto_skill: bool - 跳过自动技能匹配（用户拒绝后回传 True）

    【设计目的】
    向指定会话发送消息，触发 Agent 运行。

    【skill_name 与 skip_auto_skill 的配合】
    - skill_name 非空 → 使用指定技能（用户已确认）
    - skip_auto_skill=True → 跳过自动匹配（用户拒绝），正常处理
    - 两者都为默认值 → 触发自动匹配预检查，若命中则返回 skill_match 待确认
    - content 以 "/" 开头 → 手动触发，与 skill_name 无关

    【响应】
    SessionSendMessageResult - 包含运行 ID 或技能匹配信息
    """
    type: Literal["session.send_message"] = "session.send_message"
    session_id: str
    content: str
    # 技能确认流程：用户确认后回传 skill_name，拒绝后回传 skip_auto_skill=True
    skill_name: str = ""
    skip_auto_skill: bool = False


class SessionSendMessageResult(BaseModel):
    """
    发送消息响应 - 服务器返回的发送消息响应

    【字段说明】
    - run_id: str - 运行 ID（空字符串表示未启动 run，如技能待确认）
    - skill_match: dict | None - 自动匹配到的技能信息（name/score/description），
      非空时 TUI 应弹出确认控件，用户确认后重新发送

    【两种响应场景】
    1. 正常执行：run_id 非空，skill_match 为 None
    2. 技能待确认：run_id 为空，skill_match 包含匹配信息
    """
    run_id: str = ""
    skill_match: dict[str, Any] | None = None


class SessionGetHistoryCommand(BaseModel):
    """
    获取历史命令 - 客户端获取会话历史消息

    【字段说明】
    - type: Literal["session.get_history"] - 命令类型
    - session_id: str - 会话 ID

    【设计目的】
    获取指定会话的历史消息，用于客户端显示聊天记录。

    【响应】
    SessionGetHistoryResult - 包含消息列表
    """
    type: Literal["session.get_history"] = "session.get_history"
    session_id: str


class SessionGetHistoryResult(BaseModel):
    """
    获取历史响应 - 服务器返回的历史消息响应

    【字段说明】
    - messages: list[dict[str, Any]] - 消息列表

    【设计目的】
    返回会话历史消息，用于客户端显示聊天记录。
    """
    messages: list[dict[str, Any]]


class SessionCloseCommand(BaseModel):
    """
    关闭会话命令 - 客户端请求关闭会话

    【字段说明】
    - type: Literal["session.close"] - 命令类型
    - session_id: str - 会话 ID

    【设计目的】
    关闭指定会话，释放相关资源。

    【响应】
    SessionCloseResult - 包含会话状态
    """
    type: Literal["session.close"] = "session.close"
    session_id: str


class SessionCloseResult(BaseModel):
    """
    关闭会话响应 - 服务器返回的关闭会话响应

    【字段说明】
    - status: SessionStatus - 会话状态

    【设计目的】
    返回会话状态，用于客户端确认会话已关闭。
    """
    status: SessionStatus


class PermissionRespondCommand(BaseModel):
    """
    权限响应命令 - 客户端响应权限请求

    【字段说明】
    - type: Literal["permission.respond"] - 命令类型
    - tool_use_id: str - 工具调用 ID
    - decision: str - 决策类型（"allow_once" | "always_allow" | "deny_once" | "always_deny"）

    【设计目的】
    响应服务器的权限请求，决定是否允许工具调用。

    【决策类型】
    - allow_once: 允许一次
    - always_allow: 始终允许（更新缓存）
    - deny_once: 拒绝一次
    - always_deny: 始终拒绝（更新缓存）

    【响应】
    PermissionRespondResult - 包含是否成功
    """
    type: Literal["permission.respond"] = "permission.respond"
    tool_use_id: str
    decision: str


class PermissionRespondResult(BaseModel):
    """
    权限响应结果 - 服务器返回的权限响应结果

    【字段说明】
    - ok: bool - 是否成功（默认 True）

    【设计目的】
    返回权限响应是否成功处理。
    """
    ok: bool = True


class TrustRespondCommand(BaseModel):
    """
    信任响应命令 - 客户端答复信任询问（或主动变更某目录的信任档）

    【字段说明】
    - type: Literal["trust.respond"] - 命令类型
    - session_id: str - 触发询问的会话 ID（立即对该会话生效）
    - cwd: str - 被决定的目录；空字符串 = 用该会话的 cwd
    - decision: str - "allow" | "deny" | "ask"（ask=撤销持久决定回到未决态）
    - persistent: bool - 是否写入 trust.toml（False=仅本次会话的临时决定）

    【设计目的】
    与 permission.respond 的关键差异：信任询问不阻塞执行，
    本命令随时可发、去抖不发也无妨——它改的是"以后的规则"，
    不是"这一次的放行"。
    """
    type: Literal["trust.respond"] = "trust.respond"
    session_id: str
    cwd: str = ""
    decision: str
    persistent: bool = True


class TrustRespondResult(BaseModel):
    """
    信任响应结果 - 服务器返回的生效信任档

    【字段说明】
    - ok: bool - 是否成功处理（decision 非法时为 False）
    - trust: str - 该会话现在的生效信任档（"allow"/"deny"/"ask"）
    """
    ok: bool = True
    trust: str = "ask"


class TrustListCommand(BaseModel):
    """
    信任列表命令 - 客户端查询全部持久信任条目

    【字段说明】
    - type: Literal["trust.list"] - 命令类型

    【响应】
    TrustListResult - 归一化目录键 → 决定 的映射
    """
    type: Literal["trust.list"] = "trust.list"


class TrustListResult(BaseModel):
    """
    信任列表结果

    【字段说明】
    - entries: dict[str, str] - 归一化目录键 → "allow"/"deny"
    """
    entries: dict[str, str] = {}


class TrustRevokeCommand(BaseModel):
    """
    信任撤销命令 - 删除某目录的持久信任决定（回到未决态）

    【字段说明】
    - type: Literal["trust.revoke"] - 命令类型
    - cwd: str - 目标目录（与 trust.toml 键做同样的归一化后再比对）

    【响应】
    TrustRevokeResult - ok=是否确实删掉了条目
    """
    type: Literal["trust.revoke"] = "trust.revoke"
    cwd: str


class TrustRevokeResult(BaseModel):
    """
    信任撤销结果

    【字段说明】
    - ok: bool - 是否删掉了既有条目（目录本就无记录时 False）
    """
    ok: bool = True


class FileChangesListCommand(BaseModel):
    """
    文件变更清单命令 - 查询某次 run 被影子快照记录的文件变更（S9 Part C）

    【字段说明】
    - type: Literal["files.changes"] - 命令类型
    - session_id: str - 会话 ID
    - run_id: str - 目标 run；空字符串 = 该会话最近一次有变更账本的 run

    【响应】
    FileChangesListResult - 每个文件一行：工具/时间/是否新建/是否拍全/外部改动冲突
    """
    type: Literal["files.changes"] = "files.changes"
    session_id: str
    run_id: str = ""


class FileChangeInfo(BaseModel):
    """
    单文件变更条目 - files.changes 结果里的行模型

    【字段说明】
    - path: str - 工具当时使用的路径原样
    - tool: str - 产生变更的工具名
    - ts: str - 变更前捕获时间（ISO 8601）
    - was_new: bool - 写前不存在（还原 = 删除）
    - captured: bool - 快照是否拍全（False=超限/读不了，无法还原）
    - conflict: bool - 盘上当前内容 != 我们写完时的样子（被外部改过）
    """
    path: str
    tool: str = ""
    ts: str = ""
    was_new: bool = False
    captured: bool = True
    conflict: bool = False


class FileChangesListResult(BaseModel):
    """
    文件变更清单结果

    【字段说明】
    - run_id: str - 实际列出的是哪次 run 的账本（请求空 run_id 时由服务端选定）
    - changes: list[FileChangeInfo] - 按文件归并后的最近状态
    """
    run_id: str = ""
    changes: list[FileChangeInfo] = []


class FileRestoreCommand(BaseModel):
    """
    文件还原命令 - 把选中文件回滚到该 run 变更前状态（S9 Part C）

    【字段说明】
    - type: Literal["files.restore"] - 命令类型
    - session_id: str - 会话 ID
    - run_id: str - 目标 run；空 = 最近一次有账本的 run（与 files.changes 同规则）
    - paths: list[str] - 要还原的文件（["*"] = 全部）
    - force: bool - 冲突文件（还原后又被外部改过）是否强还原；默认跳过

    【设计目的】
    还原前先给"当前内容"记一次反向快照（tool="restore"），还原本身可撤销——
    与对话回溯是两个独立操作，用户明确选择要回滚哪个（避免 undo 语义模糊）。
    """
    type: Literal["files.restore"] = "files.restore"
    session_id: str
    run_id: str = ""
    paths: list[str] = ["*"]
    force: bool = False


class FileRestoreItem(BaseModel):
    """
    单文件还原结果行

    【字段说明】
    - path: str - 文件路径
    - status: str - "restored" | "skipped"（冲突未 force） | "failed"（没快照/写盘错误）
    - detail: str - 跳过/失败原因
    """
    path: str
    status: str
    detail: str = ""


class FileRestoreResult(BaseModel):
    """
    文件还原结果

    【字段说明】
    - ok: bool - 是否全部成功（有 skipped/failed 即 False）
    - run_id: str - 实际作用的 run
    - results: list[FileRestoreItem] - 逐文件结果
    """
    ok: bool = True
    run_id: str = ""
    results: list[FileRestoreItem] = []


class SessionSetAutoModeCommand(BaseModel):
    """
    设置自动模式命令 - 客户端请求设置会话的自动模式

    【字段说明】
    - type: Literal["session.set_auto_mode"] - 命令类型
    - session_id: str - 会话 ID
    - mode: str - 自动模式（"off" / "read_only" / "on"）

    【设计目的】
    允许客户端动态切换自动模式，控制是否自动批准低风险工具调用。

    【响应】
    SessionSetAutoModeResult - 包含设置后的模式
    """
    type: Literal["session.set_auto_mode"] = "session.set_auto_mode"
    session_id: str
    mode: str


class SessionSetAutoModeResult(BaseModel):
    """
    设置自动模式响应 - 服务器返回的设置结果

    【字段说明】
    - mode: str - 当前自动模式

    【设计目的】
    返回设置后的自动模式，用于客户端同步状态。
    """
    mode: str


class SessionSetPermissionModeCommand(BaseModel):
    """
    设置权限模式命令 - 客户端请求切换某会话的五态权限模式

    【字段说明】
    - type: Literal["session.set_permission_mode"] - 命令类型
    - session_id: str - 会话 ID
    - mode: str - 权限模式（default/acceptEdits/plan/auto/bypassPermissions）

    【设计目的】
    对齐 Claude Code 的 Shift+Tab 模式循环：模式是 per-session 的策略档，
    取代旧的三态 auto_mode（后者保留一版兼容映射）。切换只改写审批链的
    Tier3~6，deny 地板/强制 ASK/hook 永远不受模式影响。

    【响应】
    SessionSetPermissionModeResult - 包含切换前后两个模式，便于客户端回显
    """
    type: Literal["session.set_permission_mode"] = "session.set_permission_mode"
    session_id: str
    mode: str


class SessionSetPermissionModeResult(BaseModel):
    """
    设置权限模式响应 - 服务器返回的切换结果

    【字段说明】
    - mode: str - 切换后的生效模式
    - previous_mode: str - 切换前的模式（供客户端展示"从哪来"）

    【设计目的】
    返回前后两个值而非只返回新值：多端连接同一会话时，发起端需要知道
    previous 才能正确回滚 UI 状态（另一台机器已经抢先切过模式的情况）。
    """
    mode: str
    previous_mode: str


class SessionSetEffortLevelCommand(BaseModel):
    """
    设置努力等级命令 - 客户端请求设置会话的努力等级

    【字段说明】
    - type: Literal["session.set_effort_level"] - 命令类型
    - session_id: str - 会话 ID
    - level: str - 努力等级（"minimal" / "low" / "medium" / "high" / "max"）

    【设计目的】
    允许客户端动态切换努力等级，控制 Agent 执行深度。
    等级越高，Agent 会读更多文件、做更多验证、搜索更深。

    【响应】
    SessionSetEffortLevelResult - 包含设置后的等级
    """
    type: Literal["session.set_effort_level"] = "session.set_effort_level"
    session_id: str
    level: str


class SessionSetEffortLevelResult(BaseModel):
    """
    设置努力等级响应 - 服务器返回的设置结果

    【字段说明】
    - level: str - 当前努力等级

    【设计目的】
    返回设置后的努力等级，用于客户端同步状态。
    """
    level: str


class SessionSetModelCommand(BaseModel):
    """
    设置模型预设命令 - 客户端请求设置会话的模型预设

    【字段说明】
    - type: Literal["session.set_model"] - 命令类型
    - session_id: str - 会话 ID
    - preset: str - 模型预设（"fast" / "balanced" / "powerful"）

    【设计目的】
    允许客户端动态切换模型预设，控制 Agent 使用哪个 LLM 模型。
    切换后，下一次 Agent run 会使用新预设对应的模型。

    【响应】
    SessionSetModelResult - 包含设置后的预设
    """
    type: Literal["session.set_model"] = "session.set_model"
    session_id: str
    preset: str


class SessionSetModelResult(BaseModel):
    """
    设置模型预设响应 - 服务器返回的设置结果

    【字段说明】
    - preset: str - 当前模型预设

    【设计目的】
    返回设置后的模型预设，用于客户端同步状态。
    """
    preset: str


class SessionSetEngineCommand(BaseModel):
    """
    设置 Agent 引擎命令 - 客户端请求动态切换执行引擎

    【字段说明】
    - type: Literal["session.set_engine"] - 命令类型
    - session_id: str - 会话 ID
    - engine: str - 引擎名称（legacy / langgraph / plan_execute / debate / pipeline / auto）

    【设计目的】
    允许客户端在运行时动态切换 Agent 引擎，无需重启 core。
    不同引擎适合不同任务：legacy（简单）、langgraph（ReAct）、plan_execute（规划执行）、
    debate（辩论）、pipeline（多角色流水线）。

    【响应】
    SessionSetEngineResult - 包含设置后的引擎名称
    """
    type: Literal["session.set_engine"] = "session.set_engine"
    session_id: str
    engine: str


class SessionSetEngineResult(BaseModel):
    """
    设置引擎响应 - 服务器返回的设置结果

    【字段说明】
    - engine: str - 当前引擎名称
    """
    engine: str


class RunCancelCommand(BaseModel):
    """
    运行取消命令 - 客户端请求中断一个正在执行的 run

    【字段说明】
    - type: Literal["run.cancel"] - 命令类型
    - run_id: str - 要取消的运行 ID

    【设计目的】
    Claude Code 里对应"Esc 中断当前 turn"。daemon 端按 run_id 找到运行任务并
    取消：工具执行/LLM 流式输出被打断，已产生的消息仍会写入会话历史，
    RunFinishedEvent 以 status=failed / reason=cancelled 收尾。
    取消是幂等的：run 已结束或不存在时 accepted=False，不报错。

    【响应】
    RunCancelResult - accepted 表示是否命中了一个活跃 run
    """
    type: Literal["run.cancel"] = "run.cancel"
    run_id: str


class RunCancelResult(BaseModel):
    """
    取消响应 - accepted 表示取消请求是否命中活跃 run

    【字段说明】
    - accepted: bool - 是否找到并取消了该 run（False=已结束/不存在）
    """
    accepted: bool


class RunSteerCommand(BaseModel):
    """
    运行中修正命令 - 用户在 run 执行期间注入一条修正意见

    【字段说明】
    - type: Literal["run.steer"] - 命令类型
    - run_id: str - 目标运行 ID
    - message: str - 修正内容（如"方向错了，改用 asyncio"）

    【设计目的】
    Claude Code 里对应"运行中直接打字改向"（in-flight steering）。
    daemon 端把消息排进该 run 的 steering 队列，Agent 在下一次调用 LLM 前
    把它作为 user 消息注入对话，模型即刻看到并调整方向。
    不中断当前正在执行的工具调用——队列消费发生在回合边界。

    【响应】
    RunSteerResult - accepted 表示是否命中活跃 run；未命中时客户端应改发 send_message
    """
    type: Literal["run.steer"] = "run.steer"
    run_id: str
    message: str


class RunSteerResult(BaseModel):
    """
    修正入队响应 - accepted 表示消息是否成功排进活跃 run 队列

    【字段说明】
    - accepted: bool - 是否命中活跃 run
    - queued: int - 该 run 当前积压的修正消息数（含本条）
    """
    accepted: bool
    queued: int = 0


class SessionListCommand(BaseModel):
    """
    会话列表命令 - 客户端请求列出所有会话

    【字段说明】
    - type: Literal["session.list"] - 命令类型

    【设计目的】
    允许客户端获取所有会话的列表，
    用于 TUI 标签页显示和会话切换。

    【响应】
    SessionListResult - 包含会话列表
    """
    type: Literal["session.list"] = "session.list"


class SessionInfo(BaseModel):
    """
    会话信息 - 会话列表中的单个会话信息

    【字段说明】
    - id: str - 会话 ID
    - title: str - 会话标题
    - status: str - 会话状态（active / waiting_for_input / closed）
    - mode: str - 会话模式（one_shot / chat）
    - updated_at: str - 最后更新时间

    【设计目的】
    轻量级的会话信息，用于列表展示，
    不包含完整消息历史以减少传输量。
    """
    id: str
    title: str
    status: str
    mode: str
    updated_at: str


class SessionListResult(BaseModel):
    """
    会话列表响应 - 服务器返回的会话列表

    【字段说明】
    - sessions: list[SessionInfo] - 会话列表，按更新时间倒序排列

    【设计目的】
    返回所有会话的摘要信息，
    用于 TUI 标签页显示和会话切换。
    """
    sessions: list[SessionInfo]


class SessionRenameCommand(BaseModel):
    """
    重命名会话命令 - 客户端请求重命名会话标题

    【字段说明】
    - type: Literal["session.rename"] - 命令类型
    - session_id: str - 会话 ID
    - title: str - 新的会话标题

    【设计目的】
    允许用户自定义会话标题，
    便于在多标签中识别不同会话。

    【响应】
    SessionRenameResult - 包含重命名后的会话信息
    """
    type: Literal["session.rename"] = "session.rename"
    session_id: str
    title: str


class SessionRenameResult(BaseModel):
    """
    重命名会话响应 - 服务器返回的重命名结果

    【字段说明】
    - session_id: str - 会话 ID
    - title: str - 新的会话标题

    【设计目的】
    返回重命名后的会话信息，
    用于客户端同步标签页标题。
    """
    session_id: str
    title: str


class SessionCompactCommand(BaseModel):
    """
    上下文压缩命令 - 客户端请求压缩会话上下文

    【字段说明】
    - type: Literal["session.compact"] - 命令类型
    - session_id: str - 会话 ID
    - focus: str - 压缩焦点（默认空字符串）

    【设计目的】
    压缩会话上下文，减少令牌消耗，延长对话长度。

    【响应】
    SessionCompactResult - 包含压缩后的令牌数和节省的令牌数
    """
    type: Literal["session.compact"] = "session.compact"
    session_id: str
    focus: str = ""


class SessionCompactResult(BaseModel):
    """
    上下文压缩响应 - 服务器返回的上下文压缩响应

    【字段说明】
    - summary_tokens: int - 压缩后的令牌数
    - saved_tokens: int - 节省的令牌数

    【设计目的】
    返回压缩结果，用于客户端显示压缩效果。
    """
    summary_tokens: int
    saved_tokens: int


class SessionCheckpointListCommand(BaseModel):
    """
    检查点列表命令 - 客户端获取会话检查点列表

    【字段说明】
    - type: Literal["session.checkpoint.list"] - 命令类型
    - session_id: str - 会话 ID

    【设计目的】
    获取会话的检查点列表，用于客户端显示和选择检查点。

    【响应】
    SessionCheckpointListResult - 包含检查点列表和线程 ID
    """
    type: Literal["session.checkpoint.list"] = "session.checkpoint.list"
    session_id: str


class CheckpointInfo(BaseModel):
    """
    检查点信息 - 包含单个检查点的详细信息

    【字段说明】
    - checkpoint_id: str - 检查点 ID
    - step: int - 步骤编号
    - timestamp: str - 时间戳
    - summary: str - 检查点摘要
    - node: str | None - 节点名称（可选）
    - run_id: str - 该检查点所属运行 ID（checkpoint↔run 交叉索引；旧数据为空串）

    【设计目的】
    封装单个检查点的详细信息，用于客户端显示。
    """
    checkpoint_id: str
    step: int
    timestamp: str
    summary: str
    node: str | None = None
    run_id: str = ""


class SessionCheckpointListResult(BaseModel):
    """
    检查点列表响应 - 服务器返回的检查点列表响应

    【字段说明】
    - checkpoints: list[CheckpointInfo] - 检查点列表
    - thread_id: str - 线程 ID

    【设计目的】
    返回检查点列表和线程 ID，用于客户端显示和管理检查点。
    """
    checkpoints: list[CheckpointInfo]
    thread_id: str


class SessionCheckpointRestoreCommand(BaseModel):
    """
    检查点恢复命令 - 客户端请求恢复到指定检查点

    【字段说明】
    - type: Literal["session.checkpoint.restore"] - 命令类型
    - session_id: str - 会话 ID
    - checkpoint_id: str - 检查点 ID

    【设计目的】
    将会话恢复到指定检查点，实现回溯功能。

    【响应】
    SessionCheckpointRestoreResult - 包含恢复结果
    """
    type: Literal["session.checkpoint.restore"] = "session.checkpoint.restore"
    session_id: str
    checkpoint_id: str


class SessionCheckpointRestoreResult(BaseModel):
    """
    检查点恢复响应 - 服务器返回的检查点恢复响应

    【字段说明】
    - success: bool - 是否成功
    - checkpoint_id: str - 检查点 ID
    - step: int - 步骤编号
    - message: str - 消息

    【设计目的】
    返回恢复结果，用于客户端确认恢复是否成功。
    """
    success: bool
    checkpoint_id: str
    step: int
    message: str


# ==================== M2 新增面：MCP 实况 / Pull Request / 定时任务 ====================


class McpStatusCommand(BaseModel):
    """
    MCP 状态命令 - 客户端查询各 MCP 服务器的运行时连接状态

    【字段说明】
    - type: Literal["mcp.status"] - 命令类型

    【设计目的】
    配置文件只能展示"打算启动什么"，本命令给出运行时真相：连上没有、
    挂过什么错、每个服务器实际注册了哪些工具——配置是意图，状态是事实。

    【响应】
    McpStatusResult - 以配置表为准逐行报告（启动失败的服务器也会出现）
    """
    type: Literal["mcp.status"] = "mcp.status"


class McpServerStatus(BaseModel):
    """
    单个 MCP 服务器的状态行

    【字段说明】
    - name: str - 服务器名（以 [[mcp.servers]] 配置为准）
    - transport: str - "stdio" | "tcp"
    - connected: bool - 客户端存活且未断线（无实例或已 offline 时为 False）
    - tools: list[str] - 该服务器注册的工具名（mcp__server__tool 形态）
    - last_error: str - 最近一次启动/握手失败的摘要（空串=无失败记录）
    """
    name: str
    transport: str = ""
    connected: bool = False
    tools: list[str] = []
    last_error: str = ""


class McpStatusResult(BaseModel):
    """
    MCP 状态响应

    【字段说明】
    - servers: list[McpServerStatus] - 每台服务器一行，顺序与配置一致
    """
    servers: list[McpServerStatus] = []


class PrContextCommand(BaseModel):
    """
    PR 上下文命令 - 从某工作目录解析 git 远端与分支状态

    【字段说明】
    - type: Literal["pr.context"] - 命令类型
    - cwd: str - 目标仓库目录

    【设计目的】
    PR 面板的地基：owner/repo 由 origin URL 解析（ssh/https 两形态），
    ahead/behind 告诉你本地分支离推送还差几步——全部本地 git 操作，无网络。

    【响应】
    PrContextResult - ok=False 时 error 给出人类可读原因（非 git 仓库/无远端等）
    """
    type: Literal["pr.context"] = "pr.context"
    cwd: str


class PrContextResult(BaseModel):
    """
    PR 上下文响应

    【字段说明】
    - ok: bool - 是否成功解析出 owner/repo
    - owner / repo: str - GitHub 归属（解析失败为空串）
    - branch: str - 当前分支
    - default_branch: str - 远端 HEAD 指向的默认分支（探测不到为 "main" 猜测值）
    - ahead: int - 本地领先 origin/<branch> 的提交数（远端分支不存在时 = 本地全部提交数）
    - behind: int - 落后数
    - has_remote: bool - 是否配置了 origin 且能解析出 GitHub 坐标
    - error: str - 失败原因
    """
    ok: bool = False
    owner: str = ""
    repo: str = ""
    branch: str = ""
    default_branch: str = ""
    ahead: int = 0
    behind: int = 0
    has_remote: bool = False
    error: str = ""


class PrListCommand(BaseModel):
    """
    PR 列表命令 - 拉取某仓库的 Pull Request 清单

    【字段说明】
    - type: Literal["pr.list"] - 命令类型
    - cwd: str - 目标仓库目录（owner/repo 由它的 origin 远端决定）
    - state: str - "open" | "closed" | "all"
    - page: int - 页码（每页 30 条，GitHub 约定）

    【设计目的】
    daemon 端用 httpx 直连 api.github.com（本机无 gh CLI 也走得通）。
    token 缺省时公开仓库只读仍可用（受限流），私有仓库返回明确错误。

    【响应】
    PrListResult - 归一化行 + 失败原文摘要
    """
    type: Literal["pr.list"] = "pr.list"
    cwd: str
    state: str = "open"
    page: int = 1


class PrInfo(BaseModel):
    """
    PR 列表行（GitHub API 归一化）

    【字段说明】
    - number: int - PR 编号
    - title / author / state / url - 基本盘
    - updated_at: str - 最后更新时间（ISO 8601）
    - head_ref / base_ref: str - 源分支 / 目标分支
    - draft: bool - 草稿标记
    """
    number: int
    title: str = ""
    author: str = ""
    state: str = ""
    updated_at: str = ""
    url: str = ""
    head_ref: str = ""
    base_ref: str = ""
    draft: bool = False


class PrListResult(BaseModel):
    """
    PR 列表响应

    【字段说明】
    - ok: bool - 请求是否成功（网络/权限/解析任一失败为 False）
    - error: str - 失败摘要（HTTP 状态 + GitHub message 字段）
    - pulls: list[PrInfo] - 归一化列表
    """
    ok: bool = True
    error: str = ""
    pulls: list[PrInfo] = []


class PrCreateCommand(BaseModel):
    """
    PR 创建命令 - 推送当前分支并在 GitHub 开一个 Pull Request

    【字段说明】
    - type: Literal["pr.create"] - 命令类型
    - cwd: str - 目标仓库
    - title: str - PR 标题（必填）
    - body: str - 描述（可空）
    - base: str - 目标分支；空 = 远端默认分支
    - head: str - 源分支；空 = 当前分支
    - draft: bool - 先开草稿（可后改正式）

    【设计目的与安全边界】
    会改变共享状态（push + 建 PR），因此 GUI 必须二次确认后才发本命令；
    daemon 侧把动作与结果记入日志审计。push 走 `git push -u origin HEAD`，
    仅当本地有未推送提交时才执行。需要配置 [github] token 或 IWAN_GITHUB_TOKEN。
    """
    type: Literal["pr.create"] = "pr.create"
    cwd: str
    title: str
    body: str = ""
    base: str = ""
    head: str = ""
    draft: bool = False


class PrCreateResult(BaseModel):
    """
    PR 创建响应

    【字段说明】
    - ok: bool - 是否成功建出 PR
    - number: int - 新 PR 编号
    - url: str - GitHub 页面链接
    - pushed: bool - 本次是否真的推送过提交（False=分支已在远端）
    - error: str - 失败摘要（push 失败/无 token/API 拒绝）
    """
    ok: bool = False
    number: int = 0
    url: str = ""
    pushed: bool = False
    error: str = ""


class PrReviewCommand(BaseModel):
    """
    PR 评审命令 - 拉取指定 PR 的 diff 并起一个 one_shot 评审会话

    【字段说明】
    - type: Literal["pr.review"] - 命令类型
    - cwd: str - 目标仓库（解析 owner/repo 坐标）
    - pr_number: int - 要评审的 PR 编号

    【设计目的与安全边界】
    只读拉 diff（GitHub media type 直出补丁文本）+ 新建 one_shot 会话
    跑评审——diff 是喂给模型的文本、不被执行；评审会话工具链照常过
    审批门。发起 = GUI 显式点击或 config [github] auto_review 显式打开，
    两条路都是授权动作（workflow.run 同口径）。
    """
    type: Literal["pr.review"] = "pr.review"
    cwd: str
    pr_number: int


class PrReviewResult(BaseModel):
    """
    PR 评审响应

    【字段说明】
    - ok: bool - 评审会话是否成功发起（True=diff 已到手、评审已在跑）
    - session_id: str - "评审·PR#N" one_shot 会话 ID
    - error: str - 失败摘要（非 git 仓库/无远端/拉 PR 失败/无 token 权限）
    """
    ok: bool = False
    session_id: str = ""
    error: str = ""


class ScheduleListCommand(BaseModel):
    """
    定时任务列表命令 - 查询全部任务定义（含下次到期时间）

    【字段说明】
    - type: Literal["schedule.list"] - 命令类型

    【响应】
    ScheduleListResult - 任务行列表（读 ~/.iwan/scheduled.json + 内存态）
    """
    type: Literal["schedule.list"] = "schedule.list"


class ScheduleTaskInfo(BaseModel):
    """
    定时任务行

    【字段说明】
    - id: str - 任务 ID（服务端生成）
    - name / cwd / prompt: str - 名称、目标工作目录、到点执行的指令
    - kind: str - 调度档："every_minutes" | "daily" | "weekly"
    - spec: str - 档参数："N"（分钟）| "HH:MM"（每天）| "WEEKDAY@HH:MM"（每周，0=周一）
    - enabled: bool - 开关（False 时调度器跳过）
    - last_run: str - 上次触发时刻（ISO 8601，空=从未）
    - last_result: str - 上次结果摘要
    - next_due: str - 下次到期时刻（服务端算好；空=未排/停用）
    """
    id: str
    name: str = ""
    cwd: str = ""
    prompt: str = ""
    kind: str = "every_minutes"
    spec: str = ""
    enabled: bool = True
    last_run: str = ""
    last_result: str = ""
    next_due: str = ""


class ScheduleListResult(BaseModel):
    """
    定时任务列表响应

    【字段说明】
    - tasks: list[ScheduleTaskInfo] - 全部任务
    """
    tasks: list[ScheduleTaskInfo] = []


class ScheduleCreateCommand(BaseModel):
    """
    定时任务创建命令

    【字段说明】
    - type: Literal["schedule.create"] - 命令类型
    - name / cwd / prompt / kind / spec - 见 ScheduleTaskInfo 字段说明

    【设计目的】
    kind/spec 的合法性在服务端校验（如 "HH:MM" 格式、N>=1），失败返回
    ok=False + error 人类可读文案；成功则写盘（原子替换）并即时排期。

    【响应】
    ScheduleOpResult - 携带新任务 id
    """
    type: Literal["schedule.create"] = "schedule.create"
    name: str
    cwd: str
    prompt: str
    kind: str
    spec: str


class ScheduleUpdateCommand(BaseModel):
    """
    定时任务更新命令 - 字段为 None 表示不改该项

    【字段说明】
    - type: Literal["schedule.update"] - 命令类型
    - id: str - 目标任务
    - name/cwd/prompt/kind/spec: str | None - 改哪个传哪个
    - enabled: bool | None - 开关（None=不动）
    """
    type: Literal["schedule.update"] = "schedule.update"
    id: str
    name: str | None = None
    cwd: str | None = None
    prompt: str | None = None
    kind: str | None = None
    spec: str | None = None
    enabled: bool | None = None


class ScheduleDeleteCommand(BaseModel):
    """
    定时任务删除命令

    【字段说明】
    - type: Literal["schedule.delete"] - 命令类型
    - id: str - 目标任务

    【响应】
    ScheduleOpResult - ok=False 且 error="任务不存在" 表示幂等未命中
    """
    type: Literal["schedule.delete"] = "schedule.delete"
    id: str


class ScheduleRunNowCommand(BaseModel):
    """
    定时任务立即执行命令 - 手动触发一次（不影响排期）

    【字段说明】
    - type: Literal["schedule.run_now"] - 命令类型
    - id: str - 目标任务

    【设计目的】
    给"验证任务写得对不对"留个按钮：与到点触发走同一执行路径
    （one_shot 会话 + 异步运行），GUI 能立刻在会话列表看到新任务。
    """
    type: Literal["schedule.run_now"] = "schedule.run_now"
    id: str


class ScheduleOpResult(BaseModel):
    """
    定时任务写操作统一响应

    【字段说明】
    - ok: bool - 是否成功
    - id: str - 相关任务 ID（create 返回新 id）
    - run_id: str - 仅 run_now 非空：本次触发启动的运行 ID
    - error: str - 失败原因（校验文案/不存在/非法 kind）
    """
    ok: bool = True
    id: str = ""
    run_id: str = ""
    error: str = ""


# ==================== 工作流（DAG 编排）协议 ====================
# 【设计】任务节点的最小定义只有三个字段：名字、指令、依赖。依赖以节点名引用
# （不用 id）——编排发生在十几行的规模内，人名比 12 位 hex 可读得多；图合法性
# （重名/缺依赖/环）全部由服务端 Workflow.validate 权威裁决，客户端不重复实现。


class WorkflowTaskDef(BaseModel):
    """
    工作流节点定义（编辑器与存储共用的最小三字段）

    【字段说明】
    - name: str - 节点名（图内唯一，兼作事件里的 node 键与依赖引用目标）
    - prompt: str - 该节点子 Agent 的完整指令（非空）
    - depends_on: list[str] - 前置节点名列表（空=入口节点）
    """
    name: str
    prompt: str
    depends_on: list[str] = []


class WorkflowInfo(BaseModel):
    """
    工作流定义行

    【字段说明】
    - id: str - 工作流 ID（服务端生成，12 位 hex）
    - name / description: str - 名称与备注
    - tasks: list[WorkflowTaskDef] - 节点定义表
    - layers: list[list[str]] - 服务端现算的分层执行计划（GUI 直接按列渲染）
    - created_at / updated_at: str - ISO 时刻
    - last_run_id / last_status: str - 最近一次运行的反范式快照（列表页 chip 用）
    """
    id: str
    name: str = ""
    description: str = ""
    tasks: list[WorkflowTaskDef] = []
    layers: list[list[str]] = []
    created_at: str = ""
    updated_at: str = ""
    last_run_id: str = ""
    last_status: str = ""


class WorkflowNodeRunInfo(BaseModel):
    """
    单节点运行态

    【字段说明】
    - node: str - 节点名
    - status: str - "pending" | "running" | "ok" | "fail" | "cancelled"
      （pending=run 行预置骨架的初始态；cancelled=取消收口时正在跑的节点）
    - child_run_id: str - 该节点子 Agent 的 run id（深链审计用）
    - detail: str - 失败原因/取消说明（≤200 字）
    - output: str - 成功产出快照（截断存储，≤400 字）
    - started_at / finished_at: str - ISO 时刻
    """
    node: str
    status: str = "pending"
    child_run_id: str = ""
    detail: str = ""
    output: str = ""
    started_at: str = ""
    finished_at: str = ""


class WorkflowRunInfo(BaseModel):
    """
    一次工作流运行

    【字段说明】
    - id: str - run id（服务端生成）
    - workflow_id / workflow_name: str - 归属工作流
    - session_id: str - 该 run 懒建的 one_shot 会话（子 Agent 审批卡的落点；""=还没建）
    - status: str - "running" | "success" | "failed" | "interrupted" | "cancelled"
    - started_at / finished_at / error: str - 收尾信息
    - nodes: list[WorkflowNodeRunInfo] - 各节点运行态
    """
    id: str
    workflow_id: str
    workflow_name: str = ""
    session_id: str = ""
    status: str = "running"
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    nodes: list[WorkflowNodeRunInfo] = []


class WorkflowListCommand(BaseModel):
    """
    工作流列表命令 - 查询全部工作流定义

    【字段说明】
    - type: Literal["workflow.list"] - 命令类型

    【响应】
    WorkflowListResult - 定义行列表（layers 服务端算好）
    """
    type: Literal["workflow.list"] = "workflow.list"


class WorkflowListResult(BaseModel):
    """
    工作流列表响应

    【字段说明】
    - workflows: list[WorkflowInfo] - 全部工作流
    """
    workflows: list[WorkflowInfo] = []


class WorkflowGetCommand(BaseModel):
    """
    工作流详情命令 - 一次拉齐定义 + 最近运行（详情页免往返）

    【字段说明】
    - type: Literal["workflow.get"] - 命令类型
    - id: str - 目标工作流

    【响应】
    WorkflowGetResult - workflow=None 且 ok=False 表示不存在
    """
    type: Literal["workflow.get"] = "workflow.get"
    id: str


class WorkflowGetResult(BaseModel):
    """
    工作流详情响应

    【字段说明】
    - ok: bool - 是否命中
    - error: str - 未命中原因
    - workflow: WorkflowInfo | None - 定义行
    - runs: list[WorkflowRunInfo] - 该流程最近 20 次运行（新→旧）
    """
    ok: bool = True
    error: str = ""
    workflow: WorkflowInfo | None = None
    runs: list[WorkflowRunInfo] = []


class WorkflowSaveCommand(BaseModel):
    """
    工作流保存命令 - id 为空新建、非空整图覆盖更新

    【字段说明】
    - type: Literal["workflow.save"] - 命令类型
    - id: str - 空=新建
    - name / description: str - 名称（空则服务端兜底）与备注
    - tasks: list[WorkflowTaskDef] - 完整节点表（整图替换，不做增量 diff）

    【设计目的】
    图合法性（非空/≤30 节点/重名/缺依赖/环）在此命令的服务端校验，
    非法即 ok=False + 中文文案，绝不持久化半张坏图。
    """
    type: Literal["workflow.save"] = "workflow.save"
    id: str = ""
    name: str = ""
    description: str = ""
    tasks: list[WorkflowTaskDef] = []


class WorkflowDeleteCommand(BaseModel):
    """
    工作流删除命令 - 只删定义，运行历史保留（v1 不级联）

    【字段说明】
    - type: Literal["workflow.delete"] - 命令类型
    - id: str - 目标工作流
    """
    type: Literal["workflow.delete"] = "workflow.delete"
    id: str


class WorkflowRunCommand(BaseModel):
    """
    工作流运行命令 - 即发即返，节点状态全靠 workflow.node 事件

    【字段说明】
    - type: Literal["workflow.run"] - 命令类型
    - id: str - 目标工作流

    【设计目的】
    GUI 点击"运行"是用户显式授权（同 schedule.run_now，RPC 层不设审批门）；
    节点内子 Agent 的工具调用照常过 permission_manager，这里不新开任何洞。
    同一工作流已有进行中的运行时拒绝二次触发。
    """
    type: Literal["workflow.run"] = "workflow.run"
    id: str


class WorkflowCancelCommand(BaseModel):
    """
    工作流运行取消命令 - 中止某工作流当前进行中的运行

    【字段说明】
    - type: Literal["workflow.cancel"] - 命令类型
    - id: str - 目标工作流（与 workflow.run 同口径：handler 解析其活跃 run）

    【设计目的】
    GUI 点击"取消"是用户显式授权（同 workflow.run 的点击语义）；无活跃运行
    时幂等拒绝而非报错。取消是善后不是毁灭：run 落 cancelled 终态、正在跑
    的节点补标 cancelled、未轮到的节点保持 pending——历史诚实可查。
    """
    type: Literal["workflow.cancel"] = "workflow.cancel"
    id: str


class WorkflowRunsCommand(BaseModel):
    """
    工作流运行历史命令

    【字段说明】
    - type: Literal["workflow.runs"] - 命令类型
    - id: str - 空=全部工作流的运行混排
    - limit: int - 返回条数上限（服务端钳位 1..100）
    """
    type: Literal["workflow.runs"] = "workflow.runs"
    id: str = ""
    limit: int = 20


class WorkflowRunsResult(BaseModel):
    """
    工作流运行历史响应

    【字段说明】
    - runs: list[WorkflowRunInfo] - 新→旧
    """
    runs: list[WorkflowRunInfo] = []


class WorkflowOpResult(BaseModel):
    """
    工作流写操作统一响应（save/delete/run 共用）

    【字段说明】
    - ok: bool - 是否成功
    - id: str - 相关工作流 id（save/delete/get 口径）
    - run_id: str - 仅 run 非空：本次启动的运行 id
    - error: str - 失败原因（校验文案/不存在/并发上限/重复运行）
    """
    ok: bool = True
    id: str = ""
    run_id: str = ""
    error: str = ""


class SpeechTranscribeCommand(BaseModel):
    """
    语音转写命令 - GUI 录音发 PCM，daemon 用本地 whisper 转成文字

    【字段说明】
    - type: Literal["speech.transcribe"] - 命令类型
    - audio_b64: str - 单声道 int16 小端 PCM 裸字节的 base64（与 ssh.term_write 同编码纪律）
    - sample_rate: int - 采样率（8000..48000；GUI 侧固定降采样到 16000）

    【设计目的】
    一次 RPC 一问一答（非流式）：v1 定位是"说完一段填进输入框"，几十秒语音
    2.6MB base64 远在 64MB 帧限内；流式实时出字需要双向通道，留给 v2。
    """
    type: Literal["speech.transcribe"] = "speech.transcribe"
    audio_b64: str
    sample_rate: int = 16000


class SpeechTranscribeResult(BaseModel):
    """
    语音转写响应

    【字段说明】
    - ok: bool - 是否转写成功
    - text: str - 识别出的文字（失败为空；成功但没听清也可能是空串）
    - error: str - 失败原因（依赖缺失/模型加载失败/音频非法，中文原文给 GUI 回显）
    """
    ok: bool = True
    text: str = ""
    error: str = ""


class GitFile(BaseModel):
    """
    git 状态表的单行文件

    【字段说明】
    - path: str - 仓库相对路径（porcelain 原文，恒用 / 分隔）
    - index_status / worktree_status: str - porcelain XY 两列字符（M/A/D/R/?/U/空格）
    - staged: bool - X 列有内容 = 已进暂存区
    - untracked: bool - 双 ? = 未跟踪
    - conflicted: bool - 合并冲突未解决（discard/commit 前 GUI 应拦一道）
    """
    path: str
    index_status: str = ""
    worktree_status: str = ""
    staged: bool = False
    untracked: bool = False
    conflicted: bool = False


class GitStatusCommand(BaseModel):
    """
    git 状态查询命令

    【字段说明】
    - type: Literal["git.status"] - 命令类型
    - cwd: str - 仓库目录（允许是子目录，git 自己向上找根）

    【响应】
    GitStatusResult - 分支 + ahead/behind + 文件三态表
    """
    type: Literal["git.status"] = "git.status"
    cwd: str


class GitStatusResult(BaseModel):
    """
    git 状态响应

    【字段说明】
    - ok: bool - False 时 error 为人话原因（非仓库/无 git）
    - branch: str - 当前分支（游离 HEAD 为空串）
    - ahead / behind: int - 相对上游的领先/落后提交数（无上游恒 0）
    - files: list[GitFile] - 工作区/暂存区全量差异行
    """
    ok: bool = True
    error: str = ""
    branch: str = ""
    ahead: int = 0
    behind: int = 0
    files: list[GitFile] = []


class GitBranchInfo(BaseModel):
    """
    分支行

    【字段说明】
    - name: str - 本地分支名
    - current: bool - 是否 HEAD 所指
    - upstream: str - 上游分支短名（无上游空串）
    """
    name: str
    current: bool = False
    upstream: str = ""


class GitBranchesCommand(BaseModel):
    """
    git 分支列表命令

    【字段说明】
    - type: Literal["git.branches"] - 命令类型
    - cwd: str - 仓库目录

    【响应】
    GitBranchesResult - 当前分支 + 本地分支表（当前置顶）
    """
    type: Literal["git.branches"] = "git.branches"
    cwd: str


class GitBranchesResult(BaseModel):
    """
    git 分支列表响应

    【字段说明】
    - ok / error: 同上（非仓库时 ok=False）
    - current: str - 当前分支名（游离 HEAD 空串）
    - branches: list[GitBranchInfo]
    """
    ok: bool = True
    error: str = ""
    current: str = ""
    branches: list[GitBranchInfo] = []


class GitLogCommand(BaseModel):
    """
    git 提交历史命令

    【字段说明】
    - type: Literal["git.log"] - 命令类型
    - cwd: str - 仓库目录
    - limit: int - 最多返回条数（服务端钳到 1..200）
    """
    type: Literal["git.log"] = "git.log"
    cwd: str
    limit: int = 20


class GitLogEntry(BaseModel):
    """
    提交行

    【字段说明】
    - sha / short_sha: str - 本实现只跑 --format=%h，两字段同值（预留全 sha 位）
    - author / date: str - 作者名 / "YYYY-MM-DD HH:MM" 本地时间
    - subject: str - 标题行
    """
    sha: str = ""
    short_sha: str = ""
    author: str = ""
    date: str = ""
    subject: str = ""


class GitLogResult(BaseModel):
    """
    git 提交历史响应

    【字段说明】
    - ok / error: 空仓库时 error="仓库还没有任何提交"
    - entries: list[GitLogEntry] - 新→旧排序
    """
    ok: bool = True
    error: str = ""
    entries: list[GitLogEntry] = []


class GitPathsCommand(BaseModel):
    """
    git 按路径操作命令（stage/unstage/discard 共用形状，type 区分动作）

    【字段说明】
    - type: Literal - "git.stage" 暂存 | "git.unstage" 退暂存 | "git.discard" 丢弃改动
    - cwd: str - 仓库目录
    - paths: list[str] - 仓库相对路径白名单（discard 对未跟踪文件走 clean -fd -- <paths>，
      路径限定是底线：参数再错也不该全仓清空）

    【设计目的】
    type 无默认值：一个类骑三个判别值，任何默认值都会让"忘带 type 的解析"
    静默选错动作。协议规定 params 不含 type，故由 app 层 handler 按注册方法名
    注入——方法名就是权威，模型只做形状收口。

    【响应】
    GitOpResult
    """
    type: Literal["git.stage", "git.unstage", "git.discard"]
    cwd: str
    paths: list[str] = []


class GitCommitCommand(BaseModel):
    """
    git 提交命令

    【字段说明】
    - type: Literal["git.commit"] - 命令类型
    - cwd: str - 仓库目录
    - message: str - 提交信息（暂存区为空时服务端拒绝并回执提示）

    【响应】
    GitOpResult - 成功时 sha 携带新提交的短哈希
    """
    type: Literal["git.commit"] = "git.commit"
    cwd: str
    message: str


class GitCheckoutCommand(BaseModel):
    """
    git 切分支命令

    【字段说明】
    - type: Literal["git.checkout"] - 命令类型
    - cwd: str - 仓库目录
    - name: str - 目标分支（仅本地已有分支；无建分支语义）

    【设计目的】
    工作区脏时 git 会自己拒绝切换，error 原文回显——我们不预检，
    避免和 git 的判定规则赛跑。
    """
    type: Literal["git.checkout"] = "git.checkout"
    cwd: str
    name: str


class GitPullCommand(BaseModel):
    """
    git 拉取命令（--ff-only：需要 merge/rebase 时让 git 报错，不替用户做主）

    【字段说明】
    - type: Literal["git.pull"] - 命令类型
    - cwd: str - 仓库目录
    """
    type: Literal["git.pull"] = "git.pull"
    cwd: str


class GitPushCommand(BaseModel):
    """
    git 推送命令（-u origin HEAD，首次自动建上游）

    【字段说明】
    - type: Literal["git.push"] - 命令类型
    - cwd: str - 仓库目录
    """
    type: Literal["git.push"] = "git.push"
    cwd: str


class GitOpResult(BaseModel):
    """
    git 写操作统一响应

    【字段说明】
    - ok: bool - 失败时 error 为 git stderr 原文或兜底文案（GUI 直接上屏）
    - error / output: str - 失败原因 / git stdout（成功回执，GUI 可折叠）
    - sha: str - 仅 commit 成功时非空：新提交短哈希
    """
    ok: bool = True
    error: str = ""
    output: str = ""
    sha: str = ""


class SshConnInfo(BaseModel):
    """
    SSH 连接行

    【字段说明】
    - id: str - 服务端生成 ID
    - name: str - 展示名（全表唯一）
    - host / user: str - 目标主机与登录用户（user 必填：留空 ssh 会静默用本地用户名）
    - port: int - 端口（1..65535，默认 22）
    - key_file: str - 私钥路径覆盖（空 = 用 ~/.iwan/ssh/id_ed25519）
    """
    id: str
    name: str = ""
    host: str = ""
    user: str = ""
    port: int = 22
    key_file: str = ""


class SshConnListCommand(BaseModel):
    """
    SSH 连接列表命令

    【字段说明】
    - type: Literal["ssh.conn_list"] - 命令类型

    【响应】
    SshConnListResult - 按名称排序的连接表（读 ~/.iwan/ssh/connections.json）
    """
    type: Literal["ssh.conn_list"] = "ssh.conn_list"


class SshConnListResult(BaseModel):
    """
    SSH 连接列表响应

    【字段说明】
    - connections: list[SshConnInfo] - 全部连接
    """
    connections: list[SshConnInfo] = []


class SshConnAddCommand(BaseModel):
    """
    SSH 连接新建命令

    【字段说明】
    - type: Literal["ssh.conn_add"] - 命令类型
    - name / host / user / port / key_file - 见 SshConnInfo

    【设计目的】
    服务端校验：host/user 非空、host 无空白、port 区间、name 查重。
    不做连通性探测——存连接 ≠ 连得上，保存被慢主机卡住是反模式。

    【响应】
    SshConnOpResult - 成功携带新 id
    """
    type: Literal["ssh.conn_add"] = "ssh.conn_add"
    name: str
    host: str
    user: str
    port: int = 22
    key_file: str = ""


class SshConnUpdateCommand(BaseModel):
    """
    SSH 连接更新命令 - 字段为 None 表示不改该项

    【字段说明】
    - type: Literal["ssh.conn_update"] - 命令类型
    - id: str - 目标连接
    - name/host/user: str | None、port: int | None、key_file: str | None
    """
    type: Literal["ssh.conn_update"] = "ssh.conn_update"
    id: str
    name: str | None = None
    host: str | None = None
    user: str | None = None
    port: int | None = None
    key_file: str | None = None


class SshConnDeleteCommand(BaseModel):
    """
    SSH 连接删除命令

    【字段说明】
    - type: Literal["ssh.conn_delete"] - 命令类型
    - id: str - 目标连接（只删登记信息，不碰密钥与 known_hosts）
    """
    type: Literal["ssh.conn_delete"] = "ssh.conn_delete"
    id: str


class SshConnOpResult(BaseModel):
    """
    SSH 连接写操作统一响应

    【字段说明】
    - ok: bool - 失败时 error 为校验文案（重名/空字段/端口越界）
    - id: str - add 返回新 id；update/delete 回显入参 id
    - error: str - 失败原因（delete 未命中 = "连接不存在"）
    """
    ok: bool = True
    id: str = ""
    error: str = ""


class SshKeyStatusCommand(BaseModel):
    """
    SSH 密钥现状查询命令（纯本地文件检查 + ssh-keygen 指纹，无网络）

    【字段说明】
    - type: Literal["ssh.key_status"] - 命令类型

    【响应】
    SshKeyStatusResult
    """
    type: Literal["ssh.key_status"] = "ssh.key_status"


class SshKeyStatusResult(BaseModel):
    """
    SSH 密钥现状响应

    【字段说明】
    - has_key: bool - 私钥+公钥同存才算有
    - pubkey_path: str - 公钥绝对路径（无私钥时空串）
    - fingerprint: str - "SHA256:…"（公钥可读时）
    """
    has_key: bool = False
    pubkey_path: str = ""
    fingerprint: str = ""


class SshKeyGenerateCommand(BaseModel):
    """
    SSH 密钥生成命令 - ssh-keygen -t ed25519 无口令

    【字段说明】
    - type: Literal["ssh.key_generate"] - 命令类型

    【设计目的】
    已存在时拒绝且不覆盖——密钥一旦被静默替换，所有部署过旧公钥的
    远端会同时"莫名其妙连不上"。重置的责任留给用户手动删文件。

    【响应】
    SshKeyOpResult - 成功携带公钥全文与指纹（公钥可贴 authorized_keys）
    """
    type: Literal["ssh.key_generate"] = "ssh.key_generate"


class SshKeyOpResult(BaseModel):
    """
    SSH 密钥操作响应

    【字段说明】
    - ok: bool / error: str - 失败原因（已存在不覆盖 / ssh-keygen 缺失）
    - pubkey: str - 公钥全文一行（"ssh-ed25519 AAAA… comment"）
    - fingerprint: str - "SHA256:…"
    """
    ok: bool = True
    pubkey: str = ""
    fingerprint: str = ""
    error: str = ""


class SshHostTrustCommand(BaseModel):
    """
    SSH 主机信任命令 - keyscan 取回主机密钥并（目视比对后）写入 known_hosts

    【字段说明】
    - type: Literal["ssh.host_trust"] - 命令类型
    - host: str / port: int - 目标

    【设计目的】
    指纹在返回体里，由 GUI 展示给用户【比对确认】后才算建立信任；known_hosts
    用 ~/.iwan/ssh 下我们自己的文件，与用户日常终端的互不污染。
    这是"首连信任"（trust-on-first-use）的显式化：TOFU 不询问=默认信任，
    我们把它改成 GUI 上的一次点击。
    """
    type: Literal["ssh.host_trust"] = "ssh.host_trust"
    host: str
    port: int = 22


class SshTrustResult(BaseModel):
    """
    SSH 主机信任响应

    【字段说明】
    - ok: bool / error: str - keyscan 失败（不可达/端口错）时给原因
    - fingerprints: str - 多行 "SHA256:… [host]:port"，GUI 原样展示供比对
    """
    ok: bool = True
    fingerprints: str = ""
    error: str = ""


class SshTermOpenCommand(BaseModel):
    """
    SSH 终端开会话命令 - 对登记连接启动一条 ssh -tt 交互会话

    【字段说明】
    - type: Literal["ssh.term_open"] - 命令类型
    - conn_id: str - 连接库 id（目的地锁定，同 ssh_exec）
    - cols / rows: int - 初始终端尺寸（经 stty 包装设定；后续 resize
      v1 不传播——Windows 无 SIGWINCH 注入通道，属已知架构限制）

    【响应】
    SshTermOpenResult - 成功回 session_id，输出/关闭走 ssh.output/ssh.closed 事件
    """
    type: Literal["ssh.term_open"] = "ssh.term_open"
    conn_id: str
    cols: int = 80
    rows: int = 24


class SshTermOpenResult(BaseModel):
    """
    SSH 终端开会话响应

    【字段说明】
    - ok: bool / error: str - 连接不存在、ssh 缺失、立即失败时给原因
    - session_id: str - 新会话 id（后续 write/resize/close 的键）
    """
    ok: bool = True
    session_id: str = ""
    error: str = ""


class SshTermWriteCommand(BaseModel):
    """
    SSH 终端输入命令 - 键盘字节写入远端 pty

    【字段说明】
    - type: Literal["ssh.term_write"] - 命令类型
    - session_id: str - 目标会话
    - data_b64: str - 输入字节的 base64（与输出同理：整 NDJSON 行安全）

    【设计目的】
    输入是用户亲手敲的，不设大小闸门；分片责任在 GUI（≤4KB/帧），
    超限的畸形巨帧由 pydantic 之后的传输层自然消化。
    """
    type: Literal["ssh.term_write"] = "ssh.term_write"
    session_id: str
    data_b64: str


class SshTermResizeCommand(BaseModel):
    """
    SSH 终端尺寸调整命令 - v1 记录不发（协议占位），GUI 侧即点即应

    【字段说明】
    - type: Literal["ssh.term_resize"] - 命令类型
    - session_id: str / cols / rows: int - 新尺寸
    """
    type: Literal["ssh.term_resize"] = "ssh.term_resize"
    session_id: str
    cols: int = 80
    rows: int = 24


class SshTermCloseCommand(BaseModel):
    """
    SSH 终端关闭命令 - 用户点页签 ×；daemon 侧幂等收尸

    【字段说明】
    - type: Literal["ssh.term_close"] - 命令类型
    - session_id: str - 目标会话
    """
    type: Literal["ssh.term_close"] = "ssh.term_close"
    session_id: str


class SshTermOpResult(BaseModel):
    """
    SSH 终端操作统一响应（write/resize/close）

    【字段说明】
    - ok: bool / error: str - 会话不存在或已断开时给原因
    """
    ok: bool = True
    error: str = ""


# 根据 type 字段决定命令类型的判别联合
# 使用 Pydantic 的 Discriminator 实现多态类型，根据 type 字段自动推断命令类型
Command = Annotated[
    PingCommand
    | AgentRunCommand
    | EventSubscribeCommand
    | SessionCreateCommand
    | SessionSendMessageCommand
    | SessionGetHistoryCommand
    | SessionCloseCommand
    | PermissionRespondCommand
    | SessionSetAutoModeCommand
    | SessionSetPermissionModeCommand
    | SessionSetEffortLevelCommand
    | SessionSetModelCommand
    | SessionSetEngineCommand
    | RunCancelCommand
    | RunSteerCommand
    | SessionListCommand
    | SessionRenameCommand
    | SessionCompactCommand
    | SessionCheckpointListCommand
    | SessionCheckpointRestoreCommand
    | TrustRespondCommand
    | TrustListCommand
    | TrustRevokeCommand
    | FileChangesListCommand
    | FileRestoreCommand
    | McpStatusCommand
    | PrContextCommand
    | PrListCommand
    | PrCreateCommand
    | PrReviewCommand
    | ScheduleListCommand
    | ScheduleCreateCommand
    | ScheduleUpdateCommand
    | ScheduleDeleteCommand
    | ScheduleRunNowCommand
    | GitStatusCommand
    | GitBranchesCommand
    | GitLogCommand
    | GitPathsCommand
    | GitCommitCommand
    | GitCheckoutCommand
    | GitPullCommand
    | GitPushCommand
    | SshConnListCommand
    | SshConnAddCommand
    | SshConnUpdateCommand
    | SshConnDeleteCommand
    | SshKeyStatusCommand
    | SshKeyGenerateCommand
    | SshHostTrustCommand
    | SshTermOpenCommand
    | SshTermWriteCommand
    | SshTermResizeCommand
    | SshTermCloseCommand
    | WorkflowListCommand
    | WorkflowGetCommand
    | WorkflowSaveCommand
    | WorkflowDeleteCommand
    | WorkflowRunCommand
    | WorkflowCancelCommand
    | WorkflowRunsCommand
    | SpeechTranscribeCommand,
    Discriminator("type"),
]
