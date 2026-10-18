// iwan-core daemon 懒启动：探测 :7437 不通时在仓库根起 `uv run iwan-core`（抄 CLI 的既有语义）
//
// 【学习要点】daemon 冷启动要几十秒（langgraph 导入），所以这里 spawn 之后【不等】它——
// 连接重试循环（rpc-transport.start）负责"起来一个窗口期就撞上"，
// 与 GUI 退出时【不】杀 daemon 的多客户端语义配套：桌面应用是客人，不是主人。
//
// 【学习要点】daemon 的启动失败原因必须能被 GUI 读到：早先 stdio:'ignore' 把一切
// 吞掉，daemon 因缺 API key 一行 "not set" 就退出时，用户只看到界面一直"连接中"，
// 看起来像崩溃。Windows 下 detached + fd 重定向并不可靠（实测日志为空），所以改成
// "由 daemon 自己落盘"：spawn 时传 IWAN_STARTUP_ERROR_FILE，daemon 入口捕获启动期
// 异常写进去（见 core/startup.py），GUI 读该文件即可。
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

let spawning = false
let lastExit: { code: number | null; at: number } | null = null

// daemon 启动失败标记：GUI 指定路径（临时目录一定可写），daemon 负责写入
export function startupErrorPath(): string {
  return path.join(os.tmpdir(), 'iwan-daemon-startup-error.txt')
}

// 读取 daemon 自写的启动失败原因（读到空串表示没有）
function readStartupError(): string {
  try {
    const text = fs.readFileSync(startupErrorPath(), 'utf8').trim()
    return text.split(/\r?\n/).filter(Boolean).pop() ?? ''
  } catch {
    return ''
  }
}

// 提炼一句可读的失败原因（标记文件 → 退出码）
// 【设计】不再读 daemon 自己的 core.log：那是"跨运行累积"的日志，里面可能有十天前
// 的 402/超时，拿来当"现在起不来的原因"会误导（实测展示过一条陈旧余额错误）。
// 只信本次启动写入的标记文件；没有就退回退出码。
export function describeDaemonFailure(): string {
  const fromMarker = readStartupError()
  if (fromMarker) return fromMarker.slice(0, 300)
  return lastExit ? `daemon 退出（code ${lastExit.code ?? '?'}），没有留下错误输出` : ''
}

// 从安装包/开发目录两种形态反推 iwanclaude 仓库根（找同时含 pyproject.toml 与 src/iwan_claude 的目录）
function findRepoRoot(): string | null {
  // electron-vite 产物布局：out/main/index.js —— 开发态时 __dirname 位于仓库内，向上爬即可
  let dir = __dirname
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(dir, 'pyproject.toml')) && fs.existsSync(path.join(dir, 'src', 'iwan_claude'))) {
      return dir
    }
    const up = path.dirname(dir)
    if (up === dir) break
    dir = up
  }
  // 兜底：electron 进程 cwd 常为仓库根或 gui/
  for (const cand of [process.cwd(), path.join(process.cwd(), '..')]) {
    if (fs.existsSync(path.join(cand, 'pyproject.toml'))) return cand
  }
  return null
}

// 拉起 daemon 子进程（detached + windowsHide：GUI 关了它还在，也不闪黑窗）
export async function ensureDaemon(onExit?: (code: number | null) => void): Promise<void> {
  if (spawning) return
  const root = findRepoRoot()
  if (!root) {
    console.warn('[daemon] 找不到仓库根（无 pyproject.toml），跳过懒启动——请手动运行 uv run iwan-core')
    return
  }
  spawning = true
  try {
    // 启动前清掉旧标记：否则会把上一次的失败原因当成"现在的问题"
    try {
      fs.unlinkSync(startupErrorPath())
    } catch {
      /* 不存在是常态 */
    }
    // 【学习要点】不要用 shell:true 再套一层 cmd.exe：Electron 主进程里实测会以
    // "code 2 + 零输出"失败（cmd 参数转义/控制台差异），而直接 spawn uv.exe 走
    // CreateProcess 搜 PATH 是可靠的（找不到会触发 child 'error'）。因此显式补
    // .exe 后缀并 shell:false。
    const uvBin = process.platform === 'win32' ? 'uv.exe' : 'uv'
    const child = spawn(uvBin, ['run', 'iwan-core'], {
      cwd: root,
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
      shell: false,
      env: { ...process.env, IWAN_STARTUP_ERROR_FILE: startupErrorPath() }
    })
    child.on('error', (err) => console.warn('[daemon] spawn 失败:', err.message))
    child.on('exit', (code) => {
      lastExit = { code, at: Date.now() }
      // 提前把"daemon 起不来"的原因交回主进程 → 渲染层立即能看到，而不是干等 2 分钟
      onExit?.(code)
    })
    child.unref()
    console.log(`[daemon] 已在 ${root} 发起 uv run iwan-core`)
  } finally {
    spawning = false
  }
}

