// 自绘顶栏：无边框窗口的"标题栏替身"——品牌区 + 拖拽区 + 窗口控制三键
//
// 【学习要点】1) Electron 无边框窗口没有系统标题栏，拖拽/双击最大化靠 CSS
// `-webkit-app-region: drag` 让操作系统把这块区域当标题栏处理——但 drag 区域内
// 所有可交互元素（按钮）必须显式 no-drag，否则点击被窗口移动吞掉。2) 最大化
// 按钮的图标要随窗口状态换（最大化⇄还原），状态用"事件推送 + 挂载补查"双通道
// 拿：推送可能错过挂载前的变化，补查保证初值正确——与连接状态同一套模式。
import { useEffect, useState } from 'react'

// 10x10 描边矢量：笔画粗细/端点全部对齐 Windows 11 标题栏图标的观感
function GlyphMin() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <path d="M0 5h10" stroke="currentColor" strokeWidth="1" />
    </svg>
  )
}
function GlyphMax() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="1" />
    </svg>
  )
}
// 还原态：两个错位方块（Win11 样式）
function GlyphRestore() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <rect x="0.5" y="2.5" width="7" height="7" fill="none" stroke="currentColor" strokeWidth="1" />
      <path d="M2.5 2.5V0.5h7v7H7" fill="none" stroke="currentColor" strokeWidth="1" />
    </svg>
  )
}
function GlyphClose() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <path d="M0 0l10 10M10 0L0 10" stroke="currentColor" strokeWidth="1" />
    </svg>
  )
}

export function TitleBar() {
  const [maximized, setMaximized] = useState(false)

  // 初值补查 + 变化订阅：挂载时窗口可能早已最大化（重启 GUI 的场景）
  useEffect(() => {
    void window.iwan.winIsMaximized().then(setMaximized)
    return window.iwan.onWinMaximized(setMaximized)
  }, [])

  return (
    <header className="titlebar">
      <span className="tb-brand">
        <span className="tb-mark" aria-hidden="true" />iwan
      </span>
      <span className="tb-drag" />
      <button
        className="tb-btn"
        title="最小化"
        onClick={() => window.iwan.winMinimize()}
      >
        <GlyphMin />
      </button>
      <button
        className="tb-btn"
        title={maximized ? '向下还原' : '最大化'}
        onClick={() => window.iwan.winToggleMaximize()}
      >
        {maximized ? <GlyphRestore /> : <GlyphMax />}
      </button>
      <button className="tb-btn tb-close" title="关闭" onClick={() => window.iwan.winClose()}>
        <GlyphClose />
      </button>
    </header>
  )
}
