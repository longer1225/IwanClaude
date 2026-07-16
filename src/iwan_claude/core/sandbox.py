from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from iwan_claude.core.config import SandboxConfig


class SandboxManager:
    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._sandbox_root = Path(config.root).resolve()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def root(self) -> Path:
        return self._sandbox_root

    @property
    def max_file_size(self) -> int:
        return self._config.max_file_size

    @property
    def max_total_size(self) -> int:
        return self._config.max_total_size

    @property
    def ask_on_access_denied(self) -> bool:
        return self._config.ask_on_access_denied

    def is_path_allowed(self, path_str: str) -> bool:
        if not self._config.enabled:
            return True

        path = Path(path_str)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()

        try:
            path.relative_to(self._sandbox_root)
            return True
        except ValueError:
            return False

    def validate_path(self, path_str: str, operation: str = "access") -> Path:
        if not self._config.enabled:
            return Path(path_str)

        path = Path(path_str)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()

        try:
            path.relative_to(self._sandbox_root)
            return path
        except ValueError:
            if self._config.ask_on_access_denied:
                return path
            else:
                raise PermissionError(
                    f"sandbox access denied: {operation} path '{path_str}' is outside sandbox root '{self._sandbox_root}'"
                )

    def check_file_size(self, content: str | bytes) -> None:
        if not self._config.enabled:
            return

        size = len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))
        if size > self._config.max_file_size:
            raise ValueError(
                f"file size {size} bytes exceeds sandbox limit of {self._config.max_file_size} bytes"
            )

    def get_total_used(self) -> int:
        total = 0
        if not self._sandbox_root.exists() or not self._sandbox_root.is_dir():
            return 0

        for dirpath, _dirnames, filenames in os.walk(self._sandbox_root):
            for filename in filenames:
                filepath = Path(dirpath) / filename
                try:
                    total += filepath.stat().st_size
                except OSError:
                    continue
        return total

    def check_total_quota(self, additional_bytes: int = 0) -> None:
        if not self._config.enabled:
            return

        current = self.get_total_used()
        if current + additional_bytes > self._config.max_total_size:
            raise ValueError(
                f"sandbox quota exceeded: current {current} + {additional_bytes} bytes > limit {self._config.max_total_size} bytes"
            )

    def ensure_sandbox_exists(self) -> None:
        if not self._sandbox_root.exists():
            self._sandbox_root.mkdir(parents=True, exist_ok=True)


_sandbox_manager: Optional[SandboxManager] = None


def init_sandbox(config: SandboxConfig) -> None:
    global _sandbox_manager
    _sandbox_manager = SandboxManager(config)
    _sandbox_manager.ensure_sandbox_exists()


def _ensure_default_sandbox() -> None:
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = SandboxManager(SandboxConfig(enabled=False))


def get_sandbox() -> SandboxManager:
    _ensure_default_sandbox()
    return _sandbox_manager


def is_path_allowed(path_str: str) -> bool:
    return get_sandbox().is_path_allowed(path_str)


def validate_path(path_str: str, operation: str = "access") -> Path:
    return get_sandbox().validate_path(path_str, operation)


def check_file_size(content: str | bytes) -> None:
    get_sandbox().check_file_size(content)


def check_total_quota(additional_bytes: int = 0) -> None:
    get_sandbox().check_total_quota(additional_bytes)


def get_search_root() -> Path:
    sb = get_sandbox()
    if sb._config.search_limited and sb._config.enabled:
        return sb._sandbox_root
    return Path.cwd()