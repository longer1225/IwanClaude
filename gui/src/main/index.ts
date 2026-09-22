// Electron 主进程入口：建窗口 + 装 RpcTransport + 定义 ipc 通道
//
// 【学习要点】安全红线照抄官方纪律：渲染进程 nodeIntegration=false、
// contextIsolation=true——页面代码摸不到 node，一切 TCP/文件系统能力
// 都经 preload 的两个白名单通道过来。这也是 Codex/Electron 应用的标准分层。
import { app, BrowserWindow, ipcMain, dialog, shell } from 'electron'
import path from 'node:path'
import { RpcTransport } from './rpc-transport'
import { ensureDaemon } from './daemon'
import { registerFsBridge } from './fs-bridge'

let win: BrowserWindow | null = null
let transport: RpcTransport | null = null

// 创建主窗口：1440x900 起始、系统标题栏（frameless 不做——决策见设计文档复刻篇 §3 区域 A）
function createWindow(): void {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 980,
    minHeight: 640,
    title: 'iwan',
    backgroundColor: '#F6F1E7', // 首帧即暖色，避免白闪（渐变带的顶色）
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false
    }
  })

  transport = new RpcTransport()
  registerFsBridge() // 文件树/任务/meta/配置/GUI设置 五路磁盘桥
  transport.on('event', (ev) => win?.isDestroyed() || win?.webContents.send('iwan:event', ev))
  // 缓存最近一次状态：渲染层挂载晚于 connected 时，靠 ui:status 补查兜底（否则永远卡在连接中）
  let lastStatus = 'connecting'
  transport.on('status', (s) => {
    lastStatus = s
    if (!win?.isDestroyed()) win?.webContents.send('iwan:status', s)
  })
  ipcMain.handle('ui:status', () => lastStatus)
  transport.start(ensureDaemon).catch((err) =>
    console.warn('[main] daemon 连接最终失败:', String(err))
  )

  ipcMain.handle('rpc:request', async (_e, method: string, params: Record<string, unknown>) => {
    try {
      const result = await transport?.request(method, params ?? {})
      return { ok: true, result }
    } catch (err) {
      return { ok: false, error: String(err instanceof Error ? err.message : err) }
    }
  })

  // 目录选择对话框："选择项目"按钮专用——返回绝对路径或 null
  ipcMain.handle('ui:pick-directory', async () => {
    const r = await dialog.showOpenDialog(win!, { properties: ['openDirectory'], title: '选择项目目录' })
    return r.canceled ? null : r.filePaths[0]
  })

  // 外部链接一律交给系统浏览器，应用内永不导航到远端
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url)
    return { action: 'deny' }
  })

  // 渲染层诊断三件套：页面空白时靠 stdout 里的这些行定位（console/加载失败/崩溃）
  win.webContents.on('console-message', (_e, level, message, line, sourceId) => {
    console.log(`[renderer:${level}] ${message} (${sourceId.split('/').pop()}:${line})`)
  })
  win.webContents.on('did-fail-load', (_e, code, desc, url) => {
    console.warn(`[renderer] did-fail-load ${code} ${desc} ${url}`)
  })
  win.webContents.on('render-process-gone', (_e, details) => {
    console.warn(`[renderer] process-gone reason=${details.reason} code=${details.exitCode}`)
  })

  // Dev 自检钩子：IWAN_GUI_TEST_PROMPT 存在时，页面就绪后向 Composer 注入
  // 一条消息并回车——模拟真实输入走 React 事件链，不抢用户窗口焦点
  if (process.env.ELECTRON_RENDERER_URL && process.env.IWAN_GUI_TEST_PROMPT) {
    const prompt = JSON.stringify(process.env.IWAN_GUI_TEST_PROMPT)
    win.webContents.on('did-finish-load', () => {
      void win?.webContents
        .executeJavaScript(`
(async () => {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
  for (let i = 0; i < 50; i++) {
    const ta = document.querySelector('textarea.input')
    if (ta && !ta.disabled) {
      setter.call(ta, ${prompt})
      ta.dispatchEvent(new Event('input', { bubbles: true }))
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      return 'injected'
    }
    await new Promise((r) => setTimeout(r, 200))
  }
  return 'timeout: composer never became enabled'
})()`
        )
        .then((r) => {
          console.log('[devtest]', String(r))
          if (r !== 'injected') return
          // 注入成功后定时抓页面：capturePage 走合成器，不受遮挡/焦点影响
          const snap = (n: number): void => {
            setTimeout(() => {
              void win?.webContents
                .capturePage()
                .then((img) =>
                  require('node:fs').writeFileSync(
                    require('node:path').join(require('node:os').tmpdir(), `iwan-gui-cap-${n}.png`),
                    img.toPNG()
                  )
                )
                .then(() => console.log('[devtest] captured', n))
            }, n * 1000)
          }
          snap(8)
          snap(25)
          snap(50)
          // IWAN_GUI_TEST_UI：M1 侧栏链路——点旧会话验证历史加载、行内改名验证 rename
          // （display:none 的悬停按钮用 .click() 程序化触发，React 合成事件照常走）
          const js = (label: string, expr: string, at: number): void => {
            setTimeout(() => {
              void win?.webContents.executeJavaScript(expr).then(
                (r2) => console.log('[devtest ui]', label, String(r2)),
                (err) => console.warn('[devtest ui]', label, 'ERR', String(err))
              )
            }, at * 1000)
          }
          const uiMode = process.env.IWAN_GUI_TEST_UI
          if (uiMode === '2') {
            // ===== M1.5 链路（短任务先行，UI 步骤全部独立注入——上一轮实测：
            // 流式渲染高峰期的注入会撞上 React 提交延迟，一屏一步不行的误判
            // 其实是截图落后于状态。所以每步只点一次、下一拍再验证）=====
            js('pick-session', `(() => {
              const row = document.querySelector('.project .sess-row')
              if (!row) return 'no-project-session'
              row.querySelector('.sess-title').click()
              return 'session-picked'
            })()`, 14)
            js('open-settings', `(() => {
              const b = [...document.querySelectorAll('.side-head .icon-btn')].find((x) => x.title === '设置')
              if (!b) return 'no-gear'
              b.click(); return 'opened'
            })()`, 18)
            snap(21)
            js('nav-mcp', `(() => {
              const b = [...document.querySelectorAll('.set-nav-item')].find((x) => x.textContent.includes('MCP'))
              if (!b) return 'no-nav'
              b.click(); return 'mcp'
            })()`, 24)
            snap(27)
            js('nav-trust', `(() => {
              const b = [...document.querySelectorAll('.set-nav-item')].find((x) => x.textContent.includes('审批与信任'))
              if (!b) return 'no-nav'
              b.click(); return 'trust'
            })()`, 30)
            snap(33)
            js('nav-appearance', `(() => {
              const b = [...document.querySelectorAll('.set-nav-item')].find((x) => x.textContent.includes('外观'))
              if (!b) return 'no-nav'
              b.click(); return 'appearance'
            })()`, 36)
            js('click-dark', `(() => {
              const d = [...document.querySelectorAll('.seg button')].find((x) => x.textContent === '深色')
              if (!d) return 'no-dark-btn'
              d.click(); return 'dark-on'
            })()`, 39)
            snap(42) // 深色设置页
            js('close-settings', `(() => { window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return 'closed' })()`, 45)
            snap(47) // 深色主界面
            js('open-rightpanel', `(() => {
              const b = document.querySelector('.panel-toggle')
              if (!b) return 'no-toggle'
              b.click(); return 'rp-open'
            })()`, 50)
            snap(52)
            js('tree-expand', `(() => {
              const rows = document.querySelectorAll('.tree-row')
              if (!rows.length) return 'no-tree'
              rows[0].click()
              return 'expanded'
            })()`, 55)
            snap(58)
            js('file-open', `(() => {
              const f = [...document.querySelectorAll('.tree-row')].find((r) => /[.](md|py|json|toml|txt)$/.test((r.title || '') + (r.querySelector('.tree-name')?.textContent || '')))
              if (!f) return 'no-file-row'
              f.click()
              return 'clicked:' + (f.querySelector('.tree-name')?.textContent || f.title)
            })()`, 61)
            snap(64) // 文件预览浮层（深色）
            js('close-viewer', `(() => { window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return 'closed' })()`, 67)
            js('revert-light', `(() => {
              const g = [...document.querySelectorAll('.side-head .icon-btn')].find((x) => x.title === '设置')
              if (!g) return 'no-gear'
              g.click(); return 'settings-reopened'
            })()`, 70)
            js('click-light', `(() => {
              const a = [...document.querySelectorAll('.set-nav-item')].find((x) => x.textContent.includes('外观'))
              a?.click()
              return 'appearance-shown'
            })()`, 73)
            js('light-on', `(() => {
              const l = [...document.querySelectorAll('.seg button')].find((x) => x.textContent === '浅色')
              if (!l) return 'no-light-btn'
              l.click(); return 'light-on'
            })()`, 76)
            js('close-settings2', `(() => { window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return 'closed' })()`, 79)
            snap(81)
            js('tab-changes', `(() => {
              const b = [...document.querySelectorAll('.rp-tab')].find((x) => x.textContent.includes('变更'))
              if (!b) return 'no-tab'
              b.click(); return 'changes'
            })()`, 84)
            snap(87)
            js('tab-tasks', `(() => {
              const b = [...document.querySelectorAll('.rp-tab')].find((x) => x.textContent.includes('任务'))
              if (!b) return 'no-tab'
              b.click(); return 'tasks'
            })()`, 90)
            snap(93)
            js('chip-perm-open', `(() => {
              const chip = [...document.querySelectorAll('.chip')].find((c) => /默认|接受编辑|规划|自动|全部放行/.test(c.textContent || ''))
              if (!chip) return 'no-perm-chip'
              chip.click(); return 'pop-open'
            })()`, 96)
            snap(98) // 弹层应【向上翻】可见（上一轮漏拍：96 开 101 关，中间只隔 5s 但截图滞后；补 97 紧贴）
            snap(97)
            js('chip-perm-switch', `(() => {
              const it = [...document.querySelectorAll('.popover .menu-item')].find((x) => x.textContent.includes('接受编辑'))
              if (!it) return 'no-item-visible'
              it.click(); return 'switched-acceptEdits'
            })()`, 101)
            snap(104)
            js('chip-perm-back', `(() => {
              const chip = [...document.querySelectorAll('.chip')].find((c) => /接受编辑/.test(c.textContent || ''))
              if (!chip) return 'chip-not-acceptEdits'
              chip.click(); return 'reopened'
            })()`, 107)
            js('chip-default-back', `(() => {
              const it = [...document.querySelectorAll('.popover .menu-item')].find((x) => (x.textContent || '').startsWith('默认'))
              if (!it) return 'no-default-item'
              it.click(); return 'back-to-default'
            })()`, 110)
            snap(113)
            snap(120)
          } else {
            js('open-history', `(() => {
              const rows = document.querySelectorAll('.sess-row')
              if (rows.length < 3) return 'rows<3'
              const t = rows[2].querySelector('.sess-title')
              t.click()
              return 'opened: ' + t.textContent
            })()`, 56)
            snap(59)
            js('click-rename', `(() => {
              const row = document.querySelector('.sess-row.active')
              if (!row) return 'no-active-row'
              const act = row.querySelector('.act')
              if (!act) return 'no-act-btn'
              act.click()
              return 'rename-input-next'
            })()`, 61)
            js('type-rename', `(() => {
              const inp = document.querySelector('.sess-row.active .row-input')
              if (!inp) return 'no-input'
              const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
              setter.call(inp, 'GUI-M1改名验证')
              inp.dispatchEvent(new Event('input', { bubbles: true }))
              inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
              return 'renamed'
            })()`, 62.5)
            snap(65)
            // 切回仍在运行的会话：验证"运行中可切换观看"，并抓流式段切分是否修复
            js('switch-running', `(() => {
              const row = [...document.querySelectorAll('.sess-row')].find((r) => r.querySelector('.dot-run'))
              if (!row) return 'no-running-row'
              row.querySelector('.sess-title').click()
              return 'switched'
            })()`, 72)
            snap(85)
            snap(120)
          }
        })
        .catch((err) => console.warn('[devtest] failed:', String(err)))
    })
  }

  if (process.env.ELECTRON_RENDERER_URL) {
    void win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void win.loadFile(path.join(__dirname, '../renderer/index.html'))
  }
  win.on('closed', () => {
    win = null
  })
}

// 单实例锁：第二次启动聚焦已有窗口——桌面应用的基本礼貌（daemon 才是可以多开的）
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore()
      win.focus()
    }
  })
  void app.whenReady().then(createWindow)
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
  })
  app.on('before-quit', () => transport?.stop())
}
