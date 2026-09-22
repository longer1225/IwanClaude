// preload 注入的 window.iwan 桥的类型声明——渲染层全局唯一的外联面
interface IwanBridge {
  request(method: string, params?: Record<string, unknown>): Promise<{ ok: boolean; result?: Record<string, unknown>; error?: string }>
  onEvent(cb: (event: Record<string, unknown>) => void): () => void
  onStatus(cb: (status: string) => void): () => void
  pickDirectory(): Promise<string | null>
  getStatus(): Promise<string>
  readDir(root: string, rel: string): Promise<Array<{ name: string; dir: boolean }>>
  readFile(root: string, rel: string): Promise<{ text: string; truncated: boolean; size: number; binary: boolean }>
  sessionsMeta(ids: string[]): Promise<Record<string, { cwd: string; created_at: string; run_ids: string[] }>>
  runTasks(sid: string, runId: string): Promise<Array<Record<string, unknown>>>
  readConfig(): Promise<{ exists: boolean; raw: string; toml: unknown }>
  getSettings(): Promise<Record<string, unknown>>
  setSettings(patch: Record<string, unknown>): Promise<Record<string, unknown>>
  openPath(p: string): Promise<string>
  pickFile(): Promise<string | null>
}

declare global {
  interface Window {
    iwan: IwanBridge
  }
}

export {}
