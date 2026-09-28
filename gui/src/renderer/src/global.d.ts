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
  // 自绘顶栏窗口控制：最小化/最大化切换/关闭 + 最大化态订阅（初值靠 winIsMaximized 补查）
  winMinimize(): void
  winToggleMaximize(): void
  winClose(): void
  winIsMaximized(): Promise<boolean>
  onWinMaximized(cb: (maximized: boolean) => void): () => void
}

declare global {
  interface Window {
    iwan: IwanBridge
  }
}

export {}
