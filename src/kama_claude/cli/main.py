# 启用 Python 3.7+ 的延迟注解评估（PEP 563），允许在类型注解中使用尚未定义的类型
from __future__ import annotations

# 导入 Python 标准库中的命令行参数解析模块，用于构建 CLI 界面
import argparse
# 导入系统模块，提供与 Python 解释器交互的功能（如退出程序、标准错误输出）
import sys

# 从 ping 命令模块导入 cmd_ping 函数，用于处理 ping 子命令
from kama_claude.cli.commands.ping import cmd_ping
# 从 version 命令模块导入 cmd_version 函数，用于处理 --version 选项
from kama_claude.cli.commands.version import cmd_version
# 从配置模块导入 get_config 函数，用于加载运行时配置
from kama_claude.core.config import get_config
# 从日志设置模块导入 setup_logging 函数，用于初始化日志系统
from kama_claude.core.logging_setup import setup_logging


# CLI 主入口：解析命令行参数并分发到对应子命令
def main() -> None:
    # 创建参数解析器实例，设置程序名称为 "kama"，描述为 "KamaClaude CLI"
    parser = argparse.ArgumentParser(prog="kama", description="KamaClaude CLI")
    # 添加 --version 选项，使用 store_true 动作（传入时 args.version 为 True），帮助文本说明其功能
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    # 创建子命令解析器容器，将用户选择的子命令名称存储到 args.command 属性中
    subparsers = parser.add_subparsers(dest="command")
    # 添加 ping 子命令，帮助文本说明其功能是 ping core 守护进程
    subparsers.add_parser("ping", help="Ping the core daemon")

    # 解析命令行参数，将用户输入解析为 Namespace 对象，存储到 args 变量中
    args = parser.parse_args()

    # 判断用户是否传入了 --version 选项
    if args.version:
        # 调用 cmd_version() 函数，打印当前包版本号
        cmd_version()
        # 提前返回，结束函数执行
        return

    # 判断用户是否选择了 ping 子命令
    if args.command == "ping":
        # 调用 get_config() 函数，加载运行时配置（默认值 → TOML → .env → 环境变量），返回 KamaConfig 对象
        config = get_config()
        # 调用 setup_logging() 函数，根据配置初始化日志系统（设置级别、格式、输出目标）
        setup_logging(config)
        # 调用 cmd_ping() 函数，执行 ping 操作（向 core 守护进程发送 JSON-RPC 请求）
        cmd_ping(config)
    else:
        # 未匹配到任何有效子命令时，打印帮助信息
        parser.print_help()
        # 以错误码 1 退出程序，表示参数错误
        sys.exit(1)
