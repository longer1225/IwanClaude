"""
缓存管理工具模块 - 提供内存缓存的管理功能

【学习要点】
1. 内存缓存：使用字典实现简单的内存缓存
2. TTL 机制：支持缓存过期时间（Time-To-Live）
3. 容量限制：最大缓存 1000 条记录，超过时删除最旧的
4. 缓存操作：支持获取、设置、删除、清空、统计等操作

【缓存条目结构】
每个缓存条目包含：
- value: 缓存的值
- created_at: 创建时间（ISO 格式）
- expires_at: 过期时间（ISO 格式）
- ttl: 存活时间（秒）

【设计模式】
- 单例模式：使用全局 _cache_manager 实例
- 字典存储：使用字典作为底层存储结构
- LRU 策略：超过容量时删除最旧的条目

【安全注意事项】
- 缓存存储在内存中，重启后会丢失
- 不支持持久化，不适合存储关键数据
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大缓存条目数：1000 条
_MAX_CACHE_SIZE = 1000
# 默认 TTL：3600 秒（1 小时）
_DEFAULT_TTL = 3600


class CacheManager:
    """
    缓存管理器 - 内存缓存的核心管理类

    【学习要点】
    1. 字典存储：使用字典作为底层存储结构
    2. TTL 检查：获取时自动检查过期时间
    3. 容量管理：超过容量时删除最旧的条目
    4. 时间格式化：使用 ISO 格式存储时间

    【缓存条目结构】
    ```python
    {
        "value": any_value,          # 缓存的值
        "created_at": "2024-01-15T10:30:45.123456",  # 创建时间（ISO 格式）
        "expires_at": "2024-01-15T11:30:45.123456",  # 过期时间（ISO 格式）
        "ttl": 3600,                 # 存活时间（秒）
    }
    ```

    【使用示例】
    ```python
    cache = CacheManager()
    
    # 设置缓存（使用默认 TTL）
    cache.set("key1", "value1")
    
    # 设置缓存（指定 TTL）
    cache.set("key2", "value2", ttl=60)
    
    # 获取缓存
    value = cache.get("key1")
    
    # 删除缓存
    cache.delete("key1")
    
    # 清空所有缓存
    count = cache.clear()
    
    # 获取统计信息
    stats = cache.stats()
    ```
    """
    def __init__(self):
        """
        初始化缓存管理器

        创建一个空的字典作为缓存存储
        """
        # 使用字典存储缓存条目，键为缓存键，值为包含详细信息的字典
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> Any | None:
        """
        获取缓存值

        【参数说明】
        - key: str - 缓存键

        【执行流程】
        1. 从字典中获取条目
        2. 如果条目存在，检查是否过期
        3. 如果未过期，返回值
        4. 如果已过期，删除条目并返回 None
        5. 如果条目不存在，返回 None

        【返回值】
        - Any | None: 缓存的值，如果不存在或已过期则返回 None
        """
        entry = self._cache.get(key)
        if entry:
            # 检查是否过期
            if datetime.fromisoformat(entry["expires_at"]) > datetime.now():
                return entry["value"]
            else:
                # 已过期，删除条目
                del self._cache[key]
        return None

    def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        """
        设置缓存值

        【参数说明】
        - key: str - 缓存键
        - value: Any - 缓存值
        - ttl: int - 存活时间（秒），默认 3600 秒

        【执行流程】
        1. 检查缓存容量，如果超过限制，删除最旧的条目
        2. 计算过期时间（当前时间 + TTL）
        3. 创建缓存条目（包含值、创建时间、过期时间、TTL）
        4. 将条目存入字典

        【容量管理】
        - 当缓存条目数达到 _MAX_CACHE_SIZE（1000）时
        - 使用 min() 函数找到创建时间最早的键
        - 删除该键对应的条目
        """
        # 检查容量限制
        if len(self._cache) >= _MAX_CACHE_SIZE:
            # 删除最旧的条目（按创建时间排序）
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k]["created_at"])
            del self._cache[oldest_key]

        # 计算过期时间
        expires_at = (datetime.now() + timedelta(seconds=ttl)).isoformat()
        # 创建缓存条目
        self._cache[key] = {
            "value": value,
            "created_at": datetime.now().isoformat(),
            "expires_at": expires_at,
            "ttl": ttl,
        }

    def delete(self, key: str) -> bool:
        """
        删除缓存条目

        【参数说明】
        - key: str - 要删除的缓存键

        【返回值】
        - bool: 删除是否成功（键存在则返回 True，否则返回 False）
        """
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> int:
        """
        清空所有缓存

        【返回值】
        - int: 被清空的缓存条目数量
        """
        count = len(self._cache)
        self._cache.clear()
        return count

    def keys(self) -> list[str]:
        """
        获取所有缓存键

        【返回值】
        - list[str]: 所有缓存键的列表
        """
        return list(self._cache.keys())

    def stats(self) -> dict[str, Any]:
        """
        获取缓存统计信息

        【返回值】
        - dict: 包含当前大小、最大大小和默认 TTL 的字典
        """
        return {
            "size": len(self._cache),
            "max_size": _MAX_CACHE_SIZE,
            "default_ttl": _DEFAULT_TTL,
        }


# 全局缓存管理器实例（单例模式）
_cache_manager = CacheManager()


class CacheGetParams(BaseModel):
    """
    获取缓存参数模型

    【字段说明】
    - key: str - 缓存键，必填

    【参数校验】
    - 键不能为空字符串
    - 键会被原样传递给缓存管理器
    """
    model_config = ConfigDict(extra="ignore")
    key: str = Field(description="Cache key")


class CacheGetTool(BaseTool):
    """
    获取缓存工具 - 根据键获取缓存值

    【学习要点】
    1. 缓存查询：调用 _cache_manager.get() 获取值
    2. 缓存命中：返回值存在时返回成功消息
    3. 缓存未命中：值不存在时返回错误消息

    【使用示例】
    ```python
    tool = CacheGetTool()
    
    # 获取缓存值
    result = await tool.invoke({"key": "user_data_123"})
    
    # 如果缓存命中，返回 "Cache hit: value"
    # 如果缓存未命中，返回错误 "Cache miss for key: user_data_123"
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 调用 _cache_manager.get() 获取缓存值
    3. 检查返回值是否为 None
    4. 如果不为 None，返回缓存命中消息
    5. 如果为 None，返回缓存未命中错误

    【注意事项】
    - 缓存管理器会自动检查过期时间
    - 已过期的条目会被自动删除
    """
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
        """
        执行获取缓存操作

        【参数说明】
        - params: dict - 工具调用参数，包含 key

        【返回值】
        - ToolResult: 包含缓存值或错误信息
        """
        # 1. 验证输入参数
        p = CacheGetParams.model_validate(params)

        # 2. 调用缓存管理器获取值
        value = _cache_manager.get(p.key)

        # 3. 根据结果返回不同消息
        if value is not None:
            # 缓存命中
            return ToolResult(content=f"Cache hit: {value}")
        else:
            # 缓存未命中
            return ToolResult(content=f"Cache miss for key: {p.key}", is_error=True, error_type="runtime_error")


class CacheSetParams(BaseModel):
    """
    设置缓存参数模型

    【字段说明】
    - key: str - 缓存键，必填
    - value: str - 缓存值，必填
    - ttl: int - 存活时间（秒），可选，默认 3600 秒

    【参数校验】
    - key 和 value 不能为空字符串
    - ttl 必须是正整数
    """
    model_config = ConfigDict(extra="ignore")
    key: str = Field(description="Cache key")
    value: str = Field(description="Cache value")
    ttl: int = Field(default=_DEFAULT_TTL, description="Time to live in seconds")


class CacheSetTool(BaseTool):
    """
    设置缓存工具 - 设置缓存值

    【学习要点】
    1. 缓存写入：调用 _cache_manager.set() 设置值
    2. TTL 指定：支持自定义存活时间
    3. 容量管理：缓存管理器自动处理容量限制

    【使用示例】
    ```python
    tool = CacheSetTool()
    
    # 设置缓存（使用默认 TTL）
    result = await tool.invoke({"key": "user_data_123", "value": "some data"})
    
    # 设置缓存（指定 TTL）
    result = await tool.invoke({
        "key": "temp_data",
        "value": "temporary value",
        "ttl": 60
    })
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 调用 _cache_manager.set() 设置缓存值
    3. 返回成功消息，包含键和 TTL

    【注意事项】
    - 如果缓存已满，会自动删除最旧的条目
    - TTL 为 0 或负数时，缓存会立即过期
    """
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
        """
        执行设置缓存操作

        【参数说明】
        - params: dict - 工具调用参数，包含 key、value 和可选的 ttl

        【返回值】
        - ToolResult: 包含设置结果的消息
        """
        # 1. 验证输入参数
        p = CacheSetParams.model_validate(params)

        # 2. 调用缓存管理器设置值
        _cache_manager.set(p.key, p.value, p.ttl)

        # 3. 返回成功消息
        return ToolResult(content=f"Cache set: {p.key} (TTL: {p.ttl}s)")


class CacheDeleteParams(BaseModel):
    """
    删除缓存参数模型

    【字段说明】
    - key: str - 要删除的缓存键，必填

    【参数校验】
    - 键不能为空字符串
    """
    model_config = ConfigDict(extra="ignore")
    key: str = Field(description="Cache key to delete")


class CacheDeleteTool(BaseTool):
    """
    删除缓存工具 - 根据键删除缓存值

    【学习要点】
    1. 缓存删除：调用 _cache_manager.delete() 删除值
    2. 删除结果：根据返回值判断删除是否成功
    3. 错误处理：键不存在时返回错误

    【使用示例】
    ```python
    tool = CacheDeleteTool()
    
    # 删除缓存
    result = await tool.invoke({"key": "user_data_123"})
    
    # 如果键存在，返回 "Cache deleted: user_data_123"
    # 如果键不存在，返回错误 "Key not found: user_data_123"
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 调用 _cache_manager.delete() 删除缓存
    3. 根据返回值判断删除是否成功
    4. 如果成功，返回成功消息
    5. 如果失败，返回错误消息

    【注意事项】
    - 删除不存在的键不会引发异常
    - 删除操作是幂等的（多次删除同一键结果相同）
    """
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
        """
        执行删除缓存操作

        【参数说明】
        - params: dict - 工具调用参数，包含 key

        【返回值】
        - ToolResult: 包含删除结果或错误信息
        """
        # 1. 验证输入参数
        p = CacheDeleteParams.model_validate(params)

        # 2. 调用缓存管理器删除值
        success = _cache_manager.delete(p.key)

        # 3. 根据结果返回不同消息
        if success:
            # 删除成功
            return ToolResult(content=f"Cache deleted: {p.key}")
        else:
            # 键不存在
            return ToolResult(content=f"Key not found: {p.key}", is_error=True, error_type="runtime_error")


class CacheInvalidateTool(BaseTool):
    """
    缓存失效工具 - 清空所有缓存

    【学习要点】
    1. 批量清除：调用 _cache_manager.clear() 清空所有缓存
    2. 统计信息：返回被清空的条目数量
    3. 无参数工具：不需要输入参数

    【使用示例】
    ```python
    tool = CacheInvalidateTool()
    result = await tool.invoke({})
    
    # 返回 "Cache invalidated: 15 items cleared"
    ```

    【执行流程】
    1. 调用 _cache_manager.clear() 清空缓存
    2. 获取被清空的条目数量
    3. 返回结果消息

    【注意事项】
    - 此操作会删除所有缓存条目
    - 操作不可逆，请谨慎使用
    """
    name = "cache_invalidate"
    description = "Invalidate all cached items."
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行缓存失效操作

        【参数说明】
        - params: dict - 工具调用参数（无必填参数）

        【返回值】
        - ToolResult: 包含被清空条目数量的消息
        """
        # 1. 调用缓存管理器清空所有缓存
        count = _cache_manager.clear()

        # 2. 返回结果消息
        return ToolResult(content=f"Cache invalidated: {count} items cleared")


class CacheStatsTool(BaseTool):
    """
    缓存统计工具 - 获取缓存统计信息

    【学习要点】
    1. 统计信息：调用 _cache_manager.stats() 获取统计数据
    2. 格式化输出：构建包含当前大小、最大大小和默认 TTL 的字符串
    3. 无参数工具：不需要输入参数

    【使用示例】
    ```python
    tool = CacheStatsTool()
    result = await tool.invoke({})
    ```

    【输出格式】
    ```
    Cache Statistics
    ============================================================
    Current size: 42
    Max size: 1000
    Default TTL: 3600s
    ```

    【执行流程】
    1. 调用 _cache_manager.stats() 获取统计数据
    2. 构建格式化的统计信息字符串
    3. 返回结果

    【注意事项】
    - 此工具不需要输入参数
    - 返回缓存的当前状态信息
    """
    name = "cache_stats"
    description = "Get cache statistics including size and configuration."
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行缓存统计查询操作

        【参数说明】
        - params: dict - 工具调用参数（无必填参数）

        【返回值】
        - ToolResult: 包含缓存统计信息的格式化字符串
        """
        # 1. 调用缓存管理器获取统计数据
        stats = _cache_manager.stats()

        # 2. 构建格式化的统计信息
        lines = []
        lines.append("Cache Statistics")
        lines.append("=" * 60)
        lines.append(f"Current size: {stats['size']}")
        lines.append(f"Max size: {stats['max_size']}")
        lines.append(f"Default TTL: {stats['default_ttl']}s")

        # 3. 返回格式化结果
        return ToolResult(content="\n".join(lines))