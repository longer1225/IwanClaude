// 右侧面板：文件树（懒加载目录）/ 本任务文件改动与回滚 / run 任务清单
//
// 【学习要点】这是"Electron IDE 怎么列文件"的活教材：目录树【绝不递归全量扫描】，
// 每个文件夹节点展开时才发一次 fs.readdir（经主进程桥 + 路径越界校验），
// 这就是 VS Code explorer 秒开百万文件仓库的原因——树的规模 = 用户展开的规模。
// 变更/任务两个页签则是把 daemon 已有协议（files.changes/restore）与磁盘账本
// （runs/<id>/.tasks/*.json）接进界面：面板是"查看器+触发器"，不是新的数据源。
import { useCallback, useEffect, useState } from 'react'
import { useStore, loadChanges, restoreFiles } from '../store'
import type { FileChange } from '../store'
import { useGui, sessionEffectiveCwd, baseName } from '../guiHelpers'
import type { RightTab } from '../gui'
import { Icon } from './Icon'
import type { IconName } from './Icon'

// 拼接树内相对路径。【学习要点】绝不 `${rel}/${name}` 一把梭：根节点 rel 是空串，
// 会得到 `/src` 这种前导斜杠，主进程 path.resolve(root, '/src') 把它当【盘根绝对路径】
// 解析 → safeJoin 判越界。devtest 第三轮抓到：根下的文件全部打不开。空 rel 时直接用名字。
function kidRel(rel: string, name: string): string {
  return rel ? `${rel}/${name}` : name
}

// 永远跳过的目录（再点展开也不会出现——项目依赖/构建产物不是浏览对象）
const SKIP_DIRS = new Set(['.git', 'node_modules', '__pycache__', '.venv', 'venv', '.pytest_cache', '.mypy_cache', 'dist'])

interface Node {
  name: string
  dir: boolean
}

// 单层目录：展开时才拉子项；失败（权限/删除）就地显示一行错误不留幽灵节点
function TreeDir(props: { root: string; rel: string; depth: number; showHidden: boolean }) {
  const [open, setOpen] = useState(false)
  const [nodes, setNodes] = useState<Node[] | null>(null)
  const [err, setErr] = useState('')

  const toggle = (): void => {
    const next = !open
    setOpen(next)
    if (next && nodes === null && !err) {
      window.iwan
        .readDir(props.root, props.rel)
        .then((list) => {
          const keep = list
            .filter((n) => !SKIP_DIRS.has(n.name))
            .filter((n) => props.showHidden || !n.name.startsWith('.'))
            .sort((a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name, undefined, { numeric: true }))
          setNodes(keep)
        })
        .catch((e) => setErr(String(e instanceof Error ? e.message : e)))
    }
  }

  return (
    <div>
      <div className="tree-row" style={{ paddingLeft: 6 + props.depth * 14 }} onClick={toggle} title={props.rel || props.root}>
        <Icon name={open ? 'chevronDown' : 'chevronRight'} size={12} className="tree-chev" />
        <Icon name={open ? 'folderOpen' : 'folder'} size={13} className="tree-ico" />
        <span className="tree-name">{baseName(props.rel) || baseName(props.root) || props.root}</span>
      </div>
      {open && err && <div className="tree-err" style={{ paddingLeft: 20 + props.depth * 14 }}>无法读取：{err}</div>}
      {open && nodes?.map((n) =>
        n.dir ? (
          <TreeDir key={kidRel(props.rel, n.name)} root={props.root} rel={kidRel(props.rel, n.name)} depth={props.depth + 1} showHidden={props.showHidden} />
        ) : (
          <TreeFile key={kidRel(props.rel, n.name)} root={props.root} rel={kidRel(props.rel, n.name)} name={n.name} depth={props.depth} />
        )
      )}
      {open && nodes && nodes.length === 0 && <div className="tree-empty" style={{ paddingLeft: 20 + props.depth * 14 }}>（空）</div>}
    </div>
  )
}

// 叶子文件：点击 → 主进程 readFile → gui.fileView 浮层
function TreeFile(props: { root: string; rel: string; name: string; depth: number }) {
  const setFileView = useGui((s) => s.set)
  const [loading, setLoading] = useState(false)
  const open = (): void => {
    setLoading(true)
    window.iwan
      .readFile(props.root, props.rel)
      .then((f) => setFileView({ fileView: { root: props.root, rel: props.rel, ...f } }))
      .catch((e) => setFileView({ fileView: { root: props.root, rel: props.rel, text: String(e), truncated: false, binary: false, size: 0 } }))
      .finally(() => setLoading(false))
  }
  return (
    <div className="tree-row" style={{ paddingLeft: 6 + props.depth * 14 }} title={props.rel} onClick={() => void open()}>
      <span className="tree-chev" />
      <Icon name={loading ? 'refresh' : 'file'} size={13} className={`tree-ico${loading ? ' spin' : ''}`} />
      <span className="tree-name">{props.name}</span>
    </div>
  )
}

// 页签一：文件树
function FilesTab({ root }: { root: string }) {
  const [showHidden, setShowHidden] = useState(false)
  return (
    <div className="pane">
      <div className="pane-tools">
        <button className={`mini-toggle${showHidden ? ' on' : ''}`} onClick={() => setShowHidden((v) => !v)} title="显示/隐藏点文件">
          .* 点文件
        </button>
        <span className="grow" />
        <span className="pane-path" title={root}>{root}</span>
      </div>
      <div className="pane-scroll">
        <TreeDir root={root} rel="" depth={0} showHidden={showHidden} />
      </div>
    </div>
  )
}

// 页签二：文件改动（files.changes）+ 回滚（files.restore）
function ChangesTab({ sid }: { sid: string }) {
  const [rows, setRows] = useState<FileChange[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const refresh = useCallback((): void => {
    setBusy(true)
    loadChanges(sid)
      .then(setRows)
      .catch((e) => {
        setRows([])
        setNote(String(e instanceof Error ? e.message : e))
      })
      .finally(() => setBusy(false))
  }, [sid])

  useEffect(() => {
    refresh()
  }, [refresh])

  const doRestore = (paths?: string[]): void => {
    setBusy(true)
    restoreFiles(sid, paths)
      .then((r) => {
        const ok = (r.results ?? []).filter((x) => x.status === 'restored').length
        setNote(`恢复完成：${ok}/${r.results?.length ?? 0}`)
        refresh()
      })
      .catch((e) => setNote(`恢复失败：${String(e instanceof Error ? e.message : e)}`))
      .finally(() => setBusy(false))
  }

  return (
    <div className="pane">
      <div className="pane-tools">
        <button className="mini-btn" onClick={refresh} disabled={busy} title="重新查询改动清单">
          <Icon name="refresh" size={12} /> 刷新
        </button>
        <span className="grow" />
        {rows && rows.length > 0 && (
          <button className="mini-btn danger" onClick={() => doRestore()} disabled={busy}>全部恢复</button>
        )}
      </div>
      <div className="pane-scroll">
        {rows === null && <div className="pane-hint">查询中…</div>}
        {note && <div className="pane-note">{note}</div>}
        {rows?.length === 0 && <div className="pane-hint">最近一次 run 没有改动过文件。</div>}
        {(rows ?? []).map((c) => (
          <div key={c.path} className="chg-row">
            <span className={`chg-badge${c.was_new ? ' chg-add' : ''}`}>{c.was_new ? 'A' : 'M'}</span>
            <span className="chg-path" title={c.path}>{c.path}</span>
            {c.conflict && <span className="chg-conflict" title="与工作区现状冲突">冲突</span>}
            <button className="mini-btn" disabled={busy || !c.captured} title={c.captured ? '恢复到改动前' : '无快照可恢复'} onClick={() => doRestore([c.path])}>
              <Icon name="restore" size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

interface TaskItem {
  id?: number
  subject?: string
  description?: string
  status?: string
  blocked_by?: number[]
}

// 页签三：任务清单（读磁盘 .tasks/task_N.json——plan_execute/langgraph 引擎的产物）
function TasksTab({ sid }: { sid: string }) {
  const lastRunId = useStore((s) => s.lastRunId[sid])
  const metaRuns = useStore((s) => s.metaRunIds[sid])
  const runId = lastRunId ?? metaRuns?.[metaRuns.length - 1]
  const [tasks, setTasks] = useState<TaskItem[] | null>(null)

  useEffect(() => {
    setTasks(null)
    if (!runId) return
    window.iwan.runTasks(sid, runId).then((t) => setTasks(t as TaskItem[])).catch(() => setTasks([]))
  }, [sid, runId])

  const chip = (s?: string): IconName =>
    s === 'completed' ? 'check' : s === 'in_progress' ? 'zap' : s === 'blocked' || s === 'blocked_by' ? 'alert' : 'clock'
  return (
    <div className="pane">
      <div className="pane-scroll">
        {!runId && <div className="pane-hint">还没有运行记录——跑一轮任务后这里会列出计划步骤。</div>}
        {runId && tasks === null && <div className="pane-hint">读取任务清单…</div>}
        {tasks?.length === 0 && <div className="pane-hint">本次 run 未产生任务计划（单轮闲聊或 simple 路径）。</div>}
        {(tasks ?? []).map((t, i) => (
          <div key={t.id ?? i} className={`task-row st-${t.status ?? 'pending'}`}>
            <span className="task-ico"><Icon name={chip(t.status)} size={13} /></span>
            <div className="task-body">
              <div className="task-subject">{t.subject ?? `任务 ${t.id ?? i}`}</div>
              {t.description && <div className="task-desc">{t.description}</div>}
              {!!t.blocked_by?.length && <div className="task-blocked">等待：{t.blocked_by.join(', ')}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// 面板主体：三页签 + 收起钮
export function RightPanel() {
  const activeSid = useStore((s) => s.activeSid)
  const selectedCwd = useStore((s) => s.selectedCwd)
  const sessions = useStore((s) => s.sessions)
  const metaCwd = useStore((s) => s.metaCwd)
  const gui = useGui()
  const fallbackCwd = activeSid ? sessions.find((x) => x.id === activeSid)?.cwd : undefined
  const root = activeSid ? sessionEffectiveCwd(activeSid, metaCwd, gui.sessionProject, fallbackCwd) : selectedCwd ?? undefined

  const tabs: Array<[RightTab, string, IconName]> = [
    ['files', '文件', 'folder'],
    ['changes', '变更', 'list'],
    ['tasks', '任务', 'check']
  ]

  return (
    <aside className="right-panel">
      <div className="rp-head">
        {tabs.map(([id, label, icon]) => (
          <button
            key={id}
            className={`rp-tab${gui.rightTab === id ? ' active' : ''}`}
            onClick={() => gui.set({ rightTab: id })}
          >
            <Icon name={icon} size={13} /> {label}
          </button>
        ))}
        <span className="grow" />
        <button className="icon-btn tiny" title="收起面板" onClick={() => gui.set({ rightOpen: false })}>
          <Icon name="panelRight" size={14} />
        </button>
      </div>
      <div className="rp-body">
        {gui.rightTab === 'files' &&
          (root ? (
            <FilesTab root={root} />
          ) : (
            <div className="pane-hint pad">
              <p>还没有可浏览的项目目录——选一个：</p>
              <button
                className="mini-btn"
                style={{ marginTop: 8 }}
                onClick={async () => {
                  const dir = await window.iwan.pickDirectory()
                  if (dir) useStore.getState().setCwd(dir)
                }}
              >
                <Icon name="folderPlus" size={12} /> 选择项目目录
              </button>
            </div>
          ))}
        {gui.rightTab === 'changes' &&
          (activeSid ? <ChangesTab key={activeSid} sid={activeSid} /> : <div className="pane-hint pad">打开一个会话后查看它改动过的文件。</div>)}
        {gui.rightTab === 'tasks' &&
          (activeSid ? <TasksTab key={activeSid} sid={activeSid} /> : <div className="pane-hint pad">打开一个会话后查看任务计划。</div>)}
      </div>
    </aside>
  )
}

// 文件预览浮层（覆盖整个应用区，Esc/× 关闭）
export function FileViewer() {
  const fv = useGui((s) => s.fileView)
  const setG = useGui((s) => s.set)
  useEffect(() => {
    if (!fv) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setG({ fileView: null })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fv, setG])
  if (!fv) return null
  return (
    <div className="fv-overlay" onClick={() => setG({ fileView: null })}>
      <div className="fv-card" onClick={(e) => e.stopPropagation()}>
        <div className="fv-head">
          <Icon name="file" size={13} />
          <span className="fv-path" title={fv.root + '/' + fv.rel}>{fv.rel || baseName(fv.root)}</span>
          <span className="dim">{fv.binary ? '二进制文件' : `${(fv.size / 1024).toFixed(1)} KB`}</span>
          <span className="grow" />
          <button className="icon-btn tiny" onClick={() => setG({ fileView: null })} title="关闭 (Esc)">
            <Icon name="x" size={13} />
          </button>
        </div>
        {fv.binary ? (
          <div className="fv-text fv-bin">二进制文件（前 8KB 含 NUL 字节），不提供文本预览。</div>
        ) : (
          <>
            {fv.truncated && <div className="fv-note">文件较大，仅预览前 256 KB。</div>}
            <pre className="fv-text">{fv.text}</pre>
          </>
        )}
      </div>
    </div>
  )
}
