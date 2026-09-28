// SSH 连接视图（M4a）：连接表 CRUD + 本机密钥管理 + 主机信任
//
// 【学习要点】1) 密钥卡片置顶是刻意的信息架构：没有密钥时连接表填得再对
// 也连不上——把"缺什么"放在用户第一眼的位置，而不是藏在连接报错之后。
// 2) host_trust 返回的指纹原文多行展示，比对动作留给用户眼睛：信任远端
// 主机是安全决策，GUI 不代替用户判断指纹对不对，只保证"看得见"。
// 3) 公钥的复制按钮给两种载荷：裸公钥（贴进 authorized_keys 用）和整条
// ssh-copy-id 命令（Git Bash/WSL 一步到位用）——复制成本最低的选项全给。
import { useEffect, useState } from 'react'
import { rpc } from '../rpc'
import type { SshConnInfo, SshConnListResult, SshConnOpResult, SshKeyOpResult, SshKeyStatusResult, SshTrustResult } from '../protocol/types'
import { Icon } from './Icon'
import { SshTerminal } from './SshTerminal'

// 一个终端页签 = 一次会话意图（seq 保证同连接多开时 React key 唯一、组件整体重建）
interface TermTab {
  key: number
  conn: SshConnInfo
}

export function SshView() {
  const [conns, setConns] = useState<SshConnInfo[]>([])
  const [key, setKey] = useState<SshKeyStatusResult | null>(null)
  const [msg, setMsg] = useState('')
  const [formOpen, setFormOpen] = useState(false)
  const [name, setName] = useState('')
  const [host, setHost] = useState('')
  const [user, setUser] = useState('')
  const [port, setPort] = useState('22')
  const [keyFile, setKeyFile] = useState('')
  const [tabs, setTabs] = useState<TermTab[]>([])
  const [active, setActive] = useState<number | null>(null)
  const [seq, setSeq] = useState(1)

  // 全量刷新：连接表 + 密钥状态一起拉（两者都便宜，分开刷新只会有半新半旧）
  const refresh = async (): Promise<void> => {
    setMsg('')
    try {
      const [cl, ks] = await Promise.all([
        rpc<SshConnListResult>('ssh.conn_list', {}),
        rpc<SshKeyStatusResult>('ssh.key_status', {}),
      ])
      setConns(cl.connections ?? [])
      setKey(ks)
    } catch (e) {
      setMsg(`✗ ${String(e)}`)
    }
  }
  useEffect(() => {
    void refresh()
  }, [])

  // 发一个连接写操作并回显（成功后重拉列表，失败原文进 msg 条）
  const connOp = async (method: string, params: Record<string, unknown>): Promise<SshConnOpResult | null> => {
    setMsg('')
    try {
      const r = await rpc<SshConnOpResult>(method, params)
      if (!r.ok) setMsg(`✗ ${r.error ?? '操作失败'}`)
      void refresh()
      return r
    } catch (e) {
      setMsg(`✗ ${String(e)}`)
      return null
    }
  }

  const addConn = async (): Promise<void> => {
    const r = await connOp('ssh.conn_add', { name, host, user, port: Number(port) || 22, key_file: keyFile })
    if (r?.ok) {
      setFormOpen(false)
      setName(''); setHost(''); setUser(''); setPort('22'); setKeyFile('')
    }
  }

  // 生成密钥：成功后把指纹与公钥直接展示出来（生成的当下就是最该复制的时机）
  const genKey = async (): Promise<void> => {
    setMsg('')
    try {
      const r = await rpc<SshKeyOpResult>('ssh.key_generate', {})
      if (!r.ok) setMsg(`✗ ${r.error}`)
      else setMsg(`✓ 密钥已生成，指纹：${r.fingerprint}`)
      void refresh()
    } catch (e) {
      setMsg(`✗ ${String(e)}`)
    }
  }

  // 信任主机：daemon keyscan 回多行指纹，原样贴进 msg 区供目视比对
  const trust = async (c: SshConnInfo): Promise<void> => {
    setMsg('')
    try {
      const r = await rpc<SshTrustResult>('ssh.host_trust', { host: c.host, port: c.port ?? 22 })
      setMsg(r.ok ? `已记录到 known_hosts，请核对指纹：\n${r.fingerprints}` : `✗ ${r.error}`)
    } catch (e) {
      setMsg(`✗ ${String(e)}`)
    }
  }

  const copy = (text: string, what: string): void => {
    void navigator.clipboard.writeText(text).then(
      () => setMsg(`✓ 已复制${what}`),
      () => setMsg(`✗ 复制失败（浏览器拒绝剪贴板权限）`),
    )
  }

  // 开终端页签：只建页签，ssh.term_open 由 SshTerminal 挂载后按实际尺寸发起
  const openTerm = (c: SshConnInfo): void => {
    const k = seq
    setSeq(seq + 1)
    setTabs((ts) => [...ts, { key: k, conn: c }])
    setActive(k)
  }

  // 关页签：组件卸载时自己 term_close；这里只管摘页签 + 回退激活位
  const closeTab = (k: number): void => {
    setTabs((ts) => {
      const next = ts.filter((t) => t.key !== k)
      if (active === k) setActive(next.length ? next[next.length - 1].key : null)
      return next
    })
  }

  const pubkeyPath = key?.pubkey_path ?? ''
  const copyCmd = (c: SshConnInfo): string =>
    `ssh-copy-id -i "${pubkeyPath}" -p ${c.port ?? 22} ${c.user}@${c.host}`

  return (
    <div className="fn-view">
      <div className="fn-head">
        <span className="fn-title">SSH 终端</span>
        <span className="fn-tag">连接库 · 密钥 · 主机信任 · 终端</span>
        <span className="grow" />
        <button className="chip" onClick={() => void refresh()} title="重新拉取连接表与密钥状态">
          <Icon name="refresh" size={13} /> 刷新
        </button>
        <button className="chip" onClick={() => setFormOpen((v) => !v)}>
          <Icon name="plus" size={13} /> 添加连接
        </button>
      </div>
      <div className="fn-body ssh-body">
        <div className="ssh-side">
        {msg && <div className={msg.startsWith('✗') ? 'fn-err' : 'fn-ok'} style={{ whiteSpace: 'pre-wrap' }}>{msg}</div>}

        <div className="fn-card">
          <div className="fn-card-head">
            <Icon name="shield" size={14} />
            <span className="fn-card-name">本机密钥</span>
            <span className="grow" />
            {!key?.has_key && (
              <button className="chip chip-primary" onClick={() => void genKey()}>
                <Icon name="zap" size={13} /> 生成 ed25519 密钥
              </button>
            )}
          </div>
          {key?.has_key ? (
            <>
              <div className="fn-meta-row">
                <span className="fn-meta" title={pubkeyPath}>公钥：{pubkeyPath}</span>
                <span className="fn-meta">指纹：{key.fingerprint || '（读取失败）'}</span>
              </div>
              <div className="git-selbar">
                <button className="chip" onClick={() => copy(key.fingerprint ?? '', '指纹')}>复制指纹</button>
                {pubkeyPath !== '' && (
                  <button className="chip" onClick={() => copy(pubkeyPath, '公钥路径')}>复制公钥路径</button>
                )}
              </div>
            </>
          ) : (
            <div className="fn-empty">
              还没有密钥。生成后把公钥部署到远端（~/.ssh/authorized_keys），所有连接与终端都走密钥登录，不需要在这里输密码。
            </div>
          )}
        </div>

        {formOpen && (
          <div className="fn-card fn-form">
            <div className="fn-form-row">
              <input className="fn-input fn-input-slim" placeholder="名称（唯一，如 家用服务器）" value={name} onChange={(e) => setName(e.target.value)} />
              <input className="fn-input" placeholder="主机：IP 或域名" value={host} onChange={(e) => setHost(e.target.value)} />
              <input className="fn-input fn-input-slim" placeholder="登录用户 *" value={user} onChange={(e) => setUser(e.target.value)} />
              <input className="fn-num" type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} title="端口（默认 22）" />
            </div>
            <div className="fn-form-row">
              <input className="fn-input" placeholder="私钥路径（可选，留空 = ~/.iwan/ssh/id_ed25519）" value={keyFile} onChange={(e) => setKeyFile(e.target.value)} />
              <span className="grow" />
              <button className="chip" onClick={() => void addConn()} disabled={!name.trim() || !host.trim() || !user.trim()}>
                <Icon name="check" size={13} /> 保存
              </button>
            </div>
          </div>
        )}

        {!conns.length && !formOpen && (
          <div className="fn-empty">还没有 SSH 连接。点「添加连接」登记一台主机，之后可以在 SSH 终端页签里直接开远端 shell。</div>
        )}
        {conns.map((c) => (
          <div key={c.id} className="fn-card">
            <div className="fn-card-head">
              <Icon name="monitor" size={14} />
              <span className="fn-card-name">{c.name}</span>
              <span className="fn-tag">{c.user}@{c.host}{c.port !== 22 ? `:${c.port}` : ''}</span>
              {c.key_file && <span className="fn-tag" title={c.key_file}>自定义私钥</span>}
              <span className="grow" />
              <button className="chip chip-primary" onClick={() => openTerm(c)} title="对这台主机开一个交互终端页签">
                <Icon name="monitor" size={13} /> 开终端
              </button>
              <button className="chip" onClick={() => void trust(c)} title="keyscan 取回主机密钥并记录，指纹会显示出来供核对">
                <Icon name="shield" size={13} /> 信任此主机
              </button>
              {key?.has_key && pubkeyPath !== '' && (
                <button className="chip" onClick={() => copy(copyCmd(c), '部署命令')} title="在终端执行即可把公钥部署到这台主机">
                  <Icon name="send" size={13} /> 复制部署命令
                </button>
              )}
              <button
                className="icon-btn tiny"
                title="删除连接（不碰密钥与 known_hosts）"
                onClick={() => {
                  if (window.confirm(`删除连接「${c.name}」？不会碰密钥与主机信任记录。`)) void connOp('ssh.conn_delete', { id: c.id })
                }}
              >
                <Icon name="trash" size={13} />
              </button>
            </div>
          </div>
        ))}
        </div>

        <div className="ssh-term">
          {tabs.length > 0 && (
            <div className="ssh-tabs">
              {tabs.map((t) => (
                <span key={t.key} className={`ssh-tab${active === t.key ? ' ssh-tab-on' : ''}`} onClick={() => setActive(t.key)}>
                  {t.conn.name}
                  <button
                    className="ssh-tab-x"
                    title="关闭页签"
                    onClick={(e) => {
                      e.stopPropagation()
                      closeTab(t.key)
                    }}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          {active === null ? (
            <div className="ssh-term-empty">
              还没有打开的终端。左侧连接卡片点「开终端」——每个页签是一条独立的 ssh 会话。
            </div>
          ) : (
            tabs.filter((t) => t.key === active).map((t) => (
              <SshTerminal key={t.key} conn={t.conn} onClose={() => closeTab(t.key)} />
            ))
          )}
        </div>
      </div>
    </div>
  )
}
