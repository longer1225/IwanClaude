// 主进程文件系统桥：目录树 / 文件预览 / 会话 meta / 任务清单 / 配置读取 / GUI 设置
//
// 【学习要点】这就是 VS Code、Codex 这类 Electron IDE 的共同骨架：
//   渲染进程（浏览器沙箱）没有摸磁盘的权力，一切文件操作发给主进程（Node 全权
//   限）执行，结果经 IPC 回传。三件套缺一不可：①路径约束（只许在指定 root 内）
//   ②体积上限（读文件 256KB 截断，防止一个 node_modules 撑爆内存）
//   ③编码兜底（Windows 上历史文件可能是 GBK，先 utf-8 严格解码、失败退 gbk）。
// 本文件是"零后端改动"约束的合规做法：daemon 写在 ~/.iwan 下的 meta.json /
// task_*.json / config.toml 都是普通文件，只读不写，不碰任何 Python。
import { ipcMain } from 'electron'
import fs from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'

const IWAN_DIR = path.join(os.homedir(), '.iwan')
const SESSIONS_DIR = process.env.IWAN_SESSIONS_DIR || path.join(IWAN_DIR, 'sessions')
const CONFIG_PATH = path.join(IWAN_DIR, 'config.toml')
const GUI_SETTINGS_PATH = path.join(IWAN_DIR, 'gui.json')
const MAX_FILE_BYTES = 256 * 1024
const MAX_DIR_ENTRIES = 2000

interface DirEntry {
  name: string
  dir: boolean
}

// 把相对路径安全地解析进 root：越界（.. 逃逸）、含空字节一律拒绝
function safeJoin(root: string, rel: string): string | null {
  if (rel.includes('\0')) return null
  const abs = path.resolve(root, rel)
  const normRoot = path.resolve(root)
  if (abs === normRoot || abs.startsWith(normRoot + path.sep)) return abs
  return null
}

// 读一个目录（返回 名字+是否目录，排序交给渲染层决定展示口径）
async function readDir(root: string, rel: string): Promise<DirEntry[]> {
  const abs = safeJoin(root, rel)
  if (!abs) throw new Error('路径越界：只允许在项目目录内浏览')
  const dirents = await fs.readdir(abs, { withFileTypes: true })
  return dirents.slice(0, MAX_DIR_ENTRIES).map((d) => ({ name: d.name, dir: d.isDirectory() }))
}

// 【学习要点】utf-8 严格解码失败 → GBK 退路：daemon 部分历史文件在中文 Windows
// 上用系统默认编码落盘（实测 .tasks/*.json 是 GBK）。TextDecoder('gbk') 依赖
// Electron 自带的全量 ICU，普通 Node 精简构建可能没有——GUI 跑在 Electron 里，安全。
async function decodeFile(abs: string): Promise<{ text: string; truncated: boolean; size: number; binary: boolean }> {
  const buf = await fs.readFile(abs)
  const head = new Uint8Array(buf.buffer, buf.byteOffset, Math.min(buf.byteLength, 8192))
  // NUL 字节出现在前 8KB → 当作二进制文件，不渲染乱码
  if (head.includes(0)) return { text: '', truncated: false, size: buf.byteLength, binary: true }
  let text: string
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(buf)
  } catch {
    try {
      text = new TextDecoder('gbk').decode(buf)
    } catch {
      return { text: '', truncated: false, size: buf.byteLength, binary: true }
    }
  }
  const truncated = buf.byteLength > MAX_FILE_BYTES
  return { text: text.slice(0, MAX_FILE_BYTES), truncated, size: buf.byteLength, binary: false }
}

// 预览一个文本文件（root 内路径）
async function readFile(root: string, rel: string) {
  const abs = safeJoin(root, rel)
  if (!abs) throw new Error('路径越界：只允许在项目目录内读取')
  return decodeFile(abs)
}

// 会话 id/run id 都来自 daemon 数据，用后即校验再拼路径——防止把协议字段当可信输入
function isSid(s: string): boolean {
  return /^sess-[0-9a-z]+$/i.test(s)
}
function isRunId(s: string): boolean {
  return /^[0-9a-z-]{8,64}$/i.test(s)
}

// 批量读 ~/.iwan/sessions/<sid>/meta.json：session.list 不带 cwd，磁盘上的 meta 才有
async function sessionsMeta(ids: string[]): Promise<Record<string, { cwd: string; created_at: string; run_ids: string[] }>> {
  const out: Record<string, { cwd: string; created_at: string; run_ids: string[] }> = {}
  for (const id of ids.slice(0, 500)) {
    if (!isSid(id)) continue
    try {
      const f = await decodeFile(path.join(SESSIONS_DIR, id, 'meta.json'))
      const d = JSON.parse(f.text) as Record<string, unknown>
      out[id] = {
        cwd: typeof d.cwd === 'string' ? d.cwd : '',
        created_at: typeof d.created_at === 'string' ? d.created_at : '',
        run_ids: Array.isArray(d.run_ids) ? (d.run_ids as string[]).filter(isRunId) : []
      }
    } catch {
      /* 老会话或写坏的文件：跳过，前端账本兜底 */
    }
  }
  return out
}

// 读某个 run 的任务清单（.tasks/task_N.json），按 id 排序
async function runTasks(sid: string, runId: string): Promise<Array<Record<string, unknown>>> {
  if (!isSid(sid) || !isRunId(runId)) return []
  const dir = path.join(SESSIONS_DIR, sid, 'runs', runId, '.tasks')
  let names: string[] = []
  try {
    names = (await fs.readdir(dir)).filter((n) => /^task_\d+\.json$/.test(n))
  } catch {
    return []
  }
  const tasks: Array<Record<string, unknown>> = []
  for (const n of names) {
    try {
      const f = await decodeFile(path.join(dir, n))
      tasks.push(JSON.parse(f.text) as Record<string, unknown>)
    } catch {
      /* 单文件坏掉不影响整表 */
    }
  }
  tasks.sort((a, b) => Number(a.id ?? 0) - Number(b.id ?? 0))
  return tasks
}

// 【学习要点】手写的极简 TOML 子集解析器：只认 [节]、[[数组表]]、key = "值"/true/数字，
// 够取 mcp.servers 与 agent/permission/llm 的展示字段。为什么不用 npm 上的 toml 库：
// 新增依赖要走项目决策，而这里读的只是给用户"看一眼"的配置，20 行换 0 供应链风险。
interface TomlLite {
  tables: Record<string, Record<string, string | number | boolean>>
  arrays: Record<string, Array<Record<string, string>>>
}
function parseTomlLite(src: string): TomlLite {
  const out: TomlLite = { tables: {}, arrays: {} }
  let section = ''
  let arrayObj: Record<string, string> | null = null
  for (const rawLine of src.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const arr = line.match(/^\[\[([^\]]+)\]\]$/)
    if (arr) {
      section = arr[1]
      arrayObj = {}
      ;(out.arrays[section] ??= []).push(arrayObj)
      continue
    }
    const sec = line.match(/^\[([^\]]+)\]$/)
    if (sec) {
      section = sec[1]
      arrayObj = null
      continue
    }
    const kv = line.match(/^([A-Za-z0-9_.-]+)\s*=\s*(.+)$/)
    if (!kv) continue
    const key = kv[1]
    let val: string | number | boolean = kv[2].trim().replace(/\s*#.*$/, '')
    if (/^".*"$/.test(val)) val = val.slice(1, -1)
    else if (/^(true|false)$/.test(val)) val = val === 'true'
    else if (/^-?\d+(\.\d+)?$/.test(val)) val = Number(val)
    if (arrayObj && section.startsWith('mcp.')) {
      arrayObj[key] = String(val)
    } else {
      const t = (out.tables[section] ??= {})
      t[key] = val
    }
  }
  return out
}

// 读 daemon 的 config.toml（GUI 只展示不改写——写它=改后端行为，须用户自己动手）
async function readConfig(): Promise<{ exists: boolean; raw: string; toml: TomlLite | null }> {
  try {
    const f = await decodeFile(CONFIG_PATH)
    return { exists: true, raw: f.text, toml: parseTomlLite(f.text) }
  } catch {
    return { exists: false, raw: '', toml: null }
  }
}

// GUI 自身设置（主题/字号等）：~/.iwan/gui.json，Electron 写、渲染层读，daemon 不认识它
async function settingsGet(): Promise<Record<string, unknown>> {
  try {
    const f = await decodeFile(GUI_SETTINGS_PATH)
    return JSON.parse(f.text) as Record<string, unknown>
  } catch {
    return {}
  }
}

// 合并写 GUI 设置（浅合并，顶层键级覆盖；写失败冒泡给渲染层提示）
async function settingsSet(patch: Record<string, unknown>): Promise<Record<string, unknown>> {
  const cur = await settingsGet()
  const next = { ...cur, ...patch }
  await fs.mkdir(IWAN_DIR, { recursive: true })
  await fs.writeFile(GUI_SETTINGS_PATH, JSON.stringify(next, null, 2), 'utf-8')
  return next
}

// 注册全部文件桥通道（main 启动时调一次）
export function registerFsBridge(): void {
  ipcMain.handle('fs:readDir', (_e, root: string, rel: string) => {
    if (typeof root !== 'string' || !path.isAbsolute(root)) throw new Error('root 必须是绝对路径')
    return readDir(root, typeof rel === 'string' ? rel : '')
  })
  ipcMain.handle('fs:readFile', (_e, root: string, rel: string) => {
    if (typeof root !== 'string' || !path.isAbsolute(root)) throw new Error('root 必须是绝对路径')
    return readFile(root, typeof rel === 'string' ? rel : '')
  })
  ipcMain.handle('sessions:meta', (_e, ids: unknown) => sessionsMeta(Array.isArray(ids) ? ids.filter((x): x is string => typeof x === 'string') : []))
  ipcMain.handle('runs:tasks', (_e, sid: string, runId: string) => runTasks(sid, runId))
  ipcMain.handle('config:read', () => readConfig())
  ipcMain.handle('settings:get', () => settingsGet())
  ipcMain.handle('settings:set', (_e, patch: Record<string, unknown>) =>
    settingsSet(patch && typeof patch === 'object' ? patch : {})
  )
  // 在系统文件管理器里打开目录（项目菜单用；shell.openPath 不校验越界——
  // 这是"打开资源管理器"不是"读文件"，路径来自用户自己登记的项目/对话框）
  ipcMain.handle('ui:open-path', (_e, p: string) => {
    if (typeof p !== 'string') return 'bad path'
    return import('electron').then(({ shell }) => shell.openPath(p))
  })
  // 单选文件对话框（Composer 附件用）：返回绝对路径或 null
  ipcMain.handle('ui:pick-file', async (e) => {
    const { dialog, BrowserWindow } = await import('electron')
    const win = BrowserWindow.fromWebContents(e.sender)
    if (!win) return null
    const r = await dialog.showOpenDialog(win, { properties: ['openFile'], title: '添加文件' })
    return r.canceled ? null : r.filePaths[0]
  })
}
