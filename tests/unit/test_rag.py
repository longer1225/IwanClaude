"""RAG 模块测试"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from iwan_claude.core.rag.chunker import Chunk, DocumentChunker
from iwan_claude.core.rag.embedding import EmbeddingProvider, get_embedding_provider
from iwan_claude.core.rag.index import IndexResult, IndexStatus, KnowledgeIndexManager
from iwan_claude.core.rag.vectorstore import MemoryVectorStore, VectorStore


class TestDocumentChunker:
    def test_chunk_python_file(self, tmp_path: Path) -> None:
        """测试 Python 文件分块 - AST 解析"""
        python_code = """def foo():
    \"\"\"docstring\"\"\"
    return 1

async def bar():
    return 2

class Baz:
    def method(self):
        return 3
"""
        python_file = tmp_path / "test.py"
        python_file.write_text(python_code)

        chunker = DocumentChunker()
        chunks = chunker.chunk_file(python_file)

        assert len(chunks) == 4  # foo, bar, Baz, Baz.method
        assert chunks[0].symbol == "def foo"
        assert chunks[1].symbol == "async def bar"
        assert chunks[2].symbol == "class Baz"
        assert chunks[3].symbol == "class Baz.def method"

    def test_chunk_markdown_file(self, tmp_path: Path) -> None:
        """测试 Markdown 文件分块 - 标题层级"""
        markdown_content = """# Title

## Section 1

Content 1

### Subsection 1.1

Content 1.1

## Section 2

Content 2
"""
        md_file = tmp_path / "test.md"
        md_file.write_text(markdown_content)

        chunker = DocumentChunker()
        chunks = chunker.chunk_file(md_file)

        assert len(chunks) == 4  # Title + Section 1 + Subsection 1.1 + Section 2
        assert chunks[0].section_path == ["Title"]
        assert chunks[1].section_path == ["Title", "Section 1"]
        assert chunks[2].section_path == ["Title", "Section 1", "Subsection 1.1"]
        assert chunks[3].section_path == ["Title", "Section 2"]

    def test_chunk_json_file(self, tmp_path: Path) -> None:
        """测试 JSON 文件分块"""
        json_content = '{"name": "test", "value": 42, "nested": {"key": "value"}}'
        json_file = tmp_path / "test.json"
        json_file.write_text(json_content)

        chunker = DocumentChunker()
        chunks = chunker.chunk_file(json_file)

        assert len(chunks) >= 3  # name, value, nested.key

    def test_chunk_plaintext_sliding_window(self, tmp_path: Path) -> None:
        """测试纯文本滑动窗口分块"""
        # 多行文本，每行 50 个字符，共 20 行 = 1000 字符
        plaintext_content = "\n".join(["x" * 50 for _ in range(20)])
        txt_file = tmp_path / "test.txt"
        txt_file.write_text(plaintext_content)

        chunker = DocumentChunker(chunk_size=512, chunk_overlap=64)
        chunks = chunker.chunk_file(txt_file)

        assert len(chunks) >= 2  # 至少分成 2 块


class TestMemoryVectorStore:
    def test_add_and_search(self) -> None:
        """测试添加和检索"""
        store = MemoryVectorStore()
        chunks = [
            Chunk(text="hello world", source_path="test.txt", start_line=1, end_line=1),
            Chunk(text="goodbye world", source_path="test.txt", start_line=2, end_line=2),
        ]
        vectors = [[0.1, 0.2, 0.3], [0.7, 0.8, 0.9]]

        import asyncio
        asyncio.run(store.add(chunks, vectors))

        results = asyncio.run(store.search([0.1, 0.2, 0.3], top_k=1))
        assert len(results) == 1
        assert results[0][0].text == "hello world"
        assert results[0][1] >= 0.9  # 余弦相似度应该很高

    def test_delete_by_source(self) -> None:
        """测试按来源删除"""
        store = MemoryVectorStore()
        chunks = [
            Chunk(text="chunk1", source_path="file1.txt", start_line=1, end_line=1),
            Chunk(text="chunk2", source_path="file2.txt", start_line=1, end_line=1),
        ]
        vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

        import asyncio
        asyncio.run(store.add(chunks, vectors))
        asyncio.run(store.delete_by_source("file1.txt"))

        results = asyncio.run(store.search([0.1, 0.2, 0.3], top_k=5))
        assert len(results) == 1  # 只剩下 file2.txt 的 chunk

    def test_save_and_load(self, tmp_path: Path) -> None:
        """测试持久化和加载"""
        store = MemoryVectorStore()
        chunks = [Chunk(text="test", source_path="test.txt", start_line=1, end_line=1)]
        vectors = [[0.1, 0.2, 0.3]]

        import asyncio
        asyncio.run(store.add(chunks, vectors))

        store.save(tmp_path)

        # 创建新的存储实例并加载
        new_store = MemoryVectorStore()
        new_store.load(tmp_path)

        results = asyncio.run(new_store.search([0.1, 0.2, 0.3], top_k=1))
        assert len(results) == 1
        assert results[0][0].text == "test"


class TestEmbeddingProvider:
    @pytest.mark.asyncio
    async def test_embed_batch(self) -> None:
        """测试批量嵌入"""
        with patch.dict("os.environ", {"TEST_API_KEY": "test-key"}):
            # 创建 mock HTTP 客户端
            mock_http = AsyncMock(spec=httpx.AsyncClient)
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": [
                    {"embedding": [0.1, 0.2, 0.3]},
                    {"embedding": [0.4, 0.5, 0.6]},
                ]
            }
            mock_response.raise_for_status = MagicMock()
            
            # 返回一个协程
            async def mock_post(*args, **kwargs):
                return mock_response
            mock_http.post.side_effect = mock_post

            provider = EmbeddingProvider(
                model="test-model",
                base_url="https://api.test.com/v1",
                api_key_env="TEST_API_KEY",
                http_client=mock_http,
            )
            vectors = await provider.embed(["hello", "world"])

            assert len(vectors) == 2
            assert vectors[0] == [0.1, 0.2, 0.3]
            assert vectors[1] == [0.4, 0.5, 0.6]


class TestKnowledgeIndexManager:
    @pytest.mark.asyncio
    async def test_index_file(self, tmp_path: Path) -> None:
        """测试索引单个文件"""
        # 创建测试文件
        python_file = tmp_path / "test.py"
        python_file.write_text("def foo():\n    return 1")

        # 创建 mock 组件
        mock_store = MagicMock(spec=VectorStore)
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed.return_value = [[0.1, 0.2, 0.3]]

        manager = KnowledgeIndexManager(
            vector_store=mock_store,
            embedding_provider=mock_embedder,
            chunker=DocumentChunker(),
            index_path=str(tmp_path / "rag_index"),
        )

        await manager.index_file(python_file)

        # 验证嵌入和存储操作
        mock_embedder.embed.assert_called_once()
        mock_store.delete_by_source.assert_called_once()
        mock_store.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_search(self) -> None:
        """测试语义检索"""
        mock_store = MagicMock(spec=VectorStore)
        mock_store.search.return_value = [
            (Chunk(text="result", source_path="test.txt", start_line=1, end_line=1), 0.95),
        ]
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed.return_value = [[0.1, 0.2, 0.3]]

        manager = KnowledgeIndexManager(
            vector_store=mock_store,
            embedding_provider=mock_embedder,
            chunker=DocumentChunker(),
            index_path=str(Path(".") / "rag_index"),
        )

        results = await manager.search("test query")

        assert len(results) == 1
        assert results[0][0].text == "result"
        assert results[0][1] == 0.95

    def test_status(self, tmp_path: Path) -> None:
        """测试索引状态"""
        mock_store = MagicMock(spec=VectorStore)
        mock_embedder = MagicMock(spec=EmbeddingProvider)

        manager = KnowledgeIndexManager(
            vector_store=mock_store,
            embedding_provider=mock_embedder,
            chunker=DocumentChunker(),
            index_path=str(tmp_path / "rag_index"),
        )

        status = manager.status()
        assert isinstance(status, IndexStatus)
        assert status.total_sources == 0

    def test_rebuild_index(self, tmp_path: Path) -> None:
        """测试重建索引"""
        # 使用真实的 MemoryVectorStore 而不是 mock
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)

        manager = KnowledgeIndexManager(
            vector_store=store,
            embedding_provider=mock_embedder,
            chunker=DocumentChunker(),
            index_path=str(tmp_path / "rag_index"),
        )

        # 添加一些元数据
        manager._meta["sources"]["test.py"] = {"mtime": 1234567890, "chunk_count": 1}

        manager.rebuild_index()

        assert len(manager._meta["sources"]) == 0
        assert isinstance(manager._vector_store, MemoryVectorStore)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])