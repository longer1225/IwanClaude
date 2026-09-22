// preload：contextBridge 白名单桥——渲染层唯一能摸到的三个能力
//
// 【学习要点】刻意【不暴露】裸 ipcRenderer：桥面越窄，Renderer（跑不受信
// Markdown 内容的地方）能掀起的浪越小。request 返回值统一包 {ok,result|error}，
// 让渲染层不必处理主进程抛错通道的怪异语义。
import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('iwan', {
  // 发 JSON-RPC 请求；method 用 WIRE_PROTOCOL.md 里的命令名，params 不含 type 字段
  request: (method: string, params?: Record<string, unknown>): Promise<{ ok: boolean; result?: Record<string, unknown>; error?: string }> =>
    ipcRenderer.invoke('rpc:request', method, params ?? {}),
  // 订阅 daemon 推送事件；返回退订函数
  onEvent: (cb: (event: Record<string, unknown>) => void): (() => void) => {
    const h = (_e: unknown, ev: Record<string, unknown>): void => cb(ev)
    ipcRenderer.on('iwan:event', h)
    return () => ipcRenderer.removeListener('iwan:event', h)
  },
  // 订阅连接状态（connecting/connected/reconnecting/error）
  onStatus: (cb: (status: string) => void): (() => void) => {
    const h = (_e: unknown, s: string): void => cb(s)
    ipcRenderer.on('iwan:status', h)
    return () => ipcRenderer.removeListener('iwan:status', h)
  },
  // 目录选择对话框（"选择项目"）
  pickDirectory(): Promise<string | null> {
    return ipcRenderer.invoke('ui:pick-directory')
  },
  // 补查当前连接状态（渲染层挂载晚于首个 status 事件时兜底）
  getStatus: (): Promise<string> => ipcRenderer.invoke('ui:status'),
  // 文件桥：项目目录树 / 文本预览（渲染层只能浏览传入 root 以内的路径）
  readDir: (root: string, rel: string): Promise<Array<{ name: string; dir: boolean }>> =>
    ipcRenderer.invoke('fs:readDir', root, rel),
  readFile: (
    root: string,
    rel: string
  ): Promise<{ text: string; truncated: boolean; size: number; binary: boolean }> =>
    ipcRenderer.invoke('fs:readFile', root, rel),
  // 批量读会话 meta（cwd/创建时间/run 列表）——session.list 不返回这些
  sessionsMeta: (
    ids: string[]
  ): Promise<Record<string, { cwd: string; created_at: string; run_ids: string[] }>> =>
    ipcRenderer.invoke('sessions:meta', ids),
  // 读某个 run 的任务清单
  runTasks: (sid: string, runId: string): Promise<Array<Record<string, unknown>>> =>
    ipcRenderer.invoke('runs:tasks', sid, runId),
  // 只读 daemon 的 config.toml（MCP 面板/关于页用；GUI 永不写它）
  readConfig: (): Promise<{ exists: boolean; raw: string; toml: unknown }> =>
    ipcRenderer.invoke('config:read'),
  // GUI 自身设置（主题/字号/项目登记表），存 ~/.iwan/gui.json
  getSettings: (): Promise<Record<string, unknown>> => ipcRenderer.invoke('settings:get'),
  setSettings: (patch: Record<string, unknown>): Promise<Record<string, unknown>> =>
    ipcRenderer.invoke('settings:set', patch),
  // 在系统文件管理器中打开目录
  openPath: (p: string): Promise<string> => ipcRenderer.invoke('ui:open-path', p),
  // 单选文件对话框（附件）
  pickFile: (): Promise<string | null> => ipcRenderer.invoke('ui:pick-file')
})
