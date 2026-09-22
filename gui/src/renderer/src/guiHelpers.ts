// GUI 侧的小工具：项目归属计算与路径末段
//
// 【学习要点】sessionEffectiveCwd 是三级证据链的唯一出口：
// 手动移动（用户意图最强）> daemon meta.json 的 cwd（后端事实）> 窗口账本
// （GUI 本地创建记忆）。集中一处判定，侧栏/右栏/设置页读到的归属永远一致。
import { sessionCwdStore } from './store'

// 统一 GUI 存储出口：组件一律从 guiHelpers 拿 useGui，避免一半从 '../gui' 一半从助手层导入
export { useGui } from './gui'
export type { ThemeChoice, RightTab } from './gui'

// 取路径末段作为项目名（Windows/POSIX 分隔符都认）
export function baseName(p: string): string {
  const t = p.replace(/[\\/]+$/, '')
  const i = Math.max(t.lastIndexOf('/'), t.lastIndexOf('\\'))
  return i >= 0 ? t.slice(i + 1) : t
}

// 会话的有效项目目录（三级优先，见文件头）
export function sessionEffectiveCwd(
  sid: string,
  metaCwd: Record<string, string>,
  sessionProject: Record<string, string>,
  fallbackCwd?: string
): string | undefined {
  return sessionProject[sid] ?? metaCwd[sid] ?? fallbackCwd ?? sessionCwdStore[sid] ?? undefined
}
