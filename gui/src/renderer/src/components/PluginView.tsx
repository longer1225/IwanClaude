// 插件实况视图：plugin.list 一屏看全"有什么包、开没开、贡献了什么"，MCP 状态降为从属区
//
// 【学习要点】1) 启停是**双向写面**但零自由格式：UI 只发 {name, enabled} 布尔翻转，
// 名字与贡献内容由 daemon 的账本/清单说了算——写面收窄到枚举操作，
// 前端不可能拼出"半套配置"脏状态（对比：★ 协议补全里的规则编辑器暂未做）。
// 2) plugin.changed 触发静默重拉：启停的真相在 daemon 侧（撞名降级、贡献解析），
// 本地乐观更新会被账本重算打脸，索性事件一来就整表刷新。
// 3) install 输入框只透传 URL：下载/校验/防 zip-slip 全在服务端，
// 前端不预览包内容——给不可信第三方包一个"渲染面"就是给它一条注入通道。
import { useEffect, useState } from 'react'
import { onDaemonEvent, rpc } from '../rpc'
import type {
  McpServerStatus,
  McpStatusResult,
  PluginInstallResult,
  PluginListResult,
  PluginRow,
  PluginSetEnabledResult,
} from '../protocol/types'
import { Icon } from './Icon'

// 一次拉全两张表（插件与 MCP 实况同刷新节拍，避免两套 busy 态互相打架）
async function fetchAll(): Promise<{ plugins: PluginRow[]; servers: McpServerStatus[]; err: string }> {
  try {
    const [pl, mcp] = await Promise.all([
      rpc<PluginListResult>('plugin.list'),
      rpc<McpStatusResult>('mcp.status'),
    ])
    return { plugins: pl.plugins ?? [], servers: mcp.servers ?? [], err: '' }
  } catch (e) {
    return { plugins: [], servers: [], err: String(e) }
  }
}

export function PluginView() {
  const [plugins, setPlugins] = useState<PluginRow[]>([])
  const [servers, setServers] = useState<McpServerStatus[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [url, setUrl] = useState('')
  const [installing, setInstalling] = useState(false)
  const [installMsg, setInstallMsg] = useState('')

  const reload = async (): Promise<void> => {
    setBusy(true)
    const r = await fetchAll()
    setPlugins(r.plugins)
    setServers(r.servers)
    setErr(r.err)
    setBusy(false)
    setLoaded(true)
  }

  useEffect(() => {
    void reload()
    // 任何客户端（含 devtest / 另一窗口）的启停都经事件回流刷新本表
    return onDaemonEvent((ev) => {
      if (ev.type === 'plugin.changed') void reload()
    })
  }, [])

  // 翻转启停位；成功后等 plugin.changed 回流刷新（失败就现场标 error）
  const toggle = async (name: string, enabled: boolean): Promise<void> => {
    try {
      const r = await rpc<PluginSetEnabledResult>('plugin.set_enabled', { name, enabled })
      if (!r.ok) setInstallMsg(`切换失败：${r.error}`)
    } catch (e) {
      setInstallMsg(`切换异常：${e}`)
    }
  }

  // URL 安装：服务端全程做恶（防穿越/校验清单），这里只回显结果
  const install = async (): Promise<void> => {
    const target = url.trim()
    if (!target || installing) return
    setInstalling(true)
    setInstallMsg('')
    try {
      const r = await rpc<PluginInstallResult>('plugin.install', { url: target })
      setInstallMsg(r.message)
      if (r.ok) setUrl('')
    } catch (e) {
      setInstallMsg(`安装异常：${e}`)
    } finally {
      setInstalling(false)
      void reload()
    }
  }

  const statusBadge = (p: PluginRow): string =>
    p.status === 'error' ? '异常' : p.status === 'partial' ? '部分生效' : p.enabled ? '已启用' : '已禁用'

  return (
    <div className="fn-view">
      <div className="fn-head">
        <span className="fn-title">插件 / MCP</span>
        <span className="grow" />
        <button className="chip" onClick={() => void reload()} disabled={busy} title="重新拉取实况">
          <Icon name="refresh" size={13} /> 刷新
        </button>
      </div>
      <div className="fn-body">
        {err && <div className="fn-err">{err}</div>}

        {/* URL 安装框 */}
        <div className="plg-install">
          <input
            className="plg-url"
            placeholder="安装插件：GitHub 仓库 / ZIP URL"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void install()
            }}
            data-testid="plugin-url-input"
          />
          <button
            className="chip"
            onClick={() => void install()}
            disabled={installing || !url.trim()}
            data-testid="plugin-install-btn"
          >
            <Icon name="plus" size={13} /> {installing ? '安装中…' : '安装'}
          </button>
        </div>
        {installMsg && (
          <div className="fn-err fn-err-inline" data-testid="plugin-install-msg">
            {installMsg}
          </div>
        )}

        {/* 插件卡列表 */}
        {!err && loaded && !plugins.length && (
          <div className="fn-empty">暂无插件。内置包可在下方开关；外部包贴 URL 安装。</div>
        )}
        {plugins.map((p) => (
          <div key={p.name} className="fn-card" data-testid={`plugin-card-${p.name}`}>
            <div className="fn-card-head">
              <span className={`dot dot-${p.status === 'error' ? 'error' : p.enabled ? 'connected' : 'idle'}`} />
              <span className="fn-card-name">{p.name}</span>
              <span className="fn-tag">{p.source === 'builtin' ? '内置' : '已安装'}</span>
              {p.version && <span className="fn-tag">v{p.version}</span>}
              <span className="grow" />
              <span className="fn-meta" data-testid={`plugin-status-${p.name}`}>{statusBadge(p)}</span>
              <button
                className="chip"
                onClick={() => void toggle(p.name, !p.enabled)}
                disabled={p.status === 'error' && !p.enabled}
                data-testid={`plugin-toggle-${p.name}`}
              >
                {p.enabled ? '停用' : '启用'}
              </button>
            </div>
            {p.error && <div className="fn-err fn-err-inline">{p.error}</div>}
            {p.enabled && (
              <div className="fn-tools">
                {(p.skills ?? []).map((s) => (
                  <span key={`s-${s}`} className="fn-tool" title={`skill: ${s}`}>
                    <Icon name="plug" size={11} /> {s}
                  </span>
                ))}
                {(p.mcp ?? []).map((m) => (
                  <span key={`m-${m}`} className="fn-tool" title={`mcp: ${m}`}>
                    <Icon name="wrench" size={11} /> {m}
                  </span>
                ))}
                {(p.hooks ?? []).length > 0 && (
                  <span className="fn-tool" title={(p.hooks ?? []).join('\n')}>
                    <Icon name="shield" size={11} /> hooks × {(p.hooks ?? []).length}
                  </span>
                )}
              </div>
            )}
          </div>
        ))}

        {/* MCP 实况：降为从属区（插件贡献的 server 也在这张表里带 __ 前缀可见） */}
        {servers.length > 0 && (
          <>
            <div className="plg-subhead">MCP 服务器实况</div>
            {servers.map((s) => (
              <div key={s.name} className="fn-card">
                <div className="fn-card-head">
                  <span className={`dot dot-${s.connected ? 'connected' : 'error'}`} />
                  <span className="fn-card-name">{s.name}</span>
                  <span className="fn-tag">{s.transport}</span>
                  <span className="grow" />
                  <span className="fn-meta">{s.connected ? `已连接 · ${(s.tools ?? []).length} 个工具` : '未连接'}</span>
                </div>
                {(s.tools ?? []).length > 0 && (
                  <div className="fn-tools">
                    {(s.tools ?? []).map((t) => (
                      <span key={t} className="fn-tool" title={t}>
                        <Icon name="wrench" size={11} /> {t.replace(/^mcp__[^_]+__/, '')}
                      </span>
                    ))}
                  </div>
                )}
                {s.last_error && <div className="fn-err fn-err-inline">启动失败：{s.last_error}</div>}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  )
}
