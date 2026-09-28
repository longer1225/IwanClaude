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

  // Dev 自检钩子：IWAN_GUI_TEST_PROMPT 注入一条真实消息并回车（走 React 事件链），
  // IWAN_GUI_TEST_UI 只跑视图链不发对话（空串即跳过打字、仅等 composer 就绪拿 'injected'
  // 这张发车门票）——UI=5 的 git 链会真 commit/discard，绝不允许再顺带发对话
  const testPrompt = process.env.IWAN_GUI_TEST_PROMPT ?? ''
  const testUi = process.env.IWAN_GUI_TEST_UI ?? ''
  if (process.env.ELECTRON_RENDERER_URL && (testPrompt || testUi)) {
    const prompt = JSON.stringify(testPrompt)
    win.webContents.on('did-finish-load', () => {
      void win?.webContents
        .executeJavaScript(`
(async () => {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
  for (let i = 0; i < 50; i++) {
    const ta = document.querySelector('textarea.input')
    if (ta && !ta.disabled) {
      if (${prompt}) {
        setter.call(ta, ${prompt})
        ta.dispatchEvent(new Event('input', { bubbles: true }))
        ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      }
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
          } else if (uiMode === '3') {
            // ===== M2 链路：三个功能视图（PR / 定时 / MCP 实况）+ 真实调度回环。
            // 不点 pr.create（会向 GitHub 建真实 PR），只验证 pr.context 数据面；
            // 调度建一个 every_minutes=1 的任务，等 schedule.fired 自动会话出现，
            // 最后删任务收尾。删除按钮弹 window.confirm——注入端先打桩放行，
            // 否则 executeJavaScript 会被原生模态框挂住 =====
            js('nav-pr', `(() => {
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').includes('Pull Request'))
              if (!b) return 'no-nav'
              b.click(); return 'pr-view'
            })()`, 14)
            snap(18)
            js('pr-context-shown', `(() => {
              const t = document.querySelector('.fn-title')
              const cards = document.querySelectorAll('.fn-card').length
              return 'title=' + (t?.textContent || '') + ' cards=' + cards
            })()`, 22)
            js('nav-mcp', `(() => {
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').includes('MCP'))
              if (!b) return 'no-nav'
              b.click(); return 'mcp-view'
            })()`, 26)
            snap(29)
            js('nav-cron', `(() => {
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').includes('定时任务'))
              if (!b) return 'no-nav'
              b.click(); return 'cron-view'
            })()`, 33)
            snap(36)
            js('open-task-form', `(() => {
              const b = [...document.querySelectorAll('.fn-head .chip')].find((x) => (x.textContent || '').includes('新建任务'))
              if (!b) return 'no-new-btn'
              b.click(); return 'form-open'
            })()`, 40)
            js('fill-task', `(() => {
              const iSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
              const tSet = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
              const name = document.querySelector('.fn-form .fn-input-slim')
              const ta = document.querySelector('.fn-form .fn-textarea')
              const num = document.querySelector('.fn-form .fn-num')
              if (!name || !ta || !num) return 'missing-fields'
              iSet.call(name, 'devtest4'); name.dispatchEvent(new Event('input', { bubbles: true }))
              tSet.call(ta, '回复字符：喵'); ta.dispatchEvent(new Event('input', { bubbles: true }))
              iSet.call(num, '1'); num.dispatchEvent(new Event('input', { bubbles: true }))
              return 'filled'
            })()`, 44)
            snap(47)
            js('save-task', `(() => {
              const b = [...document.querySelectorAll('.fn-form .chip')].find((x) => (x.textContent || '').includes('保存'))
              if (!b) return 'no-save-btn'
              if (b.disabled) return 'save-disabled'
              b.click(); return 'saved'
            })()`, 50)
            snap(54) // 任务行应出现：每 1 分钟 + 下次时刻
            // 每 1 分钟档 + 30s tick：创建（≈50s）后触发窗口 ≈ [110s, 140s]
            js('row-visible', `(() => {
              const rows = document.querySelectorAll('.fn-card-task').length
              return 'task-rows=' + rows
            })()`, 60)
            snap(95)
            snap(146)
            js('fired-check', `(() => {
              const st = window.__iwanStore.getState()
              const mine = st.sessions.filter((s) => (s.title || '').includes('devtest3'))
              const dom = [...document.querySelectorAll('.sess-title')].filter((t) => (t.textContent || '').includes('devtest3')).length
              const m = mine.length ? st.metaCwd[mine[0].id] || '' : ''
              return 'store=' + mine.length + ' dom=' + dom + ' metaCwd=' + m + ' total=' + st.sessions.length
            })()`, 150)
            js('delete-task', `(() => {
              window.confirm = () => true
              const b = [...document.querySelectorAll('.fn-card-task .icon-btn')].find((x) => x.title === '删除任务')
              if (!b) return 'no-del-btn'
              b.click(); return 'deleted'
            })()`, 154)
            snap(158) // 回到空态文案
            js('back-chat', `(() => {
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').includes('新对话'))
              if (!b) return 'no-nav'
              b.click(); return 'chat-view'
            })()`, 164)
            snap(166)
          } else if (uiMode === '5') {
            // ===== M3 链路：Git 面板全流程。仓库由外部脚本预先建在临时目录
            // （IWAN_GUI_TEST_REPO 传入），绝不用真仓库——stage/commit/discard
            // 都是改工作区的动作。store 走 __iwanStore 缝切 cwd；confirm 全程
            // 打桩放行（原生模态会挂 executeJavaScript）。Push 故意在【无远端】
            // 仓库上点——测的正是失败文案能否原样上屏（错误展示路径）=====
            const repo = process.env.IWAN_GUI_TEST_REPO || ''
            const repoJs = JSON.stringify(repo)
            js('set-cwd', `(() => {
              if (!${repoJs}) return 'skip-no-repo-env'
              window.__iwanStore.getState().setCwd(${repoJs})
              return 'cwd-set'
            })()`, 12)
            js('nav-git', `(() => {
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').trim() === 'Git')
              if (!b) return 'no-nav'
              b.click(); return 'git-view'
            })()`, 16)
            snap(21)
            js('git-head-shown', `(() => {
              const t = document.querySelector('.fn-title')
              const rows = document.querySelectorAll('.git-row').length
              const tag = [...document.querySelectorAll('.fn-head .fn-tag')].map((x) => x.textContent).join('|')
              return 'title=' + (t?.textContent || '') + ' rows=' + rows + ' head=' + tag
            })()`, 24)
            js('stage-b', `(() => {
              window.confirm = () => true
              const row = [...document.querySelectorAll('.git-row')].find((r) => (r.querySelector('.git-path')?.textContent || '') === 'b.txt')
              if (!row) return 'no-b-row'
              const btn = [...row.querySelectorAll('.icon-btn')].find((x) => x.title === '暂存')
              if (!btn || btn.disabled) return 'no-stage-btn'
              btn.click(); return 'clicked'
            })()`, 28)
            js('commit-b', `(() => {
              const ta = document.querySelector('.git-card textarea, .fn-card .fn-textarea')
              if (!ta) return 'no-textarea'
              const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
              set.call(ta, 'devtest 提交'); ta.dispatchEvent(new Event('input', { bubbles: true }))
              return 'typed'
            })()`, 34)
            js('click-commit', `(() => {
              const b = [...document.querySelectorAll('.git-commit-row .chip')].find((x) => (x.textContent || '').includes('Commit'))
              if (!b) return 'no-commit-btn'
              if (b.disabled) return 'commit-disabled'
              b.click(); return 'committed'
            })()`, 37)
            snap(41)
            js('commit-echo', `(() => {
              const ok = document.querySelector('.fn-ok')
              const logHit = [...document.querySelectorAll('.git-row')].some((r) => (r.textContent || '').includes('devtest 提交'))
              return 'msg=' + (ok?.textContent || '') + ' inLog=' + logHit
            })()`, 44)
            js('discard-c', `(() => {
              const row = [...document.querySelectorAll('.git-row')].find((r) => (r.querySelector('.git-path')?.textContent || '') === 'c.txt')
              if (!row) return 'no-c-row'
              const btn = [...row.querySelectorAll('.icon-btn')].find((x) => x.title === '丢弃改动')
              if (!btn) return 'no-discard-btn'
              btn.click(); return 'discarded'
            })()`, 48)
            js('discard-echo', `(() => {
              const gone = ![...document.querySelectorAll('.git-row .git-path')].some((p) => p.textContent === 'c.txt')
              const ok = [...document.querySelectorAll('.fn-ok,.fn-err')].map((x) => x.textContent).join(';')
              return 'cGone=' + gone + ' echo=' + ok.slice(0, 60)
            })()`, 53)
            js('push-fail-surface', `(() => {
              const b = [...document.querySelectorAll('.fn-head .chip')].find((x) => (x.textContent || '').trim() === 'Push')
              if (!b) return 'no-push-btn'
              b.click(); return 'pushed'
            })()`, 57)
            snap(62)
            js('push-err-shown', `(() => {
              const e = document.querySelector('.fn-err')
              return 'err=' + (e?.textContent || '').slice(0, 80)
            })()`, 65)
            js('back-chat-restore-cwd', `(() => {
              window.__iwanStore.getState().setCwd(null)
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').includes('新对话'))
              if (!b) return 'no-nav'
              b.click(); return 'chat-view'
            })()`, 69)
            snap(72)
          } else if (uiMode === '6') {
            // ===== M4a 链路：SSH 视图全流程。密钥【真实生成】到 ~/.iwan/ssh
            // （拒绝覆盖语义保证重跑无害，且这把钥匙 M4c 终端本来就要用）；
            // 连接只建一条 devtest 专用并在链尾删掉——连接表不留测试残渣。
            // 信任此主机故意打 127.0.0.1:1（必然不可达）：验证失败文案上屏，
            // 且 known_hosts 绝不落盘（安全不变式的 GUI 侧证据）=====
            js('nav-ssh', `(() => {
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').trim() === 'SSH 终端')
              if (!b) return 'no-nav'
              b.click(); return 'ssh-view'
            })()`, 16)
            snap(21)
            js('key-card', `(() => {
              const card = [...document.querySelectorAll('.fn-card-name')].find((x) => x.textContent === '本机密钥')
              const gen = [...document.querySelectorAll('.chip-primary')].find((x) => (x.textContent || '').includes('生成'))
              return 'card=' + !!card + ' genBtn=' + !!gen
            })()`, 24)
            js('gen-key', `(() => {
              const gen = [...document.querySelectorAll('.chip-primary')].find((x) => (x.textContent || '').includes('生成'))
              if (!gen) return 'no-gen-btn'
              gen.click(); return 'clicked'
            })()`, 27)
            js('key-after', `(() => {
              const ok = document.querySelector('.fn-ok')
              return 'echo=' + (ok?.textContent || '').slice(0, 50)
            })()`, 33)
            snap(34)
            js('open-form', `(() => {
              const b = [...document.querySelectorAll('.fn-head .chip')].find((x) => (x.textContent || '').includes('添加连接'))
              if (!b) return 'no-add-btn'
              b.click(); return 'form-open'
            })()`, 37)
            js('fill-conn', `(() => {
              const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
              const put = (ph, v) => {
                const inp = document.querySelector('.fn-form input[placeholder^="' + ph + '"]')
                if (!inp) return false
                set.call(inp, v); inp.dispatchEvent(new Event('input', { bubbles: true }))
                return true
              }
              const a = put('名称', 'devtest-box'), b = put('主机', '127.0.0.1'), c = put('登录用户', 'dev')
              const p = document.querySelector('.fn-form .fn-num')
              if (p) { set.call(p, '1'); p.dispatchEvent(new Event('input', { bubbles: true })) }
              return 'filled=' + [a, b, c].join(',')
            })()`, 41)
            js('save-conn', `(() => {
              const b = [...document.querySelectorAll('.fn-form .chip')].find((x) => (x.textContent || '').includes('保存'))
              if (!b) return 'no-save-btn'
              if (b.disabled) return 'save-disabled'
              b.click(); return 'saved'
            })()`, 45)
            js('conn-shown', `(() => {
              const c = [...document.querySelectorAll('.fn-card-name')].find((x) => x.textContent === 'devtest-box')
              return 'card=' + !!c
            })()`, 50)
            snap(51)
            // ===== M4c 追加：第二条连接 devtest-hold 指 127.0.0.1:14224——外部
            // "假 sshd"只接 TCP 不发 banner（配合预先种入的 known_hosts 行 +
            // BatchMode），ssh 停在 banner 交换前【活着等】——这正是"会话存活时
            // 击杀 daemon"的场景（外部 killer 看到 term-clicked 后 2.5s 动手）。
            // 验证计划风险⑥：daemon 死 → ssh.exe 随 Job Object 陪葬且永无
            // ssh.closed → GUI 靠 reconnecting→connected 的跨越由页签内哨兵统一
            // 标断（recheck 期望 banner=daemon 重连，终端会话已终止）。
            // devtest-box（127.0.0.1:1 必拒端口）保留：信任失败的错误文案证明=====
            js('open-form2', `(() => {
              if (document.querySelector('.fn-form input[placeholder^="名称"]')) return 'form-already-open'
              const b = [...document.querySelectorAll('.fn-head .chip')].find((x) => (x.textContent || '').includes('添加连接'))
              if (!b) return 'no-add-btn'
              b.click(); return 'form-open'
            })()`, 54)
            js('fill-conn2', `(() => {
              const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
              const put = (ph, v) => {
                const inp = document.querySelector('.fn-form input[placeholder^="' + ph + '"]')
                if (!inp) return false
                set.call(inp, v); inp.dispatchEvent(new Event('input', { bubbles: true }))
                return true
              }
              const a = put('名称', 'devtest-hold'), b = put('主机', '127.0.0.1'), c = put('登录用户', 'dev')
              const p = document.querySelector('.fn-form .fn-num')
              if (p) { set.call(p, '14224'); p.dispatchEvent(new Event('input', { bubbles: true })) }
              return 'filled=' + [a, b, c].join(',')
            })()`, 57)
            js('save-conn2', `(() => {
              const b = [...document.querySelectorAll('.fn-form .chip')].find((x) => (x.textContent || '').includes('保存'))
              if (!b) return 'no-save-btn'
              if (b.disabled) return 'save-disabled'
              b.click(); return 'saved'
            })()`, 60)
            js('conn2-shown', `(() => {
              const c = [...document.querySelectorAll('.fn-card-name')].find((x) => x.textContent === 'devtest-hold')
              return 'card=' + !!c
            })()`, 63)
            js('trust-fail', `(() => {
              const card = [...document.querySelectorAll('.fn-card')].find((x) => (x.textContent || '').includes('devtest-box'))
              if (!card) return 'no-card'
              const b = [...card.querySelectorAll('.chip')].find((x) => (x.textContent || '').includes('信任此主机'))
              if (!b) return 'no-trust-btn'
              b.click(); return 'trusted-clicked'
            })()`, 66)
            js('trust-echo', `(() => {
              const e = document.querySelector('.fn-err')
              return 'err=' + (e?.textContent || '').slice(0, 60)
            })()`, 78)
            snap(76)
            js('open-term', `(() => {
              const card = [...document.querySelectorAll('.fn-card')].find((x) => (x.textContent || '').includes('devtest-hold'))
              if (!card) return 'no-card'
              const b = [...card.querySelectorAll('.chip-primary')].find((x) => (x.textContent || '').includes('开终端'))
              if (!b) return 'no-term-btn'
              b.click(); return 'term-clicked'
            })()`, 83)
            js('term-tab', `(() => {
              const tab = !!document.querySelector('.ssh-tab-on')
              const x = !!document.querySelector('.xterm')
              const banner = (document.querySelector('.ssh-term-banner')?.textContent || '').slice(0, 60)
              return 'tab=' + tab + ' xterm=' + x + ' banner=' + banner
            })()`, 86)
            snap(87)
            js('term-banner-recheck', `(() => {
              const b = document.querySelector('.ssh-term-banner')
              return 'banner=' + (b ? (b.textContent || '').slice(0, 70) : 'NONE')
            })()`, 102)
            snap(103)
            js('term-banner-recheck2', `(() => {
              const b = document.querySelector('.ssh-term-banner')
              return 'banner=' + (b ? (b.textContent || '').slice(0, 70) : 'NONE')
            })()`, 108)
            js('close-tab', `(() => {
              const x = document.querySelector('.ssh-tab-x')
              if (!x) return 'no-tab-x'
              x.click()
              return 'clicked'
            })()`, 111)
            js('tab-gone', `(() => {
              const still = !!document.querySelector('.ssh-tab-on')
              const empty = !!document.querySelector('.ssh-term-empty')
              return 'still=' + still + ' empty=' + empty
            })()`, 113)
            snap(114)
            js('del-conn', `(() => {
              window.confirm = () => true
              let n = 0
              for (const name of ['devtest-box', 'devtest-hold']) {
                const card = [...document.querySelectorAll('.fn-card')].find((x) => (x.textContent || '').includes(name))
                const b = card && card.querySelector('.icon-btn.tiny')
                if (b) { b.click(); n += 1 }
              }
              return 'deleted=' + n
            })()`, 116)
            js('del-echo', `(() => {
              const gone = ![...document.querySelectorAll('.fn-card-name')].some(
                (x) => x.textContent === 'devtest-box' || x.textContent === 'devtest-hold')
              return 'gone=' + gone
            })()`, 122)
            js('back-chat', `(() => {
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').includes('新对话'))
              if (!b) return 'no-nav'
              b.click(); return 'chat-view'
            })()`, 124)
            snap(126)
          } else if (uiMode === '7') {
            // ===== W2 链路：工作流全流程（新建→编排 A→B→保存→运行→事件上色→
            // 历史对账→删除）。真 LLM：节点 A/B 各跑一轮"只回复"，运行预算 48s→
            // 120s 共 72s；node-a-running 是事件链哨兵——它出现即证明
            // subscribe(topics workflow.*) + store 上色整条管道活着 =====
            js('nav-workflow', `(() => {
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').trim() === '工作流')
              if (!b) return 'no-nav'
              b.click(); return 'wf-view'
            })()`, 16)
            snap(20)
            js('open-editor', `(() => {
              const b = [...document.querySelectorAll('.fn-head .chip')].find((x) => (x.textContent || '').includes('新建工作流'))
              if (!b) return 'no-new-btn'
              b.click(); return 'editor-clicked'
            })()`, 23)
            js('fill-a', `(() => {
              const si = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
              const st = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
              const put = (el, v, set) => { if (!el) return false; set.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true })); return true }
              if (!document.querySelector('.wf-editor')) return 'editor-missing'
              const row = document.querySelector('.wf-node-edit')
              const a = put(document.querySelector('.fn-input[placeholder^="工作流名称"]'), 'devtest-wf', si)
              const b = put(row?.querySelector('.wf-ename'), 'A', si)
              const c = put(row?.querySelector('.wf-eprompt'), '只回复：A完成', st)
              return 'filled=' + [a, b, c].join(',')
            })()`, 27)
            js('add-node', `(() => {
              const b = [...document.querySelectorAll('.wf-editor .chip')].find((x) => (x.textContent || '').includes('添加节点'))
              if (!b) return 'no-add-btn'
              b.click()
              return 'rows=' + document.querySelectorAll('.wf-node-edit').length
            })()`, 31)
            js('fill-b', `(() => {
              const si = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
              const st = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
              const rows = [...document.querySelectorAll('.wf-node-edit')]
              const row = rows[rows.length - 1]
              if (!row) return 'no-row2'
              const n = row.querySelector('.wf-ename'); si.call(n, 'B'); n.dispatchEvent(new Event('input', { bubbles: true }))
              const p = row.querySelector('.wf-eprompt'); st.call(p, '只回复：B完成'); p.dispatchEvent(new Event('input', { bubbles: true }))
              const lab = [...row.querySelectorAll('.wf-dep-check')].find((l) => (l.textContent || '').trim() === 'A')
              if (!lab) return 'no-dep-A'
              lab.querySelector('input').click()
              return 'b-ready'
            })()`, 34)
            js('save-wf', `(() => {
              const b = document.querySelector('.wf-editor .chip-primary')
              if (!b) return 'no-save-btn'
              if (b.disabled) return 'save-disabled'
              b.click(); return 'saved-clicked'
            })()`, 40)
            js('detail-shown', `(() => {
              const t = document.querySelector('.fn-title')?.textContent || ''
              return 'title=' + t + ' runBtn=' + !!document.querySelector('.wf-run-btn') + ' nodes=' + document.querySelectorAll('.wf-node').length
            })()`, 45)
            snap(46)
            js('run-wf', `(() => {
              const b = document.querySelector('.wf-run-btn')
              if (!b) return 'no-run-btn'
              if (b.disabled) return 'run-disabled'
              b.click(); return 'run-clicked'
            })()`, 48)
            js('node-a-running', `(() => {
              const r = !!document.querySelector('.wf-node-running')
              const ok = document.querySelectorAll('.wf-node-ok').length
              return 'running=' + r + ' alreadyOk=' + ok
            })()`, 49.5)
            snap(54)
            js('progress-1', `(() => 'ok=' + document.querySelectorAll('.wf-node-ok').length)()`, 75)
            js('progress-2', `(() => 'ok=' + document.querySelectorAll('.wf-node-ok').length)()`, 95)
            js('progress-3', `(() => 'ok=' + document.querySelectorAll('.wf-node-ok').length + ' fail=' + document.querySelectorAll('.wf-node-fail').length)()`, 115)
            snap(116)
            js('finished', `(() => {
              const tag = document.querySelector('.wf-run-row .fn-tag')?.textContent || 'NOROW'
              const btn = document.querySelector('.wf-run-btn')
              return 'runTag=' + tag + ' btnOff=' + (btn ? btn.disabled : 'no-btn')
            })()`, 121)
            snap(122)
            // 【学习要点】点击与验证必须分两拍：executeJavaScript 里 click() 后
            // 同帧查 DOM 看不到 React 重渲染（M1.5 实测教训，整条链都守这个纪律）
            js('expand-history', `(() => {
              const row = document.querySelector('.wf-run-row')
              if (!row) return 'no-run-row'
              row.click(); return 'expanded'
            })()`, 126)
            js('history-outs', `(() => {
              const outs = [...document.querySelectorAll('.wf-out')].map((p) => (p.textContent || '').slice(0, 12)).join('|')
              return 'outs=' + outs
            })()`, 128)
            js('back-to-list', `(() => {
              const back = [...document.querySelectorAll('.fn-head .chip')].find((x) => (x.textContent || '').includes('列表'))
              if (!back) return 'no-back'
              back.click(); return 'list-clicked'
            })()`, 131)
            js('del-wf', `(() => {
              window.confirm = () => true
              const card = [...document.querySelectorAll('.wf-card')].find((x) => (x.textContent || '').includes('devtest-wf'))
              const b = card && [...card.querySelectorAll('.icon-btn.tiny')].find((x) => x.title === '删除')
              if (!b) return 'no-del-btn'
              b.click(); return 'del-clicked'
            })()`, 133)
            js('del-echo', `(() => {
              const gone = ![...document.querySelectorAll('.fn-card-name')].some((x) => x.textContent === 'devtest-wf')
              return 'gone=' + gone
            })()`, 136)
            js('back-chat', `(() => {
              const b = [...document.querySelectorAll('.nav-row')].find((x) => (x.textContent || '').includes('新对话'))
              if (!b) return 'no-nav'
              b.click(); return 'chat-view'
            })()`, 138)
            snap(140)
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
