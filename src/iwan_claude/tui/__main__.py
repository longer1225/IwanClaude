"""
TUI 入口模块

该模块是 IwanClaude TUI 的主入口，负责初始化日志系统、解析命令行参数并启动 TUI 应用。

核心功能：
- 初始化文件日志系统（不输出到 stderr，避免干扰 Textual 渲染）
- 解析命令行参数（支持 --replay 参数回放历史运行）
- 读取配置并启动 TUI 应用

设计要点：
- 使用 RotatingFileHandler 实现日志滚动，单个文件最大 5MB，保留 3 个备份
- 日志格式包含级别、时间戳、来源和消息，便于问题排查
- 通过环境变量 IWAN_TUI_LOG_FILE 可自定义日志路径

使用方式：
    python -m iwan_claude.tui                    # 启动 TUI
    python -m iwan_claude.tui --replay <RUN_ID>   # 启动并回放指定运行的事件
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
from pathlib import Path

from iwan_claude.core.config import get_config
from iwan_claude.tui.app import IwanTuiApp

# 默认 TUI 日志文件路径，位于用户 home 目录下的 .iwan/logs/tui.log
_DEFAULT_TUI_LOG = "~/.iwan/logs/tui.log"


def _setup_logging(level: str) -> None:
    """
    初始化 TUI 文件日志系统

    将日志写入文件而非 stderr，避免干扰 Textual 的终端渲染。

    参数：
        level: str - 日志级别（DEBUG、INFO、WARNING、ERROR）

    实现步骤：
    1. 获取日志文件路径（支持环境变量覆盖）
    2. 创建父目录（如果不存在）
    3. 创建 RotatingFileHandler，设置最大文件大小和备份数
    4. 配置日志格式（级别、时间戳、来源、消息）
    5. 清除默认 handler，添加文件 handler

    设计要点：
    - 使用 RotatingFileHandler 实现日志滚动，防止日志文件过大
    - 单个文件最大 5MB，保留 3 个备份文件
    - 使用 UTF-8 编码，确保中文等非 ASCII 字符正确处理
    - 清除默认 handler 避免日志同时输出到 stderr

    使用示例：
        >>> _setup_logging("INFO")
    """
    log_path = Path(os.environ.get("IWAN_TUI_LOG_FILE", _DEFAULT_TUI_LOG)).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            'level=%(levelname)s ts=%(asctime)s source=%(name)s msg="%(message)s"',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    root.handlers.clear()
    root.addHandler(handler)


def main() -> None:
    """
    TUI 入口函数

    解析命令行参数，初始化日志系统，读取配置并启动 TUI 应用。

    命令行参数：
        --replay RUN_ID: 可选，连接后回放指定运行的事件

    实现步骤：
    1. 创建 ArgumentParser，定义 --replay 参数
    2. 解析命令行参数
    3. 获取配置（主机地址、端口、日志级别等）
    4. 初始化日志系统
    5. 创建 IwanTuiApp 实例
    6. 启动 TUI 应用

    使用示例：
        >>> main()  # 直接运行时调用

    命令行使用：
        python -m iwan_claude.tui
        python -m iwan_claude.tui --replay run-abc123
    """
    parser = argparse.ArgumentParser(prog="iwan-tui", description="IwanClaude TUI")
    parser.add_argument(
        "--replay",
        metavar="RUN_ID",
        help="Replay events from a past run on connect",
    )
    args = parser.parse_args()

    config = get_config()
    _setup_logging(config.logging.level)
    app = IwanTuiApp(config.host, config.port, replay_run_id=args.replay)
    app.run()


if __name__ == "__main__":
    main()
