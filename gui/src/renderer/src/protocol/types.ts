// ⚠ AUTO-GENERATED - 由 scripts/gen_ts_types.py 从 pydantic 协议模型生成，勿手改。
// 重新生成: uv run python scripts/gen_ts_types.py   校验: --check


// ==================== 命令参数 ====================
export interface PingParams {
  client: string
}

export interface AgentRunParams {
  goal: string
}

export interface EventSubscribeParams {
  topics: Array<string>
  scope?: string
  replay_from_run?: string | null
}

export interface SessionCreateParams {
  mode?: "one_shot" | "chat"
  title?: string
  cwd?: string
  trust_decision?: string
}

export interface SessionSendMessageParams {
  session_id: string
  content: string
  skill_name?: string
  skip_auto_skill?: boolean
}

export interface SessionGetHistoryParams {
  session_id: string
}

export interface SessionCloseParams {
  session_id: string
}

export interface PermissionRespondParams {
  tool_use_id: string
  decision: string
}

export interface TrustRespondParams {
  session_id: string
  cwd?: string
  decision: string
  persistent?: boolean
}

export interface TrustListParams {

}

export interface TrustRevokeParams {
  cwd: string
}

export interface FileChangesListParams {
  session_id: string
  run_id?: string
}

export interface FileRestoreParams {
  session_id: string
  run_id?: string
  paths?: Array<string>
  force?: boolean
}

export interface SessionSetAutoModeParams {
  session_id: string
  mode: string
}

export interface SessionSetPermissionModeParams {
  session_id: string
  mode: string
}

export interface SessionSetEffortLevelParams {
  session_id: string
  level: string
}

export interface SessionSetModelParams {
  session_id: string
  preset: string
}

export interface SessionSetEngineParams {
  session_id: string
  engine: string
}

export interface RunCancelParams {
  run_id: string
}

export interface RunSteerParams {
  run_id: string
  message: string
}

export interface SessionListParams {

}

export interface SessionRenameParams {
  session_id: string
  title: string
}

export interface SessionCompactParams {
  session_id: string
  focus?: string
}

export interface SessionCheckpointListParams {
  session_id: string
}

export interface SessionCheckpointRestoreParams {
  session_id: string
  checkpoint_id: string
}

// ==================== 命令结果与数据结构 ====================
export interface FileChangeInfo {
  path: string
  tool?: string
  ts?: string
  was_new?: boolean
  captured?: boolean
  conflict?: boolean
}

export interface FileRestoreItem {
  path: string
  status: string
  detail?: string
}

export interface SessionInfo {
  id: string
  title: string
  status: string
  mode: string
  updated_at: string
}

export interface CheckpointInfo {
  checkpoint_id: string
  step: number
  timestamp: string
  summary: string
  node?: string | null
  run_id?: string
}

export interface PongResult {
  server_version: string
  uptime_ms: number
  received_at: string
}

export interface AgentRunResult {
  run_id: string
}

export interface EventSubscribeResult {
  subscription_id: string
  replayed_count?: number
}

export interface SessionCreateResult {
  session_id: string
  status: "active" | "running" | "waiting_for_input" | "interrupted" | "closed"
  auto_mode?: string
  permission_mode?: string
  effort_level?: string
  model_preset?: string
  trust?: string
}

export interface SessionSendMessageResult {
  run_id?: string
  skill_match?: Record<string, unknown> | null
}

export interface SessionGetHistoryResult {
  messages: Array<Record<string, unknown>>
}

export interface SessionCloseResult {
  status: "active" | "running" | "waiting_for_input" | "interrupted" | "closed"
}

export interface PermissionRespondResult {
  ok?: boolean
}

export interface TrustRespondResult {
  ok?: boolean
  trust?: string
}

export interface TrustListResult {
  entries?: Record<string, string>
}

export interface TrustRevokeResult {
  ok?: boolean
}

export interface FileChangesListResult {
  run_id?: string
  changes?: Array<FileChangeInfo>
}

export interface FileRestoreResult {
  ok?: boolean
  run_id?: string
  results?: Array<FileRestoreItem>
}

export interface SessionSetAutoModeResult {
  mode: string
}

export interface SessionSetPermissionModeResult {
  mode: string
  previous_mode: string
}

export interface SessionSetEffortLevelResult {
  level: string
}

export interface SessionSetModelResult {
  preset: string
}

export interface SessionSetEngineResult {
  engine: string
}

export interface RunCancelResult {
  accepted: boolean
}

export interface RunSteerResult {
  accepted: boolean
  queued?: number
}

export interface SessionListResult {
  sessions: Array<SessionInfo>
}

export interface SessionRenameResult {
  session_id: string
  title: string
}

export interface SessionCompactResult {
  summary_tokens: number
  saved_tokens: number
}

export interface SessionCheckpointListResult {
  checkpoints: Array<CheckpointInfo>
  thread_id: string
}

export interface SessionCheckpointRestoreResult {
  success: boolean
  checkpoint_id: string
  step: number
  message: string
}

// ==================== 命令名联合 ====================
export type CommandMethod =
  | "agent.run"
  | "core.ping"
  | "event.subscribe"
  | "files.changes"
  | "files.restore"
  | "permission.respond"
  | "run.cancel"
  | "run.steer"
  | "session.checkpoint.list"
  | "session.checkpoint.restore"
  | "session.close"
  | "session.compact"
  | "session.create"
  | "session.get_history"
  | "session.list"
  | "session.rename"
  | "session.send_message"
  | "session.set_auto_mode"
  | "session.set_effort_level"
  | "session.set_engine"
  | "session.set_model"
  | "session.set_permission_mode"
  | "trust.list"
  | "trust.respond"
  | "trust.revoke"

// ==================== 事件判别联合 ====================
export type BusEvent =
  | {
    type: 'core.started';
    listen_addr: string;
    version: string;
  }
  | {
    type: 'run.started';
    run_id: string;
    goal: string;
    ts: string;
  }
  | {
    type: 'run.finished';
    run_id: string;
    status: string;
    reason?: string | null;
    steps: number;
    ts: string;
  }
  | {
    type: 'step.started';
    run_id: string;
    step: number;
    ts: string;
  }
  | {
    type: 'step.finished';
    run_id: string;
    step: number;
    ts: string;
  }
  | {
    type: 'tool.call_started';
    run_id: string;
    tool_use_id: string;
    tool_name: string;
    params: Record<string, unknown>;
    ts: string;
  }
  | {
    type: 'tool.call_finished';
    run_id: string;
    tool_use_id: string;
    tool_name: string;
    elapsed_ms: number;
    output?: string;
    ts: string;
  }
  | {
    type: 'tool.call_failed';
    run_id: string;
    tool_use_id: string;
    tool_name: string;
    error_class: string;
    error_message: string;
    elapsed_ms: number;
    attempt?: number;
    ts: string;
  }
  | {
    type: 'llm.token';
    run_id: string;
    token: string;
    ts: string;
  }
  | {
    type: 'llm.usage';
    run_id: string;
    input_tokens: number;
    output_tokens: number;
    cache_read_input_tokens: number;
    cache_creation_input_tokens: number;
    context_pct?: number;
    ts: string;
  }
  | {
    type: 'llm.model_selected';
    run_id: string;
    model: string;
    strategy: string;
    ts: string;
  }
  | {
    type: 'log.line';
    run_id: string;
    level: string;
    source: string;
    message: string;
    ts: string;
  }
  | {
    type: 'session.created';
    session_id: string;
    mode: string;
    ts: string;
  }
  | {
    type: 'session.message_received';
    session_id: string;
    content: string;
    ts: string;
  }
  | {
    type: 'session.waiting_for_input';
    session_id: string;
    last_run_id: string;
    ts: string;
  }
  | {
    type: 'session.resumed';
    session_id: string;
    ts: string;
  }
  | {
    type: 'session.closed';
    session_id: string;
    ts: string;
  }
  | {
    type: 'session.auto_mode_changed';
    session_id: string;
    mode: string;
    ts: string;
  }
  | {
    type: 'session.permission_mode_changed';
    session_id: string;
    mode: string;
    previous_mode: string;
    ts: string;
  }
  | {
    type: 'session.effort_level_changed';
    session_id: string;
    level: string;
    ts: string;
  }
  | {
    type: 'session.model_changed';
    session_id: string;
    preset: string;
    model: string;
    ts: string;
  }
  | {
    type: 'session.engine_changed';
    session_id: string;
    engine: string;
    ts: string;
  }
  | {
    type: 'session.renamed';
    session_id: string;
    title: string;
    ts: string;
  }
  | {
    type: 'context.compacted';
    session_id: string;
    run_id: string;
    original_tokens: number;
    summary_tokens: number;
    ts: string;
  }
  | {
    type: 'permission.requested';
    run_id: string;
    tool_use_id: string;
    tool_name: string;
    params: Record<string, unknown>;
    param_preview: string;
    session_id: string;
    ts: string;
  }
  | {
    type: 'permission.granted';
    run_id: string;
    tool_use_id: string;
    decision: string;
    ts: string;
  }
  | {
    type: 'permission.denied';
    run_id: string;
    tool_use_id: string;
    decision: string;
    ts: string;
  }
  | {
    type: 'trust.requested';
    session_id: string;
    cwd: string;
    has_instruction_files?: boolean;
    ts: string;
  }
  | {
    type: 'trust.changed';
    session_id: string;
    cwd: string;
    decision: string;
    persistent?: boolean;
    ts: string;
  }
  | {
    type: 'subagent.started';
    run_id: string;
    parent_run_id: string;
    description: string;
    ts: string;
  }
  | {
    type: 'subagent.finished';
    run_id: string;
    parent_run_id: string;
    status: string;
    ts: string;
  }
  | {
    type: 'skill.invoked';
    skill_name: string;
    arguments: string;
    run_id: string;
    ts: string;
    auto_triggered?: boolean;
    match_score?: number;
  }
  | {
    type: 'hook.evaluated';
    run_id: string;
    session_id: string;
    tool_name: string;
    hook_event: string;
    command: string;
    decision: string;
    reason?: string;
    elapsed_ms?: number;
    ts: string;
  }
