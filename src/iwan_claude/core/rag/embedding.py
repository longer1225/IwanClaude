from __future__ import annotations

import json
import os
from hashlib import md5

import httpx

from iwan_claude.core.config import RagConfig


class EmbeddingProvider:
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str = "DEEPSEEK_API_KEY",
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(f"{api_key_env} or OPENAI_API_KEY environment variable is required")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0))

    async def embed(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            embeddings = await self._embed_batch(batch)
            results.extend(embeddings)
        return results

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": texts,
        }
        url = f"{self._base_url}/embeddings"

        resp = await self._http.post(url, headers=headers, json=payload)
        resp.raise_for_status()

        data = resp.json()
        embeddings = []
        for item in data.get("data", []):
            embeddings.append(item.get("embedding", []))

        return embeddings


def get_embedding_provider(config: RagConfig, llm_base_url: str) -> EmbeddingProvider:
    return EmbeddingProvider(
        model=config.embedding_model,
        base_url=llm_base_url,
    )
