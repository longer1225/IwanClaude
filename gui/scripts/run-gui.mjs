// electron-vite 启动包装：清掉 ELECTRON_RUN_AS_NODE 后透传参数（dev / preview）
//
// 【学习要点】VS Code 集成终端默认注入 ELECTRON_RUN_AS_NODE=1（让内置 electron
// 当 node 用）。这个环境变量会被子进程继承，而 electron.exe 一看它存在就直接
// 退化成纯 Node 跑主进程脚本——require('electron') 只剩一个字符串路径，app
// 是 undefined。桌面应用的启动器必须自己把这个环境污染挡掉。
import { spawnSync } from 'node:child_process'

delete process.env.ELECTRON_RUN_AS_NODE

// 用 shell 包装以兼容 Windows 下 npx 实际是 npx.cmd 的事实
const r = spawnSync('npx', ['electron-vite', ...process.argv.slice(2)], {
  stdio: 'inherit',
  shell: process.platform === 'win32'
})
process.exit(r.status ?? 1)
