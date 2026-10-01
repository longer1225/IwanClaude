// iwan-core daemon 的 TCP JSON-RPC 客户端（NDJSON 分帧）——Electron 主进程侧的"SocketClient"
//
// 【学习要点】本文件逐行为对齐 src/iwan_claude/core/transport/socket_client.py：
// 1) 请求带 id（uuid），响应靠 "jsonrpc" 字段识别并回填 pending 表；
// 2) 服务器主动推送包成 {"kind":"event","event":{...}}——无 id 无 jsonrpc 字段；
// 3) 单行最大 64MB（MCP 工具大结果），Node 的 readline 默认无限、够用，但仍显式设上限思想一致；
// 4) params 里【不放】type 字段（与 TUI 的 send_command 调用方式完全相同，服务端按 method 分派）。
import net from 'node:net'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { EventEmitter } from 'node:events'
import readline from 'node:readline'

// 从 ~/.iwan/config.toml 文本里抠 [core] token（极简定向正则，不引 toml 解析依赖）
// 只在行首作用域内取 [core] 段第一次出现的 token = "..."
function parseCoreToken(text: string): string {
  const core = text.match(/^\[core\][\s\S]*?(?=^\[|$)/m)
  if (!core) return ''
  const kv = core[0].match(/^\s*token\s*=\s*"([^"]*)"/m)
  return kv ? kv[1] : ''
}

export type RpcParams = Record<string, unknown>

interface Pending {
  resolve: (result: Record<string, unknown>) => void
  reject: (err: Error) => void
}

// 主进程 ⇄ 渲染进程转发的连接状态机
export type ConnStatus = 'connecting' | 'connected' | 'reconnecting' | 'error'

export class RpcTransport extends EventEmitter {
  private sock: net.Socket | null = null
  private rl: readline.Interface | null = null
  private pending = new Map<string, Pending>()
  private host = '127.0.0.1'
  private port = 7437
  // 鉴权令牌（协议缺口 #4）：空串 = 拨号后直接进入正常收发，不握手
  private token = ''
  private closedByUs = false
  status: ConnStatus = 'connecting'

  // 从环境变量/默认值确定 daemon 地址（与 config.py 的 IWAN_PORT 语义一致）
  constructor() {
    super()
    const p = Number(process.env.IWAN_PORT ?? '7437')
    if (Number.isFinite(p) && p > 0) this.port = p
    if (process.env.IWAN_HOST) this.host = process.env.IWAN_HOST
    // 令牌三级来源：IWAN_TOKEN env > ~/.iwan/config.toml [core] token > 空(免鉴权)
    this.token = process.env.IWAN_TOKEN ?? ''
    if (!this.token) {
      try {
        this.token = parseCoreToken(
          fs.readFileSync(path.join(os.homedir(), '.iwan', 'config.toml'), 'utf8')
        )
      } catch {
        this.token = '' // 配置文件不存在 = 无令牌，与 daemon 端"缺省静默跳过"同纪律
      }
    }
  }

  // 设置"连不上就拉起 daemon"的重试策略挂钩：start 内部会循环重试直到成功或放弃
  async start(ensureDaemon: () => Promise<void>): Promise<void> {
    this.closedByUs = false
    // 首次拨号失败 → 调用 ensureDaemon（懒启动）后持续重试，上限 120 秒
    let attempt = 0
    const retry = (): Promise<void> =>
      new Promise((resolve, reject) => {
        const tryOnce = (): void => {
          this.dial()
            .then(resolve)
            .catch(async (err: unknown) => {
              attempt += 1
              if (attempt === 1) await ensureDaemon().catch(() => undefined)
              if (this.closedByUs || attempt > 120) {
                this.setStatus('error')
                reject(err)
                return
              }
              this.setStatus(attempt === 1 ? 'connecting' : 'reconnecting')
              setTimeout(tryOnce, 1000)
            })
        }
        tryOnce()
      })
    await retry()
  }

  // 建立 TCP 连接并挂上行读取/派发逻辑；error 监听只在"握手未完成"阶段负责
  // reject 本次 dial，连上之后立刻摘除，换 readLoop 的常驻兜底
  private dial(): Promise<void> {
    return new Promise((resolve, reject) => {
      const sock = net.createConnection({ host: this.host, port: this.port })
      let settled = false
      sock.once('connect', () => {
        settled = true
        sock.removeListener('error', onErr)
        this.sock = sock
        this.readLoop(sock)
        // 有令牌：readLoop 已就位（hello 应答走 dispatch 回填 pending），
        // 握手成功才算 connected；失败 destroy 后 reject，交给外层重试环兜底
        if (!this.token) {
          this.setStatus('connected')
          resolve()
          return
        }
        this.request('auth.hello', { token: this.token })
          .then(() => {
            this.setStatus('connected')
            resolve()
          })
          .catch((err: Error) => {
            this.sock = null
            sock.destroy()
            reject(err)
          })
      })
      const onErr = (err: Error): void => {
        sock.destroy()
        if (!settled) reject(err)
      }
      sock.once('error', onErr)
    })
  }

  // readline 按行分帧 NDJSON；断线后清空 pending（渲染层收到 reconnecting 状态自行重订阅）
  private readLoop(sock: net.Socket): void {
    // 【学习要点】error 兜底要挂【三个对象】，少一个就是一场冻结事故：
    // taskkill 杀 daemon 以 RST 收尾时——①socket 层的 error 先由 no-op 吸收
    // （node 对无监听者的流 error 直接 throw，沿 libuv 冒成未捕获异常，
    // Electron 主进程默认弹原生错误对话框并阻塞整个主线程）；②readline 会把
    // input 流的错误【代理再发一遍到它自己的 Interface 实例】（proxyEvents
    // 机制），rl 无监听照样炸第二发；③最关键：错误被吸收后 readline 的
    // 'close' 在 RST 路径上根本不来（实测），只挂 rl.on('close') 重连会永远
    // 不触发——GUI 卡死 reconnecting 假活。重连触发点必须挂在 socket 的
    // 'close' 上（EOF/RST 都必达），与 rl.close 用 done 标志去重。
    sock.on('error', () => {})
    const rl = readline.createInterface({ input: sock })
    rl.on('error', () => {})
    this.rl = rl
    rl.on('line', (line) => this.dispatch(line))
    let done = false
    const onGone = (): void => {
      if (done) return
      done = true
      const hadPending = this.pending.size > 0
      for (const p of this.pending.values()) p.reject(new Error('connection lost'))
      this.pending.clear()
      if (!this.closedByUs) {
        this.setStatus('reconnecting')
        // 指数退避重连：1s 起步封顶 10s
        const backoff = (ms: number): void => {
          setTimeout(() => {
            this.dial().catch(() => {
              if (!this.closedByUs) backoff(Math.min(ms * 2, 10000))
            })
          }, ms)
        }
        backoff(hadPending ? 500 : 1000)
      }
    }
    rl.on('close', onGone)
    sock.on('close', onGone)
  }

  // 单行消息派发：有 jsonrpc → 回填 pending；kind=event → 发 'event' 给上层
  private dispatch(line: string): void {
    if (!line.trim()) return
    let msg: Record<string, unknown>
    try {
      msg = JSON.parse(line) as Record<string, unknown>
    } catch {
      return // 容错：坏行直接丢弃（同 Python 端 _dispatch 的 JSONDecodeError 分支）
    }
    if ('jsonrpc' in msg) {
      const id = msg.id as string | undefined
      const p = id ? this.pending.get(id) : undefined
      if (p && id) {
        this.pending.delete(id)
        if (msg.error) {
          const err = msg.error as { code?: number; message?: string }
          p.reject(new Error(`[${err.code ?? -1}] ${err.message ?? 'unknown'}`))
        } else {
          p.resolve((msg.result ?? {}) as Record<string, unknown>)
        }
      }
    } else if (msg.kind === 'event') {
      this.emit('event', msg.event as Record<string, unknown>)
    }
  }

  // 发送 JSON-RPC 请求返回 result——信封格式与 envelope.JsonRpcRequest 的 model_dump 一致
  request(method: string, params: RpcParams): Promise<Record<string, unknown>> {
    if (!this.sock || this.sock.destroyed) return Promise.reject(new Error('daemon 未连接'))
    const id = randomUUID()
    const req = { jsonrpc: '2.0', id, method, params }
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.sock!.write(JSON.stringify(req) + '\n', (err) => {
        if (err) {
          this.pending.delete(id)
          reject(err)
        }
      })
    })
  }

  // 主动关闭（app quit 时）：不再触发重连
  stop(): void {
    this.closedByUs = true
    this.rl?.close()
    this.sock?.destroy()
    this.sock = null
  }

  private setStatus(s: ConnStatus): void {
    this.status = s
    this.emit('status', s)
  }
}
