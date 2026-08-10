"""
文件写入工具 - 将文本内容写入指定文件

【学习要点】
1. 多种写入模式：支持 overwrite（覆盖）、append（追加）、fail_if_exists（存在则失败）
2. 自动备份：默认在覆盖写入前创建 .bak.<timestamp> 备份文件
3. 配额检查：通过 check_file_size 和 check_total_quota 控制磁盘使用
4. 安全检查：禁止路径遍历，验证沙箱权限
5. Mixin 模式：使用 _WriteToolMixin 添加元数据和接口

【安全机制】
- 路径遍历检测：检查路径中是否包含 ".."
- 沙箱验证：validate_path 确保文件在工作目录内
- 大小限制：单文件最大 1MB
- 配额控制：check_total_quota 检查总磁盘使用量

【写入模式】
- overwrite：覆盖现有文件（默认）
- append：追加到文件末尾
- fail_if_exists：如果文件已存在则拒绝写入
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from iwan_claude.core.sandbox import check_file_size, check_total_quota, validate_path
from iwan_claude.core.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# ===== 兜底常量（配置加载失败时使用，通常不会触发） =====
_FALLBACK_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
# 支持的写入模式
_WRITE_MODES = ("overwrite", "append", "fail_if_exists")


def _max_bytes() -> int:
    """从全局配置读取 write_file 单次写入的最大字节数"""
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.write_file_max_bytes)
    except Exception:
        return _FALLBACK_MAX_BYTES


def _validate_rel_path(path_str: str, operation: str = "write") -> Path:
    """
    验证相对路径 - 安全检查辅助函数

    【参数说明】
    - path_str: str - 路径字符串
    - operation: str - 操作类型（read/write）

    【安全检查】
    1. 检查路径中是否包含 ".."（路径遍历攻击）
    2. 验证路径是否在沙箱允许范围内

    【返回值】
    - Path: 验证后的 Path 对象

    【异常处理】
    - PermissionError: 路径包含 ".." 时抛出
    - ValueError: 路径不在沙箱范围内时抛出（由 validate_path 抛出）
    """
    if ".." in Path(path_str).parts:
        raise PermissionError(f"path traversal not allowed: {path_str}")
    validate_path(path_str, operation)
    return Path(path_str)


@dataclass
class _WriteToolMixin:
    """
    写入工具 Mixin - 为写入类工具添加元数据和接口

    【学习要点】
    1. Mixin 模式：通过多重继承为类添加功能
    2. ClassVar：类变量，不参与实例化
    3. 接口约定：定义 estimate_affected_paths 方法，由子类实现

    【用途】
    标记工具属于 "write" 类别，便于权限管理和分类

    【子类实现要求】
    必须实现 estimate_affected_paths 方法，返回受影响的路径列表
    """
    metadata: ClassVar[dict[str, str]] = {"category": "write"}

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        """
        估算受影响的路径 - 用于权限预检查

        【返回值】
        - list[str]: 可能被修改的路径列表
        """
        raise NotImplementedError


class WriteFileParams(BaseModel):
    """
    写入文件参数模型

    【字段说明】
    - path: str - 要写入的文件路径，必须是相对路径
    - content: str - 要写入的文本内容
    - mode: Literal["overwrite", "append", "fail_if_exists"] - 写入模式
      - overwrite: 覆盖现有文件（默认）
      - append: 追加到文件末尾
      - fail_if_exists: 如果文件已存在则拒绝写入
    - backup: bool - 是否创建备份（默认 True）
    """
    model_config = ConfigDict(extra="ignore")
    path: str
    content: str
    mode: Literal["overwrite", "append", "fail_if_exists"] = "overwrite"
    backup: bool = True


class WriteFileTool(BaseTool, _WriteToolMixin):
    """
    文件写入工具 - 将文本内容写入指定文件

    【学习要点】
    1. 多重继承：同时继承 BaseTool 和 _WriteToolMixin
    2. 备份机制：在覆盖写入前自动创建备份文件
    3. 父目录创建：自动创建不存在的父目录
    4. 配额控制：检查单文件大小和总磁盘使用量

    【使用示例】
    ```python
    tool = WriteFileTool()
    
    # 覆盖写入
    result = await tool.invoke({
        "path": "src/main.py",
        "content": "print('Hello')",
        "mode": "overwrite"
    })
    
    # 追加写入
    result = await tool.invoke({
        "path": "src/main.py",
        "content": "\nprint('World')",
        "mode": "append"
    })
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 验证路径安全性（路径遍历、沙箱范围）
    3. 检查内容大小（最大 1MB）
    4. 检查配额（单文件和总配额）
    5. 如果存在且模式为 fail_if_exists，返回错误
    6. 如果需要备份，创建 .bak.<timestamp> 文件
    7. 创建父目录（如果不存在）
    8. 执行写入操作
    9. 返回成功结果
    """
    params_model = WriteFileParams
    name = "write_file"
    description = (
        "Write text content to a file, creating it (and any parent directories) if it "
        "does not exist. Supports three write modes: "
        "'overwrite' (default) replaces existing contents, "
        "'append' appends to the end of the file, "
        "'fail_if_exists' refuses to modify an existing file. "
        "When backup=True (default), existing files are copied to a '.bak.<timestamp>' "
        "sibling before any destructive write, enabling rollback. "
        "Path must be relative to the current working directory. "
        "Content size is limited to 1 MB per call."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            },
            "content": {
                "type": "string",
                "description": "Text content to write or append.",
            },
            "mode": {
                "type": "string",
                "enum": list(_WRITE_MODES),
                "default": "overwrite",
                "description": (
                    "How to treat an existing destination: "
                    "'overwrite' replaces it, "
                    "'append' concatenates, "
                    "'fail_if_exists' aborts."
                ),
            },
            "backup": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true and the file already exists, copy it to "
                    "<file>.bak.<UTC-ts> before writing."
                ),
            },
        },
        "required": ["path", "content"],
    }

    async def estimate_affected_paths(self, params: dict[str, object]) -> list[str]:
        """
        估算受影响的路径 - 用于权限预检查

        【返回值】
        - list[str]: 包含目标文件路径和备份文件路径（如果需要备份）
        """
        p = WriteFileParams.model_validate(params)
        target = _validate_rel_path(p.path, "write")
        affected: list[str] = [str(target)]
        # 如果文件存在、需要备份且不是追加模式，添加备份路径
        if target.exists() and p.backup and p.mode != "append":
            backup_path = _backup_destination(target)
            affected.append(str(backup_path))
        return affected

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行文件写入操作

        【执行流程详解】
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 验证输入参数（Pydantic）                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 2. 验证路径安全性（路径遍历、沙箱范围）                        │
        ├─────────────────────────────────────────────────────────────┤
        │ 3. 检查内容大小（最大 1MB）                                    │
        ├─────────────────────────────────────────────────────────────┤
        │ 4. 检查配额（单文件和总配额）                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 5. 如果存在且模式为 fail_if_exists → 返回错误                 │
        ├─────────────────────────────────────────────────────────────┤
        │ 6. 如果需要备份 → 创建 .bak.<timestamp> 文件                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 7. 创建父目录（如果不存在）                                    │
        ├─────────────────────────────────────────────────────────────┤
        │ 8. 执行写入操作（append 或 overwrite）                         │
        ├─────────────────────────────────────────────────────────────┤
        │ 9. 返回成功结果                                              │
        └─────────────────────────────────────────────────────────────┘
        """
        # 1. 验证输入参数
        p = WriteFileParams.model_validate(params)
        logger.debug("write_file: start path=%s mode=%s content_len=%d", p.path, p.mode, len(p.content))

        # 2. 验证路径安全性
        # 注意：PermissionError 直接抛出，不包装为 ToolResult，
        # 这样 invoke_tool 可以统一检测路径遍历尝试并发布事件
        path = _validate_rel_path(p.path, "write")
        logger.debug("write_file: path validated -> %s", path)

        # 3. 检查内容大小（阈值从全局配置读取）
        encoded = p.content.encode("utf-8")
        max_b = _max_bytes()
        if len(encoded) > max_b:
            return ToolResult(
                content=f"content too large: {len(encoded)} bytes (limit {max_b} bytes)",
                is_error=True,
                error_type="runtime_error",
            )

        # 4. 检查配额
        try:
            check_file_size(encoded)  # 检查单文件大小限制
            logger.debug("write_file: check_file_size OK")
            check_total_quota(len(encoded))  # 检查总磁盘使用量
            logger.debug("write_file: check_total_quota OK")
        except ValueError as exc:
            return ToolResult(
                content=str(exc),
                is_error=True,
                error_type="runtime_error",
            )

        # 5. 处理 fail_if_exists 模式
        exists = path.exists()
        if exists and p.mode == "fail_if_exists":
            return ToolResult(
                content=f"refuse to overwrite existing file (mode=fail_if_exists): {p.path}",
                is_error=True,
                error_type="runtime_error",
            )

        # 6. 创建备份（如果需要）
        backup_path: Path | None = None
        if exists and p.backup and p.mode != "append":
            try:
                backup_path = _backup_destination(path)
                shutil.copy2(path, backup_path)  # 保留元数据的复制
            except OSError as exc:
                return ToolResult(
                    content=f"failed to create backup for {p.path}: {exc}",
                    is_error=True,
                    error_type="runtime_error",
                )

        # 7. 执行写入操作
        try:
            # 创建父目录（如果不存在）
            path.parent.mkdir(parents=True, exist_ok=True)
            logger.debug("write_file: parent dir ready, writing...")

            # 根据模式执行写入
            if p.mode == "append":
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(p.content)
            else:
                # overwrite 模式
                path.write_text(p.content, encoding="utf-8")
            logger.debug("write_file: write complete -> %s", path)
        except OSError as exc:
            return ToolResult(
                content=f"write failed for {p.path}: {exc}",
                is_error=True,
                error_type="runtime_error",
            )

        # 8. 构建成功消息
        msg_bits = [f"wrote {len(encoded)} bytes to {p.path} (mode={p.mode})"]
        if backup_path is not None:
            msg_bits.append(f"backup={backup_path.name}")
        logger.debug("write_file: done, returning result")
        return ToolResult(content="; ".join(msg_bits))


def _backup_destination(path: Path) -> Path:
    """
    生成备份文件路径

    【参数说明】
    - path: Path - 原文件路径

    【返回值】
    - Path: 备份文件路径，格式为 <原文件名>.bak.<UTC时间戳>

    【时间戳格式】
    YYYYMMDDTHHMMSSffffffZ
    - YYYY: 年份
    - MM: 月份
    - DD: 日期
    - T: 时间分隔符
    - HH: 小时
    - MM: 分钟
    - SS: 秒
    - ffffff: 微秒
    - Z: UTC 时区标记

    【示例】
    src/main.py → src/main.py.bak.20240115T103045123456Z
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f"{path.name}.bak.{ts}")
