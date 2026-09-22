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
    | FileRestoreCommand,
    Discriminator("type"),
]
