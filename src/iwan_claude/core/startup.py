"""
daemon 启动失败的落盘标记（供 GUI 读取"为什么起不来"）

【背景】
GUI 是"客人"：它拉起 daemon 后不等待，只在连不上时重试。daemon 若在启动期就
`SystemExit`（最常见：`DEEPSEEK_API_KEY ... not set`、配置写错），用户只能看到
界面转圈，像崩溃。GUI 自己也拿不到子进程输出（Windows 下 detached + 重定向不可靠）。

【方案】
GUI 用 `IWAN_STARTUP_ERROR_FILE=<可写路径>` 指定一个标记文件；daemon 入口捕获
启动期异常后把消息写进去；启动成功后（端口已监听）清掉它。GUI 读该文件即可
把真实原因显示在界面上。环境变量缺失时本模块完全是 no-op——不影响非 GUI 启动。
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV = "IWAN_STARTUP_ERROR_FILE"


# daemon 侧标记路径（环境变量未设置时返回 None，表示不落盘）
def startup_error_path() -> Path | None:
    raw = os.environ.get(_ENV, "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except (OSError, ValueError):
        return None


# 写入启动失败原因（尽力而为：写不了就算了，绝不因为诊断再抛一个异常）
def write_startup_error(message: str) -> None:
    path = startup_error_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((message or "").strip()[:2000] + "\n", encoding="utf-8")
    except OSError:
        pass


# 启动成功（端口已监听）后清掉旧标记，避免把上一次的失败当成"现在的问题"
def clear_startup_error() -> None:
    path = startup_error_path()
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
