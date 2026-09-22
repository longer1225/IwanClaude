// iwan-core daemon 懒启动：探测 :7437 不通时在仓库根起 `uv run iwan-core`（抄 CLI 的既有语义）
//
// 【学习要点】daemon 冷启动要几十秒（langgraph 导入），所以这里 spawn 之后【不等】它——
// 连接重试循环（rpc-transport.start）负责"起来一个窗口期就撞上"，
// 与 GUI 退出时【不】杀 daemon 的多客户端语义配套：桌面应用是客人，不是主人。
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

let spawning = false

// 从安装包/开发目录两种形态反推 iwanclaude 仓库根（找同时含 pyproject.toml 与 src/iwan_claude 的目录）
function findRepoRoot(): string | null {
  // electron-vite 产物布局：out/main/index.js —— 开发态时 __dirname 位于仓库内，向上爬即可
  let dir = __dirname
  for (let i = 0; i < 8; i++) {
    if (existsSync(path.join(dir, 'pyproject.toml')) && existsSync(path.join(dir, 'src', 'iwan_claude'))) {
      return dir
    }
    const up = path.dirname(dir)
    if (up === dir) break
    dir = up
  }
  // 兜底：electron 进程 cwd 常为仓库根或 gui/
  for (const cand of [process.cwd(), path.join(process.cwd(), '..')]) {
    if (existsSync(path.join(cand, 'pyproject.toml'))) return cand
  }
  return null
}

// 拉起 daemon 子进程（detached + windowsHide：GUI 关了它还在，也不闪黑窗）
export async function ensureDaemon(): Promise<void> {
  if (spawning) return
  const root = findRepoRoot()
  if (!root) {
    console.warn('[daemon] 找不到仓库根（无 pyproject.toml），跳过懒启动——请手动运行 uv run iwan-core')
    return
  }
  spawning = true
  try {
    const child = spawn('uv', ['run', 'iwan-core'], {
      cwd: root,
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
      shell: process.platform === 'win32' // uv 在 Windows 上可能是 uv.cmd，需经 shell 解析
    })
    child.on('error', (err) => console.warn('[daemon] spawn 失败:', err.message))
    child.unref()
    console.log(`[daemon] 已在 ${root} 发起 uv run iwan-core`)
  } finally {
    spawning = false
  }
}
