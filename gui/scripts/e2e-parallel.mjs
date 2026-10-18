// 端到端回归：真实 Electron GUI + 假 daemon + CDP 驱动，验证"多任务并行且回复不串台"。
//
// 为什么需要它：这两条性质（同一窗口两条 run 同时跑、模型回复只落在自己会话的线程）
// 全部发生在渲染层状态机里，只靠 tsc/单测证明不了；本脚本真的把窗口开起来、真的按
// 回车、真的读 DOM 断言。
//
// 它自带一个假 daemon（不需要 LLM、不需要 API key、不碰 ~/.iwan 里的真会话）：
//   · 阶段 1：开两扇窗，各发一条任务 → 断言各自收到自己的回复、互不串台
//   · 阶段 2：同一扇窗里 A 还在流式输出时再发 B → 断言侧栏两个运行点、
//             两边都收口、A 的尾段完整（旧实现这里会丢尾）
//
// 用法：cd gui && npm run test:e2e      （需要桌面会话；~15 秒）
import net from 'node:net'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const GUI = path.resolve(HERE, '..')
const REPO = path.resolve(GUI, '..')
const EXE = path.join(GUI, 'node_modules', 'electron', 'dist', 'electron.exe')
const PROFILE = path.join(REPO, 'runs', 'e2e-profile')
const TMP = path.join(REPO, 'runs', 'e2e-tmp')

if (!fs.existsSync(EXE)) {
  console.error('找不到 electron：' + EXE + '\n先执行 cd gui && npm install，并确认 npm run build 能过')
  process.exit(2)
}

let fails = 0
const ok = (name, cond, extra = '') => {
  if (!cond) fails++
  console.log((cond ? 'PASS' : 'FAIL') + '  ' + name + (extra ? '  · ' + extra : ''))
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// 取一个空闲端口（先占住拿号再释放，交给 electron / 假 daemon 用）
async function freePort() {
  const srv = net.createServer()
  await new Promise((res) => srv.listen(0, '127.0.0.1', res))
  const port = srv.address().port
  await new Promise((res) => srv.close(res))
  return port
}

// ---------- 假 daemon：只实现 GUI 用到的协议面 ----------
let seq = 0
const sessions = []
const clients = new Set()
const sendLine = (sock, obj) => sock.write(JSON.stringify(obj) + '\n')
const emit = (ev) => {
  for (const s of clients) sendLine(s, { kind: 'event', event: { ts: new Date().toISOString(), ...ev } })
}

// 回复内容由输入决定，便于断言"哪条回复进了哪扇窗/哪个线程"
const plan = (content) =>
  content.includes('SLOW-A') ? { tag: '回A', n: 20 }
  : content.includes('QUICK-B') ? { tag: '回B', n: 6 }
  : content.includes('TASK-A') ? { tag: '回A', n: 6 }
  : { tag: '回B', n: 6 }

function handle(sock, msg) {
  const { id, method, params = {} } = msg
  const reply = (result) => sendLine(sock, { jsonrpc: '2.0', id, result })
  switch (method) {
    case 'auth.hello':
      return reply({ ok: true })
    case 'core.ping':
      return reply({ server_version: 'fake', uptime_ms: 1, received_at: new Date().toISOString() })
    case 'event.subscribe':
      return reply({ sub_id: 'sub-1', topics: params.topics ?? [], scope: params.scope ?? 'global' })
    case 'session.list':
      return reply({
        sessions: sessions.map((s) => ({ id: s.id, title: s.title, status: s.status, mode: 'chat', updated_at: s.updated_at }))
      })
    case 'session.create': {
      seq += 1
      const s = { id: 'sess-' + seq, title: '', status: 'active', updated_at: new Date().toISOString(), messages: [] }
      sessions.unshift(s)
      reply({
        session_id: s.id,
        title: '',
        mode: 'chat',
        status: 'active',
        auto_mode: 'off',
        permission_mode: 'default',
        effort_level: 'medium',
        model_preset: 'balanced',
        engine: 'legacy',
        cwd: params.cwd ?? ''
      })
      return
    }
    case 'session.get_history':
      // 真 daemon 从 thread.jsonl 回放；这里按会话攒消息，让"迟到窗口补尾"有东西可补
      return reply({ messages: sessions.find((s) => s.id === params.session_id)?.messages ?? [] })
    case 'session.send_message': {
      const sid = params.session_id
      const content = String(params.content ?? '')
      const runId = 'run-' + (++seq)
      // 信任闸门回包：daemon 在"未获信任"时直接拒绝，不建 run、不落消息
      if (content.includes('TRUST-BLOCK')) {
        emit({ type: 'session.message_received', session_id: sid, content })
        reply({
          run_id: '',
          skill_match: null,
          trust_blocked: true,
          trust_reason: '测试：该目录尚未获得信任，任务未启动'
        })
        return
      }
      const sess = sessions.find((s) => s.id === sid)
      if (sess) {
        sess.status = 'running'
        sess.title = content.slice(0, 20)
        sess.updated_at = new Date().toISOString()
        sess.messages.push({ role: 'user', content })
      }
      emit({ type: 'session.message_received', session_id: sid, content })
      emit({ type: 'session.renamed', session_id: sid, title: content.slice(0, 20) })
      emit({ type: 'run.started', run_id: runId, goal: content, session_id: sid })
      const { tag, n } = plan(content)
      let i = 0
      let acc = ''
      const timer = setInterval(() => {
        i += 1
        const piece = tag + '-' + i + ' '
        acc += piece
        emit({ type: 'llm.token', run_id: runId, token: piece })
        if (i >= n) {
          clearInterval(timer)
          if (sess) {
            sess.status = 'waiting_for_input'
            // 回复先落盘再发"等待输入"：迟到窗口重拉历史时才能拿到完整尾巴
            sess.messages.push({ role: 'assistant', content: [{ type: 'text', text: acc.trim() }] })
          }
          emit({ type: 'run.finished', run_id: runId, status: 'success', reason: null, steps: 1 })
          emit({ type: 'session.waiting_for_input', session_id: sid, last_run_id: runId })
          // 真 daemon 也是"整轮跑完才回包"，这里如实模仿（客户端必须扛住这个时序）
          reply({ run_id: runId, skill_match: null })
        }
      }, 100)
      return
    }
    default:
      return reply({})
  }
}

const rpcPort = await freePort()
const cdpPort = await freePort()
const server = net.createServer((sock) => {
  clients.add(sock)
  let buf = ''
  sock.on('data', (d) => {
    buf += d.toString('utf8')
    let i
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim()
      buf = buf.slice(i + 1)
      if (!line) continue
      try {
        handle(sock, JSON.parse(line))
      } catch (e) {
        console.log('fake-daemon 解析失败：' + String(e))
      }
    }
  })
  sock.on('close', () => clients.delete(sock))
  sock.on('error', () => clients.delete(sock))
})
await new Promise((res) => server.listen(rpcPort, '127.0.0.1', res))
console.log('[e2e] 假 daemon 监听 ' + rpcPort)

// ---------- 启动真实 GUI ----------
fs.rmSync(PROFILE, { recursive: true, force: true })
fs.mkdirSync(PROFILE, { recursive: true })
fs.mkdirSync(TMP, { recursive: true })
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE // VS Code/CI 内置终端会注入它，会把 Electron 退化成 Node
env.IWAN_PORT = String(rpcPort)
env.TEMP = TMP
env.TMP = TMP
// --user-data-dir：测试用独立 profile，既不污染用户配置，也避开某些环境下
// 默认 AppData 不可写导致 process_singleton 建锁失败（Electron 会直接静默退出）
const child = spawn(
  EXE,
  [GUI, '--remote-debugging-port=' + cdpPort, '--user-data-dir=' + PROFILE, '--no-sandbox', '--disable-gpu'],
  { cwd: GUI, env, stdio: ['ignore', 'pipe', 'pipe'] }
)
child.stdout.on('data', (d) => process.stdout.write('[gui] ' + d))
child.stderr.on('data', (d) => {
  const t = String(d)
  if (!/DevTools listening|Lock file|active port/.test(t)) process.stdout.write('[gui:err] ' + t)
})

// ---------- CDP 小客户端 ----------
async function targets() {
  try {
    const r = await fetch('http://127.0.0.1:' + cdpPort + '/json/list')
    const list = await r.json()
    return list.filter((t) => t.type === 'page' && String(t.url).includes('index.html'))
  } catch {
    return []
  }
}
async function waitTargets(n, ms = 30000) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    const t = await targets()
    if (t.length >= n) return t
    await sleep(300)
  }
  throw new Error('等待 ' + n + ' 扇窗超时，当前 ' + (await targets()).length)
}
async function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl)
  await new Promise((res, rej) => {
    ws.onopen = res
    ws.onerror = () => rej(new Error('CDP websocket 连接失败'))
  })
  let id = 0
  const pending = new Map()
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data)
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m)
      pending.delete(m.id)
    }
  }
  const send = (method, params) =>
    new Promise((res) => {
      const i = ++id
      pending.set(i, res)
      ws.send(JSON.stringify({ id: i, method, params }))
    })
  await send('Runtime.enable', {})
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (r.result && r.result.exceptionDetails) throw new Error(String(JSON.stringify(r.result.exceptionDetails)).slice(0, 400))
    return r.result ? r.result.result.value : undefined
  }
  return { evaluate, close: () => ws.close() }
}

// ---------- 页面脚本（选择器与 main/index.ts 的 devtest 同源） ----------
const SEND_JS = (text) => `(async () => {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
  for (let i = 0; i < 120; i++) {
    const ta = document.querySelector('textarea.input')
    if (ta && !ta.disabled) {
      setter.call(ta, ${JSON.stringify(text)})
      ta.dispatchEvent(new Event('input', { bubbles: true }))
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      return 'sent'
    }
    await new Promise((r) => setTimeout(r, 100))
  }
  return 'timeout'
})()`
const READY_JS = `(async () => {
  for (let i = 0; i < 150; i++) {
    const ta = document.querySelector('textarea.input')
    if (ta && !ta.disabled && !document.querySelector('.conn-banner')) return 'ready'
    await new Promise((r) => setTimeout(r, 100))
  }
  return 'not-ready:' + document.body.innerText.slice(0, 120)
})()`
const NEW_CHAT_JS = `(() => {
  const row = [...document.querySelectorAll('.nav-row')].find((r) => r.innerText.includes('新对话'))
  if (!row) return 'no-nav'
  row.click()
  return 'clicked'
})()`
const WAIT_IDLE_JS = `(async () => {
  for (let i = 0; i < 150; i++) {
    if (document.querySelectorAll('.dot-run').length === 0) return 'idle'
    await new Promise((r) => setTimeout(r, 100))
  }
  return 'still-running:' + document.querySelectorAll('.dot-run').length
})()`
const clickSession = (needle) => `(() => {
  const rows = [...document.querySelectorAll('.sess-row')]
  const hit = rows.find((r) => r.innerText.includes(${JSON.stringify(needle)}))
  if (!hit) return 'no-row:' + rows.map((r) => r.innerText.replace(/\\n/g, ' ')).join(' | ').slice(0, 200)
  hit.querySelector('.sess-title').click()
  return 'clicked'
})()`
// 等侧栏把会话行渲染出来再点（新窗口刚连上时列表是异步拉的）
const clickSessionWhenReady = (needle) => `(async () => {
  for (let i = 0; i < 100; i++) {
    const rows = [...document.querySelectorAll('.sess-row')]
    const hit = rows.find((r) => r.innerText.includes(${JSON.stringify(needle)}))
    if (hit) { hit.querySelector('.sess-title').click(); return 'clicked' }
    await new Promise((r) => setTimeout(r, 100))
  }
  return 'no-row:' + document.body.innerText.replace(/\\n/g, ' ').slice(0, 240)
})()`

try {
  // ===== 阶段 1：两扇窗，各自一条任务 =====
  const t1 = await waitTargets(1)
  const w1 = await cdp(t1[0].webSocketDebuggerUrl)
  ok('窗口1 连接就绪', (await w1.evaluate(READY_JS)) === 'ready')
  ok('窗口1 发出任务', (await w1.evaluate(SEND_JS('TASK-A 请回答'))) === 'sent')
  await sleep(300)
  await w1.evaluate('window.iwan.newWindow(); "ok"')
  const t2 = await waitTargets(2)
  const second = t2.find((t) => t.id !== t1[0].id) || t2[1]
  const w2 = await cdp(second.webSocketDebuggerUrl)
  ok('窗口2 连接就绪', (await w2.evaluate(READY_JS)) === 'ready')
  ok('窗口2 发出任务', (await w2.evaluate(SEND_JS('TASK-B 请回答'))) === 'sent')
  await sleep(1600)
  const textA = await w1.evaluate('document.body.innerText')
  const textB = await w2.evaluate('document.body.innerText')
  ok('窗口1 收到自己的回复', textA.includes('回A-6'))
  ok('窗口2 收到自己的回复', textB.includes('回B-6'))
  ok('窗口1 没串到 B 的回复', !textA.includes('回B-'))
  ok('窗口2 没串到 A 的回复', !textB.includes('回A-'))

  // ===== 阶段 2：同一扇窗，A 还没跑完就发 B =====
  await w1.evaluate(WAIT_IDLE_JS)
  ok('窗口1 回到空闲', (await w1.evaluate(NEW_CHAT_JS)) === 'clicked')
  ok('窗口1 发 SLOW-A', (await w1.evaluate(SEND_JS('SLOW-A 慢任务'))) === 'sent')
  await sleep(400)
  ok('窗口1 再开新对话', (await w1.evaluate(NEW_CHAT_JS)) === 'clicked')
  ok('窗口1 在 A 运行中发 QUICK-B', (await w1.evaluate(SEND_JS('QUICK-B 快任务'))) === 'sent')
  await sleep(400)
  const dots = await w1.evaluate('document.querySelectorAll(".dot-run").length')
  ok('两条 run 同时在跑（侧栏两个运行点）', Number(dots) === 2, 'dots=' + dots)
  const idle = await w1.evaluate(WAIT_IDLE_JS)
  ok('两条 run 都收口', idle === 'idle', String(idle))
  const clickA = await w1.evaluate(clickSession('SLOW-A'))
  ok('切回 A 的会话', clickA === 'clicked', String(clickA))
  const textSA = await w1.evaluate('document.body.innerText')
  ok('A 的尾段完整（20/20）', textSA.includes('回A-20'), 'has20=' + textSA.includes('回A-20'))
  const clickB = await w1.evaluate(clickSession('QUICK-B'))
  ok('切到 B 的会话', clickB === 'clicked', String(clickB))
  const textSB = await w1.evaluate('document.body.innerText')
  ok('B 的回复完整（6/6）', textSB.includes('回B-6'))
  ok('A/B 线程互不污染', !textSB.includes('回A-20') && !textSA.includes('回B-6'))

  // ===== 阶段 3：迟到窗口 —— run 跑到一半才开的窗，切过去也要能补齐尾巴 =====
  const known = new Set([...t1, ...t2].map((x) => x.id))
  await w1.evaluate(WAIT_IDLE_JS)
  await w1.evaluate(NEW_CHAT_JS)
  ok('窗口1 发 SLOW-A2', (await w1.evaluate(SEND_JS('SLOW-A2 迟到窗口'))) === 'sent')
  await sleep(500) // A2 约 2s 才结束，此刻仍在流式
  await w1.evaluate('window.iwan.newWindow(); "ok"')
  const t3 = await waitTargets(3)
  const third = t3.find((t) => !known.has(t.id))
  if (!third) throw new Error('没等到第三扇窗的 target')
  const w3 = await cdp(third.webSocketDebuggerUrl)
  ok('窗口3 连接就绪', (await w3.evaluate(READY_JS)) === 'ready')
  const clickLate = await w3.evaluate(clickSessionWhenReady('SLOW-A2'))
  ok('窗口3 切到进行中的会话', clickLate === 'clicked', String(clickLate))
  await w1.evaluate(WAIT_IDLE_JS)
  await sleep(900) // 留出 waiting_for_input 触发重拉历史的时间
  const textLate = await w3.evaluate('document.body.innerText')
  ok('迟到窗口补到完整尾巴（20/20）', textLate.includes('回A-20'), 'has20=' + textLate.includes('回A-20'))

  // ===== 阶段 4：信任闸门拦下时的界面回声（假 daemon 直接回 trust_blocked）=====
  await w1.evaluate(WAIT_IDLE_JS)
  await w1.evaluate(NEW_CHAT_JS)
  ok('窗口1 发 TRUST-BLOCK', (await w1.evaluate(SEND_JS('TRUST-BLOCK 未信任目录'))) === 'sent')
  await sleep(1200)
  const textT = await w1.evaluate('document.body.innerText')
  ok('界面提示"任务未启动"', textT.includes('任务未启动'), 'len=' + textT.length)
  ok('提示里带拒绝原因', textT.includes('尚未获得信任'))
  // 新会话线程里只有用户气泡 + 提示，不应出现任何流式回复
  ok('被拦时不产生助手回复', !textT.includes('回A-') && !textT.includes('回B-'))

  w1.close()
  w2.close()
  w3.close()
} catch (e) {
  fails++
  console.log('FAIL  端到端异常：' + String(e && e.message ? e.message : e))
} finally {
  try {
    child.kill()
  } catch {
    /* 已退出 */
  }
  server.close()
  await sleep(300)
  console.log(fails === 0 ? '\n全部通过' : '\n' + fails + ' 项失败')
  process.exit(fails === 0 ? 0 : 1)
}
