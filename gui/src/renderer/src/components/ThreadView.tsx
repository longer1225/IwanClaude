// 会话消息流视图：卡片时间线（user 气泡 / assistant Markdown / tool 卡 / notice / 审批卡 / 信任卡）
//
// 【学习要点】1) 自动滚底只在"用户本来就贴着底部"时跟随，防止回看历史被流式抢视口；
// 2) 流式 assistant 文本每帧都重渲染 Markdown 看似浪费，但 react-markdown 输出的是
// 虚拟 DOM，配合"贴底才跟"策略实测无压力——不为此引 memo 化复杂的增量解析；
// 3) 工具卡用 <details>：默认只占一行，参数/输出点开才看——Codex 时间线的密度纪律；
// 4) 审批卡四键对齐 daemon decision 枚举（allow_once/always_allow/deny_once/always_deny），
//    信任卡三键对齐 trust 语义（allow/deny + persistent）——按钮文案可以人性化，
//    提交值一个都不能自创。
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Msg } from '../store'
import { respondPermission, respondTrust } from '../store'
import { Icon } from './Icon'

// 审批卡：daemon permission.requested 生成，四键对应 permission.respond 的合法 decision
function ApprovalCard({ m, sid }: { m: Msg; sid: string }) {
  const [busy, setBusy] = useState(false)
  const act = (decision: string): void => {
    if (busy || !m.toolUseId) return
    setBusy(true)
    void respondPermission(sid, m.id, m.toolUseId, decision).finally(() => setBusy(false))
  }
  const btn = (label: string, decision: string, kind = ''): React.ReactElement => (
    <button className={`card-btn ${kind}`} disabled={busy} onClick={() => act(decision)}>{label}</button>
  )
  return (
    <div className="msg card card-approval">
      <div className="card-head">
        <Icon name="shield" size={14} />
        <span className="card-title">等待审批 · {m.toolName}</span>
      </div>
      {m.paramPreview && <pre className="card-preview">{m.paramPreview}</pre>}
      <div className="card-actions">
        {btn('本次允许', 'allow_once', 'primary')}
        {btn('总是允许', 'always_allow')}
        {btn('本次拒绝', 'deny_once', 'danger')}
        {btn('总是拒绝', 'always_deny', 'danger')}
      </div>
      <div className="card-hint">拒绝=中断该工具调用并回灌原因；"总是"会写入本会话规则。</div>
    </div>
  )
}

// 信任卡：目录级信任询问（S9 信任门），persistent 决定要不要写进信任文件
function TrustCard({ m, sid }: { m: Msg; sid: string }) {
  const [busy, setBusy] = useState(false)
  const act = (decision: string, persistent: boolean): void => {
    if (busy || !m.cwd) return
    setBusy(true)
    void respondTrust(sid, m.id, m.cwd, decision, persistent).finally(() => setBusy(false))
  }
  return (
    <div className="msg card card-trust">
      <div className="card-head">
        <Icon name="folder" size={14} />
        <span className="card-title">信任此目录？</span>
      </div>
      <pre className="card-preview">{m.cwd}</pre>
      <div className="card-actions">
        <button className="card-btn primary" disabled={busy} onClick={() => act('allow', false)}>本次允许</button>
        <button className="card-btn" disabled={busy} onClick={() => act('allow', true)}>总是允许</button>
        <button className="card-btn danger" disabled={busy} onClick={() => act('deny', false)}>拒绝</button>
      </div>
      <div className="card-hint">不信任的目录里，agent 将拒绝读写该项目的任何文件。</div>
    </div>
  )
}

// 单条消息按角色分派渲染形态
function MsgRow({ m, sid }: { m: Msg; sid: string }) {
  if (m.card === 'approval') return <ApprovalCard m={m} sid={sid} />
  if (m.card === 'trust') return <TrustCard m={m} sid={sid} />
  if (m.role === 'user') {
    return (
      <div className="msg msg-user">
        <div className="bubble">{m.text}</div>
      </div>
    )
  }
  if (m.role === 'tool') {
    const icon = m.toolState === 'running' ? '⟳' : m.toolState === 'ok' ? '✓' : '✗'
    const hasDetail = !!(m.toolInput || m.toolOutput)
    return (
      <div className={`msg msg-tool state-${m.toolState ?? 'running'}`}>
        <span className="tool-icon">{icon}</span>
        <span className="tool-name">{m.toolName}</span>
        {m.text && <span className="tool-meta">{m.text}</span>}
        {hasDetail && (
          <details className="tool-detail">
            <summary>详情</summary>
            {m.toolInput && <pre className="tool-io">→ {m.toolInput}</pre>}
            {m.toolOutput && <pre className="tool-io">← {m.toolOutput}</pre>}
          </details>
        )}
      </div>
    )
  }
  if (m.role === 'notice') {
    return <div className="msg msg-notice">{m.text}</div>
  }
  return (
    <div className="msg msg-assistant">
      <div className={`md${m.streaming ? ' streaming' : ''}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
        {m.streaming && <span className="cursor">▍</span>}
      </div>
    </div>
  )
}

export function ThreadView({ sid, msgs }: { sid: string; msgs: Msg[] }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)
  const [localPreview, setLocalPreview] = useState(false)

  // 记录用户是否贴底：离底 80px 内视为"想跟着看"
  const onScroll = (): void => {
    const el = boxRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    setLocalPreview(!stickRef.current)
  }

  // 切换会话必回到底部并重置贴底意图（新线程从最近一条接着看）
  useEffect(() => {
    stickRef.current = true
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [sid])

  // 内容变化时按贴底状态决定跟滚
  const lastLen = msgs.length ? msgs[msgs.length - 1].text.length : 0
  useEffect(() => {
    const el = boxRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [msgs.length, lastLen, sid])

  return (
    <div className="thread" ref={boxRef} onScroll={onScroll}>
      {msgs.map((m) => (
        <MsgRow key={m.id} m={m} sid={sid} />
      ))}
      {localPreview && msgs.length > 0 && (
        <button className="jump-bottom" onClick={() => {
          const el = boxRef.current
          if (el) { el.scrollTop = el.scrollHeight; stickRef.current = true }
          setLocalPreview(false)
        }}>
          ↓ 回到底部
        </button>
      )}
    </div>
  )
}
