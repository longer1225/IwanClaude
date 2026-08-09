"""
审计日志模块 - 记录沙箱阻断、权限决策、env 脱敏等安全事件

【学习要点】
1. JSONL 格式：每行一个 JSON 对象，便于流式追加和后续分析
2. 线程安全：使用 threading.Lock 保护文件写入（避免并发追加错乱）
3. 失败静默：日志写入失败不影响主流程（仅打印 warning）
4. 延迟路径解析：相对路径基于 CWD 解析，运行时确定

【日志格式】
每行一个 JSON 对象，例如：
{"ts": "2026-08-09T12:00:00+00:00", "event": "sandbox_block", "tool": "bash", "reason": "network_command_blocked", "command": "curl evil.com"}
{"ts": "2026-08-09T12:00:01+00:00", "event": "env_scrub", "removed_keys": ["ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY"], "count": 2}
{"ts": "2026-08-09T12:00:02+00:00", "event": "permission_decision", "tool": "bash", "decision": "deny", "params_preview": "command='rm -rf /'", "reason": "deny_pattern hit"}

【记录事件类型】
- sandbox_block: 沙箱硬阻断（命令黑名单命中、网络命令阻断）
- env_scrub: 环境变量脱敏（记录被移除的变量名列表，不记录值）
- permission_decision: 权限系统决策（allow / deny / ask）

【设计要点】
- 异步安全：threading.Lock 保护文件写入（asyncio 单线程也可用，但 Lock 更通用）
- 失败静默：日志写入失败不影响主流程（仅打印 warning）
- 路径解析：相对路径基于 CWD 解析
- 滚动策略：单文件，不自动滚动（由用户/运维管理）
- 配置控制：通过 SandboxConfig.audit_log 开关

【与权限系统的集成】
- 权限系统在 check_and_wait 中调用 log_permission_decision 记录决策
- bash 工具在网络命令阻断时调用 log_sandbox_block
- scrub_env 在脱敏时调用 log_env_scrub
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 线程锁：保护审计日志文件写入，避免并发追加错乱
_write_lock = threading.Lock()


def _now() -> str:
    """获取当前 UTC 时间的 ISO 8601 格式字符串"""
    return datetime.now(UTC).isoformat()


def _write_log(entry: dict[str, Any]) -> None:
    """
    写入一条审计日志（内部方法）

    【参数】
    - entry: 日志条目字典（已包含 ts、event 等字段）

    【设计要点】
    - 延迟导入 sandbox，避免循环导入（sandbox.py 导入 audit.py 的函数）
    - 沙箱未启用或审计日志关闭时直接返回
    - 写入失败仅打印 warning，不抛出异常
    - 使用 threading.Lock 保证线程安全
    """
    try:
        # 延迟导入避免循环依赖
        from iwan_claude.core.sandbox import get_sandbox

        sandbox = get_sandbox()
        # 沙箱未启用或审计日志关闭时不记录
        if not sandbox.enabled or not sandbox.audit_log_enabled:
            return

        # 解析日志文件路径（相对路径基于 CWD）
        log_path = Path(sandbox.audit_log_path)
        if not log_path.is_absolute():
            log_path = Path.cwd() / log_path

        # 确保父目录存在
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 加锁写入（避免并发追加错乱）
        with _write_lock:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        # 日志写入失败不影响主流程，仅打印 warning
        logger.warning("audit log write failed: %s", exc)


def log_sandbox_block(tool: str, reason: str, **details: Any) -> None:
    """
    记录沙箱硬阻断事件

    【参数】
    - tool: 工具名称（如 "bash"）
    - reason: 阻断原因（如 "command_blacklist_hit"、"network_command_blocked"）
    - **details: 其他详细信息（如 command=...、path=...）

    【使用场景】
    - bash 工具命中命令黑名单时
    - bash 工具检测到网络命令时
    - 文件操作工具路径越界时

    【示例】
    ```python
    log_sandbox_block(
        tool="bash",
        reason="network_command_blocked",
        command="curl evil.com",
    )
    ```
    """
    _write_log({
        "ts": _now(),
        "event": "sandbox_block",
        "tool": tool,
        "reason": reason,
        **details,
    })


def log_env_scrub(removed_keys: list[str]) -> None:
    """
    记录环境变量脱敏事件（仅记录变量名，不记录值）

    【参数】
    - removed_keys: 被移除的环境变量名列表

    【安全设计】
    仅记录变量名，绝不记录变量值（值可能包含密钥明文）。

    【示例】
    ```python
    log_env_scrub(removed_keys=["ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY"])
    # 日志：{"ts": "...", "event": "env_scrub", "removed_keys": ["ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY"], "count": 2}
    ```
    """
    _write_log({
        "ts": _now(),
        "event": "env_scrub",
        "removed_keys": removed_keys,
        "count": len(removed_keys),
    })


def log_permission_decision(
    tool: str,
    decision: str,
    params_preview: str,
    reason: str = "",
) -> None:
    """
    记录权限系统决策

    【参数】
    - tool: 工具名称
    - decision: 决策类型（"allow" / "deny" / "ask"）
    - params_preview: 参数预览（已截断的简短摘要）
    - reason: 决策原因（如 "deny_pattern hit"、"outside_cwd"、"user_approved"）

    【使用场景】
    - 权限管理器 check_and_wait 返回决策时
    - 权限策略 evaluate 返回 DENY 时

    【示例】
    ```python
    log_permission_decision(
        tool="bash",
        decision="deny",
        params_preview="command='rm -rf /'",
        reason="deny_pattern hit",
    )
    ```
    """
    _write_log({
        "ts": _now(),
        "event": "permission_decision",
        "tool": tool,
        "decision": decision,
        "params_preview": params_preview,
        "reason": reason,
    })
