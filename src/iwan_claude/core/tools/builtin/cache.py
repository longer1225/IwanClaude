from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

_MAX_CACHE_SIZE = 1000
_DEFAULT_TTL = 3600


class CacheManager:
    def __init__(self):
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry:
            if datetime.fromisoformat(entry["expires_at"]) > datetime.now():
                return entry["value"]
            else:
                del self._cache[key]
        return None

    def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        if len(self._cache) >= _MAX_CACHE_SIZE:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k]["created_at"])
            del self._cache[oldest_key]

        expires_at = (datetime.now() + timedelta(seconds=ttl)).isoformat()
        self._cache[key] = {
            "value": value,
            "created_at": datetime.now().isoformat(),
            "expires_at": expires_at,
            "ttl": ttl,
        }

    def delete(self, key: str) -> bool:
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> int:
        count = len(self._cache)
        self._cache.clear()
        return count

    def keys(self) -> list[str]:
        return list(self._cache.keys())

    def stats(self) -> dict[str, Any]:
        return {
            "size": len(self._cache),
            "max_size": _MAX_CACHE_SIZE,
            "default_ttl": _DEFAULT_TTL,
        }


_cache_manager = CacheManager()


class CacheGetParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str = Field(description="Cache key")


class CacheGetTool(BaseTool):
    params_model = CacheGetParams
    name = "cache_get"
    description = "Get cached value by key."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Cache key to retrieve",
            },
        },
        "required": ["key"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = CacheGetParams.model_validate(params)
        value = _cache_manager.get(p.key)
        if value is not None:
            return ToolResult(content=f"Cache hit: {value}")
        else:
            return ToolResult(content=f"Cache miss for key: {p.key}", is_error=True, error_type="runtime_error")


class CacheSetParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str = Field(description="Cache key")
    value: str = Field(description="Cache value")
    ttl: int = Field(default=_DEFAULT_TTL, description="Time to live in seconds")


class CacheSetTool(BaseTool):
    params_model = CacheSetParams
    name = "cache_set"
    description = "Set cached value with optional TTL (default: 3600 seconds)."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Cache key",
            },
            "value": {
                "type": "string",
                "description": "Cache value",
            },
            "ttl": {
                "type": "integer",
                "description": "Optional: time to live in seconds (default: 3600)",
            },
        },
        "required": ["key", "value"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = CacheSetParams.model_validate(params)
        _cache_manager.set(p.key, p.value, p.ttl)
        return ToolResult(content=f"Cache set: {p.key} (TTL: {p.ttl}s)")


class CacheDeleteParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str = Field(description="Cache key to delete")


class CacheDeleteTool(BaseTool):
    params_model = CacheDeleteParams
    name = "cache_delete"
    description = "Delete cached value by key."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Cache key to delete",
            },
        },
        "required": ["key"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = CacheDeleteParams.model_validate(params)
        success = _cache_manager.delete(p.key)
        if success:
            return ToolResult(content=f"Cache deleted: {p.key}")
        else:
            return ToolResult(content=f"Key not found: {p.key}", is_error=True, error_type="runtime_error")


class CacheInvalidateTool(BaseTool):
    name = "cache_invalidate"
    description = "Invalidate all cached items."
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        count = _cache_manager.clear()
        return ToolResult(content=f"Cache invalidated: {count} items cleared")


class CacheStatsTool(BaseTool):
    name = "cache_stats"
    description = "Get cache statistics including size and configuration."
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        stats = _cache_manager.stats()
        lines = []
        lines.append("Cache Statistics")
        lines.append("=" * 60)
        lines.append(f"Current size: {stats['size']}")
        lines.append(f"Max size: {stats['max_size']}")
        lines.append(f"Default TTL: {stats['default_ttl']}s")
        return ToolResult(content="\n".join(lines))