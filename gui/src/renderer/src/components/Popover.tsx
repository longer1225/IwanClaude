// 轻量弹层：锚点旁展开、点外/Esc 关闭——Composer 选择器与侧栏右键菜单共用
//
// 【学习要点】没引 Radix：这个弹层的"全部难度"就是 outside-click、定位与
// 视口翻转三件事，60 行自己写，供应链零增加。定位用 fixed +
// getBoundingClientRect（VS Code 的 context-view 同理）——不塞进 overflow
// 容器里，永远不被父级裁剪。翻转是 devtest 抓出的真 bug：Composer 贴屏幕底，
// 菜单 200px 高向下展开会整个跑出视口（DOM 在、看不见）——空间不够就向上。
import { useEffect, useRef, useState } from 'react'

interface PopoverProps {
  // 触发器渲染函数：拿到 open 回调
  anchor: (open: () => void) => React.ReactNode
  children: (close: () => void) => React.ReactNode
  align?: 'left' | 'right'
  className?: string
}

export function Popover({ anchor, children, align = 'left', className }: PopoverProps) {
  const [pos, setPos] = useState<{ x: number; y: number; above: boolean } | null>(null)
  const hostRef = useRef<HTMLDivElement>(null)

  // 打开：量锚点矩形；下方放不下（预留 240px 估高）就翻到上方
  const open = (): void => {
    const el = hostRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const above = window.innerHeight - r.bottom < 240 && r.top > 260
    setPos({ x: align === 'right' ? r.right : r.left, y: above ? r.top : r.bottom + 4, above })
  }

  // 打开期间挂全局监听：点击别处或按 Esc 即关
  useEffect(() => {
    if (!pos) return
    const onDown = (e: MouseEvent): void => {
      if (hostRef.current?.contains(e.target as Node) || (e.target as Element).closest?.('.popover')) return
      setPos(null)
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setPos(null)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [pos])

  const close = (): void => setPos(null)

  return (
    <div className="popover-host" ref={hostRef}>
      {anchor(open)}
      {pos && (
        <div
          className={`popover${className ? ' ' + className : ''}`}
          style={{
            left: align === 'right' ? undefined : Math.min(pos.x, window.innerWidth - 200),
            right: align === 'right' ? window.innerWidth - Math.min(pos.x, window.innerWidth - 40) : undefined,
            top: pos.above ? undefined : pos.y,
            bottom: pos.above ? window.innerHeight - pos.y + 4 : undefined
          }}
        >
          {children(close)}
        </div>
      )}
    </div>
  )
}

// 菜单项：图标+文本+可选快捷键提示，点击即关（children(close) 的糖）
export function MenuItem({
  icon,
  label,
  hint,
  danger,
  active,
  onClick
}: {
  icon?: React.ReactNode
  label: string
  hint?: string
  danger?: boolean
  active?: boolean
  onClick: () => void
}) {
  return (
    <button
      className={`menu-item${danger ? ' danger' : ''}${active ? ' active' : ''}`}
      onClick={onClick}
    >
      {icon && <span className="menu-icon">{icon}</span>}
      <span className="menu-label">{label}</span>
      {hint && <span className="menu-hint">{hint}</span>}
      {active && <span className="menu-check">✓</span>}
    </button>
  )
}
