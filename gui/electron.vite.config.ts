import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'

// 生产构建专属：往 index.html 注入 CSP meta
//
// 【学习要点】apply:'build' 让这份安全头只在打包产物里出现——dev 下 vite 要注入
// 内联 react-refresh 脚本并连 ws 热更新，任何 CSP 都会把界面打成白屏；
// 生产 file:// 页面里资源全在 self 内，default-src 'self' 是零副作用的硬锁。
const csp: Plugin = {
  name: 'inject-csp',
  apply: 'build',
  transformIndexHtml: (html) =>
    html.replace(
      '</head>',
      `  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:" />\n  </head>`
    )
}

// electron-vite 三入口构建：main/preload 走 Node 目标，renderer 走浏览器目标 + React
export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    plugins: [react(), csp]
  }
})
