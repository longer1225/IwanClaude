// 左侧栏：导航 + 搜索 + 项目分组任务列表 + 置顶/改名/移动/关闭 + daemon 状态灯
//
// 【学习要点】1) 分组算法是"派生视图"而非存储结构：sessions 一条平铺数组，
// 每次渲染现算 项目→任务 映射（数据量小，派生比维护第二份真相可靠——Redux 时代
// 就定下的-selector 纪律）。2) cwd 归属三级优先：手动移动 > daemon meta.json >
// 本窗口创建记忆。daemon 说在哪就在哪，TUI 建的会话也能正确归组。3) 右键菜单
// 与悬停按钮共用 Popover 底座；"移动到项目"直接平铺二级项，不做嵌套弹层——
// 嵌套弹层焦点管理是深坑（VS Code 用专门 workbench 层解决，我们不值当）。
import { useMemo, useState } from 'react'
import {
  useStore,
  openSession,
  renameSession,
  cancelRun,
  steerRun,
  closeSession
} from '../store'
import type { SessionMeta } from '../store'
import { useGui, sessionEffectiveCwd, baseName } from '../guiHelpers'
import { Icon } from './Icon'
import type { IconName } from './Icon'
import { Popover, MenuItem } from './Popover'

// 相对时间（列表右端的灰色小字：5分 / 3时 / 昨天 / 6月20日）
function relTime(iso?: string): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  const m = Math.floor(diff / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m}分`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}时`
  const d = Math.floor(h / 24)
  if (d === 1) return '昨天'
  if (d < 8) return `${d}天`
  const dt = new Date(t)
  return `${dt.getMonth() + 1}月${dt.getDate()}日`
}

// 单行导航条目（顶部功能与项目条目共用）
function Row({ item }: { item: { key?: string; label: string; icon: IconName; disabled?: boolean; active?: boolean; onClick?: () => void; trailing?: React.ReactNode } }) {
  const cls = `nav-row${item.active ? ' active' : ''}${item.disabled ? ' disabled' : ''}`
  return (
    <div
      className={cls}
      title={item.disabled ? '后端尚无此功能，计划中' : undefined}
      onClick={item.disabled ? undefined : item.onClick}
    >
      <span className="nav-icon"><Icon name={item.icon} size={15} /></span>
      <span>{item.label}</span>
      <span className="grow" />
      {item.trailing}
    </div>
  )
}

// 会话行：状态点 + 标题 + 时间 + 悬停动作（置顶/改名）+ 右键菜单
function SessionRow({ s, projectRoot }: { s: SessionMeta; projectRoot?: string }) {
  const activeSid = useStore((st) => st.activeSid)
  const running = useStore((st) => st.runningSids.has(s.id))
  const pins = useGui((st) => st.pins)
  const guiSet = useGui((st) => st.set)
  const sessionProject = useGui((st) => st.sessionProject)
  const projects = useGui((st) => st.projects)
  const metaCwd = useStore((st) => st.metaCwd)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const pinned = !!pins[s.id]

  const stopEdit = (): void => {
    setEditing(false)
    setDraft('')
  }

  // 输入统一处理器：Enter 提交改名，Esc 取消
  const onKey = (e: React.KeyboardEvent): void => {
    if (e.key === 'Escape') stopEdit()
    if (e.key !== 'Enter') return
    e.preventDefault()
    const v = draft.trim()
    if (v) void renameSession(s.id, v).catch((err) => console.warn(String(err)))
    stopEdit()
  }

  // 转向输入不进菜单——运行中直接在行下展开输入框（M1 语义保留）
  const [steering, setSteering] = useState(false)
  const [steerText, setSteerText] = useState('')

  const moveTargets = useMemo(() => {
    const roots = new Set(projects)
    for (const id of Object.keys(metaCwd)) roots.add(metaCwd[id])
    if (projectRoot) roots.add(projectRoot)
    const cur = sessionProject[s.id]
    return [...roots].filter((r) => r && r !== cur)
  }, [projects, metaCwd, sessionProject, s.id, projectRoot])

  return (
    <div className={`sess-row${s.id === activeSid ? ' active' : ''}${pinned ? ' pinned' : ''}`}>
      {editing ? (
        <input
          className="row-input"
          autoFocus
          value={draft}
          placeholder="新标题，Enter 确认"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          onBlur={stopEdit}
        />
      ) : (
        <>
          <span className="sess-title" title={s.title} onClick={() => void openSession(s.id)}>
            {pinned && <Icon name="pin" size={11} className="pin-mark" />}
            {s.title || '(未命名)'}
          </span>
          {running ? (
            <span className="dot dot-run" title="运行中" />
          ) : (
            <span className="sess-time">{relTime(s.updatedAt)}</span>
          )}
          <Popover
            align="right"
            anchor={(open) => (
              <span className="row-actions">
                {running && (
                  <button
                    className="act"
                    title="转向"
                    onClick={() => {
                      setSteerText('')
                      setSteering((v) => !v)
                    }}
                  >
                    <Icon name="zap" size={13} />
                  </button>
                )}
                {running && (
                  <button
                    className="act act-stop"
                    title="停止运行"
                    onClick={() => void cancelRun(s.id)}
                  >
                    <Icon name="stop" size={13} />
                  </button>
                )}
                <button
                  className="act"
                  title={pinned ? '取消置顶' : '置顶'}
                  onClick={() => guiSet({ pins: { ...pins, [s.id]: !pinned } })}
                >
                  <Icon name="star" size={13} className={pinned ? 'star-on' : ''} />
                </button>
                <button className="act" title="更多" onClick={open}>
                  <Icon name="more" size={13} />
                </button>
              </span>
            )}
          >
            {(close) => (
              <>
                <MenuItem
                  icon={<Icon name="pencil" size={14} />}
                  label="改名"
                  onClick={() => {
                    close()
                    setDraft(s.title)
                    setEditing(true)
                  }}
                />
                <MenuItem
                  icon={<Icon name="star" size={14} />}
                  label={pinned ? '取消置顶' : '置顶'}
                  onClick={() => {
                    close()
                    guiSet({ pins: { ...pins, [s.id]: !pinned } })
                  }}
                />
                {moveTargets.map((r) => (
                  <MenuItem
                    key={r}
                    icon={<Icon name="folder" size={14} />}
                    label={`移动到 ${baseName(r)}`}
                    onClick={() => {
                      close()
                      guiSet({ sessionProject: { ...sessionProject, [s.id]: r } })
                    }}
                  />
                ))}
                {sessionProject[s.id] && (
                  <MenuItem
                    icon={<Icon name="x" size={14} />}
                    label="清除移动归属"
                    onClick={() => {
                      close()
                      const next = { ...sessionProject }
                      delete next[s.id]
                      guiSet({ sessionProject: next })
                    }}
                  />
                )}
                <div className="menu-sep" />
                <MenuItem
                  icon={<Icon name="trash" size={14} />}
                  label="关闭会话"
                  danger
                  onClick={() => {
                    close()
                    void closeSession(s.id).catch((err) => console.warn(String(err)))
                  }}
                />
              </>
            )}
          </Popover>
        </>
      )}
      {steering && (
        <input
          className="row-input steer-input"
          autoFocus
          value={steerText}
          placeholder="转向指令，Enter 发送 / Esc 取消"
          onChange={(e) => setSteerText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setSteering(false)
            if (e.key !== 'Enter') return
            e.preventDefault()
            const v = steerText.trim()
            if (v) void steerRun(s.id, v).catch((err) => console.warn(String(err)))
            setSteering(false)
          }}
          onBlur={() => setSteering(false)}
        />
      )}
    </div>
  )
}

export function Sidebar() {
  const sessions = useStore((s) => s.sessions)
  const activeSid = useStore((s) => s.activeSid)
  const status = useStore((s) => s.status)
  const setActive = useStore((s) => s.setActive)
  const metaCwd = useStore((s) => s.metaCwd)
  const gui = useGui()
  const [query, setQuery] = useState('')

  // 分组：派生视图。cwd 三级优先：手动移动 > daemon meta > 窗口账本
  const { groups, recents } = useMemo(() => {
    const byRoot = new Map<string, SessionMeta[]>()
    const recents: SessionMeta[] = []
    const q = query.trim().toLowerCase()
    for (const s of sessions) {
      if (q && !s.title.toLowerCase().includes(q)) continue
      const cwd = sessionEffectiveCwd(s.id, metaCwd, gui.sessionProject, s.cwd)
      if (!cwd) {
        recents.push(s)
        continue
      }
      const g = byRoot.get(cwd) ?? []
      g.push(s)
      byRoot.set(cwd, g)
    }
    // 空登记项目也保留一行（用户主动 Add project 的结果）
    for (const p of gui.projects) if (!byRoot.has(p)) byRoot.set(p, [])
    const groups = [...byRoot.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
    return { groups, recents }
  }, [sessions, query, metaCwd, gui.projects, gui.sessionProject])

  // 组内排序：置顶优先，其后按更新时间新→旧
  const sorted = (list: SessionMeta[]): SessionMeta[] =>
    [...list].sort((a, b) => {
      const pa = gui.pins[a.id] ? 1 : 0
      const pb = gui.pins[b.id] ? 1 : 0
      if (pa !== pb) return pb - pa
      return (b.updatedAt ?? '').localeCompare(a.updatedAt ?? '')
    })

  // 添加项目：目录对话框 → 登记表 + 立即成为新会话默认 cwd
  const addProject = async (): Promise<void> => {
    const dir = await window.iwan.pickDirectory()
    if (!dir) return
    gui.addProject(dir)
    useStore.getState().setCwd(dir)
    setActive(null)
  }

  // 项目内新建任务：切默认目录 + 回空态
  const newTaskIn = (root: string): void => {
    useStore.getState().setCwd(root)
    setActive(null)
  }

  const toggleGroup = (root: string): void =>
    gui.set({ collapsed: { ...gui.collapsed, [root]: !gui.collapsed[root] } })

  return (
    <aside className="sidebar">
      <div className="side-head">
        <span className="brand">iwan</span>
        <span className="grow" />
        <button className="icon-btn" title="设置" onClick={() => gui.set({ settingsOpen: true })}>
          <Icon name="gear" size={15} />
        </button>
        <span className={`dot dot-${status}`} title={`daemon: ${status}`} />
      </div>

      <nav className="side-nav">
        <Row item={{ key: 'new', label: '新对话', icon: 'pencil', active: activeSid === null, onClick: () => setActive(null) }} />
        <Row item={{ key: 'pr', label: 'Pull Request', icon: 'branch', disabled: true }} />
        <Row item={{ key: 'cron', label: '定时任务', icon: 'clock', disabled: true }} />
        <Row item={{ key: 'plug', label: '插件 / MCP', icon: 'plug', disabled: true }} />
      </nav>

      <div className="side-search">
        <Icon name="search" size={13} />
        <input
          placeholder="搜索任务…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button className="icon-btn tiny" onClick={() => setQuery('')} title="清除">
            <Icon name="x" size={12} />
          </button>
        )}
      </div>

      <div className="side-scroll">
        {groups.length > 0 && (
          <section className="side-group">
            <div className="group-title">
              项目
              <span className="grow" />
              <button className="icon-btn tiny" title="添加项目" onClick={() => void addProject()}>
                <Icon name="folderPlus" size={13} />
              </button>
            </div>
            {groups.map(([root, list]) => (
              <div key={root} className="project">
                <div
                  className={`project-name${activeSid && list.some((x) => x.id === activeSid) ? ' has-active' : ''}`}
                  title={root}
                >
                  <button className="icon-btn tiny" onClick={() => toggleGroup(root)} title={gui.collapsed[root] ? '展开' : '折叠'}>
                    <Icon name={gui.collapsed[root] ? 'chevronRight' : 'chevronDown'} size={13} />
                  </button>
                  <Icon name={gui.collapsed[root] ? 'folder' : 'folderOpen'} size={14} className="proj-ico" />
                  <span className="proj-name" onClick={() => toggleGroup(root)}>{baseName(root)}</span>
                  <span className="proj-count">{list.length}</span>
                  <span className="grow" />
                  <button className="icon-btn tiny" title="在此项目新建任务" onClick={() => newTaskIn(root)}>
                    <Icon name="plus" size={13} />
                  </button>
                  <Popover
                    align="right"
                    className="menu-compact"
                    anchor={(open) => (
                      <button className="icon-btn tiny" title="项目操作" onClick={open}>
                        <Icon name="more" size={13} />
                      </button>
                    )}
                  >
                    {(close) => (
                      <>
                        <MenuItem
                          icon={<Icon name="folderOpen" size={14} />}
                          label="在文件管理器中打开"
                          onClick={() => {
                            close()
                            void window.iwan.openPath(root).then((err) => err && console.warn('openPath:', err))
                          }}
                        />
                        {!list.length && (
                          <MenuItem
                            icon={<Icon name="trash" size={14} />}
                            label="从列表移除"
                            danger
                            onClick={() => {
                              close()
                              gui.removeProject(root)
                            }}
                          />
                        )}
                      </>
                    )}
                  </Popover>
                </div>
                {!gui.collapsed[root] && sorted(list).map((s) => <SessionRow key={s.id} s={s} projectRoot={root} />)}
              </div>
            ))}
          </section>
        )}

        {recents.length > 0 && (
          <section className="side-group">
            <div className="group-title">最近</div>
            {sorted(recents).map((s) => (
              <SessionRow key={s.id} s={s} />
            ))}
          </section>
        )}
        {query && !groups.length && !recents.length && (
          <div className="side-empty">没有匹配「{query}」的任务</div>
        )}
      </div>

      <div className="side-foot">
        <span className={`dot dot-${status}`} />
        <span className="foot-item">iwan-core</span>
        <span className="grow" />
        <span className="foot-item dim">7437</span>
      </div>
    </aside>
  )
}
