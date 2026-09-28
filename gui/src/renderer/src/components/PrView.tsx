// Pull Request 视图：pr.context（本地 git 坐标）+ pr.list（GitHub 列表）+ pr.create（推送并建 PR）
//
// 【学习要点】1) daemon 端 pr.create 会真的 push + 开 PR——改变共享状态的动作
// 必须在这里【二次确认】后才发命令（对齐 Claude Code "ask 先于执行"的纪律：
// 确认的责任在离用户最近的一层，daemon 只兜底记审计日志）。
// 2) 错误文案策略：context/list/create 失败都渲染 daemon 返回的 error 原文，
// 不改写不吞掉——"未配置令牌"这类提示本身带修复指引，用户读了知道下一步做什么。
import { useCallback, useEffect, useState } from 'react'
import { rpc } from '../rpc'
import { useStore } from '../store'
import { baseName } from '../guiHelpers'
import type {
  PrContextResult,
  PrCreateResult,
  PrInfo,
  PrListResult
} from '../protocol/types'
import { Icon } from './Icon'

// PR 状态标签的中文口径（GitHub 三态）
const STATE_ZH: Record<string, string> = { open: '进行中', closed: '已关闭', merged: '已合并' }

export function PrView() {
  const selectedCwd = useStore((s) => s.selectedCwd)
  const [ctx, setCtx] = useState<PrContextResult | null>(null)
  const [pulls, setPulls] = useState<PrInfo[]>([])
  const [state, setState] = useState('open')
  const [page, setPage] = useState(1)
  const [listErr, setListErr] = useState('')
  const [busy, setBusy] = useState(false)
  // 创建表单
  const [formOpen, setFormOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [base, setBase] = useState('')
  const [draft, setDraft] = useState(false)
  const [createMsg, setCreateMsg] = useState('')

  const cwd = selectedCwd ?? ''

  const loadContext = useCallback(async (): Promise<void> => {
    try {
      setCtx(await rpc<PrContextResult>('pr.context', { cwd }))
    } catch (e) {
      setCtx({ ok: false, owner: '', repo: '', branch: '', default_branch: '', ahead: 0, behind: 0, has_remote: false, error: String(e) })
    }
  }, [cwd])

  const loadList = useCallback(async (): Promise<void> => {
    setBusy(true)
    try {
      const r = await rpc<PrListResult>('pr.list', { cwd, state, page })
      setPulls(r.pulls ?? [])
      setListErr(r.ok ? '' : r.error ?? '拉取 PR 列表失败')
    } catch (e) {
      setPulls([])
      setListErr(String(e))
    } finally {
      setBusy(false)
    }
  }, [cwd, state, page])

  useEffect(() => {
    void loadContext()
  }, [loadContext])
  useEffect(() => {
    void loadList()
  }, [loadList])

  // 提交创建：本地 confirm → daemon push+POST → 回显结果并刷新两侧数据
  const submitCreate = async (): Promise<void> => {
    if (!title.trim()) return
    const pushNote = (ctx?.ahead ?? 0) > 0 ? `将先推送分支 ${ctx?.branch}（${ctx?.ahead} 个未推送提交），` : ''
    const dst = base.trim() || ctx?.default_branch || '远端默认分支'
    if (!window.confirm(`${pushNote}在 ${ctx?.owner}/${ctx?.repo} 创建 PR → ${dst}。确认执行？`)) return
    setBusy(true)
    setCreateMsg('')
    try {
      const r = await rpc<PrCreateResult>('pr.create', { cwd, title: title.trim(), body, base: base.trim(), head: '', draft })
      if (r.ok) {
        setCreateMsg(`✓ PR #${r.number} 已创建${r.pushed ? '（已推送分支）' : ''}`)
        setTitle('')
        setBody('')
        setFormOpen(false)
        void loadContext()
        void loadList()
      } else {
        setCreateMsg(`✗ ${r.error}`)
      }
    } catch (e) {
      setCreateMsg(`✗ ${String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fn-view">
      <div className="fn-head">
        <span className="fn-title">Pull Request</span>
        <span className="fn-meta" title={cwd || 'daemon 进程目录'}>
          {cwd ? baseName(cwd) : '默认目录'}
        </span>
        <span className="grow" />
        <button className="chip" onClick={() => { void loadContext(); void loadList() }} disabled={busy} title="重新拉取坐标与列表">
          <Icon name="refresh" size={13} /> 刷新
        </button>
        <button className="chip" onClick={() => setFormOpen((v) => !v)} disabled={!ctx?.ok}>
          <Icon name="plus" size={13} /> 创建 PR
        </button>
      </div>
      <div className="fn-body">
        {/* 坐标头：本地 git 事实 */}
        {ctx && !ctx.ok && <div className="fn-err">{ctx.error}</div>}
        {ctx?.ok && (
          <div className="fn-card">
            <div className="fn-card-head">
              <Icon name="branch" size={14} />
              <span className="fn-card-name">{ctx.owner}/{ctx.repo}</span>
              <span className="fn-tag">@ {ctx.branch || '游离 HEAD'}</span>
              {ctx.default_branch && <span className="fn-tag">默认 {ctx.default_branch}</span>}
              <span className="grow" />
              <span className="fn-meta">↑{ctx.ahead} ↓{ctx.behind}</span>
            </div>
          </div>
        )}
        {/* 创建表单（确认后才会动远端） */}
        {formOpen && ctx?.ok && (
          <div className="fn-card fn-form">
            <input className="fn-input" placeholder="PR 标题（必填）" value={title} onChange={(e) => setTitle(e.target.value)} />
            <textarea className="fn-textarea" rows={4} placeholder="描述（可选）" value={body} onChange={(e) => setBody(e.target.value)} />
            <div className="fn-form-row">
              <input
                className="fn-input fn-input-slim"
                placeholder={`目标分支（默认 ${ctx.default_branch || '远端默认'}）`}
                value={base}
                onChange={(e) => setBase(e.target.value)}
              />
              <label className="fn-check">
                <input type="checkbox" checked={draft} onChange={(e) => setDraft(e.target.checked)} /> 草稿
              </label>
              <span className="grow" />
              <button className="chip" onClick={() => void submitCreate()} disabled={busy || !title.trim()}>
                <Icon name="send" size={13} /> 推送并创建
              </button>
            </div>
            {createMsg && <div className={createMsg.startsWith('✓') ? 'fn-ok' : 'fn-err'}>{createMsg}</div>}
          </div>
        )}
        {/* 列表 */}
        <div className="fn-toolbar">
          {['open', 'closed', 'all'].map((st) => (
            <button
              key={st}
              className={`chip${state === st ? ' chip-on' : ''}`}
              onClick={() => { setPage(1); setState(st) }}
            >
              {st === 'open' ? '进行中' : st === 'closed' ? '已关闭' : '全部'}
            </button>
          ))}
          <span className="grow" />
          <button className="chip" disabled={page <= 1 || busy} onClick={() => setPage((p) => p - 1)}>‹</button>
          <span className="fn-meta">第 {page} 页</span>
          <button className="chip" disabled={!ctx?.ok || busy || pulls.length < 30} onClick={() => setPage((p) => p + 1)}>›</button>
        </div>
        {listErr && <div className="fn-err">{listErr}</div>}
        {!listErr && ctx?.ok && !busy && !pulls.length && <div className="fn-empty">该状态下没有 PR</div>}
        {pulls.map((p) => (
          <div key={p.number} className="fn-row" onClick={() => window.open(p.url, '_blank')} title={p.title}>
            <span className={`dot dot-${p.state === 'open' ? 'connected' : p.state === 'merged' ? '' : 'error'}`} />
            <span className="fn-row-title">{p.draft ? '【草稿】' : ''}{p.title}</span>
            <span className="fn-tag">{STATE_ZH[p.state ?? ''] ?? p.state}</span>
            <span className="grow" />
            <span className="fn-meta">{p.author} · {p.head_ref} → {p.base_ref}</span>
            <span className="fn-meta">#{p.number}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
