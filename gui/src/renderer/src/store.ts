// 渲染层全局状态（Zustand 单 store）+ daemon 事件归约
//
// 【学习要点】1) 事件信封字段名（run_id/session_id/type/...）逐一对齐 events.py，
// 不猜协议；2) llm.token 绝不可逐个 setState——先进模块级缓冲，
// requestAnimationFrame 每帧合并刷一次（设计文档"流式渲染节流"纪律）；
// 3) session.list 不返回 cwd（已核实 SessionInfo 字段），项目分组用
// 客户端 localStorage 记忆顶替——协议缺口 ★ 已在复刻篇 §4 记录。
import { create } from 'zustand'
import { rpc } from './rpc'
import type {
  BusEvent,
  SessionListResult,
  SessionCreateResult,
  SessionSendMessageResult,
  SessionGetHistoryResult,
  RunCancelResult,
  RunSteerResult
} from './protocol/types'

// 历史消息 content 块的松散形态（后端原样透传 Anthropic 消息结构，字段按需用）
interface ContentBlock {
  type?: string
  text?: string
  name?: string
  input?: Record<string, unknown>
  tool_use_id?: string
  content?: unknown
}

export interface Msg {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'notice'
  text: string
  toolName?: string
  toolState?: 'running' | 'ok' | 'fail'
  toolInput?: string
  toolOutput?: string
  streaming?: boolean
  // 审批卡/信任卡：notice 的增强形态，带按钮；answered 有值=已答复（按钮消失留回声）
  card?: 'approval' | 'trust'
  toolUseId?: string
  paramPreview?: string
  cwd?: string
  answered?: string
}

export interface SessionMeta {
  id: string
  title: string
  status: string
  mode?: string
  cwd?: string
  updatedAt?: string
}

interface State {
  status: string
  sessions: SessionMeta[]
  activeSid: string | null
  threads: Record<string, Msg[]>
  selectedCwd: string | null
  runningSids: Set<string>
  contextPct: Record<string, number>
  lastRunId: Record<string, string>
  loadedHistory: Set<string>
  sessionAttrs: Record<string, Partial<SessionAttrs>>
  metaCwd: Record<string, string>
  metaRunIds: Record<string, string[]>
  setStatus(s: string): void
  setActive(sid: string | null): void
  setCwd(cwd: string | null): void
  upsertSession(meta: SessionMeta): void
  setSessions(list: SessionMeta[]): void
  removeSession(sid: string): void
  pushMsg(sid: string, msg: Msg): void
  patchMsg(sid: string, id: string, patch: Partial<Msg>): void
  setThread(sid: string, msgs: Msg[]): void
  markRunning(sid: string, running: boolean): void
  setContextPct(sid: string, pct: number): void
  setLastRun(sid: string, runId: string): void
  markHistoryLoaded(sid: string): void
  setAttrs(sid: string, patch: Partial<SessionAttrs>): void
  setMeta(map: Record<string, { cwd: string; created_at: string; run_ids: string[] }>): void
}

// 会话可调属性（Composer 各选择器的显示值）——来源：session.create 返回值 + *_changed 事件
export interface SessionAttrs {
  permissionMode: string
  modelPreset: string
  effortLevel: string
  engine: string
  autoMode: string
  model: string
}

const LS_CWD_KEY = 'iwan.lastCwd'
const LS_SESSION_CWD = 'iwan.sessionCwd'

// 读 localStorage 里的 JSON 映射（解析失败返回空对象，绝不让存储脏数据炸界面）
function readJsonMap(raw: string | null): Record<string, string> {
  if (!raw) return {}
  try {
    return JSON.parse(raw) as Record<string, string>
  } catch {
    return {}
  }
}

// 记住每个会话属于哪个项目目录（协议缺口的客户端顶替账本）
export const sessionCwdStore: Record<string, string> = readJsonMap(localStorage.getItem(LS_SESSION_CWD))

// 会话的 run_id → session_id 路由表：llm.token 只带 run_id，靠它找回归属
const runOwner = new Map<string, string>()

// 【学习要点】严格归属防串线，但带来新竞态：run.started/首批 token 可能赶在
// send_message 响应（携带 run_id）之前到达，此时路由表还没这条 run。解法不是
// 退回 activeSid 兜底，而是"待认领"：发消息时先记下 (sid, 原文)，run.started 的
// goal（daemon 原样透传的用户消息）与哪条待认领完全相同，就把该 run 判给那个会话。
// 别的客户端/background run 的 goal 不可能撞上正在发送的原文——认领是证据式的。
const pendingClaims = new Map<string, { text: string; ts: number }>()

export const useStore = create<State>((set, get) => ({
  status: 'connecting',
  sessions: [],
  activeSid: null,
  threads: {},
  selectedCwd: localStorage.getItem(LS_CWD_KEY),
  runningSids: new Set(),
  contextPct: {},
  lastRunId: {},
  loadedHistory: new Set(),
  sessionAttrs: {},
  metaCwd: {},
  metaRunIds: {},
  setStatus: (s) => set({ status: s }),
  setActive: (sid) => set({ activeSid: sid }),
  setCwd: (cwd) => {
    if (cwd) localStorage.setItem(LS_CWD_KEY, cwd)
    else localStorage.removeItem(LS_CWD_KEY)
    set({ selectedCwd: cwd })
  },
  upsertSession: (meta) => {
    if (meta.cwd) {
      sessionCwdStore[meta.id] = meta.cwd
      localStorage.setItem(LS_SESSION_CWD, JSON.stringify(sessionCwdStore))
    }
    set((st) => {
      const prev = st.sessions.find((s) => s.id === meta.id)
      const merged = { ...prev, ...meta, cwd: meta.cwd ?? prev?.cwd ?? sessionCwdStore[meta.id] }
      // 【学习要点】原地替换而非"删了追加"：追加会让改名/起标题的会话
      // 在列表里瞬移到底部，用户视线跟丢；位置稳定才配得上"列表"二字
      const idx = st.sessions.findIndex((s) => s.id === meta.id)
      if (idx < 0) return { sessions: [...st.sessions, merged] }
      const copy = [...st.sessions]
      copy[idx] = merged
      return { sessions: copy }
    })
  },
  setSessions: (list) => set({ sessions: list.map((s) => ({ ...s, cwd: sessionCwdStore[s.id] ?? s.cwd })) }),
  removeSession: (sid) => set((st) => ({ sessions: st.sessions.filter((s) => s.id !== sid) })),
  pushMsg: (sid, msg) =>
    set((st) => ({ threads: { ...st.threads, [sid]: [...(st.threads[sid] ?? []), msg] } })),
  patchMsg: (sid, id, patch) =>
    set((st) => ({
      threads: {
        ...st.threads,
        [sid]: (st.threads[sid] ?? []).map((m) => (m.id === id ? { ...m, ...patch } : m))
      }
    })),
  markRunning: (sid, running) =>
    set((st) => {
      const next = new Set(st.runningSids)
      if (running) next.add(sid)
      else next.delete(sid)
      return { runningSids: next }
    }),
  setThread: (sid, msgs) => set((st) => ({ threads: { ...st.threads, [sid]: msgs } })),
  setLastRun: (sid, runId) => set((st) => ({ lastRunId: { ...st.lastRunId, [sid]: runId } })),
  // 【学习要点】Set 是引用类型——mutate 后必须换一个新 Set 再 set，
  // 否则订阅组件看到的引用不变、不会重渲染（zustand 用 Object.is 判等）
  markHistoryLoaded: (sid) =>
    set((st) => ({ loadedHistory: new Set(st.loadedHistory).add(sid) })),
  setAttrs: (sid, patch) =>
    set((st) => ({ sessionAttrs: { ...st.sessionAttrs, [sid]: { ...st.sessionAttrs[sid], ...patch } } })),
  setMeta: (map) => {
    const cwd: Record<string, string> = {}
    const runs: Record<string, string[]> = {}
    for (const [sid, m] of Object.entries(map)) {
      if (m.cwd) cwd[sid] = m.cwd
      runs[sid] = m.run_ids
    }
    set((st) => ({ metaCwd: { ...st.metaCwd, ...cwd }, metaRunIds: { ...st.metaRunIds, ...runs } }))
  },
  setContextPct: (sid, pct) => set((st) => ({ contextPct: { ...st.contextPct, [sid]: pct } }))
}))

// 拉取会话列表（daemon 是唯一事实源；客户端只补 cwd 归属账本）
export async function refreshSessionList(): Promise<void> {
  try {
    const r = await rpc<SessionListResult>('session.list')
    const list = r.sessions.map((s) => ({ id: s.id, title: s.title, status: s.status, mode: s.mode, updatedAt: s.updated_at }))
    useStore.getState().setSessions(list)
    // 【学习要点】session.list 刻意不返回 cwd（协议如此），但 daemon 把每个会话的
    // meta.json 落在 ~/.iwan/sessions/<sid>/——经主进程文件桥批量读回来，
    // 项目分组就能用"后端真实归属"而不是"哪个窗口创建的记忆"。只读，不改。
    void window.iwan
      .sessionsMeta(list.map((s) => s.id))
      .then((m) => useStore.getState().setMeta(m))
      .catch(() => undefined)
  } catch {
    /* 未连接时静默，状态灯已说明一切 */
  }
}

// 把任意 JSON 压成一行短预览（工具卡入参展示用；超长截断留省略号）
function shortJson(v: unknown, max = 160): string {
  try {
    const s = JSON.stringify(v) ?? ''
    return s.length > max ? `${s.slice(0, max)}…` : s
  } catch {
    return ''
  }
}

// 【学习要点】session.get_history 返回的是【Anthropic 消息原形】：content 既可能是
// 字符串也可能是 block 数组（text/tool_use/tool_result）。渲染层负责把它降级成
// 卡片序列——tool_result 不单独成卡，而是回填到同 tool_use_id 的工具卡上，
// 这跟实时事件流（tool.call_started→finished）收敛到同一种 Msg 形态。
function historyToMsgs(raw: Array<Record<string, unknown>>): Msg[] {
  const out: Msg[] = []
  for (const m of raw) {
    const role = m.role === 'assistant' ? 'assistant' : 'user'
    const content = m.content
    const texts: string[] = []
    const cards: Msg[] = []
    if (typeof content === 'string') {
      texts.push(content)
    } else if (Array.isArray(content)) {
      for (const b of content as ContentBlock[]) {
        if (b.type === 'text' && b.text) texts.push(b.text)
        else if (b.type === 'tool_use')
          cards.push({
            id: `tool-${b.name ?? ''}-${out.length}-${cards.length}`,
            role: 'tool',
            toolName: b.name ?? '?',
            toolState: 'ok',
            toolInput: shortJson(b.input),
            text: ''
          })
        else if (b.type === 'tool_result') {
          // 回填输出到最近一张同名工具卡（历史里没有 tool_use 名可配对就跳过）
          const target = [...out, ...cards].reverse().find((c) => c.role === 'tool' && !c.toolOutput)
          if (target) target.toolOutput = shortJson(b.content, 320)
        }
      }
    }
    const joined = texts.join('\n').trim()
    if (joined) out.push({ id: nextId(), role, text: joined })
    out.push(...cards)
  }
  return out
}

// 打开侧栏里的一个会话：切过去 + 首次进入时拉历史（之后复用内存线程）
export async function openSession(sid: string): Promise<void> {
  const st = useStore.getState()
  st.setActive(sid)
  if (st.loadedHistory.has(sid)) return
  st.markHistoryLoaded(sid)
  if ((st.threads[sid] ?? []).length > 0) return // 本窗口刚跑过的会话内存里已有，不覆盖
  try {
    const r = await rpc<SessionGetHistoryResult>('session.get_history', { session_id: sid })
    useStore.getState().setThread(sid, historyToMsgs(r.messages))
  } catch (err) {
    useStore.getState().pushMsg(sid, { id: nextId(), role: 'notice', text: `历史加载失败：${String(err)}` })
  }
}

// 重命名会话（乐观更新 + session.renamed 事件回声兜底一致）
export async function renameSession(sid: string, title: string): Promise<void> {
  const t = title.trim()
  if (!t) return
  const st = useStore.getState()
  const meta = st.sessions.find((s) => s.id === sid)
  await rpc('session.rename', { session_id: sid, title: t })
  if (meta) st.upsertSession({ ...meta, title: t })
}

// 停止某会话当前运行（run.cancel 按 run_id 取消）
export async function cancelRun(sid: string): Promise<boolean> {
  const st = useStore.getState()
  const runId = st.lastRunId[sid]
  if (!runId || !st.runningSids.has(sid)) return false
  const r = await rpc<RunCancelResult>('run.cancel', { run_id: runId })
  return r.accepted
}

// 纠偏（steer）：向运行中的 run 注入一条转向消息
export async function steerRun(sid: string, message: string): Promise<boolean> {
  const st = useStore.getState()
  const runId = st.lastRunId[sid]
  const msg = message.trim()
  if (!runId || !msg || !st.runningSids.has(sid)) return false
  const r = await rpc<RunSteerResult>('run.steer', { run_id: runId, message: msg })
  if (r.accepted) st.pushMsg(sid, { id: nextId(), role: 'notice', text: `⚡ 已转向：${msg}` })
  return r.accepted
}

// llm.token 缓冲（不进 store，避免每 token 触发 React 渲染）
const tokenBuf = new Map<string, string[]>()
let flushScheduled = false
let activeStream: { sid: string; msgId: string; runId: string } | null = null
// 【学习要点】每个 run 的流式气泡按"段"编号：工具调用会 closeStream 切断文本流，
// 若新段仍用 stream-${runId} 同一 id，会被 ensureStreamMsg 找回旧气泡续写——
// 结果就是 "第一段## 第二段标题" 粘连、Markdown 块级语法失效。段号保证每段独立成卡。
const streamSegs = new Map<string, number>()

// 把缓冲 token 一次性并入当前流式消息——每帧最多一次 setState
function flushTokens(): void {
  flushScheduled = false
  if (!activeStream) return
  const buf = tokenBuf.get(activeStream.sid)
  if (!buf || buf.length === 0) return
  const merged = buf.join('')
  buf.length = 0
  const { sid, msgId } = activeStream
  useStore.setState((st) => ({
    threads: {
      ...st.threads,
      [sid]: (st.threads[sid] ?? []).map((m) =>
        m.id === msgId ? { ...m, text: m.text + merged } : m
      )
    }
  }))
}

// 确保该 run 当前段有挂件的流式助手消息（首个 token 到达才建，空回复不留空气泡）
function ensureStreamMsg(runId: string): { sid: string; msgId: string } | null {
  const st = useStore.getState()
  // 【学习要点】与 run 键事件同款纪律：token 只认已登记的 run。落到 activeSid
  // 兜底会把别处（TUI/后台起标题）的流式文本灌进当前线程——宁可丢字不可串线
  const sid = runOwner.get(runId)
  if (!sid) return null
  const msgId = `stream-${runId}-${streamSegs.get(runId) ?? 0}`
  if (!activeStream || activeStream.msgId !== msgId) {
    if (!st.threads[sid]?.some((m) => m.id === msgId)) {
      st.pushMsg(sid, { id: msgId, role: 'assistant', text: '', streaming: true })
    }
    activeStream = { sid, msgId, runId }
  }
  return { sid, msgId }
}

// 结束当前流式块（工具调用/运行结束时）：去光标态 + 段号前进，下一批 token 开新气泡
function closeStream(): void {
  flushTokens()
  if (activeStream) {
    const { sid, msgId, runId } = activeStream
    useStore.getState().patchMsg(sid, msgId, { streaming: false })
    streamSegs.set(runId, (streamSegs.get(runId) ?? 0) + 1)
    activeStream = null
  }
}

let msgSeq = 0
const nextId = (): string => `m${Date.now().toString(36)}-${++msgSeq}`

// 【学习要点】TUI 时代的教训："答复后必有一行可见回声"。审批请求产生的
// ⏸ 通知卡要能被 granted/denied 事件找回并原地改写，故按 tool_use_id
// 记下通知卡坐标——没有这张表，"已批准"就无处落地，用户只看到静默。
const waitingNotices = new Map<string, { sid: string; msgId: string }>()

// 信任卡坐标：trust.requested 建卡、trust.changed 找卡改写（key = sid|cwd）
const trustNotices = new Map<string, string>()

// 处理"其它客户端（TUI/CLI）也发了消息"：与本地已发送去重后回显
function noteRemoteUserMessage(sid: string, content: string): void {
  const thread = useStore.getState().threads[sid] ?? []
  const last = thread[thread.length - 1]
  if (last && last.role === 'user' && last.text === content) return
  useStore.getState().pushMsg(sid, { id: nextId(), role: 'user', text: content })
}

// daemon 事件 → 状态变更的唯一入口（App 挂载时注册一次）
export function handleBusEvent(ev: BusEvent): void {
  const st = useStore.getState()
  const t = ev.type
  switch (t) {
    case 'llm.token': {
      const s = ensureStreamMsg(ev.run_id)
      if (!s) return
      let buf = tokenBuf.get(s.sid)
      if (!buf) tokenBuf.set(s.sid, (buf = []))
      buf.push(ev.token)
      if (!flushScheduled) {
        flushScheduled = true
        requestAnimationFrame(flushTokens)
      }
      return
    }
    case 'run.started': {
      let sid = runOwner.get(ev.run_id) // run 键事件严格归属：未知 run（如后台起标题）不许落到当前会话
      if (!sid) {
        // 证据式认领：goal 与某条在途 send_message 的原文完全一致才收编
        for (const [csid, c] of pendingClaims) {
          if (c.text.trim() === ev.goal.trim()) {
            sid = csid
            runOwner.set(ev.run_id, csid)
            pendingClaims.delete(csid)
            break
          }
        }
      }
      if (sid) {
        st.markRunning(sid, true)
        st.setLastRun(sid, ev.run_id) // stop/steer 按钮按会话找最新 run
      }
      return
    }
    case 'run.finished': {
      const sid = runOwner.get(ev.run_id) // run 键事件严格归属：未知 run（如后台起标题）不许落到当前会话
      closeStream()
      if (sid) {
        st.markRunning(sid, false)
        const ok = ev.status === 'completed' || ev.status === 'success' || ev.status === 'ok'
        st.pushMsg(sid, {
          id: nextId(),
          role: 'notice',
          text: `${ok ? '✓ 完成' : `✗ ${ev.status}`}${ev.reason ? `（${ev.reason}）` : ''} · ${ev.steps} steps`
        })
      }
      return
    }
    case 'tool.call_started': {
      if (activeStream && runOwner.get(ev.run_id) === activeStream.sid) closeStream() // 工具打断文本块，下一段另起消息
      const sid = runOwner.get(ev.run_id) // run 键事件严格归属：未知 run（如后台起标题）不许落到当前会话
      if (sid)
        st.pushMsg(sid, {
          id: `tool-${ev.tool_use_id}`,
          role: 'tool',
          toolName: ev.tool_name,
          toolState: 'running',
          toolInput: shortJson(ev.params),
          text: ''
        })
      return
    }
    case 'tool.call_finished': {
      const sid = runOwner.get(ev.run_id) // run 键事件严格归属：未知 run（如后台起标题）不许落到当前会话
      if (sid)
        st.patchMsg(sid, `tool-${ev.tool_use_id}`, {
          toolState: 'ok',
          text: `${ev.elapsed_ms}ms`,
          toolOutput: ev.output ? shortJson(ev.output, 320) : undefined
        })
      return
    }
    case 'tool.call_failed': {
      const sid = runOwner.get(ev.run_id) // run 键事件严格归属：未知 run（如后台起标题）不许落到当前会话
      if (sid)
        st.patchMsg(sid, `tool-${ev.tool_use_id}`, {
          toolState: 'fail',
          text: `${ev.error_class}: ${ev.error_message}`
        })
      return
    }
    case 'llm.usage': {
      const sid = runOwner.get(ev.run_id) // run 键事件严格归属：未知 run（如后台起标题）不许落到当前会话
      if (sid && ev.context_pct !== undefined) st.setContextPct(sid, ev.context_pct)
      return
    }
    case 'session.renamed': {
      const meta = st.sessions.find((s) => s.id === ev.session_id)
      if (meta) st.upsertSession({ ...meta, title: ev.title })
      return
    }
    case 'session.created': {
      void refreshSessionList()
      return
    }
    case 'session.closed': {
      st.removeSession(ev.session_id)
      return
    }
    case 'session.message_received': {
      noteRemoteUserMessage(ev.session_id, ev.content)
      return
    }
    case 'permission.requested': {
      const msgId = nextId()
      waitingNotices.set(ev.tool_use_id, { sid: ev.session_id, msgId })
      // 【学习要点】审批卡走 Msg.card 而不是纯 notice：daemon 的 permission.respond
      // 需要 tool_use_id+decision，卡片自带按钮才能就地答复；granted/denied 事件
      // 再把 answered 写回同一张卡——"答复必有回声"在 GUI 里的同一条纪律。
      st.pushMsg(ev.session_id, {
        id: msgId,
        role: 'notice',
        card: 'approval',
        toolUseId: ev.tool_use_id,
        toolName: ev.tool_name,
        paramPreview: ev.param_preview,
        text: `⏸ 等待审批：${ev.tool_name}`
      })
      return
    }
    case 'permission.granted':
    case 'permission.denied': {
      // 原地改写审批卡为结果回声；找不到坐标（GUI 中途加入的会话）就补一条新通知
      const spot = waitingNotices.get(ev.tool_use_id)
      waitingNotices.delete(ev.tool_use_id)
      const text =
        t === 'permission.granted'
          ? `▶ 审批通过（${ev.decision}）：${ev.tool_use_id.slice(0, 8)}`
          : `🚫 审批拒绝（${ev.decision}）：${ev.tool_use_id.slice(0, 8)}`
      if (spot) st.patchMsg(spot.sid, spot.msgId, { text, answered: ev.decision, card: undefined })
      else {
        const sid = runOwner.get(ev.run_id) // run 键事件严格归属：未知 run（如后台起标题）不许落到当前会话
        if (sid) st.pushMsg(sid, { id: nextId(), role: 'notice', text })
      }
      return
    }
    case 'skill.invoked': {
      const sid = runOwner.get(ev.run_id) // run 键事件严格归属：未知 run（如后台起标题）不许落到当前会话
      if (sid)
        st.pushMsg(sid, {
          id: nextId(),
          role: 'notice',
          text: `⚡ 技能 ${ev.skill_name}${ev.auto_triggered ? '（自动命中）' : ''}`
        })
      return
    }
    case 'trust.requested': {
      const msgId = nextId()
      trustNotices.set(`${ev.session_id}|${ev.cwd}`, msgId)
      st.pushMsg(ev.session_id, {
        id: msgId,
        role: 'notice',
        card: 'trust',
        cwd: ev.cwd,
        text: `🔒 信任询问：${ev.cwd}${ev.has_instruction_files ? '（目录内有指令文件，建议先审阅）' : ''}`
      })
      return
    }
    case 'trust.changed': {
      const msgId = trustNotices.get(`${ev.session_id}|${ev.cwd}`)
      if (msgId) {
        trustNotices.delete(`${ev.session_id}|${ev.cwd}`)
        st.patchMsg(ev.session_id, msgId, {
          text: `🔒 信任更新：${ev.cwd} → ${ev.decision}${ev.persistent ? '（已持久）' : ''}`,
          card: undefined,
          answered: ev.decision
        })
      } else {
        st.pushMsg(ev.session_id, { id: nextId(), role: 'notice', text: `🔒 信任更新：${ev.cwd} → ${ev.decision}` })
      }
      return
    }
    case 'session.permission_mode_changed':
      st.setAttrs(ev.session_id, { permissionMode: ev.mode })
      return
    case 'session.model_changed':
      st.setAttrs(ev.session_id, { modelPreset: ev.preset, model: ev.model })
      return
    case 'session.effort_level_changed':
      st.setAttrs(ev.session_id, { effortLevel: ev.level })
      return
    case 'session.engine_changed':
      st.setAttrs(ev.session_id, { engine: ev.engine })
      return
    case 'session.auto_mode_changed':
      st.setAttrs(ev.session_id, { autoMode: ev.mode })
      return
    case 'llm.model_selected': {
      const sid = runOwner.get(ev.run_id)
      if (sid) st.setAttrs(sid, { model: ev.model })
      return
    }
    case 'subagent.started': {
      const sid = runOwner.get(ev.parent_run_id) // 同 run 键纪律：父 run 不归本窗口的子任务卡不进当前线程
      if (sid) st.pushMsg(sid, { id: `sub-${ev.run_id}`, role: 'notice', text: `🤖 子任务：${ev.description}` })
      return
    }
    case 'subagent.finished': {
      const sid = runOwner.get(ev.parent_run_id)
      if (sid) st.patchMsg(sid, `sub-${ev.run_id}`, { text: `🤖 子任务 ${ev.status}` })
      return
    }
    case 'context.compacted': {
      st.pushMsg(ev.session_id, {
        id: nextId(),
        role: 'notice',
        text: `🗜 上下文压缩：摘要 ${ev.summary_tokens} tokens`
      })
      return
    }
    case 'session.waiting_for_input': {
      st.markRunning(ev.session_id, false)
      st.setLastRun(ev.session_id, ev.last_run_id)
      return
    }
    default:
      return
  }
}

// 发送一条用户消息并跑一轮——M0 的核心动作序列
export async function sendMessage(text: string): Promise<void> {
  const st = useStore.getState()
  let sid = st.activeSid
  if (!sid) {
    const r = await rpc<SessionCreateResult & Partial<SessionAttrs>>('session.create', {
      mode: 'chat',
      cwd: st.selectedCwd ?? undefined
    })
    sid = r.session_id
    st.upsertSession({
      id: sid,
      title: '(未命名)',
      status: 'active',
      cwd: st.selectedCwd ?? undefined
    })
    st.setActive(sid)
    // create 结果自带全套会话属性（daemon 已确认字段：auto_mode/permission_mode/...）
    st.setAttrs(sid, {
      permissionMode: r.permission_mode,
      modelPreset: r.model_preset,
      effortLevel: r.effort_level,
      autoMode: r.auto_mode
    })
  }
  st.pushMsg(sid, { id: nextId(), role: 'user', text })
  // 【学习要点】daemon 的 send_message 会 await 整个 run 才带 run_id 返回——
  // 首帧事件（run.started/tokens）必然赶在响应前到达。所以运行态不能在这里
  // markRunning(true)（那会把已跑完的 run 又点绿），而是记一条"待认领"，由
  // run.started 按 goal==原文 认领后驱动灯；响应回来只补路由表。
  pendingClaims.set(sid, { text, ts: Date.now() })
  for (const [oldSid, c] of pendingClaims) {
    if (Date.now() - c.ts > 60_000) pendingClaims.delete(oldSid) // 认领证据保鲜，防旧文本误收编后来的同名 run
  }
  const res = await rpc<SessionSendMessageResult>('session.send_message', { session_id: sid, content: text })
  pendingClaims.delete(sid) // 响应已回，认领窗口关闭（无论成败，之后的事件都该按 owner 表走）
  // run_id 在模型里带默认值（可选字段）；没有 run 就没什么可路由的，静默跳过
  if (res.run_id) {
    runOwner.set(res.run_id, sid)
    st.setLastRun(sid, res.run_id)
  }
  st.markHistoryLoaded(sid) // 本窗口全程事件可见，无需再拉历史
}

// ==================== 卡片与选择器的动作面（协议命令全部真实存在） ====================

// 审批卡按钮 → permission.respond；本地立即把卡压成"已答复"回声防重复点击
export async function respondPermission(sid: string, msgId: string, toolUseId: string, decision: string): Promise<void> {
  const st = useStore.getState()
  st.patchMsg(sid, msgId, { answered: decision, card: undefined, text: `⏳ 已提交答复（${decision}），等待 daemon 确认…` })
  try {
    await rpc('permission.respond', { tool_use_id: toolUseId, decision })
  } catch (err) {
    st.patchMsg(sid, msgId, { card: 'approval', text: `⚠ 答复失败：${String(err instanceof Error ? err.message : err)}` })
  }
}

// 信任卡按钮 → trust.respond（persistent=是否写进信任文件）
export async function respondTrust(sid: string, msgId: string, cwd: string, decision: string, persistent: boolean): Promise<void> {
  const st = useStore.getState()
  st.patchMsg(sid, msgId, { answered: decision, card: undefined, text: `⏳ 已提交（${decision}），等待 daemon 确认…` })
  try {
    await rpc('trust.respond', { session_id: sid, cwd, decision, persistent })
  } catch (err) {
    st.patchMsg(sid, msgId, { card: 'trust', text: `⚠ 答复失败：${String(err instanceof Error ? err.message : err)}` })
  }
}

// 切换会话权限模式（Composer 审批选择器；五态来自 daemon PERMISSION_MODES）
export async function setPermissionMode(sid: string, mode: string): Promise<void> {
  const r = await rpc<{ mode: string }>('session.set_permission_mode', { session_id: sid, mode })
  useStore.getState().setAttrs(sid, { permissionMode: r.mode ?? mode })
}

// 切换模型预设（fast/balanced/powerful；daemon 语义=全局 PermissionManager，卡片文案要诚实）
export async function setModelPreset(sid: string, preset: string): Promise<void> {
  const r = await rpc<{ preset: string }>('session.set_model', { session_id: sid, preset })
  useStore.getState().setAttrs(sid, { modelPreset: r.preset ?? preset })
}

// 切换努力等级（minimal/low/medium/high/max）
export async function setEffortLevel(sid: string, level: string): Promise<void> {
  const r = await rpc<{ level: string }>('session.set_effort_level', { session_id: sid, level })
  useStore.getState().setAttrs(sid, { effortLevel: r.level ?? level })
}

// 切换引擎（六引擎全列：legacy/langgraph/plan_execute/debate/pipeline/auto）
export async function setEngine(sid: string, engine: string): Promise<void> {
  const r = await rpc<{ engine: string }>('session.set_engine', { session_id: sid, engine })
  useStore.getState().setAttrs(sid, { engine: r.engine ?? engine })
}

// 关闭会话（session.close 真实存在——侧栏右键菜单用）
export async function closeSession(sid: string): Promise<void> {
  await rpc('session.close', { session_id: sid })
  const st = useStore.getState()
  st.removeSession(sid)
  if (st.activeSid === sid) st.setActive(null)
}

// 手动压缩上下文（composer 溢出时的一键动作）
export async function compactSession(sid: string): Promise<void> {
  const r = await rpc<{ summary_tokens: number; saved_tokens: number }>('session.compact', { session_id: sid })
  useStore.getState().pushMsg(sid, {
    id: nextId(),
    role: 'notice',
    text: `🗜 已压缩：摘要 ${r.summary_tokens} tokens，省 ${r.saved_tokens} tokens`
  })
}

// ==================== 文件改动（files.changes / files.restore，S9 协议现成） ====================

export interface FileChange {
  path: string
  tool?: string
  ts?: string
  was_new?: boolean
  captured?: boolean
  conflict?: boolean
}

export async function loadChanges(sid: string): Promise<FileChange[]> {
  const runId = useStore.getState().lastRunId[sid]
  const r = await rpc<{ changes?: FileChange[] }>('files.changes', {
    session_id: sid,
    run_id: runId || undefined
  })
  return r.changes ?? []
}

// 恢复指定路径（paths 省略=恢复该 run 全部）
export async function restoreFiles(sid: string, paths?: string[]): Promise<{ ok?: boolean; results?: Array<{ path: string; status: string; detail?: string }> }> {
  const runId = useStore.getState().lastRunId[sid]
  const r = await rpc<{ ok?: boolean; results?: Array<{ path: string; status: string; detail?: string }> }>('files.restore', {
    session_id: sid,
    run_id: runId || undefined,
    paths,
    force: true
  })
  return r
}
