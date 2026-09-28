// Composer：输入卡 + 全量运行参数选择器（审批/模型/努力/引擎）+ 上下文水位 + 附件
//
// 【学习要点】1) 每个 chip 都是"当前值 + Popover 单选"的受控控件，值来自
// store.sessionAttrs（真源是 daemon 的 *_changed 事件回显，本地乐观更新只防闪烁）。
// 2) 诚实边界：set_model / set_effort 在 daemon 侧写的是【全局】PermissionManager
// 单例（app.py handler 如此），并不只影响本会话——tooltip 明说，不假装会话级隔离。
// 3) 六引擎全列，一个不少：这是本项目的核心能力面，GUI 不许提供"降级视图"。
// 4) 运行中 Enter = 转向（run.steer 排队注入），发送键变停止键——同一输入框两种
// 语义，用 placeholder 与图标形状明示，与 Codex 的排队输入同型。
// 5) 语音 v2 是"会话式流式"：点🎤开听，worklet 按能量 VAD 自动断句，每句
// 转写完增量追加到输入框尾部——边说边出字。转写必有错字，输入框就是校对台；
// 自动直接发送会把听错的词当金口。段与段之间用串行队列排话，防并发 RPC
// 谁先回天定、回填文字乱序拼接。
import { Fragment, useEffect, useRef, useState } from 'react'
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
import { rpc } from '../rpc'
import type { SpeechTranscribeResult } from '../protocol/types'
import { VoiceRecorder, downsample, pcmToB64, TARGET_RATE } from '../voiceCapture'
import type { VoiceCapture } from '../voiceCapture'
import { Popover, MenuItem } from './Popover'
import { Icon } from './Icon'

// 审批档位呈现层（对标 Codex 2026"审批模式收敛三档"的教训）：daemon 五态内核
// 与 deny→ask→allow 评估序一个不动，这里只是把用户词汇从"五种模式名"改成
// "三档意图 + 高级收纳"。语义映射：谨慎=default（该问就问的安全基线）、
// 顺滑=acceptEdits、全自动=bypassPermissions；plan/auto 归"高级"。
// 第三条=行为描述（下拉里给足信息，chip 上只留两字词）
const PERM_MODES: Array<[string, string, string]> = [
  ['default', '谨慎', '该问就问：每次工具调用按规则决策（安全基线）'],
  ['acceptEdits', '顺滑', '编辑免批：项目内文件编辑不再逐条确认，其余照问'],
  ['bypassPermissions', '全自动', '跳过确认层：deny 规则与强制 ask 仍然生效'],
  ['plan', '高级·规划', '只读研究：任何写操作都被拒绝'],
  ['auto', '高级·分类器', '分类器模型判断风险，低风险免批']
]
// 前 3 项是日常档，第 4 项起归"高级"——下拉里的分隔线插在这个下标前
const PERM_DIVIDER = 3
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

// 一个参数 chip：label=当前值中文标签，items=单选表（第三元=行为描述，下拉里
// 当 hint 显示；缺省回退显示原始值），dividerBefore=分隔线下标（"高级"分组），
// pick=异步提交（失败回弹提示）
function ParamChip(props: {
  icon: 'shield' | 'sparkle' | 'zap' | 'cpu'
  value: string
  items: Array<[string, string, string?]>
  dividerBefore?: number
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
          {props.items.map(([v, label, desc], idx) => (
            <Fragment key={v}>
              {props.dividerBefore === idx && (
                <div className="pop-sep">高级 · 五态内核原样保留</div>
              )}
              <MenuItem
                label={label}
                hint={desc ?? v}
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
            </Fragment>
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
  // 语音会话两态机：idle↔rec（v2 会话式：rec 期间用户持续说话，段段入队，
  // 不存在 V1 那种整钮 busy 禁用态——你还在说呢，凭什么把按钮焊死）
  const [voice, setVoice] = useState<'idle' | 'rec'>('idle')
  const [voiceErr, setVoiceErr] = useState('')
  const [segCount, setSegCount] = useState(0) // 已回填的句子数（.voice-seg 的显示值+devtest 锚点）
  const [vadCount, setVadCount] = useState(0) // VAD 断出的段数镜像（渲染用；判据在 segSeenRef）
  const [segBusy, setSegBusy] = useState(false) // 当前有段正在转写（轻量指示，不拦点击）
  const recRef = useRef<VoiceRecorder | null>(null)
  // 串行转写队列：段 A 的 RPC 没回来就不发段 B——并发回话先后无定序，
  // 回填会拼出"错乱的句接"；队列让文字顺序=说话顺序
  const queueRef = useRef<Promise<void>>(Promise.resolve())
  // 本场会话 VAD 断出的段数（含空文本段）：区别于 segCount（有字的段），
  // 零段判定"整段没检测到人声"用它
  const segSeenRef = useRef(0)

  // 会话有效目录（归属链与侧栏同一函数）：空态=新会话将用的目录，会话态=展示用
  const cwdForHint = activeSid
    ? sessionProject[activeSid] ?? metaCwd[activeSid] ?? selectedCwd ?? undefined
    : selectedCwd ?? undefined

  // 变为可用（连接完成）后自动聚焦输入框——Codex 同款开屏肌肉记忆
  useEffect(() => {
    if (booted && status === 'connected') taRef.current?.focus()
  }, [booted, status])

  // 卸载时释放麦克风：不拆 track = Windows 隐私指示灯常亮，用户只当我们偷听
  useEffect(() => () => recRef.current?.cancel(), [])

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

  // 单段转写：段 PCM→16k→base64→speech.transcribe→文本追加到输入框尾部。
  // 失败只记 voiceErr 不打断会话——第 3 句网断了，第 4 句还能转
  const transcribeSegment = async (cap: VoiceCapture): Promise<void> => {
    setSegBusy(true)
    try {
      const b64 = pcmToB64(downsample(cap.frames, cap.sampleRate))
      const res = await rpc<SpeechTranscribeResult>('speech.transcribe', {
        audio_b64: b64,
        sample_rate: TARGET_RATE
      })
      if (!res.ok) {
        setVoiceErr(`这句转写失败：${res.error}`)
      } else if (res.text) {
        setVoiceErr('')
        const t = res.text
        setText((prev) => (prev.trim() ? `${prev} ${t}` : t))
        setSegCount((c) => c + 1)
      }
      // ok 且空文本：whisper 把呼吸/杂音判成了无声——静默丢弃，不弹"没听清"
      // 打断说话节奏（会话级的"整场没声"由停止路径统一判定）
    } catch (e) {
      setVoiceErr(`转写出错：${String(e instanceof Error ? e.message : e)}`)
    } finally {
      setSegBusy(false)
    }
  }

  // 段入队：进串行 promise 链；捕获异常防一段炸链、后段全哑。
  // vadCount 镜像段数给计数条——"断了几段"和"进了几句"分开显示，
  // 才能一眼区分"VAD 没断句"和"whisper 判了非人声"（UI=9 纯音场景就靠这个）
  const enqueueSegment = (cap: VoiceCapture): void => {
    segSeenRef.current += 1
    setVadCount(segSeenRef.current)
    queueRef.current = queueRef.current
      .then(() => transcribeSegment(cap))
      .catch(() => undefined)
  }

  // 语音按钮统一入口：idle=开听（会话）；rec=停听（要尾段+看零段脸色）。
  // 所有失败都汇到 voiceErr 小字条——不弹 alert，弹窗会劫持键盘且没法在
  // devtest 里断言
  const toggleVoice = async (): Promise<void> => {
    if (voice === 'rec') {
      const rec = recRef.current
      recRef.current = null
      setVoice('idle')
      try {
        const tail = await (rec ? rec.stop() : Promise.resolve<VoiceCapture | null>(null))
        if (tail) enqueueSegment(tail)
      } catch (e) {
        setVoiceErr(`语音会话收尾失败：${String(e instanceof Error ? e.message : e)}`)
      }
      if (segSeenRef.current === 0) setVoiceErr('没听清——整段没检测到人声，靠近麦克风再说一遍？')
      return
    }
    setVoiceErr('')
    segSeenRef.current = 0
    setVadCount(0)
    queueRef.current = Promise.resolve()
    setSegCount(0)
    try {
      const rec = new VoiceRecorder()
      await rec.start(
        (cap) => enqueueSegment(cap),
        // 到点自动停（5 分钟上限）：Recorder 已放麦，这里只对齐 UI 状态；
        // 判 rec 身份是因为用户可能早已手动停过、开了新会话
        () => {
          if (recRef.current !== rec) return
          recRef.current = null
          setVoice('idle')
          if (segSeenRef.current === 0) setVoiceErr('没听清——整段没检测到人声，靠近麦克风再说一遍？')
        },
        300
      )
      recRef.current = rec
      setVoice('rec')
      requestAnimationFrame(() => taRef.current?.focus())
    } catch (e) {
      setVoiceErr(`无法使用麦克风：${String(e instanceof Error ? e.message : e)}（检查 设置→隐私→麦克风）`)
    }
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
  const permLabel = PERM_MODES.find(([v]) => v === (attrs?.permissionMode ?? 'default'))?.[1] ?? '谨慎'
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
          <button
            className={`chip icon-chip${voice === 'rec' ? ' mic-rec' : ''}`}
            title={
              voice === 'rec'
                ? '停止聆听（说完这句直接点，尾段照常入字）'
                : '语音输入（自动断句，边说边出字；再点一下停止）'
            }
            disabled={!booted || status !== 'connected'}
            onClick={() => void toggleVoice()}
          >
            <Icon name="mic" size={13} />
          </button>
          <ParamChip
            icon="shield"
            value={permLabel}
            items={PERM_MODES}
            dividerBefore={PERM_DIVIDER}
            hint="审批模式（本会话）"
            disabled={!activeSid}
            onPick={async (v) => {
              if (!activeSid) return
              // 【学习要点】只有升到"全自动"设卡：方向不对称——更严格永远无害，
              // 放宽才需要一次知情。confirm 文案必须如实写 deny/强制 ask 地板
              // 仍在（对齐 CLAUDE.md 安全事实模型），不许把 bypass 说成"什么都不问"
              if (
                v === 'bypassPermissions' &&
                !window.confirm(
                  '开启「全自动」？\n\n将跳过审批确认层——deny 规则与强制 ask 仍然生效，不是"什么都不问"。\n建议仅在完全信任的项目目录使用。'
                )
              )
                return
              await setPermissionMode(activeSid, v)
            }}
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
        {voice === 'rec' && (
          <div className="voice-seg" title="能量 VAD 自动断句；每句转写完自动追加到输入框尾部">
            <span className="voice-seg-dot" />
            <span>已断 {vadCount} 段 · 已入 {segCount} 句</span>
            {segBusy && <span className="voice-seg-busy">· 转写中…</span>}
          </div>
        )}
        {voiceErr && <div className="voice-err">{voiceErr}</div>}
      </div>
    </div>
  )
}
