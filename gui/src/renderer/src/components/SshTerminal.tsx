// SSH 终端页签组件（M4c）：xterm.js ↔ daemon ssh -tt 会话的字节桥
//
// 【学习要点】1) 字节通路绕开 zustand：ssh.output 是高频流（16KB/30ms 一帧），
// 每帧过一遍全局 store 会把 React 重渲染的税交给人眼看不见的延迟——组件
// 直接订 window.iwan.onEvent、按 session_id 过滤，xterm.write 全程无 setState
// （只有 closed/error 这类低频状态才进 React）。
// 2) 会话生命周期归页签：mount 时量好尺寸再 term_open（远端 pty 的初始终
// 寸就对了），unmount 时没断就 term_close——谁开谁关，池子里不留孤魂。
// 3) 输入分片 ≤4KB/帧：粘贴大段文本是最大的一波输入，切帧让 typing 的
// 小帧不被排在几十 KB 后面；b64 后仍是单行 JSON，NDJSON 帧完整性不破。
// 4) resize 只发不候：v1 daemon 不传播（无 SIGWINCH 通道），发送是为将来
// 服务端支持时零改动升级；失败静默。
// 5) daemon 重启 = 终端全灭（计划风险⑥）：ssh.exe 挂在 daemon 的 Job Object
// 上随其陪葬，但 GUI 收不到任何 ssh.closed——事件是 daemon 发的，它自己死了
// 谁发？所以判"会话死亡"的哨兵是连接状态：reconnecting→connected 的跨越
// 即旧进程已作古，页签统一标断。v1 不做 resume（远端 shell 状态不可恢复）。
import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { onConnStatus, getStatus, rpc } from '../rpc'
import type { SshConnInfo, SshTermOpenResult } from '../protocol/types'

const INPUT_CHUNK = 4096

// 字符串 → UTF-8 字节 → base64（btoa 只认 latin1，逐字符转码兜住中文输入）
function b64Encode(s: string): string {
  const bytes = new TextEncoder().encode(s)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin)
}

// base64 → Uint8Array（xterm.write 收字节，不碰 UTF-8 边界问题）
function b64Bytes(b64: string): Uint8Array {
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

export function SshTerminal({ conn, onClose }: { conn: SshConnInfo; onClose: () => void }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [banner, setBanner] = useState('')

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Consolas, "Cascadia Mono", "Courier New", monospace',
      theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host)
    fit.fit()

    let sid = ''
    let dead = false
    const pendingInput: string[] = []

    const sendInput = (data: string): void => {
      if (dead) return
      if (!sid) {
        pendingInput.push(data)  // 会话没建成：暂存，成功后一次性补发
        return
      }
      for (let i = 0; i < data.length; i += INPUT_CHUNK) {
        void rpc('ssh.term_write', { session_id: sid, data_b64: b64Encode(data.slice(i, i + INPUT_CHUNK)) }).catch(() => {})
      }
    }
    const inputDisp = term.onData(sendInput)

    // 输出/关闭：一帧一订，字节直写 xterm，不走 React
    const offEvent = window.iwan.onEvent((ev) => {
      const t = ev['type']
      if (t === 'ssh.output' && ev['session_id'] === sid) {
        term.write(b64Bytes(String(ev['data_b64'] ?? '')))
      } else if (t === 'ssh.closed' && ev['session_id'] === sid) {
        dead = true
        term.write(`\r\n\x1b[90m── 会话结束：${String(ev['reason'] ?? '')} ──\x1b[0m\r\n`)
        setBanner(String(ev['reason'] ?? '会话结束'))
      }
    })

    // 断线哨兵：见过"离开 connected"后再回到 connected = daemon 重启过，
    // 旧会话必死（Job Object 陪葬）——主动标断，别让页签装作还能打字
    let prevStatus = 'connected'
    void getStatus().then((s) => { prevStatus = s }).catch(() => {})
    const offStatus = onConnStatus((s) => {
      const wasDropped = prevStatus === 'reconnecting' || prevStatus === 'connecting' || prevStatus === 'error'
      prevStatus = s
      if (s === 'connected' && wasDropped && sid && !dead) {
        dead = true
        term.write('\r\n\x1b[90m── daemon 已重连：此会话随旧进程终止 ──\x1b[0m\r\n')
        setBanner('daemon 重连，终端会话已终止')
      }
    })

    // 尺寸：150ms 防抖 → fit → 通知 daemon（v1 仅登记）
    let resizeTimer: ReturnType<typeof setTimeout> | null = null
    const ro = new ResizeObserver(() => {
      if (resizeTimer) clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => {
        fit.fit()
        if (sid && !dead) {
          void rpc('ssh.term_resize', { session_id: sid, cols: term.cols, rows: term.rows }).catch(() => {})
        }
      }, 150)
    })
    ro.observe(host)

    void (async () => {
      try {
        const r = await rpc<SshTermOpenResult>('ssh.term_open', {
          conn_id: conn.id, cols: term.cols, rows: term.rows,
        })
        if (!r.ok || !r.session_id) {
          dead = true
          setBanner(r.error || '打开会话失败')
          return
        }
        sid = r.session_id
        for (const p of pendingInput.splice(0)) sendInput(p)
      } catch (e) {
        dead = true
        setBanner(String(e))
      }
    })()

    return () => {
      offEvent()
      offStatus()
      ro.disconnect()
      inputDisp.dispose()
      if (sid && !dead) void rpc('ssh.term_close', { session_id: sid }).catch(() => {})
      term.dispose()
    }
    // conn.id 变化视作换页签（SshView 用 key 保证整组件重建，这里只防 lint）
  }, [conn.id])

  return (
    <div className="ssh-term-host">
      {banner && (
        <div className="ssh-term-banner">
          {banner}
          <button className="chip" onClick={onClose}>关闭页签</button>
        </div>
      )}
      <div ref={hostRef} className="ssh-xterm" />
    </div>
  )
}
