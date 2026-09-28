// Git 面板（M3）：工作区状态 → 暂存 → 提交 → 分支 → 历史 → pull/push 一屏闭环
//
// 【学习要点】1) 破坏性分级在 GUI 落地：discard（丢未提交改动）走双确认——先
// confirm 文案里数清楚要牺牲几个文件；push 动的是共享远端，也要 confirm；
// checkout/commit/stage 可逆或仅本地，直给按钮。daemon 端只记审计日志不拦截，
// 这与 Claude Code "确认责任在离用户最近一层"同构。
// 2) 每次写操作后 refreshAll()：git 状态是牵一发动全身的派生视图（add 会同时
// 改变 staged/untracked 两组），做局部 diff 更新是过度设计，整表重拉最诚实。
// 3) 所有列表字段 `?? []` 防御：types.ts 由协议生成，带默认值的字段在 TS 里是
// 可选的——daemon 不会漏发，但类型系统需要我们自己收口。
import { useCallback, useEffect, useMemo, useState } from 'react'
import { rpc } from '../rpc'
import { useStore } from '../store'
import { baseName } from '../guiHelpers'
import type {
  GitBranchesResult,
  GitBranchInfo,
  GitLogEntry,
  GitLogResult,
  GitOpResult,
  GitStatusResult
} from '../protocol/types'
import { Icon } from './Icon'

// 一次数据刷新：状态/分支/历史三路并拉，任一失败不打断其他两路
interface Snapshot {
  st: GitStatusResult | null
  br: GitBranchInfo[]
  log: GitLogEntry[]
}

export function GitPanel() {
  const selectedCwd = useStore((s) => s.selectedCwd)
  const cwd = selectedCwd ?? ''
  const [snap, setSnap] = useState<Snapshot>({ st: null, br: [], log: [] })
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [commitText, setCommitText] = useState('')

  const refreshAll = useCallback(async (): Promise<void> => {
    const [st, br, lg] = await Promise.all([
      rpc<GitStatusResult>('git.status', { cwd }).catch(
        (e): GitStatusResult => ({ ok: false, error: String(e), branch: '', ahead: 0, behind: 0, files: [] })
      ),
      rpc<GitBranchesResult>('git.branches', { cwd }).catch(() => null),
      rpc<GitLogResult>('git.log', { cwd, limit: 20 }).catch(() => null)
    ])
    setSnap({ st, br: br?.branches ?? [], log: lg?.entries ?? [] })
  }, [cwd])

  useEffect(() => {
    void refreshAll()
    setSel(new Set())
  }, [refreshAll])

  const files = snap.st?.files ?? []
  const staged = useMemo(() => files.filter((f) => f.staged), [files])
  const unstaged = useMemo(() => files.filter((f) => !f.staged && !f.untracked), [files])
  const untracked = useMemo(() => files.filter((f) => f.untracked), [files])

  // 勾选框只作用于"可写组"（untracked + unstaged → Stage；staged + untracked 可 Discard）
  const toggle = (p: string): void =>
    setSel((s) => {
      const n = new Set(s)
      if (n.has(p)) n.delete(p); else n.add(p)
      return n
    })

  const showOp = (r: GitOpResult, okText: string): void => {
    setMsg(r.ok ? `✓ ${okText}${r.sha ? `（${r.sha}）` : ''}` : `✗ ${r.error || '操作失败'}`)
  }

  // 发一个写操作并整表刷新（paths 为空组时直接跳过，不发空命令）
  const runOp = async (method: string, payload: Record<string, unknown>, okText: string): Promise<void> => {
    setBusy(true)
    try {
      const r = await rpc<GitOpResult>(method, payload)
      showOp(r, okText)
      await refreshAll()
      setSel(new Set())
    } catch (e) {
      setMsg(`✗ ${String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const stagePaths = (paths: string[]): void => {
    if (paths.length) void runOp('git.stage', { cwd, paths }, `已暂存 ${paths.length} 个文件`)
  }
  const unstagePaths = (paths: string[]): void => {
    if (paths.length) void runOp('git.unstage', { cwd, paths }, `已退暂存 ${paths.length} 个文件`)
  }
  const discardPaths = (paths: string[]): void => {
    if (!paths.length) return
    if (!window.confirm(`将丢弃 ${paths.length} 个文件的未提交改动（未跟踪文件会被删除），不可恢复。确认？`)) return
    void runOp('git.discard', { cwd, paths }, `已丢弃 ${paths.length} 个文件的改动`)
  }

  const doCommit = async (): Promise<void> => {
    const m = commitText.trim()
    if (!m) return
    setBusy(true)
    try {
      const r = await rpc<GitOpResult>('git.commit', { cwd, message: m })
      showOp(r, '已提交')
      if (r.ok) {
        setCommitText('')
        await refreshAll()
      }
    } catch (e) {
      setMsg(`✗ ${String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const doNetwork = async (which: 'git.pull' | 'git.push'): Promise<void> => {
    const zh = which === 'git.pull' ? '拉取（--ff-only）' : '推送（origin HEAD）'
    if (which === 'git.push' && !window.confirm(`把当前分支 ${snap.st?.branch || '?'} 推送到 origin？`)) return
    setBusy(true)
    try {
      const r = await rpc<GitOpResult>(which, { cwd })
      setMsg(r.ok ? `✓ ${zh}完成` : `✗ ${zh}失败：${r.error || r.output || '未知错误'}`)
      await refreshAll()
    } catch (e) {
      setMsg(`✗ ${String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const doCheckout = (name: string): void => {
    setBusy(true)
    void rpc<GitOpResult>('git.checkout', { cwd, name })
      .then((r) => {
        setMsg(r.ok ? `✓ 已切换到 ${name}` : `✗ ${r.error}`)
        return refreshAll()
      })
      .finally(() => setBusy(false))
  }

  // 一组文件行：复选框 + 路径 + 状态字母 + hover 动作钮
  const fileRows = (list: typeof files, opts: { canStage: boolean; canUnstage: boolean; canDiscard: boolean }) => (
    <>
      {list.map((f) => (
        <div key={f.path} className="git-row" title={f.path}>
          <input type="checkbox" checked={sel.has(f.path)} onChange={() => toggle(f.path)} />
          <span className={`git-letter${f.conflicted ? ' git-conflict' : ''}`}>
            {(f.index_status === ' ' || f.index_status === '?') ? f.worktree_status : f.index_status}
          </span>
          <span className="git-path">{f.path}</span>
          <span className="grow" />
          {opts.canStage && !f.staged && (
            <button className="icon-btn" title="暂存" disabled={busy || f.conflicted} onClick={() => stagePaths([f.path])}>＋</button>
          )}
          {opts.canUnstage && f.staged && (
            <button className="icon-btn" title="退暂存" disabled={busy} onClick={() => unstagePaths([f.path])}>－</button>
          )}
          {opts.canDiscard && (
            <button className="icon-btn" title="丢弃改动" disabled={busy || f.conflicted} onClick={() => discardPaths([f.path])}>
              <Icon name="trash" size={12} />
            </button>
          )}
        </div>
      ))}
    </>
  )

  const groupHead = (title: string, count: number, actions: React.ReactNode) => (
    <div className="git-group-head">
      <span className="fn-card-name">{title}</span>
      <span className="fn-tag">{count}</span>
      <span className="grow" />
      {actions}
    </div>
  )

  const okCtx = snap.st?.ok === true

  return (
    <div className="fn-view">
      <div className="fn-head">
        <span className="fn-title">Git</span>
        {okCtx && (
          <span className="fn-tag" title="当前分支">
            <Icon name="branch" size={12} /> {snap.st?.branch || '游离 HEAD'}
          </span>
        )}
        {okCtx && <span className="fn-meta">↑{snap.st?.ahead ?? 0} ↓{snap.st?.behind ?? 0}</span>}
        <span className="fn-meta" title={cwd || 'daemon 进程目录'}>{cwd ? baseName(cwd) : '默认目录'}</span>
        <span className="grow" />
        <button className="chip" disabled={busy} onClick={() => void refreshAll()} title="重拉状态/分支/历史">
          <Icon name="refresh" size={13} /> 刷新
        </button>
        <button className="chip" disabled={busy || !okCtx} onClick={() => void doNetwork('git.pull')}>Pull</button>
        <button className="chip" disabled={busy || !okCtx} onClick={() => void doNetwork('git.push')}>Push</button>
      </div>
      <div className="fn-body">
        {snap.st && !snap.st.ok && <div className="fn-err">{snap.st.error}</div>}
        {msg && <div className={msg.startsWith('✓') ? 'fn-ok' : 'fn-err'}>{msg}</div>}

        {/* 变更三区 */}
        {okCtx && (
          <div className="fn-card">
            {groupHead('已暂存', staged.length, staged.length > 0 && (
              <button className="chip" disabled={busy} onClick={() => unstagePaths(staged.map((f) => f.path ?? ''))}>全部退暂存</button>
            ))}
            {fileRows(staged, { canStage: false, canUnstage: true, canDiscard: false })}
            {groupHead('更改', unstaged.length, unstaged.length > 0 && (
              <>
                <button className="chip" disabled={busy} onClick={() => stagePaths(unstaged.map((f) => f.path ?? ''))}>全部暂存</button>
                <button className="chip chip-danger" disabled={busy} onClick={() => discardPaths(unstaged.map((f) => f.path ?? ''))}>全部丢弃</button>
              </>
            ))}
            {fileRows(unstaged, { canStage: true, canUnstage: false, canDiscard: true })}
            {groupHead('未跟踪', untracked.length, untracked.length > 0 && (
              <>
                <button className="chip" disabled={busy} onClick={() => stagePaths(untracked.map((f) => f.path ?? ''))}>全部暂存</button>
                <button className="chip chip-danger" disabled={busy} onClick={() => discardPaths(untracked.map((f) => f.path ?? ''))}>全部丢弃</button>
              </>
            ))}
            {fileRows(untracked, { canStage: true, canUnstage: false, canDiscard: true })}
            {!staged.length && !unstaged.length && !untracked.length && <div className="fn-empty">工作区干净</div>}
            {/* 选中批量条 */}
            {sel.size > 0 && (
              <div className="git-selbar">
                <span className="fn-meta">已选 {sel.size}</span>
                <button className="chip" disabled={busy} onClick={() => stagePaths([...sel])}>Stage</button>
                <button className="chip" disabled={busy} onClick={() => unstagePaths([...sel])}>Unstage</button>
                <button className="chip chip-danger" disabled={busy} onClick={() => discardPaths([...sel])}>Discard</button>
              </div>
            )}
            {/* 提交区 */}
            <textarea
              className="fn-textarea"
              rows={3}
              placeholder="提交信息（先暂存文件）"
              value={commitText}
              onChange={(e) => setCommitText(e.target.value)}
            />
            <div className="git-commit-row">
              <span className="fn-meta">{staged.length} 个文件在暂存区</span>
              <span className="grow" />
              <button className="chip chip-primary" disabled={busy || staged.length === 0 || !commitText.trim()} onClick={() => void doCommit()}>
                <Icon name="branch" size={13} /> Commit
              </button>
            </div>
          </div>
        )}

        {/* 分支 */}
        {okCtx && (
          <div className="fn-card">
            {groupHead('分支', snap.br.length, null)}
            <div className="git-branchbar">
              {snap.br.map((b) => (
                <button
                  key={b.name}
                  className={`chip${b.current ? ' chip-on' : ''}`}
                  disabled={busy || b.current}
                  title={b.upstream ? `上游 ${b.upstream}` : '无上游'}
                  onClick={() => doCheckout(b.name ?? '')}
                >
                  {b.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 历史 */}
        {okCtx && (
          <div className="fn-card">
            {groupHead('最近提交', snap.log.length, null)}
            {snap.log.map((e) => (
              <div key={`${e.short_sha}-${e.subject}`} className="git-row" title={e.subject}>
                <span className="git-sha">{e.short_sha}</span>
                <span className="git-path">{e.subject}</span>
                <span className="grow" />
                <span className="fn-meta">{e.author} · {e.date}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
