from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iwan_claude.core.rag.chunker import Chunk, DocumentChunker
from iwan_claude.core.rag.embedding import EmbeddingProvider
from iwan_claude.core.rag.vectorstore import VectorStore


@dataclass
class IndexResult:
    added_chunks: int = 0
    updated_chunks: int = 0
    deleted_chunks: int = 0
    total_tokens: int = 0


@dataclass
class IndexStatus:
    total_chunks: int = 0
    total_sources: int = 0
    last_indexed_at: str = ""
    index_size_bytes: int = 0


class KnowledgeIndexManager:
    def __init__(
        self,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        chunker: DocumentChunker,
        index_path: str = ".iwan/rag_index",
    ) -> None:
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._chunker = chunker
        self._index_path = Path(index_path)
        self._meta_path = self._index_path / "index_meta.json"
        self._load_meta()

    def _load_meta(self) -> None:
        if self._meta_path.exists():
            with open(self._meta_path, "r", encoding="utf-8") as f:
                self._meta = json.load(f)
        else:
            self._meta = {"sources": {}}

    def _save_meta(self) -> None:
        self._index_path.mkdir(parents=True, exist_ok=True)
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(self._meta, f, ensure_ascii=False, indent=2)

    async def index_directory(
        self,
        root: str = ".",
        include: list[str] = ["**/*.py", "**/*.md"],
        exclude: list[str] = [".git/**", "node_modules/**", ".venv/**"],
        incremental: bool = True,
    ) -> IndexResult:
        result = IndexResult()
        root_path = Path(root).resolve()

        all_files: list[Path] = []
        for pattern in include:
            all_files.extend(root_path.glob(pattern))

        excluded_patterns = [Path(p) for p in exclude]

        def is_excluded(file_path: Path) -> bool:
            rel_path = file_path.relative_to(root_path)
            for pattern in excluded_patterns:
                if rel_path.match(str(pattern)):
                    return True
            return False

        files_to_index = [f for f in all_files if not is_excluded(f)]

        for file_path in files_to_index:
            rel_path = str(file_path.relative_to(root_path))

            if incremental:
                mtime = os.path.getmtime(file_path)
                if rel_path in self._meta["sources"]:
                    last_mtime = self._meta["sources"][rel_path].get("mtime", 0)
                    if mtime <= last_mtime:
                        continue

            await self.index_file(file_path)
            self._meta["sources"][rel_path] = {
                "mtime": mtime,
                "chunk_count": 0,
            }
            result.added_chunks += 1

        self._save_meta()
        return result

    async def index_file(self, path: Path) -> None:
        chunks = self._chunker.chunk_file(path)
        if not chunks:
            return

        texts = [c.text for c in chunks]
        vectors = await self._embedding_provider.embed(texts)

        await self._vector_store.delete_by_source(str(path))
        await self._vector_store.add(chunks, vectors)

    async def remove_file(self, path: Path) -> None:
        await self._vector_store.delete_by_source(str(path))
        rel_path = str(path)
        if rel_path in self._meta["sources"]:
            del self._meta["sources"][rel_path]
            self._save_meta()

    def status(self) -> IndexStatus:
        import time

        total_chunks = 0
        total_sources = len(self._meta["sources"])
        last_indexed_at = self._meta.get("last_indexed_at", "")
        index_size_bytes = 0

        if self._index_path.exists():
            for file in self._index_path.rglob("*"):
                if file.is_file():
                    index_size_bytes += file.stat().st_size

        return IndexStatus(
            total_chunks=total_chunks,
            total_sources=total_sources,
            last_indexed_at=last_indexed_at,
            index_size_bytes=index_size_bytes,
        )

    async def search(
        self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None
    ) -> list[tuple[Chunk, float]]:
        query_vector = await self._embedding_provider.embed([query])
        if not query_vector or not query_vector[0]:
            return []
        return await self._vector_store.search(query_vector[0], top_k, filters)

    async def hybrid_search(
        self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None,
        keyword_weight: float = 0.3, semantic_weight: float = 0.7,
    ) -> list[tuple[Chunk, float]]:
        rewritten_queries = self._rewrite_query(query)
        all_results: dict[str, tuple[Chunk, float]] = {}

        for q in rewritten_queries:
            query_vector = await self._embedding_provider.embed([q])
            if not query_vector or not query_vector[0]:
                continue
            results = await self._vector_store.search(query_vector[0], top_k * 2, filters)
            for chunk, score in results:
                if chunk.chunk_id in all_results:
                    if score > all_results[chunk.chunk_id][1]:
                        all_results[chunk.chunk_id] = (chunk, score)
                else:
                    all_results[chunk.chunk_id] = (chunk, score)

        keyword_results = self._keyword_search(query, list(all_results.values()), top_k)

        scored_results: list[tuple[Chunk, float]] = []
        for chunk_id, (chunk, semantic_score) in all_results.items():
            keyword_score = keyword_results.get(chunk_id, 0.0)
            combined_score = (semantic_score * semantic_weight) + (keyword_score * keyword_weight)
            scored_results.append((chunk, combined_score))

        scored_results.sort(key=lambda x: x[1], reverse=True)
        return scored_results[:top_k]

    def _rewrite_query(self, query: str) -> list[str]:
        queries = [query]

        synonyms = {
            "config": ["configuration", "setting", "setup"],
            "function": ["method", "def", "func"],
            "class": ["type", "model"],
            "file": ["document", "module"],
            "search": ["find", "lookup"],
            "error": ["exception", "bug", "issue"],
            "test": ["verify", "check"],
        }

        for word, syns in synonyms.items():
            if word.lower() in query.lower():
                for syn in syns:
                    new_query = query.replace(word, syn, 1)
                    if new_query not in queries:
                        queries.append(new_query)

        return queries

    def _keyword_search(
        self, query: str, candidates: list[tuple[Chunk, float]], top_k: int
    ) -> dict[str, float]:
        import re

        keywords = re.findall(r"\w+", query.lower())
        results: dict[str, float] = {}

        for chunk, _ in candidates:
            chunk_text = chunk.text.lower()
            score = 0.0
            for keyword in keywords:
                if keyword in chunk_text:
                    score += 1.0
                    score += chunk_text.count(keyword) * 0.1

            if score > 0:
                results[chunk.chunk_id] = min(score / len(keywords), 1.0)

        return results

    def rebuild_index(self) -> None:
        self._meta = {"sources": {}}
        self._vector_store = type(self._vector_store)()
        self._save_meta()

    def cleanup_index(self) -> None:
        if self._index_path.exists():
            import shutil

            shutil.rmtree(self._index_path)
        self._meta = {"sources": {}}

    def backup_index(self, backup_path: str) -> None:
        import shutil

        backup = Path(backup_path)
        if self._index_path.exists():
            shutil.copytree(self._index_path, backup, dirs_exist_ok=True)
        self._meta_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._meta_path, backup / "index_meta.json")

    def save(self) -> None:
        self._vector_store.save(self._index_path)
        self._save_meta()

    def load(self) -> None:
        self._vector_store.load(self._index_path)
        self._load_meta()
