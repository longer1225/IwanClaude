// Composer：输入卡 + 全量运行参数选择器（审批/模型/努力/引擎）+ 上下文水位 + 附件
//
// 【学习要点】1) 每个 chip 都是"当前值 + Popover 单选"的受控控件，值来自
// store.sessionAttrs（真源是 daemon 的 *_changed 事件回显，本地乐观更新只防闪烁）。
// 2) 诚实边界：set_model / set_effort 在 daemon 侧写的是【全局】PermissionManager
// 单例（app.py handler 如此），并不只影响本会话——tooltip 明说，不假装会话级隔离。
// 3) 六引擎全列，一个不少：这是本项目的核心能力面，GUI 不许提供"降级视图"。
// 4) 运行中 Enter = 转向（run.steer 排队注入），发送键变停止键——同一输入框两种
// 语义，用 placeholder 与图标形状明示，与 Codex 的排队输入同型。
import { useEffect, useRef, useState } from 'react'
import {
  useStore,
  sendMessage,
  steerRun,
  cancelRun,
  setPermissionMode,
  setModelPreset,
  setEffortLevel,
  setEngine,
  compactSession
} from '../store'
import { useGui, baseName } from '../guiHelpers'
import { Popover, MenuItem } from './Popover'
import { Icon } from './Icon'

// 审批五态（对齐 daemon PERMISSION_MODES；描述语给用户讲清每种态的行为差异）
const PERM_MODES: Array<[string, string, string]> = [
  ['default', '默认', '每次工具调用都询问'],
  ['acceptEdits', '接受编辑', '项目内文件编辑免批，其余仍询问'],
  ['plan', '规划', '只读研究，不允许任何写操作'],
  ['auto', '自动', '由分类器判断风险，低风险免批'],
  ['bypassPermissions', '全部放行', '跳过审批（信任目录内才建议）']
]
const MODEL_PRESETS: Array<[string, string]> = [
  ['fast', '快速'],
  ['balanced', '均衡'],
  ['powerful', '强力']
]
const EFFORTS: Array<[string, string]> = [
  ['minimal', '极简'],
  ['low', '低'],
  ['medium', '中'],
  ['high', '高'],
  ['max', '极高']
]
const ENGINES: Array<[string, string]> = [
  ['auto', '自动选择'],
  ['legacy', 'legacy 单循环'],
  ['langgraph', '图状态机'],
  ['plan_execute', '规划-执行'],
  ['debate', '多模型辩论'],
  ['pipeline', '流水线']
]

// 一个参数 chip：label=当前值中文标签，items=单选表，pick=异步提交（失败回弹提示）
function ParamChip(props: {
  icon: 'shield' | 'sparkle' | 'zap' | 'cpu'
  value: string
  items: Array<[string, string]>
  disabled?: boolean
  hint: string
  onPick: (v: string) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  return (
    <Popover
      className="chip-pop"
      anchor={(open) => (
        <button
          className={`chip${props.disabled ? ' chip-disabled' : ''}${busy ? ' chip-busy' : ''}`}
          title={props.disabled ? '先创建/选择一个会话' : props.hint}
          onClick={() => !props.disabled && open()}
        >
          <Icon name={props.icon} size={12} />
          {props.value}
        </button>
      )}
    >
      {(close) => (
        <>
          <div className="pop-title">{props.hint}</div>
          {props.items.map(([v, label]) => (
            <MenuItem
              key={v}
              label={label}
              hint={v}
              active={props.value === label || props.value === v}
              onClick={() => {
                close()
                setBusy(true)
                void props
                  .onPick(v)
                  .catch((err) => window.alert(`切换失败：${String(err instanceof Error ? err.message : err)}`))
                  .finally(() => setBusy(false))
              }}
            />
          ))}
        </>
      )}
    </Popover>
  )
}

export function Composer({ booted }: { booted: boolean }) {
  const selectedCwd = useStore((s) => s.selectedCwd)
  const setCwd = useStore((s) => s.setCwd)
  const activeSid = useStore((s) => s.activeSid)
  const running = useStore((s) => (activeSid ? s.runningSids.has(activeSid) : false))
  const attrs = useStore((s) => (activeSid ? s.sessionAttrs[activeSid] : undefined))
  const ctxPct = useStore((s) => (activeSid ? s.contextPct[activeSid] ?? 0 : 0))
  const status = useStore((s) => s.status)
  const sessionProject = useGui((s) => s.sessionProject)
  const metaCwd = useStore((s) => s.metaCwd)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const taRef = useRef<HTMLTextAreaElement>(null)

  // 会话有效目录（归属链与侧栏同一函数）：空态=新会话将用的目录，会话态=展示用
  const cwdForHint = activeSid
    ? sessionProject[activeSid] ?? metaCwd[activeSid] ?? selectedCwd ?? undefined
    : selectedCwd ?? undefined

  // 变为可用（连接完成）后自动聚焦输入框——Codex 同款开屏肌肉记忆
  useEffect(() => {
    if (booted && status === 'connected') taRef.current?.focus()
  }, [booted, status])

  // 自增高：每次输入重置为 auto 再按 scrollHeight 撑开（上限 180px）
  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 180)}px`
  }, [text])

  // 选目录：走主进程原生对话框；取消返回 null 保持原值
  const pickProject = async (): Promise<void> => {
    const dir = await window.iwan.pickDirectory()
    if (dir) setCwd(dir)
  }

  // 附件：单选文件对话框 → 把绝对路径插入光标处（模型侧走"按路径读取"的工具链）
  const attachFile = async (): Promise<void> => {
    const p = await window.iwan.pickFile()
    if (!p) return
    const ta = taRef.current
    const pos = ta?.selectionStart ?? text.length
    const snippet = ` 文件:${p} `
    const next = `${text.slice(0, pos)}${snippet}${text.slice(pos)}`
    setText(next.replace(/ {2,}/g, ' '))
    requestAnimationFrame(() => {
      const el = taRef.current
      if (el) { el.focus(); el.selectionStart = el.selectionEnd = Math.min(pos + snippet.length, next.length) }
    })
  }

  // 提交：空闲=发消息；运行中=把输入当转向指令排队
  const doSend = async (): Promise<void> => {
    const value = text.trim()
    if (!value || sending || status !== 'connected') return
    setSending(true)
    setText('')
    try {
      if (running && activeSid) await steerRun(activeSid, value)
      else await sendMessage(value)
    } catch (err) {
      window.alert(`发送失败：${String(err instanceof Error ? err.message : err)}`)
    } finally {
      setSending(false)
      taRef.current?.focus()
    }
  }

  const onKeyDown = (e: React.KeyboardEvent): void => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void doSend()
    }
  }

  // 当前值 → 中文标签（找不到就原样显示：防 daemon 回了没见过的枚举值时界面空白）
  const labelOf = (table: Array<[string, string]>, v?: string): string =>
    table.find(([x]) => x === v)?.[1] ?? v ?? (table === EFFORTS ? '中' : table === MODEL_PRESETS ? '均衡' : '默认')
  const permLabel = PERM_MODES.find(([v]) => v === (attrs?.permissionMode ?? 'default'))?.[1] ?? '默认'
  const modelLabel = `${labelOf(MODEL_PRESETS, attrs?.modelPreset ?? 'balanced')}${attrs?.model ? ` · ${attrs.model.split('/').pop()}` : ''}`
  const effortLabel = labelOf(EFFORTS, attrs?.effortLevel ?? 'medium')
  const engineLabel = labelOf(ENGINES, attrs?.engine ?? 'auto')

  return (
    <div className="composer-wrap">
      <div className="composer">
        <div className="proj-row" onClick={() => void pickProject()} title={cwdForHint ?? '选择项目目录'}>
          <span className="nav-icon"><Icon name="folder" size={13} /></span>
          <span>{cwdForHint ? baseName(cwdForHint) : '选择项目'}</span>
          <span className="proj-path">{cwdForHint ?? ''}</span>
        </div>
        <textarea
          ref={taRef}
          className="input"
          rows={1}
          placeholder={
            !booted || status !== 'connected'
              ? '等待 daemon 连接…'
              : running
                ? '运行中——输入即转向，Enter 排队注入'
                : activeSid
                  ? '继续这个任务…'
                  : '随心输入（回车发送，先选好项目目录）'
          }
          value={text}
          disabled={!booted || status !== 'connected'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="toolbar">
          <button
            className="chip icon-chip"
            title="添加文件（插入路径引用）"
            disabled={!booted}
            onClick={() => void attachFile()}
          >
            <Icon name="plus" size={13} />
          </button>
          <ParamChip
            icon="shield"
            value={permLabel}
            items={PERM_MODES.map(([v, l]) => [v, `${l}`])}
            hint="审批模式（本会话）"
            disabled={!activeSid}
            onPick={(v) => (activeSid ? setPermissionMode(activeSid, v) : Promise.resolve())}
          />
          <ParamChip
            icon="sparkle"
            value={modelLabel}
            items={MODEL_PRESETS}
            hint="模型预设 · 注意：daemon 侧为全局切换，影响后续所有运行"
            disabled={!activeSid}
            onPick={(v) => (activeSid ? setModelPreset(activeSid, v) : Promise.resolve())}
          />
          <ParamChip
            icon="zap"
            value={effortLabel}
            items={EFFORTS}
            hint="努力等级 · 注意：daemon 侧为全局切换"
            disabled={!activeSid}
            onPick={(v) => (activeSid ? setEffortLevel(activeSid, v) : Promise.resolve())}
          />
          <ParamChip
            icon="cpu"
            value={engineLabel}
            items={ENGINES}
            hint="执行引擎（本会话下一轮生效）"
            disabled={!activeSid}
            onPick={(v) => (activeSid ? setEngine(activeSid, v) : Promise.resolve())}
          />
          <span className="grow" />
          {ctxPct > 0 && (
            <button
              className={`ctx-meter${ctxPct > 80 ? ' ctx-hot' : ''}`}
              title={`上下文占用 ${ctxPct.toFixed(0)}%——点击立即压缩`}
              onClick={() => {
                if (activeSid && window.confirm('压缩上下文？会将早期对话折叠为摘要。'))
                  void compactSession(activeSid).catch((err) => window.alert(String(err)))
              }}
            >
              <span className="ctx-bar" style={{ width: `${Math.min(ctxPct, 100)}%` }} />
              <span className="ctx-text">{ctxPct.toFixed(0)}%</span>
            </button>
          )}
          {running ? (
            <button
              className="send send-stop"
              title="停止当前运行"
              onClick={() => activeSid && void cancelRun(activeSid).catch((err) => console.warn(String(err)))}
              aria-label="停止"
            >
              <Icon name="stop" size={15} />
            </button>
          ) : (
            <button
              className="send"
              onClick={() => void doSend()}
              disabled={!booted || status !== 'connected'}
              aria-label="发送"
            >
              <Icon name="send" size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
