// 插件 / MCP 实况视图：mcp.status 一屏看全"配了什么、连没连上、带什么工具"
//
// 【学习要点】1) 本视图刻意【只读】：MCP 服务器的配置源是 config.toml [mcp.servers]，
// 界面编辑属于 ★ 协议补全范围（规则/配置写面），先不越权造半套编辑 UI。
// 2) "connected" 来自 daemon 内存态而非本前端探测：展示层永远不自己发探测包，
// 否则每个开着的 GUI 窗口都会给每台 stdio 服务器添一份子进程。
import { useEffect, useState } from 'react'
import { rpc } from '../rpc'
import type { McpServerStatus, McpStatusResult } from '../protocol/types'
import { Icon } from './Icon'

// 拉一次实况（挂载与手动刷新共用）
async function fetchStatus(): Promise<{ rows: McpServerStatus[]; err: string }> {
  try {
    const r = await rpc<McpStatusResult>('mcp.status')
    return { rows: r.servers ?? [], err: '' }
  } catch (e) {
    return { rows: [], err: String(e) }
  }
}

export function McpView() {
  const [rows, setRows] = useState<McpServerStatus[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const reload = async (): Promise<void> => {
    setBusy(true)
    const { rows: r, err: e } = await fetchStatus()
    setRows(r)
    setErr(e)
    setBusy(false)
    setLoaded(true)
  }
  useEffect(() => {
    void reload()
  }, [])

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
        {!err && loaded && !rows.length && (
          <div className="fn-empty">
            未配置任何 MCP 服务器。在 ~/.iwan/config.toml 的 [mcp] servers 列表里添加后重启 daemon，
            这里就能看到每台服务器的连接状态与工具清单。
          </div>
        )}
        {rows.map((s) => (
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
      </div>
    </div>
  )
}
