// 设置页：外观 | 项目 | MCP | 审批与信任 | 引擎与模型 | 关于
//
// 【学习要点】这里演示了混合数据源的诚实分层——同一张界面里三种"写权限"：
// ① 外观/项目：GUI 自己的 ~/.iwan/gui.json，随便改；
// ② 信任表：daemon 的 trust.list/revoke，真命令真写；
// ③ config.toml（MCP、模型表）：只读展示——它是后端的行为合同，GUI 改它=改后端，
//    越过"零后端改动"红线，所以给出文件路径让用户自己动。
// 一个设置页把"什么能改、改了归谁管"讲明白，比功能堆砌更有价值。
import { useEffect, useState } from 'react'
import { rpc } from '../rpc'
import { useGui } from '../guiHelpers'
import type { PongResult, TrustListResult } from '../protocol/types'
import { Icon } from './Icon'
import type { IconName } from './Icon'
import { baseName } from '../guiHelpers'

type Section = 'appearance' | 'projects' | 'mcp' | 'trust' | 'engine' | 'quality' | 'about'

const SECTIONS: Array<[Section, string, IconName]> = [
  ['appearance', '外观', 'sun'],
  ['projects', '项目', 'folder'],
  ['mcp', 'MCP 服务', 'plug'],
  ['trust', '审批与信任', 'shield'],
  ['engine', '引擎与模型', 'cpu'],
  ['quality', '检索质量', 'search'],
  ['about', '关于', 'message']
]

// 外观区：主题三选 + 字号步进
function AppearanceSection() {
  const gui = useGui()
  return (
    <>
      <div className="set-row">
        <span className="set-label">主题</span>
        <div className="seg">
          {(['light', 'dark', 'system'] as const).map((t) => (
            <button key={t} className={gui.theme === t ? 'on' : ''} onClick={() => gui.set({ theme: t })}>
              {t === 'light' ? '浅色' : t === 'dark' ? '深色' : '跟随系统'}
            </button>
          ))}
        </div>
      </div>
      <div className="set-row">
        <span className="set-label">界面字号</span>
        <Stepper value={gui.uiFontPx} onChange={(v) => gui.set({ uiFontPx: v })} />
      </div>
      <div className="set-row">
        <span className="set-label">代码字号</span>
        <Stepper value={gui.codeFontPx} onChange={(v) => gui.set({ codeFontPx: v })} />
      </div>
      <div className="set-row">
        <span className="set-label">整体缩放</span>
        <div className="seg">
          {[0.9, 1, 1.1, 1.25].map((f) => (
            <button key={f} className={gui.fontScale === f ? 'on' : ''} onClick={() => gui.set({ fontScale: f })}>
              {Math.round(f * 100)}%
            </button>
          ))}
        </div>
      </div>
      <p className="set-note">设置写入 ~/.iwan/gui.json——daemon 对这份文件一无所知（用户设置与服务数据分轨，VS Code 同款纪律）。</p>
    </>
  )
}

// 字号步进器（8~24px 限幅）
function Stepper(props: { value: number; onChange: (v: number) => void }) {
  return (
    <div className="stepper">
      <button onClick={() => props.onChange(Math.max(10, Math.round((props.value - 0.5) * 10) / 10))}>−</button>
      <span>{props.value}px</span>
      <button onClick={() => props.onChange(Math.min(24, Math.round((props.value + 0.5) * 10) / 10))}>＋</button>
    </div>
  )
}

// 项目区：登记表管理（添加/打开/移除）
function ProjectsSection() {
  const gui = useGui()
  const add = async (): Promise<void> => {
    const dir = await window.iwan.pickDirectory()
    if (dir) gui.addProject(dir)
  }
  return (
    <>
      <p className="set-note">登记的项目会固定显示在侧栏分组里（哪怕暂时没有会话）。</p>
      {gui.projects.length === 0 && <div className="pane-hint">还没有登记项目。</div>}
      {gui.projects.map((p) => (
        <div key={p} className="set-row row-line" title={p}>
          <Icon name="folder" size={14} />
          <span className="set-label grow">{baseName(p)}</span>
          <button className="mini-btn" onClick={() => void window.iwan.openPath(p)}><Icon name="folderOpen" size={12} /></button>
          <button className="mini-btn danger" onClick={() => gui.removeProject(p)}><Icon name="trash" size={12} /></button>
        </div>
      ))}
      <button className="mini-btn" style={{ marginTop: 10 }} onClick={() => void add()}>
        <Icon name="folderPlus" size={12} /> 添加项目
      </button>
    </>
  )
}

// MCP 区：config.toml 的 [[mcp.servers]] 只读卡片
interface McpServer { name: string; transport: string; command?: string; host?: string; port?: string; timeout_sec?: string }
function McpSection() {
  const [cfg, setCfg] = useState<{ exists: boolean; raw: string; toml: unknown } | null>(null)
  const [showRaw, setShowRaw] = useState(false)
  useEffect(() => {
    void window.iwan.readConfig().then(setCfg).catch(() => setCfg({ exists: false, raw: '', toml: null }))
  }, [])
  const servers: McpServer[] = ((cfg?.toml as { arrays?: Record<string, Array<Record<string, string>>> } | null)?.arrays?.['mcp.servers'] ?? []).map((s) => ({
    name: s.name ?? '(未命名)',
    transport: s.transport ?? (s.command ? 'stdio' : s.host ? 'tcp' : '?'),
    command: s.command ? `${s.command} ${s.args ?? ''}`.trim() : undefined,
    host: s.host,
    port: s.port,
    timeout_sec: s.timeout_sec
  }))
  return (
    <>
      <p className="set-note">
        MCP（Model Context Protocol）让 daemon 挂载外部工具服务器，工具以
        <code>mcp__服务器名__工具名</code> 注册进模型的可调用清单。此面板为<b>只读</b>：
        配置在 <code>~/.iwan/config.toml</code> 的 <code>[[mcp.servers]]</code> 段，改完重启 iwan-core 生效。
      </p>
      {!cfg && <div className="pane-hint">读取 config.toml…</div>}
      {cfg && !cfg.exists && <div className="pane-hint">~/.iwan/config.toml 不存在——当前没有任何 MCP 服务器。</div>}
      {cfg?.exists && servers.length === 0 && <div className="pane-hint">config.toml 里没有 [[mcp.servers]] 条目。</div>}
      {servers.map((s) => (
        <div key={s.name} className="mcp-card">
          <div className="mcp-head">
            <Icon name="plug" size={14} />
            <span className="mcp-name">{s.name}</span>
            <span className="mcp-tag">{s.transport}</span>
            <span className="grow" />
            <span className="dot dot-connected" title="已配置（在线状态随 daemon）" />
          </div>
          <div className="mcp-body">
            {s.command && <div><code>{s.command}</code></div>}
            {s.host && <div>{s.host}:{s.port ?? '?'} · timeout {s.timeout_sec ?? '30'}s</div>}
          </div>
        </div>
      ))}
      {cfg?.exists && (
        <>
          <button className="mini-btn" style={{ marginTop: 8 }} onClick={() => setShowRaw((v) => !v)}>
            {showRaw ? '收起原文' : '查看 config.toml 原文'}
          </button>
          {showRaw && <pre className="toml-raw">{cfg.raw}</pre>}
        </>
      )}
    </>
  )
}

// 审批与信任区：规则序讲解 + trust.list 表 + revoke
function TrustSection() {
  const [entries, setEntries] = useState<Record<string, string> | null>(null)
  const [err, setErr] = useState('')
  const refresh = (): void => {
    rpc<TrustListResult>('trust.list')
      .then((r) => setEntries(r.entries ?? {}))
      .catch((e) => {
        setEntries({})
        setErr(String(e instanceof Error ? e.message : e))
      })
  }
  useEffect(refresh, [])
  const revoke = (cwd: string): void => {
    void rpc('trust.revoke', { cwd }).then(refresh).catch((e) => setErr(String(e)))
  }
  return (
    <>
      <p className="set-note">
        权限判定序：<b>deny → ask → allow，先匹配先赢</b>；未命中任何规则的动作默认弹询问（default-ask）。
        审批模式是会话级开关（输入框左下），而下表是<b>目录级信任</b>——daemon 记住你对某项目目录的放行决定。
      </p>
      {err && <div className="pane-note">{err}</div>}
      {entries === null && <div className="pane-hint">读取信任表…</div>}
      {entries && Object.keys(entries).length === 0 && <div className="pane-hint">信任表为空——首次进入未信任目录时会在会话里弹出询问卡。</div>}
      {Object.entries(entries ?? {}).map(([cwd, decision]) => (
        <div key={cwd} className="set-row row-line" title={cwd}>
          <Icon name="shield" size={14} />
          <span className="set-label grow">{baseName(cwd) || cwd}</span>
          <span className={`chg-badge${decision === 'allow' ? ' chg-add' : ''}`}>{decision}</span>
          <button className="mini-btn danger" onClick={() => revoke(cwd)} title="撤销该目录的信任决定">撤销</button>
        </div>
      ))}
      <p className="set-note">★ 权限规则（allow/deny 清单）的 GUI 编辑尚未开放——后端规则引擎在 settings 层，接入需协议补全，另行立项。</p>
    </>
  )
}

// 引擎与模型区：session.engine_info + config [agent]/[llm] 只读
function EngineSection() {
  const [info, setInfo] = useState<Record<string, string> | null>(null)
  const [cfg, setCfg] = useState<{ tables?: Record<string, Record<string, string | number | boolean>> } | null>(null)
  useEffect(() => {
    void rpc<Record<string, string>>('session.engine_info', {}).then(setInfo).catch((e) => setInfo({ error: String(e) }))
    void window.iwan.readConfig().then((c) => setCfg((c.toml as { tables?: Record<string, Record<string, string | number | boolean>> } | null) ?? null))
  }, [])
  const rows: Array<[string, string | number | boolean | undefined]> = [
    ['当前引擎', info?.engine],
    ['检查点后端', info?.checkpoint_backend],
    ['模型（默认档）', cfg?.tables?.agent?.model ?? cfg?.tables?.llm?.model],
    ['上下文窗口', cfg?.tables?.llm?.context_window]
  ]
  return (
    <>
      <p className="set-note">引擎在会话级可切（输入框 cpu 选择器，六种执行策略全列）；下表展示的是 daemon 全局配置。★ 模型的各档位映射定义在 config.toml，只读。</p>
      {info === null && <div className="pane-hint">查询 daemon…</div>}
      {rows.filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => (
        <div key={k} className="set-row">
          <span className="set-label">{k}</span>
          <span className="set-value"><code>{String(v)}</code></span>
        </div>
      ))}
      {info?.error && <div className="pane-note">{info.error}</div>}
    </>
  )
}

// 检索质量区：读 ~/.iwan/rag_eval_latest.json（rag.eval --run 落盘）——纯文件桥，零 daemon 协议改动
//
// 【学习要点】这是"只读展示外部产物"的第三种数据源模式（区别于 gui.json 可写、
// trust.list 真命令）：评估由 Python 侧跑（要真打 embedding API，GUI 里不该做），
// GUI 只负责把落盘 JSON 翻成人话。缺文件不报错而是给出生成命令——空态即教程。
interface RagEvalData {
  ts?: string
  root?: string
  embedding_model?: string
  chunk?: { size?: number; overlap?: number }
  index?: { chunks?: number; sources?: number; index_s?: number }
  total_questions?: number
  recall_at_k?: Record<string, number>
  precision_at_k?: Record<string, number>
  mrr?: number
  hit_rate?: number
  category_breakdown?: Record<string, { count?: number; ['avg_recall@5']?: number; hit_rate?: number }>
}

// 百分比格式化（0.875 → "87.5%"；缺值回显 —）
function pct(v: number | undefined): string {
  return v === undefined ? '—' : `${(v * 100).toFixed(1)}%`
}

// 检索质量面板：Recall@K / Precision / MRR / 命中率 + 元信息 + 分类拆解
function QualitySection() {
  const [state, setState] = useState<{ exists: boolean; data: unknown } | null>(null)
  useEffect(() => {
    void window.iwan.readRagEval().then(setState).catch(() => setState({ exists: false, data: null }))
  }, [])
  const d = (state?.exists ? state.data : null) as RagEvalData | null
  const rk = d?.recall_at_k ?? {}
  const pk = d?.precision_at_k ?? {}
  return (
    <>
      <p className="set-note">
        衡量 RAG 检索质量的基准测试：16 个针对本仓库源码的固定问题，看<b>正确文件能否被检回</b>。
        数据来自 <code>~/.iwan/rag_eval_latest.json</code>，由
        <code>uv run python -m iwan_claude.core.rag.eval --run</code> 生成（要真调 embedding API，只在 CLI 侧跑）。
      </p>
      {!state && <div className="pane-hint">读取评估结果…</div>}
      {state && !state.exists && (
        <div className="pane-hint">尚未运行过检索评估——执行 <code>uv run python -m iwan_claude.core.rag.eval --run</code> 后回到本页。</div>
      )}
      {d && (
        <>
          <div className="set-row"><span className="set-label">评估时间</span><span className="set-value">{d.ts ?? '—'}</span></div>
          <div className="set-row"><span className="set-label">Embedding 模型</span><span className="set-value"><code>{d.embedding_model ?? '—'}</code></span></div>
          <div className="set-row"><span className="set-label">索引规模</span><span className="set-value">{d.index?.chunks ?? '?'} 块 / {d.index?.sources ?? '?'} 文件 · 建索引 {d.index?.index_s ?? '?'}s</span></div>
          <div className="set-row"><span className="set-label">题目数</span><span className="set-value">{d.total_questions ?? '—'}</span></div>
          {(['1', '3', '5', '10'] as const).map((k) => (
            <div key={k} className="set-row">
              <span className="set-label">Recall@{k}</span>
              <span className="set-value">{pct(rk[k])}</span>
            </div>
          ))}
          <div className="set-row"><span className="set-label">Precision@5</span><span className="set-value">{pct(pk['5'])}</span></div>
          <div className="set-row"><span className="set-label">MRR（平均倒数排名）</span><span className="set-value">{d.mrr !== undefined ? d.mrr.toFixed(3) : '—'}</span></div>
          <div className="set-row"><span className="set-label">命中率 Hit@10</span><span className="set-value">{pct(d.hit_rate)}</span></div>
          {d.category_breakdown && Object.keys(d.category_breakdown).length > 0 && (
            <>
              <p className="set-note">按题目类别拆解（Recall@5）：</p>
              {Object.entries(d.category_breakdown).map(([cat, v]) => (
                <div key={cat} className="set-row row-line">
                  <span className="set-label grow">{cat}</span>
                  <span className="chg-badge">{pct(v['avg_recall@5'])}</span>
                  <span className="set-value">{v.count ?? '?'} 题</span>
                </div>
              ))}
            </>
          )}
        </>
      )}
    </>
  )
}

// 关于区：core.ping 探活 + 路径速览
function AboutSection() {
  const [pong, setPong] = useState<PongResult | null>(null)
  useEffect(() => {
    void rpc<PongResult>('core.ping', { client: 'gui-settings' }).then(setPong).catch(() => setPong(null))
  }, [])
  return (
    <>
      <div className="set-row"><span className="set-label">协议服务</span><span className="set-value">iwan-core · 127.0.0.1:7437</span></div>
      <div className="set-row"><span className="set-label">daemon 版本</span><span className="set-value">{pong ? pong.server_version : '—（未应答）'}</span></div>
      <div className="set-row"><span className="set-label">运行时长</span><span className="set-value">{pong ? `${Math.floor(pong.uptime_ms / 1000)}s` : '—'}</span></div>
      <div className="set-row"><span className="set-label">数据目录</span><span className="set-value"><code>~/.iwan/</code></span></div>
      <div className="set-row"><span className="set-label">后端配置（只读）</span><span className="set-value"><code>~/.iwan/config.toml</code></span></div>
      <div className="set-row"><span className="set-label">GUI 设置</span><span className="set-value"><code>~/.iwan/gui.json</code></span></div>
      <p className="set-note">iwanclaude · 双进程架构：GUI 与 TUI/CLI 是平级客户端，daemon 是唯一事实源。</p>
    </>
  )
}

export function SettingsView() {
  const open = useGui((s) => s.settingsOpen)
  const setG = useGui((s) => s.set)
  const [sec, setSec] = useState<Section>('appearance')
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setG({ settingsOpen: false })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setG])
  if (!open) return null
  return (
    <div className="set-overlay" onClick={() => setG({ settingsOpen: false })}>
      <div className="set-card" onClick={(e) => e.stopPropagation()}>
        <div className="set-nav">
          <div className="set-brand">
            <Icon name="gear" size={14} /> 设置
          </div>
          {SECTIONS.map(([id, label, icon]) => (
            <button key={id} className={`set-nav-item${sec === id ? ' active' : ''}`} onClick={() => setSec(id)}>
              <Icon name={icon} size={14} /> {label}
            </button>
          ))}
        </div>
        <div className="set-body">
          <div className="set-head">
            <span className="grow">{SECTIONS.find(([id]) => id === sec)?.[1]}</span>
            <button className="icon-btn tiny" onClick={() => setG({ settingsOpen: false })} title="关闭 (Esc)">
              <Icon name="x" size={14} />
            </button>
          </div>
          {sec === 'appearance' && <AppearanceSection />}
          {sec === 'projects' && <ProjectsSection />}
          {sec === 'mcp' && <McpSection />}
          {sec === 'trust' && <TrustSection />}
          {sec === 'engine' && <EngineSection />}
          {sec === 'quality' && <QualitySection />}
          {sec === 'about' && <AboutSection />}
        </div>
      </div>
    </div>
  )
}
