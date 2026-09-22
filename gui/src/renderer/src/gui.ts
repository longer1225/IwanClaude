// GUI 自身状态：外观主题、项目登记表、置顶、右栏/设置页开关、文件预览
//
// 【学习要点】刻意与 store.ts（daemon 事件归约）分家：这份状态 daemon 完全不知道，
// 持久化落 ~/.iwan/gui.json（Electron 主进程读写）。这正是 VS Code 的做法——
// "用户设置"和"产品运行时数据"两条轨：settings.json 永远不进服务端。
// 主题实现走 data-theme 属性换 CSS 变量组：一次属性修改，全站 token 热切换。
import { create } from 'zustand'

export type ThemeChoice = 'light' | 'dark' | 'system'
export type RightTab = 'files' | 'changes' | 'tasks'

export interface GuiState {
  theme: ThemeChoice
  fontScale: number
  uiFontPx: number
  codeFontPx: number
  projects: string[] // 登记过的项目根目录（Codex 的 Add project）
  pins: Record<string, boolean> // 会话置顶（客户端概念，daemon 无此字段 → ★本地顶替）
  sessionProject: Record<string, string> // 手动"移动到项目"的归属账本（优先于 metaCwd）
  collapsed: Record<string, boolean> // 分组折叠状态
  rightOpen: boolean
  rightTab: RightTab
  settingsOpen: boolean
  fileView: { root: string; rel: string; text: string; truncated: boolean; binary: boolean; size: number } | null
  hydrated: boolean
  applyFromDisk(): Promise<void>
  set(p: Partial<Pick<GuiState,
    'theme' | 'fontScale' | 'uiFontPx' | 'codeFontPx' | 'rightOpen' | 'rightTab' |
    'settingsOpen' | 'fileView' | 'pins' | 'sessionProject' | 'collapsed'>>): void
  addProject(root: string): void
  removeProject(root: string): void
}

// 把设置写到磁盘（~/.iwan/gui.json；写失败只在控制台留痕，不打断操作）
function persist(s: GuiState): void {
  void window.iwan
    .setSettings({
      theme: s.theme,
      fontScale: s.fontScale,
      uiFontPx: s.uiFontPx,
      codeFontPx: s.codeFontPx,
      projects: s.projects,
      pins: s.pins,
      sessionProject: s.sessionProject,
      collapsed: s.collapsed
    })
    .catch((err) => console.warn('gui.json 写入失败:', String(err)))
}

export const useGui = create<GuiState>((set, get) => ({
  theme: 'light',
  fontScale: 1,
  uiFontPx: 14,
  codeFontPx: 12.5,
  projects: [],
  pins: {},
  sessionProject: {},
  collapsed: {},
  rightOpen: false,
  rightTab: 'files',
  settingsOpen: false,
  fileView: null,
  hydrated: false,
  // 启动时从 gui.json 拉一次全量设置（localStorage 旧账本 sessionCwd 做兜底迁移）
  async applyFromDisk() {
    try {
      const d = await window.iwan.getSettings()
      const patch: Partial<GuiState> = { hydrated: true }
      if (typeof d.theme === 'string') patch.theme = d.theme as ThemeChoice
      if (typeof d.fontScale === 'number') patch.fontScale = d.fontScale
      if (typeof d.uiFontPx === 'number') patch.uiFontPx = d.uiFontPx
      if (typeof d.codeFontPx === 'number') patch.codeFontPx = d.codeFontPx
      if (Array.isArray(d.projects)) patch.projects = d.projects.filter((x): x is string => typeof x === 'string')
      if (d.pins && typeof d.pins === 'object') patch.pins = d.pins as Record<string, boolean>
      if (d.sessionProject && typeof d.sessionProject === 'object')
        patch.sessionProject = d.sessionProject as Record<string, string>
      if (d.collapsed && typeof d.collapsed === 'object') patch.collapsed = d.collapsed as Record<string, boolean>
      set(patch)
      applyTheme(get())
    } catch {
      set({ hydrated: true })
    }
  },
  set(p) {
    set(p)
    const s = get()
    if ('theme' in p || 'fontScale' in p || 'uiFontPx' in p || 'codeFontPx' in p ||
      'pins' in p || 'sessionProject' in p || 'collapsed' in p) persist(s)
    if ('theme' in p || 'fontScale' in p || 'uiFontPx' in p || 'codeFontPx' in p) applyTheme(s)
  },
  addProject(root) {
    if (get().projects.includes(root)) return
    set({ projects: [...get().projects, root] })
    persist(get())
  },
  removeProject(root) {
    set({ projects: get().projects.filter((p) => p !== root) })
    persist(get())
  }
}))

// 把主题/字号落到 <html> 上：data-theme 换变量组 + 两个字体尺寸 CSS 变量
export function applyTheme(s: Pick<GuiState, 'theme' | 'uiFontPx' | 'codeFontPx' | 'fontScale'>): void {
  const root = document.documentElement
  const dark =
    s.theme === 'dark' ||
    (s.theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  root.dataset.theme = dark ? 'dark' : 'light'
  root.style.setProperty('--ui-font-px', `${Math.round(s.uiFontPx * s.fontScale)}px`)
  root.style.setProperty('--code-font-px', `${Math.round(s.codeFontPx * s.fontScale * 10) / 10}px`)
}
