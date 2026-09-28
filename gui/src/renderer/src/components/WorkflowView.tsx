// 工作流视图（W2）：列表 / 详情（分层图 + 运行历史）/ 编辑——单组件三态
//
// 【学习要点】1) 数据面（workflows/wfRuns/wfLiveRunId）放 store——workflow.*
// 事件会从远处掀它们；视图模式与表单草稿留组件本地，"会被事件掀动的"进
// store、"窗口自用的"不进（ScheduleView 同款分界）。2) 图校验不重复发明：
// 环/空指令/重名全部交 daemon 权威裁决，错误文案原样回显 .fn-err——同一份
// 规则只该有一处权威实现，前端造轮子必漂移。3) 不画 SVG 连线：layers 是
// 服务端拓扑排序的产物，横向分列摆放本身就在表达依赖方向；节点下挂一行
// 依赖 chip 补足"依赖谁"的信息，免得引一个图布局引擎只为看 5 个方块。
import { useEffect, useState } from 'react'
import { rpc } from '../rpc'
import { refreshWfRuns, refreshWorkflows, useStore } from '../store'
import type {
  WorkflowInfo,
  WorkflowNodeRunInfo,
  WorkflowOpResult,
  WorkflowRunInfo
} from '../protocol/types'
import { Icon } from './Icon'

// 编辑表单里的节点草稿：key 只给 React 列表用（改名不丢焦点/勾选）
interface DraftNode {
  key: number
  name: string
  prompt: string
  depends_on: string[]
}

type Mode = 'list' | 'detail' | 'edit'

// run/last_status 终态的人话标签（daemon 落 success|failed|interrupted|running）
const RUN_LABEL: Record<string, string> = {
  running: '运行中', success: '成功', failed: '失败', interrupted: '中断', cancelled: '取消'
}
// 节点态标签（骨架 pending → 事件推 running → 终态 ok/fail）
const NODE_LABEL: Record<string, string> = { pending: '待跑', running: '运行中', ok: '完成', fail: '失败' }

let draftSeq = 1

// ISO 时刻 → 本地短展示（与 ScheduleView 同口径：今天只显示时分）
function whenShort(iso?: string): string {
  if (!iso) return '—'
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return iso
  const hm = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`
  const now = new Date()
  return t.toDateString() === now.toDateString() ? hm : `${t.getMonth() + 1}-${t.getDate()} ${hm}`
}

// run 状态 → 色点 class（复用状态灯那套 token，不另发明配色）
function runDot(status?: string): string {
  if (status === 'success') return 'dot dot-connected'
  if (status === 'failed' || status === 'cancelled') return 'dot dot-error'
  if (status === 'running' || status === 'interrupted') return 'dot dot-connecting'
  return 'dot'
}

export function WorkflowView() {
  const workflows = useStore((s) => s.workflows)
  const wfRuns = useStore((s) => s.wfRuns)
  const wfLiveRunId = useStore((s) => s.wfLiveRunId)
  const [mode, setMode] = useState<Mode>('list')
  const [selId, setSelId] = useState('')
  const [msg, setMsg] = useState('')
  const [openRun, setOpenRun] = useState('')
  // 编辑草稿（editId 空=新建）
  const [editId, setEditId] = useState('')
  const [dName, setDName] = useState('')
  const [dDesc, setDDesc] = useState('')
  const [dNodes, setDNodes] = useState<DraftNode[]>([])

  useEffect(() => {
    void refreshWorkflows()
  }, [])

  const sel = workflows.find((w) => w.id === selId)

  // 打开详情：先拉该流的运行历史（服务端行自带全节点骨架，事件上色有处可贴）
  const openDetail = (id: string): void => {
    setSelId(id)
    setMode('detail')
    setMsg('')
    setOpenRun('')
    void refreshWfRuns(id)
  }

  const backToList = (): void => {
    setMode('list')
    setMsg('')
    useStore.getState().setWfRuns([])
  }

  const startNew = (): void => {
    setEditId('')
    setDName('')
    setDDesc('')
    setDNodes([{ key: draftSeq++, name: '', prompt: '', depends_on: [] }])
    setMsg('')
    setMode('edit')
  }

  const startEdit = (w: WorkflowInfo): void => {
    setEditId(w.id)
    setDName(w.name ?? '')
    setDDesc(w.description ?? '')
    setDNodes((w.tasks ?? []).map((t) => ({
      key: draftSeq++, name: t.name, prompt: t.prompt, depends_on: [...(t.depends_on ?? [])]
    })))
    setMsg('')
    setMode('edit')
  }

  // 改名连带更新：其他节点勾选了旧名的，跟随改成新名（否则保存必被
  // daemon 以"依赖不存在"打回——让用户手改第二处是平白的人工同步税）
  const renameNode = (key: number, name: string): void => {
    setDNodes((ns) => {
      const old = ns.find((n) => n.key === key)?.name ?? ''
      return ns.map((n) => n.key === key
        ? { ...n, name }
        : { ...n, depends_on: n.depends_on.map((d) => (d === old ? name : d)) })
    })
  }

  const toggleDep = (key: number, dep: string, on: boolean): void => {
    setDNodes((ns) => ns.map((n) => n.key === key
      ? { ...n, depends_on: on ? [...n.depends_on, dep] : n.depends_on.filter((d) => d !== dep) }
      : n))
  }

  // 发一个写操作并回显（失败原文进 msg 条——daemon 中文文案就是给用户看的）
  const op = async (method: string, params: Record<string, unknown>): Promise<WorkflowOpResult | null> => {
    setMsg('')
    try {
      const r = await rpc<WorkflowOpResult>(method, params)
      if (!r.ok) setMsg(`✗ ${r.error}`)
      return r
    } catch (e) {
      setMsg(`✗ ${String(e)}`)
      return null
    }
  }

  const save = async (): Promise<void> => {
    const tasks = dNodes.map((n) => ({ name: n.name.trim(), prompt: n.prompt.trim(), depends_on: n.depends_on }))
    const r = await op('workflow.save', { id: editId, name: dName.trim(), description: dDesc.trim(), tasks })
    if (!r?.ok) return
    await refreshWorkflows()
    openDetail(r.id ?? editId)
  }

  const del = async (w: WorkflowInfo): Promise<void> => {
    if (!window.confirm(`删除工作流「${w.name}」？运行历史会保留。`)) return
    const r = await op('workflow.delete', { id: w.id })
    if (!r?.ok) return
    await refreshWorkflows()
    if (selId === w.id) backToList()
  }

  // 点运行：RPC 即发即返（run 行已在服务端落好 pending 骨架）；登记 live 后
  // 重拉一次运行历史，此后全靠 workflow.node 事件逐节点上色
  const run = async (id: string): Promise<void> => {
    const r = await op('workflow.run', { id })
    if (!r?.ok) return
    useStore.getState().setWfLiveRun(r.run_id ?? '')
    void refreshWfRuns(id)
  }

  // 图区取色用行：live run 优先（正在上色），否则最新一条历史
  const shown: WorkflowRunInfo | undefined = wfRuns.find((r) => r.id === wfLiveRunId) ?? wfRuns[0]
  const nodeMap = new Map<string, WorkflowNodeRunInfo>()
  for (const n of shown?.nodes ?? []) nodeMap.set(n.node, n)
  const runningHere = wfRuns.some((r) => r.status === 'running')

  // ==================== 编辑态 ====================
  if (mode === 'edit') {
    const named = dNodes.filter((n) => n.name.trim())
    return (
      <div className="fn-view">
        <div className="fn-head">
          <button className="chip" onClick={editId ? () => openDetail(editId) : backToList}>
            <Icon name="chevronLeft" size={13} /> 返回
          </button>
          <span className="fn-title">{editId ? '编辑工作流' : '新建工作流'}</span>
        </div>
        <div className="fn-body">
          {msg && <div className="fn-err">{msg}</div>}
          <div className="fn-card wf-editor">
            <div className="fn-form-row">
              <input className="fn-input" placeholder="工作流名称（可选）" value={dName} onChange={(e) => setDName(e.target.value)} />
              <input className="fn-input" placeholder="备注（可选）" value={dDesc} onChange={(e) => setDDesc(e.target.value)} />
            </div>
            {dNodes.map((n, i) => (
              <div key={n.key} className="wf-node-edit">
                <div className="fn-form-row">
                  <span className="fn-tag">节点 {i + 1}</span>
                  <input className="fn-input wf-ename" placeholder="节点名（如：调研）" value={n.name} onChange={(e) => renameNode(n.key, e.target.value)} />
                  <span className="grow" />
                  <button
                    className="icon-btn tiny"
                    title="删除该节点"
                    disabled={dNodes.length <= 1}
                    onClick={() => setDNodes((ns) => ns.filter((x) => x.key !== n.key))}
                  >
                    <Icon name="trash" size={13} />
                  </button>
                </div>
                <textarea className="fn-textarea wf-eprompt" rows={3} placeholder="节点指令（作为子 Agent 的目标执行；上游产出会自动拼进来）" value={n.prompt} onChange={(e) => setDNodes((ns) => ns.map((x) => x.key === n.key ? { ...x, prompt: e.target.value } : x))} />
                <div className="fn-form-row wf-dep-row">
                  <span className="fn-meta">依赖：</span>
                  {!named.filter((o) => o.key !== n.key).length && <span className="fn-meta dim">（暂无其他命名节点）</span>}
                  {named.filter((o) => o.key !== n.key).map((o) => (
                    <label key={o.key} className="fn-check wf-dep-check">
                      <input
                        type="checkbox"
                        checked={n.depends_on.includes(o.name.trim())}
                        onChange={(e) => toggleDep(n.key, o.name.trim(), e.target.checked)}
                      />
                      {o.name.trim()}
                    </label>
                  ))}
                </div>
              </div>
            ))}
            <div className="fn-form-row">
              <button className="chip" onClick={() => setDNodes((ns) => [...ns, { key: draftSeq++, name: '', prompt: '', depends_on: [] }])}>
                <Icon name="plus" size={13} /> 添加节点
              </button>
              <span className="grow" />
              <button
                className="chip chip-primary"
                disabled={!dNodes.some((n) => n.name.trim() && n.prompt.trim())}
                onClick={() => void save()}
              >
                <Icon name="check" size={13} /> 保存
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ==================== 详情态 ====================
  if (mode === 'detail') {
    if (!sel) {
      return (
        <div className="fn-view">
          <div className="fn-head"><span className="fn-title">工作流</span></div>
          <div className="fn-body"><div className="fn-empty">该工作流不存在（可能已被删除）。<button className="chip" onClick={backToList}>返回列表</button></div></div>
        </div>
      )
    }
    const layers = sel.layers ?? []
    return (
      <div className="fn-view">
        <div className="fn-head">
          <button className="chip" onClick={backToList}><Icon name="chevronLeft" size={13} /> 列表</button>
          <span className="fn-title">{sel.name}</span>
          <span className="grow" />
          <button className="chip" onClick={() => startEdit(sel)}><Icon name="pencil" size={13} /> 编辑</button>
          <button
            className="chip chip-primary wf-run-btn"
            disabled={runningHere}
            title={runningHere ? '已有运行进行中' : '按 DAG 分层依次派子 Agent 执行'}
            onClick={() => void run(sel.id)}
          >
            <Icon name="zap" size={13} /> 运行
          </button>
        </div>
        <div className="fn-body">
          {msg && <div className="fn-err">{msg}</div>}
          {sel.description && <div className="fn-meta">{sel.description}</div>}
          <div className="fn-card">
            <div className="wf-graph">
              {layers.map((names, i) => (
                <div key={i} className="wf-layer">
                  <div className="wf-layer-tag">第 {i + 1} 层</div>
                  {names.map((nm) => {
                    const nd = nodeMap.get(nm)
                    const stt = nd?.status ?? 'pending'
                    const task = sel.tasks?.find((t) => t.name === nm)
                    return (
                      <div key={nm} className={`wf-node wf-node-${stt}`} title={nd?.detail || task?.prompt}>
                        <div className="wf-node-top">
                          <span className="wf-node-name">{nm}</span>
                          <span className="wf-node-state">{NODE_LABEL[stt] ?? stt}</span>
                        </div>
                        {!!task?.depends_on?.length && (
                          <div className="wf-deps">
                            {task.depends_on.map((d) => <span key={d} className="fn-tag">← {d}</span>)}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              ))}
              {!layers.length && <div className="fn-meta">（该定义无分层信息——请先在编辑器保存）</div>}
            </div>
          </div>
          <div className="fn-card wf-drawer">
            <div className="wf-drawer-head">运行历史</div>
            {!wfRuns.length && <div className="fn-meta">还没有跑过。点「运行」开第一枪。</div>}
            {wfRuns.map((r) => {
              const nodes = r.nodes ?? []
              const done = nodes.filter((n) => n.status === 'ok').length
              return (
                <div key={r.id} className="wf-run-block">
                  <div className={`wf-run-row${openRun === r.id ? ' open' : ''}`} onClick={() => setOpenRun(openRun === r.id ? '' : r.id)}>
                    <span className={runDot(r.status)} />
                    <span className="fn-tag">{RUN_LABEL[r.status ?? ''] ?? r.status}</span>
                    <span className="fn-meta">{whenShort(r.started_at)}</span>
                    {!!r.error && <span className="wf-run-err" title={r.error}>{r.error}</span>}
                    <span className="grow" />
                    <span className="fn-meta">{done}/{nodes.length} 节点</span>
                    <Icon name={openRun === r.id ? 'chevronDown' : 'chevronRight'} size={13} />
                  </div>
                  {openRun === r.id && (
                    <div className="wf-run-detail">
                      {nodes.map((n) => (
                        <div key={n.node} className="wf-nrow">
                          <span className={`wf-node-chip wf-node-chip-${n.status ?? 'pending'}`}>{n.node} · {NODE_LABEL[n.status ?? 'pending'] ?? n.status}</span>
                          {n.detail && <pre className="wf-out wf-out-err">{n.detail}</pre>}
                          {n.output && <pre className="wf-out">{n.output}</pre>}
                        </div>
                      ))}
                      {!!r.session_id && <div className="fn-meta">审批卡落在「工作流·{sel.name}」线程</div>}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    )
  }

  // ==================== 列表态 ====================
  return (
    <div className="fn-view">
      <div className="fn-head">
        <span className="fn-title">工作流</span>
        <span className="grow" />
        <button className="chip" onClick={() => void refreshWorkflows()} title="重新拉取定义表">
          <Icon name="refresh" size={13} /> 刷新
        </button>
        <button className="chip chip-primary" onClick={startNew}>
          <Icon name="plus" size={13} /> 新建工作流
        </button>
      </div>
      <div className="fn-body">
        {msg && <div className="fn-err">{msg}</div>}
        {!workflows.length && !msg && (
          <div className="fn-empty">还没有工作流。点「新建工作流」编排第一个 DAG：每个节点是一个子 Agent，按依赖分层执行、上游产出自动接力。</div>
        )}
        {workflows.map((w) => (
          <div key={w.id} className="fn-card wf-card" onClick={() => openDetail(w.id)}>
            <div className="fn-card-head">
              <span className={runDot(w.last_status)} title={RUN_LABEL[w.last_status ?? ''] ?? '尚未运行'} />
              <span className="fn-card-name">{w.name}</span>
              <span className="fn-tag">{w.tasks?.length ?? 0} 节点 · {w.layers?.length ?? 0} 层</span>
              <span className="grow" />
              <button
                className="icon-btn tiny"
                title="立即运行"
                onClick={(e) => { e.stopPropagation(); void run(w.id) }}
              >
                <Icon name="zap" size={13} />
              </button>
              <button
                className="icon-btn tiny"
                title="编辑"
                onClick={(e) => { e.stopPropagation(); startEdit(w) }}
              >
                <Icon name="pencil" size={13} />
              </button>
              <button
                className="icon-btn tiny"
                title="删除"
                onClick={(e) => { e.stopPropagation(); void del(w) }}
              >
                <Icon name="trash" size={13} />
              </button>
            </div>
            {!!w.description && <div className="fn-prompt" title={w.description}>{w.description}</div>}
            <div className="fn-meta-row">
              <span>上次：{RUN_LABEL[w.last_status ?? ''] ?? '未运行'}</span>
              <span>更新：{whenShort(w.updated_at)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
