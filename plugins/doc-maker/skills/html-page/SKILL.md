---
name: html-page
description: 生成单文件 HTML 页面：报告页/落地页/数据展示页，自包含、响应式、离线可开、禁外部依赖默认
invocation: both
icon: 🌐
keywords:
  - html
  - 网页
  - 做个页面
  - landing
  - 报告页
  - 数据展示页
  - 可视化页面
allowed_tools:
  - read_file
  - list_dir
  - bash
  - run_python
  - write_file
---

帮我生成一个 HTML 页面，需求如下：

$ARGUMENTS

## 流程（按序执行）

1. **先定页面骨架**：一句话说清这页给谁看、看完该干什么（读懂结论 / 被吸引操作），
   据此选形态——报告页（标题+摘要+图表+明细表）、落地页（主张+要点+行动按钮）、
   数据页（指标卡+表格+图）。骨架不清楚先问，别先写。
2. **写单文件** `<主题>.html`：CSS/JS 全部内嵌，双击 file:// 就能看，不依赖任何
   本地文件或服务器。
3. **自检**（写完必做，用 run_python）：
   - `html.parser` 过一遍确认标签配平、无未闭合；
   - grep 自己的产物：`src=` / `href=` 不得出现 http(s) 外链（除非用户点名要 CDN）；
   - 报告页数：段落/表格行/图表元素数量与素材对得上，无"TODO/占位"残留。

## 结构规范（硬约束）

- `<html lang="zh-CN">` + `<meta charset="utf-8">` + `<meta name="viewport" content="width=device-width, initial-scale=1">`
- 标题层级不跳号：h1 全页唯一，h2/h3 逐级嵌套；语义标签 header/main/section/footer，
  禁一屏 `<div>` 海
- 样式：系统字体栈 `-apple-system, "Segoe UI", "Microsoft YaHei", sans-serif`；
  间距走 8px 栅格（8/16/24/32/48）；圆角统一一档（8px 或 12px）
- 配色：一个主色 + 中性灰阶 + 语义红绿，与文档类同纪律；**必须同时给深浅色**
  （`prefers-color-scheme` 媒体查询，两套 CSS 变量，禁写死白底黑字）
- 响应式：≥900px 宽屏排版、<640px 单列堆叠；表格窄屏允许横向滚动但表头 sticky
- 打印：报告页加 `@media print` 隐藏按钮/导航，表格断行 `tr { break-inside: avoid }`

## 图表与脚本纪律

- 简单图（柱/折线/占比）直接**内嵌 SVG** 手绘——零依赖、打印不糊；数据点 >30 或要
  交互再用 ECharts CDN，且必须：`<script src>` 处注释"此处引入外部 CDN"，并配
  `<noscript>`/onerror 降级成数据表格（离线打开不给白屏）
- **页面里绝不写入任何敏感数据**：token、密钥、内网地址、个人联系方式——HTML 是会被
  转发出去的东西；示例数据用假名
- JS 只做增强（筛选、复制、折叠），页面核心内容不依赖 JS 存在——禁脚本环境（如
  iwan work 右栏源码视图旁的静态渲染器）下内容必须完整可读
- 动画克制：只在入场和状态切换用 transition，不做装饰性循环动画

## 已知坑

- file:// 下 `fetch()` 本地 JSON 必挂（CORS）——数据写死进 HTML，别学 web 项目分离
- `<table>` 里 `white-space: nowrap` 在窄屏会撑爆容器，别给单元格加它
- ECharts 初始化在 DOM 就绪后：脚本放 `</body>` 前或 `DOMContentLoaded` 里
- 中文字体在 Linux 截图机上没有微软雅黑：字体栈必须带 `sans-serif` 兜底

交付时告诉用户：双击文件或文件树里点它 = 系统浏览器打开；GUI 右栏预览页看到的是源码。
