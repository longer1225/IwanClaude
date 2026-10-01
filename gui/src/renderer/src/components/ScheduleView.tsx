// 定时任务视图：schedule.list/create/update/delete/run_now 的完整操作面
//
// 【学习要点】1) 任务表数据放 store（schedule.fired 事件到达时要全局刷新），
// 表单等瞬时状态留在组件本地——"会被事件掀动的"进 store，"窗口自用的"不进。
// 2) 三档 kind 的 spec 输入按 kind 变形（分钟数/时刻/星期+时刻），校验在
// daemon 兜底，前端只做输入形态约束——同一份规则只该有一处权威实现。
// 3) run_now 后拉回对话视图：触发的是另一个 one_shot 会话，侧栏里那条新会话
// 的运行灯就是最好的"已执行"回执，不需要再造成功 toast。
import { useEffect, useState } from 'react'
import { rpc } from '../rpc'
import { refreshSchedules, useStore } from '../store'
import { baseName, useGui } from '../guiHelpers'
import type { ScheduleOpResult } from '../protocol/types'
import { Icon } from './Icon'

// 三档调度的人话展示
function humanize(kind?: string, spec?: string): string {
  if (kind === 'every_minutes') return `每 ${spec} 分钟`
  if (kind === 'daily') return `每天 ${spec}`
  const w = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
  const [d, hm] = (spec ?? '').split('@')
  return `每${w[Number(d)] ?? `周${d}`} ${hm ?? ''}`
}

// ISO 时刻 → 本地短展示（今天只显示时分，跨日补月-日）
function whenShort(iso?: string): string {
  if (!iso) return '—'
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return iso
  const hm = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`
  const now = new Date()
  return t.toDateString() === now.toDateString() ? hm : `${t.getMonth() + 1}-${t.getDate()} ${hm}`
}

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

export function ScheduleView() {
  const tasks = useStore((s) => s.scheduleTasks)
  const selectedCwd = useStore((s) => s.selectedCwd)
  const guiSet = useGui((s) => s.set)
  const [msg, setMsg] = useState('')
  // 新建表单
  const [formOpen, setFormOpen] = useState(false)
  const [name, setName] = useState('')
  const [cwd, setCwd] = useState(selectedCwd ?? '')
  const [prompt, setPrompt] = useState('')
  const [kind, setKind] = useState('every_minutes')
  const [mins, setMins] = useState('5')
  const [dailyAt, setDailyAt] = useState('09:00')
  const [wday, setWday] = useState('0')
  const [wtime, setWtime] = useState('09:00')

  useEffect(() => {
    void refreshSchedules()
  }, [])

  // 按档拼 spec（与后端 validate_spec 同一口径）
  const spec = kind === 'every_minutes' ? mins : kind === 'daily' ? dailyAt : `${wday}@${wtime}`

  // 发一个写操作并回显结果（失败原文进 msg 条）
  const op = async (method: string, params: Record<string, unknown>): Promise<ScheduleOpResult | null> => {
    setMsg('')
    try {
      const r = await rpc<ScheduleOpResult>(method, params)
      if (!r.ok) setMsg(`✗ ${r.error}`)
      void refreshSchedules()
      return r
    } catch (e) {
      setMsg(`✗ ${String(e)}`)
      return null
    }
  }

  const create = async (): Promise<void> => {
    const r = await op('schedule.create', { name, cwd, prompt, kind, spec })
    if (r?.ok) {
      setFormOpen(false)
      setName('')
      setPrompt('')
    }
  }

  const runNow = async (id: string): Promise<void> => {
    const r = await op('schedule.run_now', { id })
    if (r?.ok) {
      // 触发的是另一个 one_shot 会话：拉回对话视图，侧栏里那条新会话自己亮运行灯
      guiSet({ mainView: 'chat' })
    }
  }

  return (
    <div className="fn-view">
      <div className="fn-head">
        <span className="fn-title">定时任务</span>
        <span className="grow" />
        <button className="chip" onClick={() => void refreshSchedules()} title="重新拉取任务表">
          <Icon name="refresh" size={13} /> 刷新
        </button>
        <button className="chip" onClick={() => setFormOpen((v) => !v)}>
          <Icon name="plus" size={13} /> 新建任务
        </button>
      </div>
      <div className="fn-body">
        {msg && <div className={msg.startsWith('✓') ? 'fn-ok' : 'fn-err'}>{msg}</div>}
        {formOpen && (
          <div className="fn-card fn-form">
            <div className="fn-form-row">
              <input className="fn-input fn-input-slim" placeholder="任务名（可选）" value={name} onChange={(e) => setName(e.target.value)} />
              <input className="fn-input" placeholder="工作目录（留空=daemon 默认）" value={cwd} readOnly title={cwd || 'daemon 默认目录'} />
              <button
                className="chip"
                onClick={() => {
                  void window.iwan.pickDirectory().then((d) => d && setCwd(d))
                }}
              >
                <Icon name="folder" size={13} />
              </button>
            </div>
            <textarea className="fn-textarea" rows={3} placeholder="到点执行的指令（会作为一条消息发给新会话）" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            <div className="fn-form-row">
              <select className="fn-input fn-input-slim" value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="every_minutes">每 N 分钟</option>
                <option value="daily">每天定时</option>
                <option value="weekly">每周定时</option>
              </select>
              {kind === 'every_minutes' && (
                <input className="fn-num" type="number" min={1} value={mins} onChange={(e) => setMins(e.target.value)} title="分钟数" />
              )}
              {kind === 'daily' && <input className="fn-num" type="time" value={dailyAt} onChange={(e) => setDailyAt(e.target.value)} />}
              {kind === 'weekly' && (
                <>
                  <select className="fn-input fn-input-slim" value={wday} onChange={(e) => setWday(e.target.value)}>
                    {WEEKDAYS.map((w, i) => (
                      <option key={w} value={String(i)}>{w}</option>
                    ))}
                  </select>
                  <input className="fn-num" type="time" value={wtime} onChange={(e) => setWtime(e.target.value)} />
                </>
              )}
              <span className="fn-meta">→ {humanize(kind, spec)}</span>
              <span className="grow" />
              <button className="chip" onClick={() => void create()} disabled={!prompt.trim()}>
                <Icon name="check" size={13} /> 保存
              </button>
            </div>
          </div>
        )}
        {!tasks.length && !formOpen && (
          <div className="fn-empty">还没有定时任务。点「新建任务」，到点会自动开一个一次性会话执行指令。</div>
        )}
        {tasks.map((t) => (
          <div key={t.id} className={`fn-card fn-card-task${t.enabled ? '' : ' fn-off'}`}>
            <div className="fn-card-head">
              <span className={`dot dot-${t.enabled ? 'connected' : ''}`} />
              <span className="fn-card-name">{t.name}</span>
              <span className="fn-tag">{humanize(t.kind, t.spec)}</span>
              {t.cwd && <span className="fn-tag" title={t.cwd}>{baseName(t.cwd)}</span>}
              <span className="grow" />
              <button
                className="icon-btn tiny"
                title={t.enabled ? '停用' : '启用'}
                onClick={() => void op('schedule.update', { id: t.id, enabled: !t.enabled })}
              >
                <Icon name={t.enabled ? 'stop' : 'zap'} size={13} />
              </button>
              <button className="icon-btn tiny" title="立即运行一次" onClick={() => void runNow(t.id)}>
                <Icon name="send" size={13} />
              </button>
              <button
                className="icon-btn tiny"
                title="删除任务"
                onClick={() => {
                  if (window.confirm(`删除定时任务「${t.name}」？`)) void op('schedule.delete', { id: t.id })
                }}
              >
                <Icon name="trash" size={13} />
              </button>
            </div>
            <div className="fn-prompt" title={t.prompt}>{t.prompt}</div>
            <div className="fn-meta-row">
              <span>下次：{whenShort(t.next_due)}</span>
              {/* 【学习要点】"成功"是 daemon 对 fire 回调是否落地的判定（触发成功），
                  不代表 agent run 跑完——两件事混在一个词里，用户会误读成"任务已执行完"。
                  tooltip 把语义摊开并给出查看真实结果的入口（侧栏"定时·"会话） */}
              <span title="成功=任务已触发并创建了会话；运行是否真正跑完，看侧栏「定时·」会话的回复（需审批时会一直等待）">
                上次：{whenShort(t.last_run)}{t.last_result ? ` · ${t.last_result}` : ''}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
