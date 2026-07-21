"""
核心服务模块入口

【学习要点】
1. Python 模块入口：__main__.py 是 Python 模块的主入口文件
2. 命令行运行：python -m iwan_claude.core 会执行这个文件
3. 异步运行：使用 asyncio.run() 启动异步应用

【使用方式】
python -m iwan_claude.core

或者通过 CLI 命令：
iwan core start
"""
# 导入核心应用的 run 函数
from iwan_claude.core.app import run

# 启动核心应用
# asyncio.run() 会创建事件循环，运行异步函数，然后关闭事件循环
run()
