"""
IwanClaude CLI 主入口模块

【学习要点】
1. argparse 模块：Python 标准库，用于解析命令行参数
2. 子命令模式：通过 add_subparsers 创建子命令，实现命令的层次结构
3. 配置加载：在执行具体命令前加载全局配置
4. 日志初始化：确保所有命令都有统一的日志格式

【命令结构】
iwan [全局选项] <子命令> [子命令选项]

全局选项：
  --version       显示版本信息

子命令：
  ping            测试与核心服务的连接
  chat            启动交互式聊天会话
  run             运行单个 agent 任务
  core start/stop/status  管理核心服务
  trace           查看系统追踪日志
"""
from __future__ import annotations

# argparse：Python 标准库，用于解析命令行参数
# sys：提供对 Python 运行时环境的访问，如退出程序
import argparse
import sys

# 导入各个子命令的实现函数
from iwan_claude.cli.commands.chat import cmd_chat           # 聊天命令
from iwan_claude.cli.commands.core import (                  # 核心服务管理命令
    cmd_core_start,
    cmd_core_status,
    cmd_core_stop,
)
from iwan_claude.cli.commands.ping import cmd_ping           # 连接测试命令
from iwan_claude.cli.commands.run import cmd_run             # 任务执行命令
from iwan_claude.cli.commands.trace import cmd_trace         # 日志追踪命令
from iwan_claude.cli.commands.version import cmd_version     # 版本显示命令

# 导入核心模块
from iwan_claude.core.config import get_config               # 配置加载函数
from iwan_claude.core.logging_setup import setup_logging     # 日志初始化函数


# CLI 主入口函数：解析命令行参数并分发到对应子命令
def main() -> None:
    # 创建主解析器，prog 指定程序名，description 是帮助信息
    parser = argparse.ArgumentParser(prog="iwan", description="IwanClaude CLI")
    
    # 添加全局选项：--version，action="store_true" 表示出现该选项时值为 True
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    
    # 创建子命令解析器，dest="command" 将子命令名存储到 args.command
    subparsers = parser.add_subparsers(dest="command")

    # ===== 简单子命令（无额外参数）=====
    subparsers.add_parser("ping", help="Ping the core daemon")
    subparsers.add_parser("chat", help="Start a multi-turn chat session")

    # ===== run 子命令（需要参数）=====
    run_parser = subparsers.add_parser("run", help="Run an agent task")
    # --goal 参数，required=True 表示必须提供
    run_parser.add_argument("--goal", required=True, help="Goal for the agent to accomplish")

    # ===== core 子命令（有子子命令）=====
    core_parser = subparsers.add_parser("core", help="Manage the core daemon")
    core_sub = core_parser.add_subparsers(dest="core_command")
    core_sub.add_parser("start", help="Start the daemon in the background")
    core_sub.add_parser("stop", help="Stop the running daemon")
    core_sub.add_parser("status", help="Show daemon status")

    # ===== trace 子命令（多个可选参数）=====
    trace_parser = subparsers.add_parser("trace", help="View system trace log")
    # run_id：位置参数，nargs="?" 表示可选，default=None 是默认值
    trace_parser.add_argument("run_id", nargs="?", default=None, help="Filter by run ID")
    # --layer：可选参数，choices 限制取值范围
    trace_parser.add_argument("--layer", choices=["ipc", "event", "llm"], help="Filter by layer")
    # --direction：普通可选参数
    trace_parser.add_argument("--direction", help="Filter by direction (e.g. CORE→LLM)")
    # --raw：开关参数，action="store_true"
    trace_parser.add_argument("--raw", action="store_true", help="Output raw NDJSON")
    # --follow/-f：带短选项的开关参数
    trace_parser.add_argument("--follow", "-f", action="store_true", help="Follow new records")

    # 解析命令行参数，结果存储在 args 对象中
    args = parser.parse_args()

    # 处理 --version（全局选项，不需要加载配置）
    if args.version:
        cmd_version()
        return

    # 加载全局配置（从环境变量、配置文件等）
    config = get_config()
    # 初始化日志系统
    setup_logging(config)

    # ===== 命令分发逻辑 =====
    # 根据 args.command 的值调用对应的处理函数
    if args.command == "ping":
        cmd_ping(config)
    elif args.command == "chat":
        cmd_chat(config)
    elif args.command == "run":
        cmd_run(args.goal, config)
    elif args.command == "core":
        # core 命令需要进一步判断子子命令
        if args.core_command == "start":
            cmd_core_start(config)
        elif args.core_command == "stop":
            cmd_core_stop(config)
        elif args.core_command == "status":
            cmd_core_status(config)
        else:
            # 未提供子子命令，显示帮助并退出
            core_parser.print_help()
            sys.exit(1)
    elif args.command == "trace":
        cmd_trace(
            args.run_id,
            config,
            layer=args.layer,
            direction=args.direction,
            raw=args.raw,
            follow=args.follow,
        )
    else:
        # 未提供命令，显示帮助并退出
        parser.print_help()
        sys.exit(1)
