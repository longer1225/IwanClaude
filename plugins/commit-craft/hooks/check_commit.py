#!/usr/bin/env python3
"""
commit-craft 的 PreToolUse 守卫：拦截不符合 Conventional Commit 的 git commit -m

【学习要点】
1. hook 协议三态：exit 0 = 弃权放行；exit 2 = 硬阻断（stderr 回灌模型改错）；
   其他退出码/崩溃 = 转人工审批（ask）——本脚本故意不做兜底 try-except 吞异常，
   脚本自身的 bug 应当暴露成 ask，而不是装作检查通过。
2. 只管"带 -m 的 git commit"：交互式（无 -m，信息在编辑器里写）不可见，放行；
   merge/revert 自动消息自带格式，放行。守卫只裁它能看见的东西。
3. stdin 载荷 schema 与官方对齐（tool_name/tool_input），生态脚本可零改动移植。
"""
from __future__ import annotations

import json
import re
import sys

# Conventional Commit 头部：type(scope)!: subject（scope 与 ! 可选）
_HEADER_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([^)]+\))?!?: .{4,}"
)

# 从 bash 命令串里抓 -m/--message 后的第一段引号内容（够守门用，不解析完整 shell）
_MSG_RE = re.compile(r"(?:-m|--message)\s+(?:\"([^\"]+)\"|'([^']+)')")


# 提取工具参数里的 commit 信息；返回 None 表示"这不是可见格式的 commit，放行"
def extract_commit_message(command: str) -> str | None:
    if not re.search(r"\bgit\s+commit\b", command):
        return None
    match = _MSG_RE.search(command)
    if match is None:
        return None  # 交互式/merge 自动消息：内容不可见，无权裁——守卫只裁它能看见的
    return match.group(1) or match.group(2)


# 主流程：读 stdin 载荷 → 判定 → 三态退出
def main() -> int:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return 0
    command = str(payload.get("tool_input", {}).get("command", ""))
    message = extract_commit_message(command)
    if message is None:
        return 0
    head = message.splitlines()[0] if message.strip() else ""
    if _HEADER_RE.match(head):
        return 0
    sys.stderr.write(
        "提交信息不符合 Conventional Commit："
        f"「{head[:60]}」\n"
        "格式：<type>(<scope>): <subject>，"
        "type ∈ feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert\n"
        "示例：feat: 插件系统①——三级贡献汇入 hooks/MCP/skills\n"
        "（正文空行后自由书写）"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
