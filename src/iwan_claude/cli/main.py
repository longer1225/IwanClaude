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
  cancel <run_id>          取消运行中的任务
  steer <run_id> <message> 向运行中的任务注入修正评论
  trust list|grant|deny|revoke <dir>  管理项目目录信任（Layer 0）
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
from iwan_claude.cli.commands.runctl import cmd_cancel, cmd_steer  # 运行中取消/修正命令
from iwan_claude.cli.commands.trace import cmd_trace         # 日志追踪命令
from iwan_claude.cli.commands.trust import cmd_trust         # 项目信任管理命令（Layer 0）
from iwan_claude.cli.commands.version import cmd_version     # 版本显示命令

# 导入懒启动 daemon 守护模块
from iwan_claude.cli.daemon_guard import ensure_daemon       # 懒启动 daemon

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

    # ===== cancel 子命令（按 run_id 取消运行中任务）=====
    cancel_parser = subparsers.add_parser("cancel", help="Cancel a running task by run ID")
    cancel_parser.add_argument("run_id", help="ID of the run to cancel")

    # ===== steer 子命令（向运行中任务注入修正评论）=====
    steer_parser = subparsers.add_parser("steer", help="Send a mid-run correction to a running task")
    steer_parser.add_argument("run_id", help="ID of the target run")
    steer_parser.add_argument("message", help="Correction text")

    # ===== trust 子命令（Layer 0 项目信任管理，S9 Part A）=====
    trust_parser = subparsers.add_parser("trust", help="Manage project trust (Layer 0)")
    trust_sub = trust_parser.add_subparsers(dest="trust_command")
    trust_sub.add_parser("list", help="List persisted trust entries")
    # grant/deny/revoke 都需要目标目录参数
    for _verb, _help in (
        ("grant", "Persistently trust a folder (allow writes & exec)"),
        ("deny", "Persistently deny a folder (block file writes & exec)"),
        ("revoke", "Remove a folder's persistent decision (back to ask)"),
    ):
        _tp = trust_sub.add_parser(_verb, help=_help)
        _tp.add_argument("dir", help="Target directory")

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
        # 懒启动 daemon：没跑就起一个，5 秒超时
        if not ensure_daemon(config):
            print("error: daemon not ready, try `iwan core start` manually", file=sys.stderr)
            sys.exit(1)
        cmd_chat(config)
    elif args.command == "run":
        # 懒启动 daemon：没跑就起一个，5 秒超时
        if not ensure_daemon(config):
            print("error: daemon not ready, try `iwan core start` manually", file=sys.stderr)
            sys.exit(1)
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
    elif args.command == "cancel":
        cmd_cancel(args.run_id, config)
    elif args.command == "steer":
        cmd_steer(args.run_id, args.message, config)
    elif args.command == "trust":
        # 懒启动 daemon：信任条目住在 daemon 的 trust.toml，查询/变更都走 RPC
        if not ensure_daemon(config):
            print("error: daemon not ready, try `iwan core start` manually", file=sys.stderr)
            sys.exit(1)
        if args.trust_command in (None, ""):
            trust_parser.print_help()
            sys.exit(1)
        # list 无目录参数，getattr 兜底空串
        cmd_trust(args.trust_command, getattr(args, "dir", "") or "", config)
    else:
        # 未提供命令，显示帮助并退出
        parser.print_help()
        sys.exit(1)
