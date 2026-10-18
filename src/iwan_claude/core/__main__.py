"""
核心服务模块入口

【学习要点】
1. Python 模块入口：__main__.py 是 Python 模块的主入口文件
2. 命令行运行：python -m iwan_claude.core 会执行这个文件
3. 异步运行：使用 asyncio.run() 启动异步应用
4. 启动期异常兜底：GUI 拉起 daemon 时拿不到子进程输出，所以把失败原因写到
   IWAN_STARTUP_ERROR_FILE 指定的标记文件里，界面才能显示"为什么起不来"

【使用方式】
python -m iwan_claude.core

或者通过 CLI 命令：
iwan core start
"""
from __future__ import annotations

# 导入核心应用的 run 函数（启动期失败的落盘兜底在 run() 内部，CLI 入口同样生效）
from iwan_claude.core.app import run

# 启动核心应用
# asyncio.run() 会创建事件循环，运行异步函数，然后关闭事件循环
run()
