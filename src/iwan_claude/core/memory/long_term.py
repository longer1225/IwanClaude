"""
长期记忆模块 - 跨会话的持久化记忆

【设计理念】
长期记忆是 Agent 跨会话记住信息的能力。与短期记忆（会话上下文）不同，
长期记忆在会话结束后依然保留，下一次会话可以读取。

【记忆类型】
- preference：用户偏好（如"用户喜欢用 TypeScript"）
- decision：历史决策（如"选择 FAISS 而非 Milvus，因为轻量"）
- fact：项目事实（如"项目使用 LangGraph 作为 Agent 引擎"）
- feedback：用户反馈（如"不要用 print 调试，用 logging"）

【存储格式】
JSONL（每行一个 JSON 对象），文件路径：~/.iwan_claude/memory/long_term.jsonl
JSONL 优点：
- 追加写入高效（不需要读取整个文件再重写）
- 可流式读取（逐行解析）
- 人类可读

【搜索方式】
简单的关键词匹配（TF-IDF 风格）：
- 对 content 和 tags 进行关键词匹配
- 按匹配度排序
- 不依赖外部 API（不需要 Embedding）

【与向量记忆的区别】
- 长期记忆（本模块）：关键词搜索，精确匹配，适合"用户偏好"等明确信息
- 向量记忆（vector_memory.py）：语义搜索，模糊匹配，适合"历史对话"等长文本
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ======================================================================
# 数据结构定义
# ======================================================================


def _now() -> str:
    """获取当前 UTC 时间的 ISO 8601 格式字符串"""
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    """生成唯一的记忆 ID（uuid4 的前 8 位，简短易读）"""
    return uuid.uuid4().hex[:8]


@dataclass
class MemoryEntry:
    """
    单条长期记忆的数据结构

    【字段说明】
    - id: str - 唯一标识符（8 字符的 hex）
    - content: str - 记忆内容（自然语言描述）
    - type: str - 记忆类型（preference/decision/fact/feedback）
    - timestamp: str - 创建时间（ISO 8601 格式）
    - session_id: str | None - 来源会话 ID（可选，用于追溯）
    - tags: list[str] - 标签列表（用于关键词搜索）

    【示例】
    ```python
    MemoryEntry(
        id="a3f1b2c4",
        content="用户偏好使用 TypeScript 而非 JavaScript",
        type="preference",
        timestamp="2024-01-01T12:00:00+00:00",
        session_id="sess_abc123",
        tags=["typescript", "javascript", "preference"],
    )
    ```
    """
    content: str = ""
    type: str = "fact"
    id: str = field(default_factory=_new_id)
    timestamp: str = field(default_factory=_now)
    session_id: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典（用于 JSON 序列化）"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> MemoryEntry:
        """从字典创建（用于 JSON 反序列化）"""
        return cls(
            id=data.get("id", _new_id()),
            content=data.get("content", ""),
            type=data.get("type", "fact"),
            timestamp=data.get("timestamp", _now()),
            session_id=data.get("session_id"),
            tags=data.get("tags", []),
        )


# ======================================================================
# 长期记忆存储
# ======================================================================


class LongTermMemory:
    """
    长期记忆存储管理器

    【职责】
    - 管理长期记忆的增删查
    - 持久化到 JSONL 文件
    - 关键词搜索（不依赖外部 API）

    【使用方式】
    ```python
    memory = LongTermMemory(Path("~/.iwan_claude/memory/long_term.jsonl"))
    memory.load()  # 加载已有记忆

    # 添加记忆
    memory.add("用户偏好用 pytest", type="preference", tags=["pytest", "testing"])

    # 搜索记忆
    results = memory.search("测试框架", top_k=5)

    # 遗忘记忆
    memory.forget("a3f1b2c4")
    ```

    【线程安全】
    本模块不是线程安全的，适合在单线程异步环境中使用。
    如果需要多线程访问，需要加锁。
    """

    def __init__(self, path: Path) -> None:
        """
        初始化长期记忆存储

        【参数说明】
        - path: Path - JSONL 文件路径
            文件不存在时会在 save() 时自动创建。
            父目录不存在时会自动创建。
        """
        self._path = path
        # 内存中的记忆列表（启动时从文件加载）
        self._entries: list[MemoryEntry] = []

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def load(self) -> None:
        """
        从 JSONL 文件加载所有记忆

        【执行流程】
        1. 检查文件是否存在，不存在则跳过（空记忆）
        2. 逐行读取 JSONL 文件
        3. 每行解析为 JSON，创建 MemoryEntry
        4. 跳过解析失败的行（容错）
        """
        if not self._path.exists():
            return

        self._entries.clear()
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                self._entries.append(MemoryEntry.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                # 跳过格式错误的行（容错处理）
                continue

    def save(self) -> None:
        """
        保存所有记忆到 JSONL 文件

        【执行流程】
        1. 确保父目录存在（自动创建）
        2. 将所有记忆序列化为 JSON
        3. 每条记忆一行，写入文件
        """
        # 确保父目录存在
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 逐行写入 JSONL
        lines = [json.dumps(entry.to_dict(), ensure_ascii=False) for entry in self._entries]
        self._path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")

    # ------------------------------------------------------------------
    # 增删查
    # ------------------------------------------------------------------

    def add(
        self,
        content: str,
        *,
        type: str = "fact",
        tags: list[str] | None = None,
        session_id: str | None = None,
    ) -> MemoryEntry:
        """
        添加一条长期记忆

        【参数说明】
        - content: str - 记忆内容（自然语言描述）
        - type: str - 记忆类型（preference/decision/fact/feedback）
        - tags: list[str] | None - 标签列表（用于关键词搜索）
        - session_id: str | None - 来源会话 ID

        【返回值】
        - MemoryEntry: 创建的记忆条目（含生成的 id 和 timestamp）

        【自动保存】
        添加后自动调用 save() 持久化到文件。
        """
        entry = MemoryEntry(
            content=content,
            type=type,
            tags=tags or [],
            session_id=session_id,
        )
        self._entries.append(entry)
        self.save()
        return entry

    def forget(self, memory_id: str) -> bool:
        """
        遗忘一条记忆（删除）

        【参数说明】
        - memory_id: str - 要删除的记忆 ID

        【返回值】
        - bool: 是否成功删除（True=找到并删除，False=未找到）

        【自动保存】
        删除后自动调用 save() 持久化。
        """
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.id != memory_id]
        deleted = len(self._entries) < before
        if deleted:
            self.save()
        return deleted

    def list_all(self) -> list[MemoryEntry]:
        """
        列出所有长期记忆

        【返回值】
        - list[MemoryEntry]: 所有记忆条目（按时间倒序，最新的在前）
        """
        return sorted(self._entries, key=lambda e: e.timestamp, reverse=True)

    def get(self, memory_id: str) -> MemoryEntry | None:
        """
        按 ID 获取单条记忆

        【参数说明】
        - memory_id: str - 记忆 ID

        【返回值】
        - MemoryEntry | None: 找到则返回，未找到返回 None
        """
        for entry in self._entries:
            if entry.id == memory_id:
                return entry
        return None

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    def search(self, query: str, *, top_k: int = 5) -> list[tuple[MemoryEntry, float]]:
        """
        关键词搜索记忆

        【搜索算法】
        简单的关键词匹配（类似 TF-IDF 的简化版）：
        1. 将查询分词（按空格分割）
        2. 对每条记忆，计算匹配分数
        3. 按分数降序排序，返回前 top_k 个

        【匹配分数计算】
        - content 中每匹配一个关键词：+2 分
        - tags 中每匹配一个关键词：+3 分（标签更精确）
        - type 完全匹配：+5 分
        - 最终分数归一化到 [0, 1]

        【参数说明】
        - query: str - 搜索查询
        - top_k: int - 返回前 K 个结果（默认 5）

        【返回值】
        - list[tuple[MemoryEntry, float]]: 记忆条目 + 匹配分数，按分数降序

        【与向量记忆的区别】
        本方法使用关键词匹配，不依赖 Embedding API。
        适合搜索明确的关键词（如"pytest"、"TypeScript"）。
        语义搜索（如"测试框架怎么选"）请用 VectorMemory。
        """
        if not self._entries:
            return []

        # 分词：按空格分割，转小写
        query_words = set(query.lower().split())
        if not query_words:
            return []

        scored: list[tuple[MemoryEntry, float]] = []
        max_score = 0.0

        for entry in self._entries:
            score = 0.0
            # 在 content 中匹配关键词
            content_lower = entry.content.lower()
            for word in query_words:
                if word in content_lower:
                    score += 2.0
            # 在 tags 中匹配关键词（权重更高）
            tags_lower = [t.lower() for t in entry.tags]
            for word in query_words:
                if word in tags_lower:
                    score += 3.0
            # type 完全匹配（权重最高）
            if query.lower() == entry.type.lower():
                score += 5.0

            if score > 0:
                scored.append((entry, score))
                if score > max_score:
                    max_score = score

        # 归一化分数到 [0, 1]
        if max_score > 0:
            scored = [(e, s / max_score) for e, s in scored]

        # 按分数降序排序，取前 top_k 个
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def count(self) -> int:
        """返回记忆总数"""
        return len(self._entries)

    def clear(self) -> None:
        """清空所有记忆（慎用！）"""
        self._entries.clear()
        self.save()
