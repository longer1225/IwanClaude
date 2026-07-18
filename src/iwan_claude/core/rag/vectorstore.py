from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from iwan_claude.core.rag.chunker import Chunk


class VectorStore(ABC):
    @abstractmethod
    async def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        ...

    @abstractmethod
    async def delete(self, chunk_ids: list[str]) -> None:
        ...

    @abstractmethod
    async def delete_by_source(self, source_path: str) -> None:
        ...

    @abstractmethod
    async def search(
        self, query_vector: list[float], top_k: int = 5,
        filters: dict[str, Any] | None = None
    ) -> list[tuple[Chunk, float]]:
        ...

    @abstractmethod
    def save(self, path: Path) -> None:
        ...

    @abstractmethod
    def load(self, path: Path) -> None:
        ...


class MemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._vectors: list[list[float]] = []
        self._chunk_id_map: dict[str, int] = {}
        self._source_map: dict[str, list[int]] = {}

    async def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for i, chunk in enumerate(chunks):
            idx = len(self._chunks)
            self._chunks.append(chunk)
            self._vectors.append(vectors[i])
            self._chunk_id_map[chunk.chunk_id] = idx

            source = chunk.source_path
            if source not in self._source_map:
                self._source_map[source] = []
            self._source_map[source].append(idx)

    async def delete(self, chunk_ids: list[str]) -> None:
        indices_to_remove = set()
        for chunk_id in chunk_ids:
            if chunk_id in self._chunk_id_map:
                indices_to_remove.add(self._chunk_id_map[chunk_id])

        if not indices_to_remove:
            return

        self._chunks = [
            c for i, c in enumerate(self._chunks) if i not in indices_to_remove
        ]
        self._vectors = [
            v for i, v in enumerate(self._vectors) if i not in indices_to_remove
        ]

        self._chunk_id_map = {c.chunk_id: i for i, c in enumerate(self._chunks)}

        self._source_map = {}
        for i, chunk in enumerate(self._chunks):
            source = chunk.source_path
            if source not in self._source_map:
                self._source_map[source] = []
            self._source_map[source].append(i)

    async def delete_by_source(self, source_path: str) -> None:
        if source_path not in self._source_map:
            return

        indices_to_remove = set(self._source_map[source_path])
        self._chunks = [
            c for i, c in enumerate(self._chunks) if i not in indices_to_remove
        ]
        self._vectors = [
            v for i, v in enumerate(self._vectors) if i not in indices_to_remove
        ]

        self._chunk_id_map = {c.chunk_id: i for i, c in enumerate(self._chunks)}

        self._source_map = {}
        for i, chunk in enumerate(self._chunks):
            source = chunk.source_path
            if source not in self._source_map:
                self._source_map[source] = []
            self._source_map[source].append(i)

    async def search(
        self, query_vector: list[float], top_k: int = 5,
        filters: dict[str, Any] | None = None
    ) -> list[tuple[Chunk, float]]:
        if not self._vectors:
            return []

        scores: list[tuple[int, float]] = []
        for i, vector in enumerate(self._vectors):
            score = self._cosine_similarity(query_vector, vector)
            scores.append((i, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        results: list[tuple[Chunk, float]] = []
        for idx, score in scores[:top_k]:
            chunk = self._chunks[idx]
            if filters:
                if "source_path" in filters and chunk.source_path != filters["source_path"]:
                    continue
                if "symbol" in filters and chunk.symbol != filters["symbol"]:
                    continue
            results.append((chunk, score))

        return results

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a: float = sum(x * x for x in a) ** 0.5
        norm_b: float = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

        chunks_data = [c.model_dump() for c in self._chunks]
        with open(path / "chunks.json", "w", encoding="utf-8") as f:
            json.dump(chunks_data, f, ensure_ascii=False, indent=2)

        with open(path / "vectors.json", "w", encoding="utf-8") as f:
            json.dump(self._vectors, f, ensure_ascii=False)

    def load(self, path: Path) -> None:
        if not path.exists():
            return

        chunks_path = path / "chunks.json"
        vectors_path = path / "vectors.json"

        if chunks_path.exists():
            with open(chunks_path, "r", encoding="utf-8") as f:
                chunks_data = json.load(f)
            self._chunks = [Chunk(**c) for c in chunks_data]

        if vectors_path.exists():
            with open(vectors_path, "r", encoding="utf-8") as f:
                self._vectors = json.load(f)

        self._chunk_id_map = {c.chunk_id: i for i, c in enumerate(self._chunks)}

        self._source_map = {}
        for i, chunk in enumerate(self._chunks):
            source = chunk.source_path
            if source not in self._source_map:
                self._source_map[source] = []
            self._source_map[source].append(i)


def get_vector_store(config: dict[str, Any] | None = None) -> VectorStore:
    return MemoryVectorStore()
