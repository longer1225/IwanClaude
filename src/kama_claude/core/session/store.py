# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 json：用于序列化/反序列化
import json
# 导入 logging：用于日志记录
import logging
# 导入 datetime：用于生成时间戳
from datetime import UTC, datetime
# 导入 Path：用于文件路径操作
from pathlib import Path
# 导入 Any：类型提示
from typing import Any

# 导入 Session：会话模型
from kama_claude.core.session.model import Session

# 创建日志记录器
logger = logging.getLogger(__name__)

# MessageContent：消息内容类型（可以是字符串或内容块列表）
MessageContent = str | list[dict[str, Any]]


# 返回当前 UTC 时间的 ISO 8601 字符串
def _now() -> str:
    return datetime.now(UTC).isoformat()


# SessionStore 类：负责会话数据的持久化存储
# 什么是持久化？就是把数据保存到文件，重启后还能恢复
class SessionStore:
    # 初始化：设置存储根目录（默认 ~/.kama/sessions）
    def __init__(self, root: Path) -> None:
        self._root = root.expanduser()  # 展开用户目录（~ → 用户主目录）
        self._root.mkdir(parents=True, exist_ok=True)  # 创建目录（如果不存在）

    # 返回指定 session 的目录路径（~/.kama/sessions/{session_id}/）
    def session_dir(self, sid: str) -> Path:
        return self._root / sid

    # 返回指定 session 下的 runs 目录路径（~/.kama/sessions/{session_id}/runs/）
    def runs_dir(self, sid: str) -> Path:
        return self.session_dir(sid) / "runs"

    # 将 session 元数据写入 meta.json 文件
    def write_meta(self, session: Session) -> None:
        # 获取 session 目录路径
        path = self.session_dir(session.id)
        # 创建目录（如果不存在）
        path.mkdir(parents=True, exist_ok=True)
        # 将 session 对象转为字典，写入 meta.json
        (path / "meta.json").write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # 从 meta.json 文件读取 session 元数据
    def read_meta(self, sid: str) -> Session:
        # 读取 meta.json 文件内容，解析为字典，然后转为 Session 对象
        data = json.loads((self.session_dir(sid) / "meta.json").read_text(encoding="utf-8"))
        return Session.from_dict(data)

    # 追加一条消息到 thread.jsonl（对话历史文件）
    # 什么是 thread？就是连续的对话消息序列（用户消息 + AI 消息）
    def append_message(
        self,
        sid: str,
        role: str,
        content: MessageContent,
        run_id: str | None = None,
    ) -> None:
        # 构建消息行（包含时间戳、角色、内容、run_id）
        row: dict[str, Any] = {"ts": _now(), "role": role, "content": content}
        if run_id is not None:
            row["run_id"] = run_id
        # 获取 session 目录并创建（如果不存在）
        path = self.session_dir(sid)
        path.mkdir(parents=True, exist_ok=True)
        # 以追加模式打开 thread.jsonl，写入一行 JSON
        with (path / "thread.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 批量追加多条消息到 thread.jsonl
    def append_messages(
        self,
        sid: str,
        messages: list[dict[str, Any]],
        run_id: str,
    ) -> None:
        for msg in messages:
            self.append_message(
                sid,
                role=str(msg["role"]),
                content=msg["content"],
                run_id=run_id,
            )

    # 读取完整对话历史并返回可直接传给 Anthropic API 的 messages 格式
    def read_messages(self, sid: str) -> list[dict[str, Any]]:
        # 获取 thread.jsonl 文件路径
        path = self.session_dir(sid) / "thread.jsonl"
        # 如果文件不存在，返回空列表
        if not path.exists():
            return []

        messages: list[dict[str, Any]] = []
        # 逐行读取 thread.jsonl
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            # 跳过空行
            if not line:
                continue
            try:
                # 解析 JSON 行
                row = json.loads(line)
            except json.JSONDecodeError:
                # JSON 解析失败，记录警告并跳过
                logger.warning("skip broken thread row sid=%s line=%s", sid, line_no)
                continue
            # 获取消息角色
            role = row.get("role")
            # 只保留 user 和 assistant 角色的消息
            if role not in ("user", "assistant"):
                logger.warning(
                    "skip unknown thread role sid=%s line=%s role=%s",
                    sid,
                    line_no,
                    role,
                )
                continue
            # 添加到消息列表
            messages.append({"role": role, "content": row.get("content", "")})

        # 清理未配对的 tool_use（避免传给 Anthropic API 时报错）
        return self._trim_orphan_tool_use(messages)

    # 清理未配对的 tool_use 和 tool_result
    # 什么是未配对？LLM 调用了工具（tool_use），但没有收到工具结果（tool_result）
    # 这种情况可能发生在程序异常退出时，会导致 Anthropic API 报错
    def _trim_orphan_tool_use(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # pending：等待配对的 tool_use ID 集合
        pending: set[str] = set()
        # last_balanced：最后一个配对完整的消息索引
        last_balanced = 0
        
        # 遍历所有消息，追踪 tool_use 和 tool_result 的配对
        for idx, msg in enumerate(messages, start=1):
            content = msg.get("content")
            if isinstance(content, list):
                # assistant 消息中的 tool_use：添加到 pending
                if msg.get("role") == "assistant":
                    for block in content:
                        if block.get("type") == "tool_use":
                            pending.add(str(block.get("id", "")))
                # user 消息中的 tool_result：从 pending 移除
                elif msg.get("role") == "user":
                    for block in content:
                        if block.get("type") == "tool_result":
                            pending.discard(str(block.get("tool_use_id", "")))
            # 如果 pending 为空，说明所有 tool_use 都已配对
            if not pending:
                last_balanced = idx
        
        # 如果还有未配对的 tool_use，截断消息列表
        if pending:
            logger.warning("trim orphan tool_use blocks from thread")
            return messages[:last_balanced]
        return messages

    # 读取 notes.md 文件内容（笔记系统）
    def read_notes(self, sid: str) -> str:
        path = self.session_dir(sid) / "notes.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # 追加一条笔记到 notes.md
    def append_note(self, sid: str, content: str, run_id: str) -> None:
        # 获取 session 目录并创建（如果不存在）
        path = self.session_dir(sid)
        path.mkdir(parents=True, exist_ok=True)
        # 以追加模式打开 notes.md，写入笔记内容
        with (path / "notes.md").open("a", encoding="utf-8") as f:
            f.write(f"## Note ({_now()}, {run_id})\n{content}\n\n")
