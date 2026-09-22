// 渲染层的 RPC 出站口：把 preload 桥的 {ok,result|error} 解包成 Promise<result>
import type { BusEvent } from './protocol/types'

// 发一条命令；失败抛 Error（消息含 daemon 的 JSON-RPC 错误码语义）
export async function rpc<T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  const r = await window.iwan.request(method, params)
  if (!r.ok) throw new Error(r.error ?? `${method} 调用失败`)
  return (r.result ?? {}) as T
}

// 订阅 daemon 事件推送，返回退订函数
export function onDaemonEvent(cb: (ev: BusEvent) => void): () => void {
  return window.iwan.onEvent((raw) => cb(raw as unknown as BusEvent))
}

// 订阅连接状态变化，返回退订函数
export function onConnStatus(cb: (s: string) => void): () => void {
  return window.iwan.onStatus(cb)
}

// 补查主进程缓存的当前连接状态（挂载晚于首个事件时兜底）
export function getStatus(): Promise<string> {
  return window.iwan.getStatus()
}
