"""
hook 子进程执行器：argv 执行、stdin JSON 载荷、退出码协议、超时保护

【学习要点】
1. 退出码协议是本模块的全部契约：
   - exit 0 + 无输出/非 JSON     → 弃权（NONE，继续走权限链）
   - exit 0 + {"permissionDecision": allow/deny/ask}（defer=弃权）
   - exit 2                      → 硬性 BLOCK，stderr 原文回灌给模型
     （官方语义：exit 2 时 stdout/JSON 一律忽略——stderr 是唯一通道）
   - 其他非零 / 超时 / 起不来    → fail-closed ASK（坏掉的守卫必须变成"问人"，
     绝不能静默视而不见；ASK 是 fail-closed 家族里唯一保留可用性的档位）
2. 载荷走 stdin JSON 而非命令行参数：argv 会出现在系统进程列表里，
   tool_input 可能含密钥/路径——管道只属于这对父子进程。
3. 输出截断（_MAX_FEED）：hook 是外部程序，日志注入/回灌文本撑爆上下文
   都是现实风险，进日志和进模型前都必须设上限。
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from iwan_claude.core.hooks.spec import HookSpec

# hook 输出进入日志/模型反馈的最大字节数
_MAX_FEED = 512


class HookDecision(StrEnum):
    """
    hook 裁定档位：NONE=弃权（不参与裁定），其余对应退出码协议
    """
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    NONE = "none"


@dataclass
class HookOutcome:
    """
    单个 hook（或聚合后）的裁定结果

    【字段说明】
    - decision: HookDecision
    - reason: 人类可读理由（JSON reason / stderr 摘要），进事件与审批弹窗
    - stderr_to_model: exit 2 专用的回灌文本（其余路径为空串）
    - spec: 产生该裁定的 hook 规格（聚合时取最严格者对应的 spec）
    """
    decision: HookDecision
    reason: str
    stderr_to_model: str
    spec: HookSpec | None


# 截断外部输出并做不可打印字符消毒（GBK 控制台友好）
def _clamp(text: str) -> str:
    text = text.replace("\x00", "")
    if len(text.encode("utf-8", "replace")) > _MAX_FEED:
        text = text.encode("utf-8", "replace")[:_MAX_FEED].decode("utf-8", "ignore")
        return text + "…[truncated]"
    return text


# 解析 exit 0 的 stdout JSON（非 JSON / 缺字段 = 弃权；defer 是官方显式弃权别名）
def _parse_stdout_decision(stdout_text: str) -> tuple[HookDecision, str]:
    stripped = stdout_text.strip()
    if not stripped:
        return HookDecision.NONE, ""
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return HookDecision.NONE, ""  # 非 JSON 输出：官方语义为忽略
    if not isinstance(obj, dict):
        return HookDecision.NONE, ""
    raw = str(obj.get("permissionDecision", "")).lower()
    reason = _clamp(str(obj.get("reason", "")))
    if raw in ("allow", "deny", "ask"):
        return HookDecision(raw), reason
    return HookDecision.NONE, reason  # 含 defer 与非法值


# 执行单个 hook 子进程并按退出码协议翻译为 HookOutcome
async def run_hook(spec: HookSpec, payload: dict[str, Any]) -> HookOutcome:
    """
    唯一对外入口：跑一个 hook，永不抛异常（失败全部折叠为 ASK fail-closed）

    【参数说明】
    - spec: HookSpec - 已校验的 hook 规格
    - payload: 传给 hook stdin 的 JSON（hook_event_name/tool_name/tool_input/...）

    【返回值】
    - HookOutcome: 按退出码协议翻译的裁定；调用方只需按 decision 分支
    """
    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *spec.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        # 可执行文件不存在/不可执行：守卫起不来 ≠ 守卫说可以
        return HookOutcome(
            HookDecision.ASK, f"hook 进程启动失败: {exc}", "", spec)
    try:
        stdin_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(stdin_bytes), timeout=spec.timeout_s)
    except TimeoutError:
        # 超时：kill 后按 ASK 处理。Windows 上 kill 可能留下子进程（官方问题
        # #46740 同源），Job Object 兜底接线在 roadmap OS 沙箱批次统一做
        try:
            proc.kill()
            await proc.communicate()
        except ProcessLookupError:
            pass
        return HookOutcome(
            HookDecision.ASK,
            f"hook 超时（>{spec.timeout_s:.0f}s）已终止: {spec.argv[0]!r}", "", spec)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    stdout_text = _clamp(stdout_b.decode("utf-8", "replace"))
    stderr_text = _clamp(stderr_b.decode("utf-8", "replace"))
    rc = proc.returncode if proc.returncode is not None else -1

    if rc == 2:
        # 硬 BLOCK：stdout/JSON 一律忽略（官方语义），stderr 是回灌通道
        return HookOutcome(
            HookDecision.DENY,
            f"hook 硬阻断（exit 2, {elapsed_ms}ms）: {stderr_text}",
            stderr_text or "(hook 未输出 stderr)", spec)
    if rc != 0:
        # 非零非 2：外部程序坏掉了 → fail-closed ASK，不猜它想说什么
        return HookOutcome(
            HookDecision.ASK,
            f"hook 异常退出（rc={rc}, {elapsed_ms}ms）: {stderr_text}", "", spec)
    decision, reason = _parse_stdout_decision(stdout_text)
    return HookOutcome(decision, reason, "", spec)
