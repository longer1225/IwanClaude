"""RAG 模块测试"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from iwan_claude.core.rag.adaptive import AdaptiveRetriever, RetrievalResult
from iwan_claude.core.rag.chunker import Chunk, DocumentChunker
from iwan_claude.core.rag.embedding import EmbeddingProvider
from iwan_claude.core.rag.index import IndexStatus, KnowledgeIndexManager
from iwan_claude.core.rag.llm_client import LLMClient
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


class TestChunkParentChild:
    """测试分块器的 Parent-Child 父子关系"""

    def test_python_ast_parent_child(self, tmp_path: Path) -> None:
        """测试 Python AST 分块：方法的 parent_id 指向所属类"""
        python_code = """class MyClass:
    def method_a(self):
        return 1

    def method_b(self):
        return 2

def top_level_func():
    return 3
"""
        python_file = tmp_path / "test.py"
        python_file.write_text(python_code)

        chunker = DocumentChunker()
        chunks = chunker.chunk_file(python_file)

        # 4 个 chunk: MyClass, method_a, method_b, top_level_func
        assert len(chunks) == 4

        class_chunk = next(c for c in chunks if c.symbol == "class MyClass")
        method_a = next(c for c in chunks if c.symbol == "class MyClass.def method_a")
        method_b = next(c for c in chunks if c.symbol == "class MyClass.def method_b")
        top_func = next(c for c in chunks if c.symbol == "def top_level_func")

        # 类的 parent_id 应该是 None（顶级）
        assert class_chunk.parent_id is None
        # 方法的 parent_id 应该指向类
        assert method_a.parent_id == class_chunk.chunk_id
        assert method_b.parent_id == class_chunk.chunk_id
        # 顶级函数的 parent_id 应该是 None
        assert top_func.parent_id is None

    def test_markdown_parent_child(self, tmp_path: Path) -> None:
        """测试 Markdown 分块：子章节的 parent_id 指向父章节"""
        markdown_content = """# Title

Intro text.

## Section 1

Content 1.

### Subsection 1.1

Sub content.
"""
        md_file = tmp_path / "test.md"
        md_file.write_text(markdown_content)

        chunker = DocumentChunker()
        chunks = chunker.chunk_file(md_file)

        # 3 个 chunk: # Title, ## Section 1, ### Subsection 1.1
        assert len(chunks) == 3
        # 顶级章节 parent_id 为 None
        assert chunks[0].parent_id is None
        # 子章节 parent_id 指向父章节
        assert chunks[1].parent_id == chunks[0].chunk_id
        assert chunks[2].parent_id == chunks[1].chunk_id

    def test_chunk_context_field_default_none(self) -> None:
        """测试 Chunk 的 context 和 parent_id 字段默认为 None"""
        chunk = Chunk(text="test", source_path="test.py", start_line=1, end_line=1)
        assert chunk.context is None
        assert chunk.parent_id is None


class TestLLMClient:
    """测试轻量 LLM 客户端"""

    def test_complete_success(self) -> None:
        """测试 LLM 调用成功"""
        os.environ["DEEPSEEK_API_KEY"] = "test-key"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello world"}}]
        }
        mock_response.raise_for_status.return_value = None

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = mock_response

        client = LLMClient(
            model="test-model", base_url="https://api.test.com/v1",
            http_client=mock_http,
        )
        result = asyncio.run(client.complete([{"role": "user", "content": "Hi"}]))
        assert result == "Hello world"

    def test_complete_api_error_returns_empty(self) -> None:
        """测试 API 错误时返回空字符串（降级处理）"""
        os.environ["DEEPSEEK_API_KEY"] = "test-key"

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.side_effect = httpx.HTTPError("API error")

        client = LLMClient(
            model="test-model", base_url="https://api.test.com/v1",
            http_client=mock_http,
        )
        result = asyncio.run(client.complete([{"role": "user", "content": "Hi"}]))
        assert result == ""

    def test_rewrite_query(self) -> None:
        """测试查询重写生成多个变体"""
        os.environ["DEEPSEEK_API_KEY"] = "test-key"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "how to setup config\nconfig method\nsetting up configuration"}}]
        }
        mock_response.raise_for_status.return_value = None

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = mock_response

        client = LLMClient(
            model="test-model", base_url="https://api.test.com/v1",
            http_client=mock_http,
        )
        queries = asyncio.run(client.rewrite_query("how to configure"))
        # 原始查询 + 3 个变体
        assert queries[0] == "how to configure"
        assert len(queries) == 4

    def test_rewrite_query_dedup(self) -> None:
        """测试查询重写去重"""
        os.environ["DEEPSEEK_API_KEY"] = "test-key"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "how to configure\nhow to configure\nduplicate"}}]
        }
        mock_response.raise_for_status.return_value = None

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = mock_response

        client = LLMClient(
            model="test-model", base_url="https://api.test.com/v1",
            http_client=mock_http,
        )
        queries = asyncio.run(client.rewrite_query("how to configure"))
        # 去重后：原始 + 1 个非重复
        assert queries[0] == "how to configure"
        assert len(queries) == 2


class TestContextualRetrieval:
    """测试 Contextual Retrieval 上下文增强"""

    def test_index_file_with_context(self, tmp_path: Path) -> None:
        """测试索引时生成 context"""
        python_code = "def foo():\n    return 1\n"
        python_file = tmp_path / "test.py"
        python_file.write_text(python_code)

        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.generate_context = AsyncMock(return_value="This is foo function")

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )
        asyncio.run(manager.index_file(python_file))

        chunks = store._chunks
        assert len(chunks) == 1
        assert chunks[0].context == "This is foo function"
        # embedding 时拼接了 context
        called_texts = mock_embedder.embed.call_args[0][0]
        assert "This is foo function" in called_texts[0]

    def test_index_file_without_llm(self, tmp_path: Path) -> None:
        """测试无 LLM 时不生成 context（降级）"""
        python_code = "def foo():\n    return 1\n"
        python_file = tmp_path / "test.py"
        python_file.write_text(python_code)

        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
        )
        asyncio.run(manager.index_file(python_file))

        chunks = store._chunks
        assert len(chunks) == 1
        assert chunks[0].context is None


class TestParentChildRetrieval:
    """测试 Parent-Child 检索上下文增强"""

    def test_get_by_ids(self) -> None:
        """测试 VectorStore.get_by_ids"""
        store = MemoryVectorStore()
        chunk1 = Chunk(text="a", source_path="a.py", start_line=1, end_line=1)
        chunk2 = Chunk(text="b", source_path="b.py", start_line=1, end_line=1)
        asyncio.run(store.add([chunk1, chunk2], [[0.1], [0.2]]))

        result = asyncio.run(store.get_by_ids([chunk1.chunk_id, chunk2.chunk_id, "nonexistent"]))
        assert len(result) == 2
        assert result[0].text == "a"
        assert result[1].text == "b"

    def test_parent_context_enrichment(self, tmp_path: Path) -> None:
        """测试检索结果附带父级上下文"""
        python_code = """class MyClass:
    def search(self, query):
        return query
"""
        python_file = tmp_path / "test.py"
        python_file.write_text(python_code)

        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        # 类和方法的向量
        mock_embedder.embed = AsyncMock(return_value=[[0.9, 0.1], [0.1, 0.9]])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
        )
        asyncio.run(manager.index_file(python_file))

        # 模拟查询向量和方法的向量相似
        mock_embedder.embed = AsyncMock(return_value=[[0.1, 0.9]])
        results = asyncio.run(manager.hybrid_search("search method", top_k=5))

        assert len(results) > 0
        # 找到有 parent_id 的 chunk，检查 parent_context
        found_parent = False
        for chunk, _ in results:
            if chunk.parent_id:
                assert "parent_context" in chunk.metadata
                assert "class MyClass" in chunk.metadata["parent_context"]
                found_parent = True
        assert found_parent, "应该有至少一个 chunk 包含 parent_context"


class TestLLMQueryRewrite:
    """测试 LLM 查询重写"""

    def test_hybrid_search_with_llm_rewrite(self, tmp_path: Path) -> None:
        """测试 hybrid_search 用 LLM 重写查询"""
        python_code = 'def configure():\n    """Configure settings"""\n    pass\n'
        python_file = tmp_path / "test.py"
        python_file.write_text(python_code)

        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.5, 0.5]])

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.rewrite_query = AsyncMock(return_value=[
            "how to configure", "how to setup", "configuration method",
        ])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )
        asyncio.run(manager.index_file(python_file))

        mock_embedder.embed = AsyncMock(return_value=[[0.5, 0.5]])
        asyncio.run(manager.hybrid_search("how to configure", top_k=5))

        # LLM rewrite_query 应该被调用
        mock_llm.rewrite_query.assert_called_once_with("how to configure")

    def test_hybrid_search_without_llm_fallback(self, tmp_path: Path) -> None:
        """测试无 LLM 时降级为硬编码同义词"""
        python_code = "def configure():\n    pass\n"
        python_file = tmp_path / "test.py"
        python_file.write_text(python_code)

        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.5, 0.5]])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
        )
        asyncio.run(manager.index_file(python_file))

        mock_embedder.embed = AsyncMock(return_value=[[0.5, 0.5]])
        results = asyncio.run(manager.hybrid_search("how to config", top_k=5))
        assert isinstance(results, list)


class TestSearchByText:
    """测试关键词搜索（grep 策略）"""

    def test_search_by_text(self) -> None:
        """测试关键词搜索匹配"""
        store = MemoryVectorStore()
        chunk1 = Chunk(text="class AuthService:\n    def login(self):", source_path="a.py", start_line=1, end_line=2)
        chunk2 = Chunk(text="def configure():\n    pass", source_path="b.py", start_line=1, end_line=2)
        asyncio.run(store.add([chunk1, chunk2], [[0.1], [0.2]]))

        results = asyncio.run(store.search_by_text("AuthService", top_k=5))
        assert len(results) == 1
        assert "AuthService" in results[0][0].text

    def test_search_by_text_no_match(self) -> None:
        """测试关键词搜索无匹配"""
        store = MemoryVectorStore()
        chunk = Chunk(text="hello world", source_path="a.py", start_line=1, end_line=1)
        asyncio.run(store.add([chunk], [[0.1]]))

        results = asyncio.run(store.search_by_text("nonexistent", top_k=5))
        assert len(results) == 0


class TestAdaptiveRetriever:
    """测试自适应检索器"""

    def test_classify_direct(self, tmp_path: Path) -> None:
        """测试简单问题路由到 direct（不检索）"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.1]])

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="direct")

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )

        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)
        result = asyncio.run(retriever.retrieve("what is 1+1"))

        assert result.strategy == "direct"
        assert result.chunks == []

    def test_classify_grep(self, tmp_path: Path) -> None:
        """测试精确查找路由到 grep"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.1]])

        # 先索引一些数据
        chunk = Chunk(text="class AuthService:\n    pass", source_path="auth.py", start_line=1, end_line=2)
        asyncio.run(store.add([chunk], [[0.5]]))

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="grep")

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )

        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)
        result = asyncio.run(retriever.retrieve("find AuthService"))

        assert result.strategy == "grep"
        assert len(result.chunks) > 0
        assert "AuthService" in result.chunks[0][0].text

    def test_classify_rag(self, tmp_path: Path) -> None:
        """测试语义问题路由到 rag"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.5, 0.5]])

        chunk = Chunk(text="def configure():\n    pass", source_path="config.py", start_line=1, end_line=2)
        asyncio.run(store.add([chunk], [[0.5, 0.5]]))

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="rag")
        mock_llm.rewrite_query = AsyncMock(return_value=["how to configure"])
        # rerank 返回传入的 chunks（模拟不改变顺序）
        mock_llm.rerank = AsyncMock(side_effect=lambda q, c, **kw: c[:kw.get("top_k", 5)])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )

        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)
        result = asyncio.run(retriever.retrieve("how to configure settings"))

        assert result.strategy == "rag"
        assert result.rewritten is True

    def test_fallback_without_llm(self, tmp_path: Path) -> None:
        """测试无 LLM 时降级为 RAG"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.5]])

        chunk = Chunk(text="def foo():\n    pass", source_path="a.py", start_line=1, end_line=2)
        asyncio.run(store.add([chunk], [[0.5]]))

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
        )

        retriever = AdaptiveRetriever(manager, llm_client=None)
        result = asyncio.run(retriever.retrieve("anything"))

        assert result.strategy == "rag"
        assert result.rewritten is False

    def test_fallback_on_unknown_classification(self, tmp_path: Path) -> None:
        """测试 LLM 返回未知类型时降级为 RAG"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.5]])

        chunk = Chunk(text="def foo():\n    pass", source_path="a.py", start_line=1, end_line=2)
        asyncio.run(store.add([chunk], [[0.5]]))

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="unknown_type")
        mock_llm.rewrite_query = AsyncMock(return_value=["query"])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )

        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)
        result = asyncio.run(retriever.retrieve("query"))

        assert result.strategy == "rag"


class TestCRAG:
    """测试 CRAG 修正型 RAG"""

    def test_evaluate_quality_correct(self) -> None:
        """测试质量评估：高分 → correct"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=".",
        )
        retriever = AdaptiveRetriever(manager)

        chunk = Chunk(text="test", source_path="a.py", start_line=1, end_line=1)
        quality = retriever._evaluate_quality([(chunk, 0.8)])
        assert quality == "correct"

    def test_evaluate_quality_ambiguous(self) -> None:
        """测试质量评估：中分 → ambiguous"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=".",
        )
        retriever = AdaptiveRetriever(manager)

        chunk = Chunk(text="test", source_path="a.py", start_line=1, end_line=1)
        quality = retriever._evaluate_quality([(chunk, 0.4)])
        assert quality == "ambiguous"

    def test_evaluate_quality_incorrect(self) -> None:
        """测试质量评估：低分 → incorrect"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=".",
        )
        retriever = AdaptiveRetriever(manager)

        chunk = Chunk(text="test", source_path="a.py", start_line=1, end_line=1)
        quality = retriever._evaluate_quality([(chunk, 0.1)])
        assert quality == "incorrect"

    def test_evaluate_quality_empty(self) -> None:
        """测试质量评估：空结果 → incorrect"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=".",
        )
        retriever = AdaptiveRetriever(manager)

        quality = retriever._evaluate_quality([])
        assert quality == "incorrect"

    def test_crag_correct_high_score(self, tmp_path: Path) -> None:
        """测试 CRAG：高分结果直接使用"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="rag")
        mock_llm.rewrite_query = AsyncMock(return_value=["query"])
        # rerank 返回传入的 chunks（模拟不改变顺序）
        mock_llm.rerank = AsyncMock(side_effect=lambda q, c, **kw: c[:kw.get("top_k", 5)])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )

        # mock hybrid_search 返回高分结果
        chunk = Chunk(text="relevant code", source_path="a.py", start_line=1, end_line=1)
        manager.hybrid_search = AsyncMock(return_value=[(chunk, 0.8)])

        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)
        result = asyncio.run(retriever.retrieve("query"))

        assert result.strategy == "rag"
        assert result.quality == "correct"
        assert result.reranked is True

    def test_crag_ambiguous_rewrite(self, tmp_path: Path) -> None:
        """测试 CRAG：中等分数触发改写查询"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="rag")
        # 改写查询返回多个变体
        mock_llm.rewrite_query = AsyncMock(return_value=[
            "original", "rewritten1", "rewritten2"
        ])
        # rerank 返回传入的 chunks（模拟不改变顺序）
        mock_llm.rerank = AsyncMock(side_effect=lambda q, c, **kw: c[:kw.get("top_k", 5)])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )

        # mock hybrid_search：第一次中等分，改写后更高分
        chunk1 = Chunk(text="code1", source_path="a.py", start_line=1, end_line=1)
        chunk2 = Chunk(text="code2", source_path="b.py", start_line=1, end_line=1)
        manager.hybrid_search = AsyncMock(side_effect=[
            [(chunk1, 0.4)],   # 原始查询：中等分
            [(chunk2, 0.7)],   # 改写1：高分
            [(chunk1, 0.3)],   # 改写2：低分
        ])

        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)
        result = asyncio.run(retriever.retrieve("query"))

        assert result.strategy == "crag_rewrite"
        assert result.chunks[0][0].text == "code2"  # 取了最高分的结果
        assert result.rewritten is True
        assert result.reranked is True

    def test_crag_incorrect_fallback_grep(self, tmp_path: Path) -> None:
        """测试 CRAG：低分回退到 grep"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)

        # 先添加一些 chunk 到 store，供 grep 搜索
        grep_chunk = Chunk(text="class AuthService", source_path="auth.py", start_line=1, end_line=1)
        asyncio.run(store.add([grep_chunk], [[0.5]]))

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="rag")
        mock_llm.rewrite_query = AsyncMock(return_value=["query"])
        # rerank 返回传入的 chunks（模拟不改变顺序）
        mock_llm.rerank = AsyncMock(side_effect=lambda q, c, **kw: c[:kw.get("top_k", 5)])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )

        # mock hybrid_search 返回低分结果
        rag_chunk = Chunk(text="unrelated", source_path="c.py", start_line=1, end_line=1)
        manager.hybrid_search = AsyncMock(return_value=[(rag_chunk, 0.1)])

        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)
        result = asyncio.run(retriever.retrieve("AuthService"))

        assert result.strategy == "crag_fallback_grep"
        assert result.quality == "incorrect"
        assert "AuthService" in result.chunks[0][0].text  # grep 找到了


class TestReranking:
    """测试 LLM Reranking 重排序"""

    def test_rerank_success(self) -> None:
        """测试 LLM rerank 正常重排序"""
        os.environ["DEEPSEEK_API_KEY"] = "test-key"

        # mock LLM 返回分数：chunk2 高分，chunk1 低分
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "3\n9"}}]
        }
        mock_response.raise_for_status.return_value = None

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = mock_response

        client = LLMClient(
            model="test-model", base_url="https://api.test.com/v1",
            http_client=mock_http,
        )

        chunk1 = Chunk(text="code1", source_path="a.py", start_line=1, end_line=1)
        chunk2 = Chunk(text="code2", source_path="b.py", start_line=1, end_line=1)
        chunks = [(chunk1, 0.9), (chunk2, 0.3)]

        reranked = asyncio.run(client.rerank("query", chunks, top_k=2))

        # chunk2 分数 0.9 应该排在前面
        assert reranked[0][0].text == "code2"
        assert reranked[1][0].text == "code1"

    def test_rerank_api_error_returns_original(self) -> None:
        """测试 rerank API 失败时返回原始顺序"""
        os.environ["DEEPSEEK_API_KEY"] = "test-key"

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.side_effect = httpx.HTTPError("API error")

        client = LLMClient(
            model="test-model", base_url="https://api.test.com/v1",
            http_client=mock_http,
        )

        chunk1 = Chunk(text="code1", source_path="a.py", start_line=1, end_line=1)
        chunk2 = Chunk(text="code2", source_path="b.py", start_line=1, end_line=1)
        chunks = [(chunk1, 0.9), (chunk2, 0.3)]

        reranked = asyncio.run(client.rerank("query", chunks, top_k=2))

        # 失败时返回原始顺序的前 top_k 个
        assert len(reranked) == 2
        assert reranked[0][0].text == "code1"

    def test_rerank_empty_chunks(self) -> None:
        """测试 rerank 空结果"""
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        client = LLMClient(
            model="test-model", base_url="https://api.test.com/v1",
            http_client=mock_http,
        )
        reranked = asyncio.run(client.rerank("query", [], top_k=5))
        assert reranked == []

    def test_rerank_mismatched_scores(self) -> None:
        """测试 rerank 分数数量不匹配时返回原始顺序"""
        os.environ["DEEPSEEK_API_KEY"] = "test-key"

        mock_response = MagicMock()
        # 只返回 1 个分数，但有 2 个 chunk
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "5"}}]
        }
        mock_response.raise_for_status.return_value = None

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = mock_response

        client = LLMClient(
            model="test-model", base_url="https://api.test.com/v1",
            http_client=mock_http,
        )

        chunk1 = Chunk(text="code1", source_path="a.py", start_line=1, end_line=1)
        chunk2 = Chunk(text="code2", source_path="b.py", start_line=1, end_line=1)
        chunks = [(chunk1, 0.9), (chunk2, 0.3)]

        reranked = asyncio.run(client.rerank("query", chunks, top_k=2))
        # 分数不匹配，返回原始顺序
        assert reranked[0][0].text == "code1"

    def test_reranking_in_retrieve(self, tmp_path: Path) -> None:
        """测试 retrieve 中集成了 reranking"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="rag")
        mock_llm.rewrite_query = AsyncMock(return_value=["query"])
        # rerank 被调用时返回 reordered 结果
        chunk_a = Chunk(text="best_match", source_path="a.py", start_line=1, end_line=1)
        chunk_b = Chunk(text="other", source_path="b.py", start_line=1, end_line=1)
        mock_llm.rerank = AsyncMock(return_value=[(chunk_a, 0.95), (chunk_b, 0.3)])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )

        # mock hybrid_search 返回高分结果（correct 路径）
        orig_chunk = Chunk(text="original", source_path="c.py", start_line=1, end_line=1)
        manager.hybrid_search = AsyncMock(return_value=[(orig_chunk, 0.8)])

        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)
        result = asyncio.run(retriever.retrieve("query"))

        assert result.reranked is True
        # rerank 的结果应该被使用
        assert result.chunks[0][0].text == "best_match"


class TestSearchToolAdaptive:
    """测试 SearchKnowledgeTool 带 adaptive_retriever"""

    def test_search_with_adaptive_direct(self, tmp_path: Path) -> None:
        """测试自适应搜索工具：direct 策略"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="direct")

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )
        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)

        from iwan_claude.core.rag.tools import SearchKnowledgeTool
        tool = SearchKnowledgeTool(manager, retriever)
        result = asyncio.run(tool.invoke({"query": "what is 1+1"}))

        assert "direct" in result.content.lower()

    def test_search_with_adaptive_rag(self, tmp_path: Path) -> None:
        """测试自适应搜索工具：rag 策略"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="rag")
        mock_llm.rewrite_query = AsyncMock(return_value=["query"])
        mock_llm.rerank = AsyncMock(side_effect=lambda q, c, **kw: c[:kw.get("top_k", 5)])

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
            llm_client=mock_llm,
        )

        chunk = Chunk(text="relevant code", source_path="a.py", start_line=1, end_line=1)
        manager.hybrid_search = AsyncMock(return_value=[(chunk, 0.8)])

        retriever = AdaptiveRetriever(manager, llm_client=mock_llm)

        from iwan_claude.core.rag.tools import SearchKnowledgeTool
        tool = SearchKnowledgeTool(manager, retriever)
        result = asyncio.run(tool.invoke({"query": "how does it work"}))

        # 应该包含策略信息
        assert "Strategy" in result.content
        assert "relevant code" in result.content

    def test_search_without_adaptive_fallback(self, tmp_path: Path) -> None:
        """测试无 adaptive_retriever 时降级为原始搜索"""
        store = MemoryVectorStore()
        mock_embedder = MagicMock(spec=EmbeddingProvider)
        mock_embedder.embed = AsyncMock(return_value=[[0.5]])

        chunk = Chunk(text="test code", source_path="a.py", start_line=1, end_line=1)
        asyncio.run(store.add([chunk], [[0.5]]))

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=mock_embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "rag_index"),
        )

        from iwan_claude.core.rag.tools import SearchKnowledgeTool
        # 不传 adaptive_retriever
        tool = SearchKnowledgeTool(manager)
        result = asyncio.run(tool.invoke({"query": "test", "hybrid": True}))

        # 应该正常返回结果（原始逻辑）
        assert "test code" in result.content


class _FakeEmbedder:
    """不触网的 embedding 替身：给任意文本返回 4 维确定性向量，供纯存储层测试用"""

    # 功能：按文本内容派生稳定向量（同文本同向量，跨进程 hash 随机化不参与）
    # 设计：duck-type 而非继承 EmbeddingProvider——真构造器会校验 api_key_env，
    #       这些测试根本不关心 embedding 语义，只要维度和顺序正确
    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for t in texts:
            digest = sum((i + 1) * (ord(c) % 7) for i, c in enumerate(t))
            vectors.append([(digest % 97) / 97, len(t) / 100, 0.5, 0.25])
        return vectors


class TestRagReviewFixes202609:
    """2026-09 RAG 评审修复的回归测试：每锁一个实锤 bug 一条"""

    # 功能：index_directory(incremental=False) 必须正常完成而非抛 UnboundLocalError
    # 设计：mtime 变量旧实现只在 incremental 分支内赋值、分支外读取，False 路径
    #       首个文件必炸；这里跑最小真实目录树（真 store + 假 embedder），
    #       用结果 chunk 数断言"确实索引了"而不只是"没抛异常"
    def test_index_directory_full_scan_no_unbound_mtime(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("def foo():\n    return 1\n")
        store = MemoryVectorStore()
        embedder = _FakeEmbedder()
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        result = asyncio.run(manager.index_directory(str(tmp_path), incremental=False))
        assert result.added_chunks >= 1
        assert store.size() == result.added_chunks

    # 功能：exclude 的 ".git/**" 必须排除任意深度的 .git 子树（旧实现只挡一层）
    # 设计：Python 3.12 的 Path.match 不支持 ** 递归，".git/hooks/evil.py" 曾实测漏网；
    #       树里同时放 node_modules（多层）和顶层普通文件，断言只有 a.py 入库——
    #       漏一个就是漏一次付费 embedding 外发
    def test_exclude_patterns_any_depth(self, tmp_path: Path) -> None:
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        (tmp_path / ".git" / "hooks" / "evil.py").write_text("def evil():\n    pass\n")
        (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
        (tmp_path / "node_modules" / "pkg" / "dep.py").write_text("def dep():\n    pass\n")
        (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
        store = MemoryVectorStore()
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=_FakeEmbedder(),
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        asyncio.run(manager.index_directory(str(tmp_path)))
        sources = {Path(c.source_path).name for c in store._chunks}
        assert sources == {"a.py"}

    # 功能：search 的 filters 必须在截断 top_k 之前生效（旧实现过滤后置，带 filter 会少给结果）
    # 设计：造 6 条同分向量 chunk、5 条属于被排除文件、1 条属于目标文件且 top_k=3；
    #       后置过滤会返回 0 条（前 3 全是别的文件），前置过滤必须精确返回那 1 条
    def test_filters_applied_before_topk_cutoff(self) -> None:
        store = MemoryVectorStore()
        chunks = [
            Chunk(text=f"t{i}", source_path="b.py" if i else "a.py", start_line=1, end_line=1)
            for i in range(6)
        ]
        vectors = [[1.0, 0.0] for _ in chunks]
        asyncio.run(store.add(chunks, vectors))
        results = asyncio.run(store.search([1.0, 0.0], top_k=3, filters={"source_path": "a.py"}))
        assert len(results) == 1
        assert results[0][0].source_path == "a.py"

    # 功能：add() 在 chunks 与 vectors 长度不匹配时必须 raise（旧实现 zip 静默截断）
    # 设计：错位入库是"检索在错误数据上打分且无从排查"级事故；
    #       用 size() 断言异常路径没有留下半成品数据
    def test_add_length_mismatch_raises(self) -> None:
        store = MemoryVectorStore()
        chunks = [
            Chunk(text="a", source_path="a.py", start_line=1, end_line=1),
            Chunk(text="b", source_path="a.py", start_line=2, end_line=2),
        ]
        with pytest.raises(ValueError):
            asyncio.run(store.add(chunks, [[1.0, 0.0]]))
        assert store.size() == 0

    # 功能：重复 chunk_id 再 add 必须覆盖而非追加（旧实现列表错位产生孤儿向量）
    # 设计：同 id 第二次带新向量 add，search 必须命中新向量；
    #       若走了 zip 错位路径，size 或相似度必然露馅
    def test_duplicate_chunk_id_overwrites(self) -> None:
        store = MemoryVectorStore()
        c1 = Chunk(chunk_id="dup", text="old", source_path="a.py", start_line=1, end_line=1)
        c2 = Chunk(chunk_id="dup", text="new", source_path="a.py", start_line=1, end_line=1)
        asyncio.run(store.add([c1], [[1.0, 0.0]]))
        asyncio.run(store.add([c2], [[0.0, 1.0]]))
        assert store.size() == 1
        top = asyncio.run(store.search([0.0, 1.0], top_k=1))
        assert top[0][0].text == "new"

    # 功能：_embed_batch 必须按响应 item["index"] 排序回填，乱序响应不能错位
    # 设计：OpenAI 协议不保证 data[] 顺序（兼容端点确实会乱序）；
    #       构造 index=1 在前的响应，断言请求 texts[0] 拿到 index=0 的向量
    def test_embed_batch_orders_by_index_field(self) -> None:
        with patch.dict("os.environ", {"TEST_API_KEY": "test-key"}):
            mock_http = AsyncMock(spec=httpx.AsyncClient)
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": [
                    {"index": 1, "embedding": [0.7, 0.8]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            }
            mock_response.raise_for_status = MagicMock()

            async def mock_post(*args, **kwargs):
                return mock_response
            mock_http.post.side_effect = mock_post

            provider = EmbeddingProvider(
                model="test-model", base_url="https://api.test.com/v1",
                api_key_env="TEST_API_KEY", http_client=mock_http,
            )
            vectors = asyncio.run(provider.embed(["hello", "world"]))
            assert vectors[0] == [0.1, 0.2]
            assert vectors[1] == [0.7, 0.8]

    # 功能：_rewrite_query 必须按词边界命中，"profile" 不得触发 file 的同义词变体
    # 设计：子串包含旧实现会让 default→def、profile→file 误改写，
    #       每个误变体都是一次真实 embedding 调用；断言变体集合恰好为空（除原查询外）
    def test_rewrite_query_word_boundary(self, tmp_path: Path) -> None:
        manager = KnowledgeIndexManager(
            vector_store=MagicMock(spec=VectorStore),
            embedding_provider=MagicMock(spec=EmbeddingProvider),
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        queries = manager._rewrite_query("user profile default value")
        assert queries == ["user profile default value"]
        # 整词命中时仍要正常扩展
        queries2 = manager._rewrite_query("configure the file")
        assert len(queries2) > 1

    # 功能：index_directory 的 meta key 必须是绝对路径，remove_file 传相对路径也能清掉
    # 设计：旧实现写入用相对 key、remove 查绝对 str(path)，两把钥匙永不相交，
    #       删除文件的 meta 幽灵记录永远残留；这里先相对路径索引、再相对路径删除，
    #       断言 sources 归零（两端各自 resolve 后落在同一坐标空间）
    def test_meta_key_absolute_paths_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("def foo():\n    return 1\n")
        store = MemoryVectorStore()
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=_FakeEmbedder(),
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        asyncio.run(manager.index_directory(".", include=["**/*.py"]))
        assert all(Path(k).is_absolute() for k in manager._meta["sources"])
        asyncio.run(manager.remove_file(Path("a.py")))
        assert manager._meta["sources"] == {}

    # 功能：空文件重新索引必须清掉旧向量（旧实现 return 太早留下陈旧 chunk）
    # 设计：先索引有内容的文件，截空再索引，store.size()==0 才算"文件清空 = 检索清空"
    def test_index_file_truncated_removes_stale(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("def foo():\n    return 1\n")
        store = MemoryVectorStore()
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=_FakeEmbedder(),
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        count = asyncio.run(manager.index_file(f))
        assert count >= 1
        f.write_text("")
        assert asyncio.run(manager.index_file(f)) == 0
        assert store.size() == 0

    # 功能：status().total_chunks 必须反映向量存储真实条数（旧实现恒 0）
    # 设计：add 两条后查 status，锁死"装饰性字段"退化路径
    def test_status_reports_real_chunk_count(self, tmp_path: Path) -> None:
        store = MemoryVectorStore()
        chunks = [
            Chunk(text="x", source_path="a.py", start_line=1, end_line=1),
            Chunk(text="y", source_path="a.py", start_line=2, end_line=2),
        ]
        asyncio.run(store.add(chunks, [[1.0, 0.0], [0.0, 1.0]]))
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=_FakeEmbedder(),
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        assert manager.status().total_chunks == 2

    # 功能：hybrid_search 的关键词腿必须能引入语义腿漏掉的新候选，并携带 semantic_score
    # 设计：语义 mock 只返回 chunk A（真 store 下 top_k*2 的宽切片会把所有 chunk
    #       都捞进语义池，关键词腿"独立引入"就无从区分新旧实现）；全库
    #       search_by_text 命中含 "needle42" 的 chunk B。断言 B 出现在结果中——
    #       旧实现只在语义池内加分，B 永远进不来。另断言每个返回 chunk 的
    #       metadata 带原始语义分（CRAG 评估的数据源），B 无语义分记 0.0
    def test_hybrid_keyword_leg_introduces_candidates(self, tmp_path: Path) -> None:
        chunk_a = Chunk(chunk_id="a", text="semantic match", source_path="a.py", start_line=1, end_line=1)
        chunk_b = Chunk(chunk_id="b", text="contains needle42 token", source_path="b.py", start_line=1, end_line=1)
        store = MagicMock(spec=VectorStore)
        store.search = AsyncMock(return_value=[(chunk_a, 1.0)])
        store.search_by_text = AsyncMock(return_value=[(chunk_b, 1.0)])
        store.get_by_ids = AsyncMock(return_value=[])
        embedder = MagicMock(spec=EmbeddingProvider)
        embedder.embed = AsyncMock(return_value=[[1.0, 0.0]])
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        results = asyncio.run(manager.hybrid_search("needle42", top_k=5))
        ids = {c.chunk_id for c, _ in results}
        assert "b" in ids  # 关键词腿独立引入
        for c, _ in results:
            assert c.metadata["semantic_score"] == (1.0 if c.chunk_id == "a" else 0.0)

    # 功能：enable_rerank=False 时 retrieve 不得调用 LLM rerank（结果 reranked 标记为 False）
    # 设计：rerank 每次检索多一整轮 LLM 调用，配置开关只在此处生效；
    #       mock_llm.rerank 若被调用 side_effect 直接 fail，断言比查 flag 更强
    def test_rerank_toggle_off_skips_llm(self, tmp_path: Path) -> None:
        store = MemoryVectorStore()
        chunk = Chunk(text="def configure():\n    pass", source_path="config.py", start_line=1, end_line=2)
        asyncio.run(store.add([chunk], [[0.5, 0.5]]))
        embedder = MagicMock(spec=EmbeddingProvider)
        embedder.embed = AsyncMock(return_value=[[0.5, 0.5]])

        def _fail_rerank(*args, **kwargs):
            raise AssertionError("rerank must not be called when disabled")

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.complete = AsyncMock(return_value="rag")
        mock_llm.rewrite_query = AsyncMock(return_value=["query"])
        mock_llm.rerank = AsyncMock(side_effect=_fail_rerank)

        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
            llm_client=mock_llm,
        )
        retriever = AdaptiveRetriever(manager, llm_client=mock_llm, enable_rerank=False)
        result = asyncio.run(retriever.retrieve("how to configure"))
        assert result.reranked is False

    # 功能：_evaluate_quality 必须优先用 metadata 里的原始语义分，关键词加分不得虚抬置信度
    # 设计：CRAG 的 0.6 阈值语义上对照相似度；构造综合分 0.9 但 semantic_score=0.2
    #       的 top 结果（关键词把分数抬爆），必须判 incorrect 触发回退——
    #       若实现退回"直接看综合分"，这条 0.9 会判 correct 直接放行垃圾结果
    def test_crag_uses_raw_semantic_score(self, tmp_path: Path) -> None:
        store = MemoryVectorStore()
        embedder = MagicMock(spec=EmbeddingProvider)
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=embedder,
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        chunk = Chunk(
            text="x", source_path="a.py", start_line=1, end_line=1,
            metadata={"semantic_score": 0.2},
        )
        retriever = AdaptiveRetriever(manager)
        assert retriever._evaluate_quality([(chunk, 0.9)]) == "incorrect"
        # metadata 缺失时回退综合分（grep 腿等无 semantic_score 的路径）
        plain = Chunk(text="y", source_path="a.py", start_line=1, end_line=1)
        assert retriever._evaluate_quality([(plain, 0.9)]) == "correct"

    # 功能：IndexKnowledgeTool 空 paths 不炸（旧 NameError）、文件路径报真实 chunk 数
    # 设计：用真 store + 假 embedder 走端到端：两行函数文件应报 "Added: 1 chunks"
    #       （旧实现恒 1 恰好撞上，所以另加一条多 chunk 文件用例断言 >=2），
    #       空列表单独断言 "No paths given."，不存在路径断言跳过备注且不中断
    def test_index_tool_empty_and_real_counts(self, tmp_path: Path) -> None:
        from iwan_claude.core.rag.tools import IndexKnowledgeTool

        store = MemoryVectorStore()
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=_FakeEmbedder(),
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        tool = IndexKnowledgeTool(manager)

        empty = asyncio.run(tool.invoke({"paths": []}))
        assert "No paths given." in empty.content

        big = tmp_path / "big.py"
        big.write_text(
            "def a():\n    return 1\n\n"
            "def b():\n    return 2\n\n"
            "def c():\n    return 3\n"
        )
        res = asyncio.run(tool.invoke({"paths": [str(big)]}))
        # 报告数字必须来自真实 chunk 数而非旧实现的硬编码 1——
        # 用入库后的 store.size() 反推，chunker 策略变化也不会误伤断言
        assert f"Added: {store.size()} chunks" in res.content
        assert store.size() >= 2

        ghost = asyncio.run(tool.invoke({"paths": [str(tmp_path / "nope.py")]}))
        assert "skipped" in ghost.content

    # 功能：SearchKnowledgeTool adaptive 分支必须把 filters 透传给 retrieve（旧实现静默丢弃）
    # 设计：retriever 用 MagicMock（spec 保真方法签名），断言 retrieve 收到 filters=；
    #       工具层参数被吞是最难闻的 bug——schema 告诉模型能过滤、结果全库返回
    def test_search_tool_passes_filters_to_adaptive(self, tmp_path: Path) -> None:
        from iwan_claude.core.rag.tools import SearchKnowledgeTool

        store = MemoryVectorStore()
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=_FakeEmbedder(),
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=RetrievalResult(chunks=[]))
        tool = SearchKnowledgeTool(manager, mock_retriever)
        asyncio.run(tool.invoke({"query": "x", "filters": {"source_path": "a.py"}}))
        _, kwargs = mock_retriever.retrieve.await_args
        assert kwargs.get("filters") == {"source_path": "a.py"}

    # 功能：eval 的相关性比对必须先归一化路径（chunks 绝对 vs 标注相对）
    # 设计：旧实现拿 "src/x.py" 与绝对 source_path 求交集恒空，指标全 0 还"看起来在跑"；
    #       这里以 _evaluate_single 为最小面：mock manager.search 返回绝对路径 chunk，
    #       标注用未 resolve 的相对形态，断言 recall@1 == 1.0——
    #       不跑全量 evaluate() 是为了绕开 embedding/LLM，只钉路径换算这一件事
    def test_eval_normalizes_paths_before_matching(self, tmp_path: Path, monkeypatch) -> None:
        from iwan_claude.core.rag.eval import EvalQuestion, RAGEvaluator

        monkeypatch.chdir(tmp_path)
        store = MemoryVectorStore()
        manager = KnowledgeIndexManager(
            vector_store=store, embedding_provider=_FakeEmbedder(),
            chunker=DocumentChunker(), index_path=str(tmp_path / "idx"),
        )
        abs_src = str((tmp_path / "src" / "a.py").resolve())
        manager.search = AsyncMock(  # type: ignore[method-assign]
            return_value=[(Chunk(text="hit", source_path=abs_src, start_line=1, end_line=1), 0.9)]
        )
        evaluator = RAGEvaluator(manager)
        question = EvalQuestion(
            query="q",
            relevant_sources=["src/a.py"],  # 相对形态标注（chdir 进 tmp_path 后可正确 resolve）
        )
        result = asyncio.run(evaluator._evaluate_single(question))
        assert result.recall_at_k[1] == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])