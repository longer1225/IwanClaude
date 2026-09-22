"""
记忆系统测试模块

测试三层记忆架构：
1. LongTermMemory：长期记忆（关键词搜索 + JSONL 持久化）
2. VectorMemory：向量记忆（语义搜索 + 向量存储）
3. MemoryManager：统一记忆管理器（整合三层记忆）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from iwan_claude.core.memory.long_term import LongTermMemory, MemoryEntry
from iwan_claude.core.memory.manager import MemoryManager
from iwan_claude.core.memory.vector_memory import VectorMemory
from iwan_claude.core.rag.chunker import Chunk
from iwan_claude.core.rag.embedding import EmbeddingProvider
from iwan_claude.core.rag.vectorstore import MemoryVectorStore


# ======================================================================
# LongTermMemory 测试
# ======================================================================


class TestLongTermMemory:
    """测试长期记忆存储"""

    def test_add_and_get(self, tmp_path: Path) -> None:
        """测试添加和获取记忆"""
        memory = LongTermMemory(tmp_path / "memory.jsonl")
        memory.load()

        entry = memory.add("用户偏好用 pytest", type="preference", tags=["pytest", "testing"])

        assert entry.id is not None
        assert entry.content == "用户偏好用 pytest"
        assert entry.type == "preference"
        assert "pytest" in entry.tags

        # 通过 ID 获取
        retrieved = memory.get(entry.id)
        assert retrieved is not None
        assert retrieved.content == "用户偏好用 pytest"

    def test_forget(self, tmp_path: Path) -> None:
        """测试遗忘记忆"""
        memory = LongTermMemory(tmp_path / "memory.jsonl")
        memory.load()

        entry = memory.add("测试记忆", type="fact")
        assert memory.count() == 1

        # 遗忘
        deleted = memory.forget(entry.id)
        assert deleted is True
        assert memory.count() == 0

        # 再次遗忘（不存在）
        deleted = memory.forget(entry.id)
        assert deleted is False

    def test_search_by_keyword(self, tmp_path: Path) -> None:
        """测试关键词搜索"""
        memory = LongTermMemory(tmp_path / "memory.jsonl")
        memory.load()

        memory.add("用户喜欢用 TypeScript", type="preference", tags=["typescript"])
        memory.add("项目使用 LangGraph 框架", type="fact", tags=["langgraph"])
        memory.add("用户偏好 pytest 测试框架", type="preference", tags=["pytest"])

        # 搜索 "typescript"
        results = memory.search("typescript")
        assert len(results) > 0
        assert "TypeScript" in results[0][0].content

        # 搜索 "pytest"
        results = memory.search("pytest")
        assert len(results) > 0
        assert "pytest" in results[0][0].content

        # 搜索无结果
        results = memory.search("java")
        assert len(results) == 0

    def test_search_score_ordering(self, tmp_path: Path) -> None:
        """测试搜索结果按分数排序"""
        memory = LongTermMemory(tmp_path / "memory.jsonl")
        memory.load()

        # content 匹配 + tags 匹配（高分）
        memory.add("pytest pytest pytest", type="fact", tags=["pytest"])
        # 只有 content 匹配（中分）
        memory.add("pytest is good", type="fact", tags=[])
        # 只有 tags 匹配
        memory.add("something else", type="fact", tags=["pytest"])

        results = memory.search("pytest")
        assert len(results) == 3
        # 分数应该降序
        assert results[0][1] >= results[1][1] >= results[2][1]

    def test_persistence(self, tmp_path: Path) -> None:
        """测试持久化（保存后重新加载）"""
        path = tmp_path / "memory.jsonl"
        memory = LongTermMemory(path)
        memory.load()

        memory.add("记忆1", type="fact")
        memory.add("记忆2", type="preference", tags=["tag1"])

        assert memory.count() == 2

        # 创建新实例，从文件加载
        memory2 = LongTermMemory(path)
        memory2.load()

        assert memory2.count() == 2
        entries = memory2.list_all()
        contents = [e.content for e in entries]
        assert "记忆1" in contents
        assert "记忆2" in contents

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        """测试加载不存在的文件"""
        memory = LongTermMemory(tmp_path / "nonexistent.jsonl")
        memory.load()  # 不应抛异常
        assert memory.count() == 0

    def test_load_corrupted_file(self, tmp_path: Path) -> None:
        """测试加载损坏的文件（容错）"""
        path = tmp_path / "corrupted.jsonl"
        # 写入一行有效 + 一行无效
        path.write_text(
            json.dumps({"content": "valid", "type": "fact"}, ensure_ascii=False)
            + "\nINVALID JSON LINE\n",
            encoding="utf-8",
        )

        memory = LongTermMemory(path)
        memory.load()
        # 应该只加载有效的那行
        assert memory.count() == 1
        assert memory.list_all()[0].content == "valid"

    def test_list_all_sorted_by_time(self, tmp_path: Path) -> None:
        """测试 list_all 按时间倒序"""
        memory = LongTermMemory(tmp_path / "memory.jsonl")
        memory.load()

        e1 = memory.add("第一", type="fact")
        e2 = memory.add("第二", type="fact")

        entries = memory.list_all()
        # 最新的在前
        assert entries[0].content == "第二"
        assert entries[1].content == "第一"

    def test_clear(self, tmp_path: Path) -> None:
        """测试清空记忆"""
        memory = LongTermMemory(tmp_path / "memory.jsonl")
        memory.load()
        memory.add("记忆1", type="fact")
        memory.add("记忆2", type="fact")

        memory.clear()
        assert memory.count() == 0

    def test_empty_search(self, tmp_path: Path) -> None:
        """测试空记忆搜索"""
        memory = LongTermMemory(tmp_path / "memory.jsonl")
        memory.load()
        results = memory.search("anything")
        assert results == []

    def test_empty_query_search(self, tmp_path: Path) -> None:
        """测试空查询搜索"""
        memory = LongTermMemory(tmp_path / "memory.jsonl")
        memory.load()
        memory.add("记忆", type="fact")
        results = memory.search("")
        assert results == []


# ======================================================================
# VectorMemory 测试
# ======================================================================


class TestVectorMemory:
    """测试向量记忆存储"""

    def test_add_conversation(self, tmp_path: Path) -> None:
        """测试添加对话到向量记忆"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

        vm = VectorMemory(store, mock_embedder, str(tmp_path / "vm.json"))
        vm.load()

        chunk = asyncio.run(vm.add_conversation(
            "怎么配置 RAG？", "你可以在 config.py 中设置...", session_id="sess1",
        ))

        assert chunk is not None
        assert "怎么配置 RAG" in chunk.text
        assert vm.count() == 1

    def test_add_without_embedder(self, tmp_path: Path) -> None:
        """测试没有 embedder 时降级"""
        store = MemoryVectorStore()
        vm = VectorMemory(store, None, str(tmp_path / "vm.json"))

        chunk = asyncio.run(vm.add_conversation("hello", "world"))
        assert chunk is None
        assert vm.count() == 0

    def test_search(self, tmp_path: Path) -> None:
        """测试语义搜索"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)

        # 添加对话时的向量
        conv_vector = [0.9, 0.1, 0.0]
        # 搜索查询的向量
        query_vector = [0.85, 0.15, 0.0]

        mock_embedder.embed = AsyncMock(side_effect=[
            [conv_vector],   # add_conversation 时
            [query_vector],  # search 时
        ])

        vm = VectorMemory(store, mock_embedder, str(tmp_path / "vm.json"))
        vm.load()

        asyncio.run(vm.add_conversation("RAG 配置", "在 config.py 中", session_id="s1"))

        results = asyncio.run(vm.search("如何设置 RAG"))
        assert len(results) > 0
        assert "RAG" in results[0][0].text

    def test_search_without_embedder(self, tmp_path: Path) -> None:
        """测试没有 embedder 时搜索降级"""
        store = MemoryVectorStore()
        vm = VectorMemory(store, None)
        results = asyncio.run(vm.search("query"))
        assert results == []

    def test_delete_by_session(self, tmp_path: Path) -> None:
        """测试按会话删除"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.5, 0.5]])

        vm = VectorMemory(store, mock_embedder, str(tmp_path / "vm.json"))
        vm.load()

        asyncio.run(vm.add_conversation("msg1", "resp1", session_id="sess_a"))
        asyncio.run(vm.add_conversation("msg2", "resp2", session_id="sess_b"))

        assert vm.count() == 2

        deleted = asyncio.run(vm.delete_by_session("sess_a"))
        assert deleted == 1
        assert vm.count() == 1

    def test_persistence(self, tmp_path: Path) -> None:
        """测试持久化"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.5, 0.5]])

        path = str(tmp_path / "vm.json")
        vm = VectorMemory(store, mock_embedder, path)
        vm.load()

        asyncio.run(vm.add_conversation("hello", "world", session_id="s1"))

        # 新实例加载
        store2 = MemoryVectorStore()
        vm2 = VectorMemory(store2, mock_embedder, path)
        vm2.load()
        assert vm2.count() == 1


# ======================================================================
# MemoryManager 测试
# ======================================================================


class TestMemoryManager:
    """测试统一记忆管理器"""

    def _create_manager(self, tmp_path: Path) -> MemoryManager:
        """创建测试用的 MemoryManager"""
        lt = LongTermMemory(tmp_path / "lt.jsonl")
        lt.load()

        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.5, 0.5]])

        vm = VectorMemory(store, mock_embedder, str(tmp_path / "vm.json"))
        vm.load()

        return MemoryManager(lt, vm, project_context="Project: Test Project")

    def test_remember(self, tmp_path: Path) -> None:
        """测试存储长期记忆"""
        manager = self._create_manager(tmp_path)

        entry = manager.remember("用户偏好 pytest", type="preference", tags=["pytest"])
        assert entry.content == "用户偏好 pytest"
        assert manager.stats()["long_term"] == 1

    def test_remember_conversation(self, tmp_path: Path) -> None:
        """测试存储对话到向量记忆"""
        manager = self._create_manager(tmp_path)

        asyncio.run(manager.remember_conversation("hello", "world", session_id="s1"))
        assert manager.stats()["vector_memory"] == 1

    def test_recall_with_long_term(self, tmp_path: Path) -> None:
        """测试检索长期记忆"""
        manager = self._create_manager(tmp_path)
        manager.remember("用户偏好 pytest", type="preference", tags=["pytest"])

        result = asyncio.run(manager.recall("pytest"))
        assert "pytest" in result
        assert "Long-term Memory" in result

    def test_recall_with_vector_memory(self, tmp_path: Path) -> None:
        """测试检索向量记忆"""
        manager = self._create_manager(tmp_path)

        asyncio.run(manager.remember_conversation("RAG 配置", "在 config.py", session_id="s1"))

        result = asyncio.run(manager.recall("RAG"))
        # 应该包含相关对话
        assert "Relevant Conversations" in result or "RAG" in result

    def test_recall_empty(self, tmp_path: Path) -> None:
        """测试无记忆时检索返回空"""
        manager = self._create_manager(tmp_path)
        result = asyncio.run(manager.recall("anything"))
        assert result == ""

    def test_forget(self, tmp_path: Path) -> None:
        """测试遗忘记忆"""
        manager = self._create_manager(tmp_path)
        entry = manager.remember("测试记忆", type="fact")

        deleted = manager.forget(entry.id)
        assert deleted is True
        assert manager.stats()["long_term"] == 0

    def test_forget_session(self, tmp_path: Path) -> None:
        """测试遗忘会话的向量记忆"""
        manager = self._create_manager(tmp_path)
        asyncio.run(manager.remember_conversation("msg", "resp", session_id="sess1"))

        deleted = asyncio.run(manager.forget_session("sess1"))
        assert deleted == 1
        assert manager.stats()["vector_memory"] == 0

    def test_get_project_context(self, tmp_path: Path) -> None:
        """测试获取项目级记忆"""
        manager = self._create_manager(tmp_path)
        assert manager.get_project_context() == "Project: Test Project"

    def test_stats(self, tmp_path: Path) -> None:
        """测试统计信息"""
        manager = self._create_manager(tmp_path)
        manager.remember("记忆1", type="fact")
        manager.remember("记忆2", type="fact")
        asyncio.run(manager.remember_conversation("msg", "resp", session_id="s1"))

        stats = manager.stats()
        assert stats["long_term"] == 2
        assert stats["vector_memory"] == 1
        assert stats["project_context"] == 1

    def test_list_long_term(self, tmp_path: Path) -> None:
        """测试列出长期记忆"""
        manager = self._create_manager(tmp_path)
        manager.remember("记忆1", type="fact")
        manager.remember("记忆2", type="preference")

        entries = manager.list_long_term()
        assert len(entries) == 2

    def test_recall_combined(self, tmp_path: Path) -> None:
        """测试同时检索长期记忆和向量记忆"""
        manager = self._create_manager(tmp_path)

        # 添加长期记忆
        manager.remember("用户偏好 pytest", type="preference", tags=["pytest"])
        # 添加向量记忆
        asyncio.run(manager.remember_conversation(
            "pytest 怎么用", "用 pip install pytest", session_id="s1",
        ))

        result = asyncio.run(manager.recall("pytest"))
        # 应该同时包含长期记忆和向量记忆
        assert "pytest" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
