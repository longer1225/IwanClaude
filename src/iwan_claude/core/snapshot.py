"""
工作快照模块 - 记录 Agent 执行进度，用于崩溃恢复

【学习要点】
1. 事件订阅模式：通过 EventBus 订阅工具调用和步骤事件，实现非侵入式进度追踪
2. 增量快照：每步完成后写入完整快照（非增量），崩溃时只需读取最后一个 snapshot.json
3. 文件变更追踪：从工具调用参数中提取文件路径，记录哪些文件被修改过
4. 恢复上下文生成：将快照内容格式化为 LLM 可理解的恢复提示

【快照内容说明】
- goal: 用户原始目标（任务是什么）
- step: 当前执行到第几步（进度参考）
- status: 快照写入时的运行状态
- file_changes: 被修改的文件路径列表（去重）
- last_tool: 最后一次工具调用摘要（名称 + 关键参数 + 是否成功）
- updated_at: 快照更新时间

【崩溃恢复流程】
1. Agent 运行中崩溃 → snapshot.json 保留最后一次成功步骤的快照
2. Core 重启 → 检测 status="running" 的会话，标记为 "interrupted"
3. TUI 连接 → 发现 interrupted 会话，提示用户恢复
4. 用户确认 → 读取 snapshot.json，格式化为恢复上下文注入 system prompt
5. Agent 收到恢复上下文，从断点继续执行，不偏离原计划

【设计特点】
- 非侵入式：通过事件订阅实现，不修改引擎代码
- 轻量：只记录关键信息，不记录完整消息历史（消息已持久化在 thread.jsonl）
- 容错：快照写入失败不影响 Agent 正常运行
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iwan_claude.core.bus.events import (
    StepFinishedEvent,
    ToolCallFinishedEvent,
    ToolCallFailedEvent,
    ToolCallStartedEvent,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    """获取当前 UTC 时间的 ISO 8601 格式字符串"""
    return datetime.now(UTC).isoformat()


# 会修改文件的工具名称集合
# 这些工具的 params 中通常包含 "path" 字段，用于追踪文件变更
_FILE_MODIFYING_TOOLS = frozenset({
    "write_file",
    "edit_by_lines",
    "edit_by_search",
    "insert_at_line",
    "delete_lines",
    "delete_file",
    "rename_file",
    "copy_file",
    "mkdir",
})


def _extract_file_path(tool_name: str, params: dict[str, Any]) -> str:
    """
    从工具参数中提取文件路径

    【参数说明】
    - tool_name: 工具名称
    - params: 工具参数字典

    【返回值】
    - str: 文件路径（可能为空字符串）
    """
    # 文件操作工具的路径字段名
    for key in ("path", "file_path", "src", "source"):
        val = params.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _summarize_params(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    从工具参数中提取关键信息用于快照摘要

    【设计目的】
    避免在快照中保存完整参数（可能很大），只保留关键字段
    """
    summary: dict[str, Any] = {}
    # 文件路径
    path = _extract_file_path(tool_name, params)
    if path:
        summary["path"] = path
    # bash 命令
    if tool_name == "bash":
        cmd = params.get("command", "")
        # 截断过长的命令
        summary["command"] = str(cmd)[:200]
    # 目标路径（rename/copy 有 dst/destination）
    for key in ("dst", "destination", "to", "new_path"):
        val = params.get(key)
        if isinstance(val, str) and val:
            summary[key] = val
    return summary


class SnapshotWriter:
    """
    工作快照写入器 - 订阅事件总线，记录 Agent 执行进度

    【工作原理】
    1. 订阅 ToolCallStartedEvent：记录工具名称和参数（待确认成功）
    2. 订阅 ToolCallFinishedEvent：标记工具调用成功，更新文件变更列表
    3. 订阅 ToolCallFailedEvent：标记工具调用失败
    4. 订阅 StepFinishedEvent：将累积的进度写入 snapshot.json

    【使用方式】
    ```python
    writer = SnapshotWriter(run_path / "snapshot.json", goal="实现登录功能")
    bus.subscribe(writer.handle_step_finished)
    bus.subscribe(writer.handle_tool_started)
    bus.subscribe(writer.handle_tool_finished)
    bus.subscribe(writer.handle_tool_failed)
    # Agent 运行...
    # 快照自动写入
    ```

    【容错设计】
    - 快照写入失败只记录日志，不抛异常（不影响 Agent 运行）
    - 快照文件采用原子写入（先写临时文件再重命名）
    """

    def __init__(self, snapshot_path: Path, goal: str) -> None:
        """
        初始化快照写入器

        【参数说明】
        - snapshot_path: 快照文件路径（通常为 run_path / "snapshot.json"）
        - goal: 用户原始目标（用于恢复时提醒 Agent 原始任务）
        """
        self._path = snapshot_path
        self._goal = goal
        self._step = 0
        self._file_changes: list[str] = []  # 去重的文件路径列表
        self._file_changes_set: set[str] = set()  # 用于快速去重
        self._last_tool: dict[str, Any] | None = None
        # 待确认的工具调用（tool_use_id → {name, params}）
        # 用于在 ToolCallFinished 时关联参数
        self._pending: dict[str, dict[str, Any]] = {}

    def handle_tool_started(self, event: ToolCallStartedEvent) -> None:
        """
        处理工具调用开始事件 - 记录工具名称和参数

        【为什么需要在 Started 时记录参数】
        ToolCallFinishedEvent 不包含 params 字段，
        所以在 Started 时缓存参数，Finished 时取出使用
        """
        self._pending[event.tool_use_id] = {
            "name": event.tool_name,
            "params": event.params,
        }

    def handle_tool_finished(self, event: ToolCallFinishedEvent) -> None:
        """
        处理工具调用完成事件 - 更新文件变更列表和最后工具摘要
        """
        pending = self._pending.pop(event.tool_use_id, None)
        tool_name = event.tool_name
        params = pending.get("params", {}) if pending else {}

        # 提取文件路径，更新变更列表
        if tool_name in _FILE_MODIFYING_TOOLS:
            path = _extract_file_path(tool_name, params)
            if path and path not in self._file_changes_set:
                self._file_changes.append(path)
                self._file_changes_set.add(path)

        # 记录最后工具调用摘要
        self._last_tool = {
            "name": tool_name,
            "params": _summarize_params(tool_name, params),
            "success": True,
            "elapsed_ms": event.elapsed_ms,
        }

    def handle_tool_failed(self, event: ToolCallFailedEvent) -> None:
        """
        处理工具调用失败事件 - 记录失败信息
        """
        pending = self._pending.pop(event.tool_use_id, None)
        params = pending.get("params", {}) if pending else {}

        self._last_tool = {
            "name": event.tool_name,
            "params": _summarize_params(event.tool_name, params),
            "success": False,
            "error": event.error_message,
        }

    def handle_step_finished(self, event: StepFinishedEvent) -> None:
        """
        处理步骤完成事件 - 将当前进度写入 snapshot.json

        【为什么在 StepFinished 时写入】
        每步完成是一个安全的检查点：
        - 之前所有工具调用都已完成
        - 消息历史已更新
        - 此时崩溃，下次恢复能从此处继续
        """
        self._step = event.step
        self._write()

    async def handle(self, event: Any) -> None:
        """
        统一事件处理入口 - 根据事件类型分发到对应处理方法

        【为什么需要统一入口】
        EventBus.subscribe() 会将所有事件发送给每个订阅者，
        所以需要在此方法中检查事件类型并分发。

        【事件路由】
        - ToolCallStartedEvent → handle_tool_started（缓存工具参数）
        - ToolCallFinishedEvent → handle_tool_finished（记录文件变更）
        - ToolCallFailedEvent → handle_tool_failed（记录失败）
        - StepFinishedEvent → handle_step_finished（写入快照）
        - 其他事件 → 忽略
        """
        if isinstance(event, ToolCallStartedEvent):
            self.handle_tool_started(event)
        elif isinstance(event, ToolCallFinishedEvent):
            self.handle_tool_finished(event)
        elif isinstance(event, ToolCallFailedEvent):
            self.handle_tool_failed(event)
        elif isinstance(event, StepFinishedEvent):
            self.handle_step_finished(event)

    def _write(self) -> None:
        """将当前快照状态写入文件（原子写入）"""
        data = {
            "goal": self._goal,
            "step": self._step,
            "status": "running",
            "file_changes": list(self._file_changes),
            "last_tool": self._last_tool,
            "updated_at": _now(),
        }
        try:
            # 原子写入：先写临时文件，再重命名
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception:
            logger.warning("snapshot write failed: %s", self._path, exc_info=True)


def read_snapshot(snapshot_path: Path) -> dict[str, Any] | None:
    """
    读取快照文件

    【参数说明】
    - snapshot_path: 快照文件路径

    【返回值】
    - dict | None: 快照内容字典，文件不存在或损坏时返回 None
    """
    if not snapshot_path.exists():
        return None
    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("snapshot read failed: %s", snapshot_path, exc_info=True)
        return None


def format_recovery_context(snapshot: dict[str, Any]) -> str:
    """
    将快照内容格式化为 LLM 可理解的恢复上下文

    【设计目的】
    将快照中的结构化数据转换为自然语言提示，
    注入 system prompt 的 ## Recovery Context 部分，
    帮助 Agent 理解之前的进度并从断点继续

    【输出格式】
    ```
    你正在恢复一个上次崩溃的任务。请从断点继续执行，不要偏离原计划。

    ### 原始任务目标
    <goal>

    ### 上次执行进度
    已完成步骤数：N

    ### 已修改的文件
    - src/auth.py
    - tests/test_auth.py

    ### 最后执行的工具
    write_file (成功) - path: src/auth.py

    ### 恢复指令
    请继续执行原始任务。已完成的步骤不需要重复，从上次中断处继续。
    如果任务已基本完成，请验证结果并总结。
    ```
    """
    goal = snapshot.get("goal", "(未知)")
    step = snapshot.get("step", 0)
    file_changes = snapshot.get("file_changes", [])
    last_tool = snapshot.get("last_tool")

    lines: list[str] = [
        "你正在恢复一个上次崩溃的任务。请从断点继续执行，不要偏离原计划。",
        "",
        f"### 原始任务目标\n{goal}",
        "",
        f"### 上次执行进度\n已完成步骤数：{step}",
    ]

    # 文件变更列表
    if file_changes:
        lines.append("\n### 已修改的文件")
        for path in file_changes:
            lines.append(f"- {path}")
    else:
        lines.append("\n### 已修改的文件\n（无）")

    # 最后工具调用
    if last_tool:
        tool_name = last_tool.get("name", "?")
        success = last_tool.get("success", False)
        status_str = "成功" if success else "失败"
        params = last_tool.get("params", {})
        params_str = " ".join(f"{k}: {v}" for k, v in params.items()) if params else ""
        lines.append(f"\n### 最后执行的工具\n{tool_name} ({status_str}) - {params_str}")

    # 恢复指令
    lines.append(
        "\n### 恢复指令\n"
        "请继续执行原始任务。已完成的步骤不需要重复，从上次中断处继续。\n"
        "如果任务已基本完成，请验证结果并总结。"
    )

    return "\n".join(lines)
