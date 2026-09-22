"""
会话存储模块 - 管理会话数据的持久化存储

【学习要点】
1. 文件系统存储：使用文件系统存储会话数据（meta.json、thread.jsonl、notes.md）
2. JSON 序列化：使用 JSON 格式存储会话元数据和消息
3. JSONL 格式：使用 JSONL（每行一个 JSON）格式存储消息历史
4. 线程安全：使用文件追加模式保证并发写入安全
5. 工具调用配对：检测并移除未配对的 tool_use 消息

【存储结构】
每个会话存储在独立目录下：
```
sessions/
└── sess-abc123/
    ├── meta.json          # 会话元数据
    ├── thread.jsonl       # 消息历史（JSONL 格式）
    ├── notes.md           # 会话笔记
    └── runs/              # 运行记录目录
```

【文件格式】
- meta.json: 包含会话 ID、模式、状态、标题、时间戳等
- thread.jsonl: 每行一个消息对象，包含 ts（时间戳）、role（角色）、content（内容）
- notes.md: Markdown 格式的笔记文件

【设计特点】
- 使用 pathlib 处理文件路径
- 使用 UTF-8 编码确保中文支持
- 使用 JSONL 格式支持流式写入和读取
- 自动创建目录结构
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iwan_claude.core.session.model import Session

logger = logging.getLogger(__name__)

# 消息内容类型：字符串或字典列表
# 字符串：普通文本消息
# 字典列表：包含 tool_use、tool_result 等结构化内容
MessageContent = str | list[dict[str, Any]]


def _now() -> str:
    """
    返回当前 UTC 时间的 ISO 8601 字符串

    【返回值】
    - str: 当前 UTC 时间的 ISO 8601 格式字符串（如 "2024-01-15T10:30:45.123456+00:00"）

    【设计目的】
    统一时间格式，便于存储和比较
    """
    return datetime.now(UTC).isoformat()


class SessionStore:
    """
    会话存储管理器 - 管理会话数据的文件存储

    【学习要点】
    1. 路径管理：使用 pathlib 管理文件路径
    2. 文件读写：使用 UTF-8 编码进行文件读写
    3. JSON 序列化：使用 JSON 格式存储会话元数据
    4. JSONL 格式：使用 JSONL 格式存储消息历史
    5. 目录创建：自动创建所需的目录结构

    【存储结构】
    ```
    root/
    └── sess-abc123/
        ├── meta.json          # 会话元数据
        ├── thread.jsonl       # 消息历史（JSONL 格式）
        └── notes.md           # 会话笔记
    ```

    【使用示例】
    ```python
    from pathlib import Path
    from iwan_claude.core.session.store import SessionStore
    
    store = SessionStore(Path("~/.iwan_claude/sessions"))
    
    # 获取会话目录
    dir_path = store.session_dir("sess-abc123")
    
    # 写入会话元数据
    store.write_meta(session)
    
    # 读取会话元数据
    session = store.read_meta("sess-abc123")
    ```
    """

    def __init__(self, root: Path) -> None:
        """
        初始化会话存储管理器

        【参数说明】
        - root: Path - 会话存储的根目录路径

        【执行流程】
        1. 使用 expanduser() 展开波浪号（~）为实际路径
        2. 创建根目录（如果不存在），包括所有父目录
        """
        # 展开波浪号为实际路径
        self._root = root.expanduser()
        # 自动创建目录（包括所有父目录）
        self._root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, sid: str) -> Path:
        """
        返回指定会话的目录路径

        【参数说明】
        - sid: str - 会话 ID

        【返回值】
        - Path: 会话目录的路径对象

        【设计目的】
        构建会话数据存储的基础路径
        """
        return self._root / sid

    def runs_dir(self, sid: str) -> Path:
        """
        返回指定会话下的 runs 目录路径

        【参数说明】
        - sid: str - 会话 ID

        【返回值】
        - Path: runs 目录的路径对象

        【设计目的】
        构建运行记录存储的路径
        """
        return self.session_dir(sid) / "runs"

    def write_meta(self, session: Session) -> None:
        """
        将会话元数据写入 meta.json

        【参数说明】
        - session: Session - 会话对象

        【执行流程】
        1. 获取会话目录路径
        2. 创建会话目录（如果不存在）
        3. 将会话对象转换为字典
        4. 将字典序列化为 JSON 字符串（带缩进，UTF-8 编码）
        5. 写入 meta.json 文件

        【JSON 格式】
        ```json
        {
            "id": "sess-abc123",
            "mode": "chat",
            "status": "active",
            "title": "代码审查",
            "created_at": "2024-01-15T10:30:00",
            "updated_at": "2024-01-15T10:30:00",
            "run_ids": ["run-456"]
        }
        ```
        """
        # 获取会话目录路径
        path = self.session_dir(session.id)
        # 创建会话目录（如果不存在）
        path.mkdir(parents=True, exist_ok=True)
        # 将会话对象转换为字典并写入 JSON 文件
        (path / "meta.json").write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_meta(self, sid: str) -> Session:
        """
        从 meta.json 读取会话元数据

        【参数说明】
        - sid: str - 会话 ID

        【返回值】
        - Session: 从 JSON 文件还原的会话对象

        【执行流程】
        1. 读取 meta.json 文件内容
        2. 解析 JSON 字符串为字典
        3. 使用 Session.from_dict() 还原会话对象

        【注意事项】
        - 文件不存在时会引发 FileNotFoundError
        - JSON 格式错误时会引发 json.JSONDecodeError
        """
        # 读取 meta.json 文件内容并解析为字典
        data = json.loads((self.session_dir(sid) / "meta.json").read_text(encoding="utf-8"))
        # 从字典还原会话对象
        return Session.from_dict(data)

    def append_message(
        self,
        sid: str,
        role: str,
        content: MessageContent,
        run_id: str | None = None,
    ) -> None:
        """
        追加一条消息到 thread.jsonl

        【参数说明】
        - sid: str - 会话 ID
        - role: str - 消息角色（user / assistant）
        - content: MessageContent - 消息内容（字符串或字典列表）
        - run_id: str | None - 运行 ID（可选）

        【执行流程】
        1. 构建消息行字典（包含时间戳、角色、内容）
        2. 如果提供了 run_id，添加到字典中
        3. 创建会话目录（如果不存在）
        4. 以追加模式打开 thread.jsonl 文件
        5. 将消息行序列化为 JSON 字符串并写入文件

        【JSONL 格式】
        ```
        {"ts": "2024-01-15T10:30:45", "role": "user", "content": "Hello"}
        {"ts": "2024-01-15T10:30:46", "role": "assistant", "content": "Hi!"}
        ```

        【注意事项】
        - 使用追加模式（"a"）确保并发写入安全
        - 每条消息占一行，便于流式读取
        """
        # 构建消息行字典
        row: dict[str, Any] = {"ts": _now(), "role": role, "content": content}
        # 如果提供了 run_id，添加到字典中
        if run_id is not None:
            row["run_id"] = run_id
        # 获取会话目录路径
        path = self.session_dir(sid)
        # 创建会话目录（如果不存在）
        path.mkdir(parents=True, exist_ok=True)
        # 以追加模式写入消息到 thread.jsonl
        with (path / "thread.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def append_messages(
        self,
        sid: str,
        messages: list[dict[str, Any]],
        run_id: str,
    ) -> None:
        """
        批量追加消息到 thread.jsonl

        【参数说明】
        - sid: str - 会话 ID
        - messages: list[dict[str, Any]] - 消息列表
        - run_id: str - 运行 ID

        【执行流程】
        1. 遍历消息列表
        2. 对每条消息调用 append_message() 方法

        【设计目的】
        批量处理一次运行产生的多条消息
        """
        for msg in messages:
            self.append_message(
                sid,
                role=str(msg["role"]),
                content=msg["content"],
                run_id=run_id,
            )

    def read_messages(self, sid: str) -> list[dict[str, Any]]:
        """
        读取完整消息历史并返回可直接传给 Anthropic 的 messages

        【参数说明】
        - sid: str - 会话 ID

        【返回值】
        - list[dict[str, Any]]: 消息列表，可直接传给 Anthropic API

        【执行流程】
        1. 检查 thread.jsonl 文件是否存在
        2. 如果不存在，返回空列表
        3. 逐行读取文件内容
        4. 跳过空行和格式错误的行
        5. 跳过角色不是 user/assistant 的消息
        6. 构建消息列表（只保留 role 和 content）
        7. 调用 _trim_orphan_tool_use() 移除未配对的 tool_use
        8. 调用 truncate_tool_results() 截断过长的工具结果

        【注意事项】
        - 文件不存在时返回空列表（而不是抛出异常）
        - 格式错误的行会被跳过并记录警告
        - 只保留 user 和 assistant 角色的消息
        """
        # 获取 thread.jsonl 文件路径
        path = self.session_dir(sid) / "thread.jsonl"
        # 文件不存在时返回空列表
        if not path.exists():
            return []

        # 初始化消息列表
        messages: list[dict[str, Any]] = []
        # 逐行读取文件内容
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            # 跳过空行
            if not line:
                continue
            try:
                # 解析 JSON 行
                row = json.loads(line)
            except json.JSONDecodeError:
                # 格式错误，记录警告并跳过
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

        # 移除未配对的 tool_use
        messages = self._trim_orphan_tool_use(messages)
        # 截断过长的工具结果（导入放在函数内部避免循环导入）
        from iwan_claude.core.compact.budget import truncate_tool_results
        return truncate_tool_results(messages)

    def _trim_orphan_tool_use(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        移除尾部未配对的 tool_use 以及其后的消息

        【参数说明】
        - messages: list[dict[str, Any]] - 消息列表

        【返回值】
        - list[dict[str, Any]]: 清理后的消息列表

        【问题背景】
        Anthropic API 要求每个 tool_use 必须有对应的 tool_result，
        如果消息历史中存在未配对的 tool_use，API 会返回错误。

        【算法逻辑】
        1. 使用集合 pending 追踪未配对的 tool_use ID
        2. 遍历消息列表：
           - assistant 消息中的 tool_use 添加到 pending
           - user 消息中的 tool_result 从 pending 移除
        3. 记录最后一次 pending 为空的位置（last_balanced）
        4. 如果最后 pending 不为空，截断到 last_balanced

        【设计目的】
        确保传给 Anthropic API 的消息历史中没有未配对的 tool_use
        """
        # 用于追踪未配对的 tool_use ID
        pending: set[str] = set()
        # 最后一次配对平衡的位置
        last_balanced = 0
        # 遍历消息列表
        for idx, msg in enumerate(messages, start=1):
            content = msg.get("content")
            # 只处理结构化内容（字典列表）
            if isinstance(content, list):
                if msg.get("role") == "assistant":
                    # assistant 消息中的 tool_use 添加到 pending
                    for block in content:
                        if block.get("type") == "tool_use":
                            pending.add(str(block.get("id", "")))
                elif msg.get("role") == "user":
                    # user 消息中的 tool_result 从 pending 移除
                    for block in content:
                        if block.get("type") == "tool_result":
                            pending.discard(str(block.get("tool_use_id", "")))
            # 如果 pending 为空，更新最后平衡位置
            if not pending:
                last_balanced = idx
        # 如果还有未配对的 tool_use，截断消息列表
        if pending:
            logger.warning("trim orphan tool_use blocks from thread")
            return messages[:last_balanced]
        return messages

    def write_compacted(self, sid: str, messages: list[dict[str, Any]]) -> None:
        """
        将压缩后的消息覆盖写入 thread.jsonl，原文件备份

        【参数说明】
        - sid: str - 会话 ID
        - messages: list[dict[str, Any]] - 压缩后的消息列表

        【执行流程】
        1. 获取 thread.jsonl 文件路径
        2. 生成备份文件名（包含时间戳）
        3. 如果原文件存在，重命名为备份文件
        4. 以写入模式打开新文件
        5. 将每条消息写入文件

        【备份机制】
        原文件会被重命名为 thread_<timestamp>.jsonl.bak
        这样在压缩失败时可以恢复原始数据

        【设计目的】
        用于消息压缩后覆盖写入原始消息文件
        """
        # 获取 thread.jsonl 文件路径
        path = self.session_dir(sid) / "thread.jsonl"
        # 生成备份文件名（包含时间戳）
        ts_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        bak = self.session_dir(sid) / f"thread_{ts_str}.jsonl.bak"
        # 如果原文件存在，重命名为备份文件
        if path.exists():
            path.rename(bak)
        # 以写入模式覆盖写入新文件
        with path.open("w", encoding="utf-8") as f:
            for msg in messages:
                row: dict[str, Any] = {"ts": _now(), "role": msg["role"], "content": msg["content"]}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def write_messages(self, sid: str, messages: list[dict[str, Any]]) -> None:
        """
        将消息列表覆盖写入 thread.jsonl，原文件备份

        【参数说明】
        - sid: str - 会话 ID
        - messages: list[dict[str, Any]] - 消息列表

        【执行流程】
        1. 获取 thread.jsonl 文件路径
        2. 生成备份文件名（包含时间戳）
        3. 如果原文件存在，重命名为备份文件
        4. 以写入模式打开新文件
        5. 将每条消息写入文件

        【备份机制】
        原文件会被重命名为 thread_<timestamp>.jsonl.bak

        【设计目的】
        用于 checkpoint 恢复时覆盖写入消息历史
        """
        # 获取 thread.jsonl 文件路径
        path = self.session_dir(sid) / "thread.jsonl"
        # 生成备份文件名（包含时间戳）
        ts_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        bak = self.session_dir(sid) / f"thread_{ts_str}.jsonl.bak"
        # 如果原文件存在，重命名为备份文件
        if path.exists():
            path.rename(bak)
        # 以写入模式覆盖写入新文件
        with path.open("w", encoding="utf-8") as f:
            for msg in messages:
                row: dict[str, Any] = {"ts": _now(), "role": msg["role"], "content": msg["content"]}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_notes(self, sid: str) -> str:
        """
        读取 notes.md 全文

        【参数说明】
        - sid: str - 会话 ID

        【返回值】
        - str: notes.md 的内容，文件不存在时返回空字符串

        【设计目的】
        读取会话笔记内容，用于展示或分析
        """
        path = self.session_dir(sid) / "notes.md"
        # 文件不存在时返回空字符串
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def append_note(self, sid: str, content: str, run_id: str) -> None:
        """
        将一条笔记追加到 notes.md

        【参数说明】
        - sid: str - 会话 ID
        - content: str - 笔记内容
        - run_id: str - 运行 ID

        【执行流程】
        1. 获取会话目录路径
        2. 创建会话目录（如果不存在）
        3. 以追加模式打开 notes.md 文件
        4. 写入笔记内容（包含标题行，格式为 "## Note (时间戳, run_id)"）

        【笔记格式】
        ```markdown
        ## Note (2024-01-15T10:30:45, run-456)
        用户需求：需要实现登录功能

        ## Note (2024-01-15T10:35:00, run-789)
        技术选型：使用 JWT 进行身份验证
        ```

        【设计目的】
        保存会话中的重要决策和事实，便于后续参考
        """
        # 获取会话目录路径
        path = self.session_dir(sid)
        # 创建会话目录（如果不存在）
        path.mkdir(parents=True, exist_ok=True)
        # 以追加模式写入笔记到 notes.md
        with (path / "notes.md").open("a", encoding="utf-8") as f:
            f.write(f"## Note ({_now()}, {run_id})\n{content}\n\n")

    def list_sessions(self) -> list[Session]:
        """
        列出所有已存储的会话，按更新时间倒序排列

        【返回值】
        - list[Session]: 会话列表，按 updated_at 从新到旧排序

        【执行流程】
        1. 遍历根目录下的所有子目录
        2. 检查每个子目录是否有 meta.json 文件
        3. 读取 meta.json 并还原为 Session 对象
        4. 按 updated_at 降序排序
        5. 返回排序后的列表

        【设计目的】
        支持 TUI 标签页显示所有会话，
        以及启动时恢复最近的会话。

        【注意事项】
        - 只读取 meta.json，不加载消息历史（性能考虑）
        - 格式错误的 meta.json 会被跳过并记录警告
        """
        # 初始化会话列表
        sessions: list[Session] = []
        # 根目录不存在时返回空列表
        if not self._root.exists():
            return sessions
        # 遍历根目录下的所有子目录
        for entry in self._root.iterdir():
            # 只处理目录
            if not entry.is_dir():
                continue
            # 检查是否有 meta.json 文件
            meta_path = entry / "meta.json"
            if not meta_path.exists():
                continue
            try:
                # 读取 meta.json 并还原为 Session 对象
                session = self.read_meta(entry.name)
                sessions.append(session)
            except (json.JSONDecodeError, KeyError) as e:
                # 格式错误，记录警告并跳过
                logger.warning("skip broken session meta %s: %s", entry.name, e)
                continue
        # 按 updated_at 降序排序（最新的在前）
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions
