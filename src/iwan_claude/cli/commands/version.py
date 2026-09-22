"""
版本显示命令模块

【学习要点】
1. __version__ 属性：Python 包的版本信息通常定义在 __init__.py 中
2. 模块导入：直接导入包名即可访问其顶层属性
3. print() 函数：最简单的输出方式，适合版本信息这种单行输出

【使用示例】
iwan --version
"""
# 导入 iwan_claude 包，访问其 __version__ 属性
import iwan_claude


# 版本显示命令实现函数
def cmd_version() -> None:
    """打印当前 iwan_claude 包的版本号"""
    # __version__ 是 Python 包的标准属性，通常定义在包的 __init__.py 中
    # 这里直接打印版本号，简单明了
    print(iwan_claude.__version__)
