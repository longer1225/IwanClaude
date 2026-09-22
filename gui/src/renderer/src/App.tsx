// 应用根组件：三栏布局（侧栏 + 主区 + 右栏）+ 设置/预览浮层 + 连接编排
//
// 【学习要点】1) 连接生命周期是"事件驱动重连"：主进程 transport 断线自动重连，
// 渲染层只监听 status——每次变 connected 都重新 event.subscribe（幂等新订阅）
// 并刷新会话列表。订阅不放在 App mount 一次搞定，是因为 daemon 重启后旧
// subscription 已随连接死亡，这跟 TUI 重连语义同源。2) topics 是 daemon 侧的
// 发布过滤清单：多订不误伤（不存在的 topic 无事件可推），漏订才是 bug——
// 所以 S9 全部用户可见面（subagent/skill/context/model_selected）一次订齐。
import { useEffect, useState } from 'react'
import { useStore, handleBusEvent, refreshSessionList } from './store'
import { onConnStatus, onDaemonEvent, rpc, getStatus } from './rpc'
import { useGui } from './guiHelpers'
import { Sidebar } from './components/Sidebar'
import { ThreadView } from './components/ThreadView'
import { Composer } from './components/Composer'
import { RightPanel, FileViewer } from './components/RightPanel'
import { SettingsView } from './components/SettingsView'
import { Icon } from './components/Icon'

// 空态插画：Codex 风格的云形轮廓（单色描边，不引图片资源）
function EmptyCloud() {
  return (
    <svg width="72" height="48" viewBox="0 0 72 48" fill="none" aria-hidden="true">
      <path
        d="M18 38c-7 0-12-5-12-11 0-6 5-11 11-11 1-8 8-14 16-14 7 0 13 4 15 11 1 0 2 0 3 0 8 0 14 6 14 13s-6 12-14 12H18z"
        stroke="#C9C4BC"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path d="M33 22l-5 5 5 5M41 22l5 5-5 5" stroke="#C9C4BC" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// 【学习要点】zustand 的选择器每次快照必须返回【同一个引用】（否则
// useSyncExternalStore 认为状态一直在变→无限重渲染）。缺线程时若返回
// 新建的 [] 就踩这个坑，所以共享一个模块级 EMPTY 常量。
const EMPTY: never[] = []

export default function App() {
  const status = useStore((s) => s.status)
  const activeSid = useStore((s) => s.activeSid)
  const thread = useStore((s) => (activeSid ? s.threads[activeSid] ?? EMPTY : EMPTY))
  const setStatus = useStore((s) => s.setStatus)
  const [booted, setBooted] = useState(false)
  const guiHydrate = useGui((s) => s.applyFromDisk)
  const hydrated = useGui((s) => s.hydrated)
  const rightOpen = useGui((s) => s.rightOpen)
  const guiSet = useGui((s) => s.set)

  // 启动拉一次 gui.json（主题/字号/项目表）——在应用样式后才渲染，避免闪白
  useEffect(() => {
    void guiHydrate()
  }, [guiHydrate])

  // 事件与状态订阅：组件卸载时全部退订
  useEffect(() => {
    // 收到 connected（含重连）时的统一动作：重新订阅事件 + 拉会话列表
    const onConnected = (): void => {
      // 每次（重）连接重新订阅；topics 覆盖 S9 全部用户可见面
      void rpc('event.subscribe', {
        topics: [
          'session.*', 'run.*', 'step.*', 'tool.*', 'llm.*',
          'permission.*', 'trust.*', 'subagent.*', 'skill.*', 'context.*'
        ],
        scope: 'global'
      })
        .then(() => refreshSessionList())
        .catch((err) => console.warn('订阅失败:', String(err)))
      setBooted(true)
    }
    const applyStatus = (s: string): void => {
      setStatus(s)
      if (s === 'connected') onConnected()
    }
    const offEvent = onDaemonEvent(handleBusEvent)
    const offStatus = onConnStatus(applyStatus)
    // 补查当前状态：主进程可能在我们挂载前就 connected 了（事件已错过）
    void getStatus().then(applyStatus).catch(() => undefined)
    return () => {
      offEvent()
      offStatus()
    }
  }, [setStatus])

  const empty = !activeSid || thread.length === 0

  return (
    <div className={`shell status-${status}${rightOpen ? ' rp-open' : ''}`}>
      <Sidebar />
      <main className="main">
        <div className="main-inner">
          {empty ? (
            <div className="empty">
              <EmptyCloud />
              <div className="empty-title">我们要构建什么？</div>
              <div className="empty-sub">先选项目目录，回车开一个新会话；运行中随时输入即转向</div>
            </div>
          ) : (
            <ThreadView sid={activeSid!} msgs={thread} />
          )}
        </div>
        <button
          className={`panel-toggle${rightOpen ? ' on' : ''}`}
          title={rightOpen ? '收起右侧面板' : '展开右侧面板：文件 / 变更 / 任务'}
          onClick={() => guiSet({ rightOpen: !rightOpen })}
        >
          <Icon name="panelRight" size={15} />
        </button>
        <Composer booted={booted} />
        {status !== 'connected' && (
          <div className="conn-banner">
            {status === 'connecting' && '正在连接 iwan-core…（首次会自动启动 daemon，冷启动需要几十秒）'}
            {status === 'reconnecting' && '与 daemon 的连接断开，正在重连…'}
            {status === 'error' && '无法连接 daemon —— 可手动运行：uv run iwan-core'}
          </div>
        )}
      </main>
      {rightOpen && <RightPanel />}
      <FileViewer />
      {hydrated && <SettingsView />}
    </div>
  )
}
