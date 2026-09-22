// 线性图标集：单文件、纯 SVG、stroke=currentColor，不引第三方图标包
//
// 【学习要点】1) VS Code/Codex 的"高级感"一半来自图标统一：同一网格(24)、同一
// 线宽(1.7)、同一圆角端点。混用 emoji/Unicode 符号会立刻露怯（✎▱ 各家字体画得
// 都不一样）。2) aria-hidden：装饰图标对读屏软件是噪音。3) 手写 path 而非装
// lucide-react：30 个图标不到 200 行，省一个依赖、SVG 树全可控。
interface IconProps {
  name: IconName
  size?: number
  className?: string
  title?: string
}

// 图标注册表：name → JSX path 集合（24x24 视图框内绘制）
const PATHS: Record<string, React.ReactNode> = {
  folder: <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.7l1.8 2H19.5A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-11z" />,
  folderOpen: (
    <>
      <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.2l1.8 2h6.9A1.5 1.5 0 0 1 21 9.5v.5" />
      <path d="M3 19l2.4-6.2A1.6 1.6 0 0 1 6.9 12H22l-2.6 6.2a1.6 1.6 0 0 1-1.5 1H4.6A1.6 1.6 0 0 1 3 19z" />
    </>
  ),
  folderPlus: (
    <>
      <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.7l1.8 2H19.5A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-11z" />
      <path d="M12 11v5M9.5 13.5h5" />
    </>
  ),
  chevronDown: <path d="M7 10l5 5 5-5" />,
  chevronRight: <path d="M10 7l5 5-5 5" />,
  chevronLeft: <path d="M14 7l-5 5 5 5" />,
  plus: <path d="M12 5v14M5 12h14" />,
  star: <path d="M12 4l2.3 4.9 5.2.6-3.9 3.6 1 5.2-4.6-2.6L7.4 18.9l1-5.2-3.9-3.6 5.2-.6L12 4z" />,
  pencil: (
    <>
      <path d="M4 20l.9-3.6L15.8 5.4a1.8 1.8 0 0 1 2.6 0l.2.2a1.8 1.8 0 0 1 0 2.6L7.6 19.1 4 20z" />
      <path d="M14.5 6.7l2.8 2.8" />
    </>
  ),
  zap: <path d="M13 3L5 13.5h5.5L11 21l8-10.5h-5.5L13 3z" />,
  stop: <rect x="6.5" y="6.5" width="11" height="11" rx="1.8" />,
  gear: (
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4L18 18M18 6l-1.6 1.6M7.6 16.4L6 18" />
    </>
  ),
  x: <path d="M6 6l12 12M18 6L6 18" />,
  search: (
    <>
      <circle cx="10.8" cy="10.8" r="5.3" />
      <path d="M14.8 14.8L20 20" />
    </>
  ),
  file: (
    <>
      <path d="M6.5 3.5h7.5L18.5 8v12a1.5 1.5 0 0 1-1.5 1.5H6.5A1.5 1.5 0 0 1 5 20V5a1.5 1.5 0 0 1 1.5-1.5z" />
      <path d="M14 3.5V8h4.5" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.2" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  robot: (
    <>
      <rect x="5.5" y="8.5" width="13" height="10" rx="2.2" />
      <path d="M12 5v3.5M9 13v1.5M15 13v1.5M9.5 18h5" />
    </>
  ),
  wrench: <path d="M14.5 6.5a4 4 0 0 0-5.3 5L4 16.6V20h3.4l5.1-5.1a4 4 0 0 0 5-5.4L15.7 12l-2.1-2.1 2.9-3.4z" />,
  shield: <path d="M12 3.5l7 2.5v5.5c0 4.3-3 7.6-7 9-4-1.4-7-4.7-7-9V6l7-2.5z" />,
  info: (
    <>
      <circle cx="12" cy="12" r="8.2" />
      <path d="M12 11v5.5M12 7.8v.4" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3.5v2M12 18.5v2M3.5 12h2M18.5 12h2M6.1 6.1l1.4 1.4M16.5 16.5l1.4 1.4M17.9 6.1l-1.4 1.4M7.5 16.5l-1.4 1.4" />
    </>
  ),
  moon: <path d="M19 14.5A7.8 7.8 0 0 1 9.5 5 7.9 7.9 0 1 0 19 14.5z" />,
  monitor: (
    <>
      <rect x="4" y="5" width="16" height="11" rx="1.6" />
      <path d="M9.5 19.5h5M12 16v3.5" />
    </>
  ),
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  send: <path d="M12 19.5V5M6 11l6-6 6 6" />,
  refresh: (
    <>
      <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
      <path d="M19.8 4.5V8h-3.5" />
    </>
  ),
  panelRight: (
    <>
      <rect x="4" y="5" width="16" height="14" rx="1.8" />
      <path d="M14.5 5v14" />
    </>
  ),
  message: <path d="M5 6.5A1.5 1.5 0 0 1 6.5 5h11A1.5 1.5 0 0 1 19 6.5v8A1.5 1.5 0 0 1 17.5 16H10l-5 4v-13.5z" />,
  branch: (
    <>
      <circle cx="7" cy="6" r="2" />
      <circle cx="7" cy="18" r="2" />
      <circle cx="17" cy="9" r="2" />
      <path d="M7 8v8M17 11c0 3-3 4-6.5 4.5" />
    </>
  ),
  cpu: (
    <>
      <rect x="7.5" y="7.5" width="9" height="9" rx="1.4" />
      <path d="M10 4.5v3M14 4.5v3M10 16.5v3M14 16.5v3M4.5 10h3M4.5 14h3M16.5 10h3M16.5 14h3" />
    </>
  ),
  plug: <path d="M9 3.5v5M15 3.5v5M6.5 8.5h11v3a5.5 5.5 0 0 1-11 0v-3zM12 17v3.5" />,
  trash: (
    <>
      <path d="M5 7.5h14M10 4.5h4M7 7.5l.7 12a1.4 1.4 0 0 0 1.4 1.3h5.8a1.4 1.4 0 0 0 1.4-1.3L17 7.5" />
      <path d="M10.5 11v6M13.5 11v6" />
    </>
  ),
  more: (
    <>
      <circle cx="6" cy="12" r="1.4" />
      <circle cx="12" cy="12" r="1.4" />
      <circle cx="18" cy="12" r="1.4" />
    </>
  ),
  pin: (
    <>
      <path d="M9.5 4.5l5 0 4.5 4.5-2.5 1-2.5 5-3-3-5 2.5 3-5-2.5-1 3-4.5z" />
      <path d="M12.5 12.5L6 19" />
    </>
  ),
  alert: (
    <>
      <path d="M12 4.5L21 19H3L12 4.5z" />
      <path d="M12 10.5v3.5M12 16.3v.4" />
    </>
  ),
  list: <path d="M9 6.5h11M9 12h11M9 17.5h11M4.8 6.5h.4M4.8 12h.4M4.8 17.5h.4" />,
  circle: <circle cx="12" cy="12" r="7.5" />,
  circleFilled: (
    <>
      <circle cx="12" cy="12" r="7.5" />
      <circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none" />
    </>
  ),
  down: <path d="M12 4.5v12M6.5 11.5L12 17l5.5-5.5" />,
  restore: (
    <>
      <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" />
      <path d="M4.2 4.5V8h3.5" />
    </>
  ),
  sparkle: <path d="M12 4l1.7 4.3L18 10l-4.3 1.7L12 16l-1.7-4.3L6 10l4.3-1.7L12 4zM18.5 15.5l.8 1.9 1.9.8-1.9.8-.8 1.9-.8-1.9-1.9-.8 1.9-.8.8-1.9z" />
}

export type IconName = keyof typeof PATHS

// 渲染一个线性图标（size 默认 16，可传 className 微调颜色）
export function Icon({ name, size = 16, className, title }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`icon${className ? ' ' + className : ''}`}
      aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}
    >
      {title && <title>{title}</title>}
      {PATHS[name]}
    </svg>
  )
}
