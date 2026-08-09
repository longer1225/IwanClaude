"""
沙箱模块 - 路径级别的文件访问控制

【学习要点】
1. 路径白名单：检查文件路径是否在 sandbox_root 内，非 OS 级隔离
2. resolve() 防 symlink 逃逸：先解析真实路径再判断，symlink 指向沙箱外会被拦截
3. allow_parent_dirs：允许访问 sandbox_root 的祖先目录（monorepo 场景）
4. 单例模式：模块级 _sandbox_manager，init_sandbox() 初始化，get_sandbox() 获取

【安全模型】
沙箱根 = 项目工作目录（CWD），Agent 可操作项目文件，但不能越界到 ~/.ssh、/etc 等。
- validate_path() 在路径越界时抛出 PermissionError（不再静默放行）
- ask_on_access_denied 控制错误提示措辞，但不改变拦截行为
- 权限系统（permissions/）在工具执行前做工具级检查，沙箱在工具内部做路径级检查

【与权限系统的分工】
- 权限系统：工具级审批（bash 命令是否危险 → ASK 用户）
- 沙箱：路径级强制拦截（文件是否在项目目录内 → 硬阻断）
两者互补：权限系统是"门卫"，沙箱是"围墙"
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from iwan_claude.core.config import SandboxConfig


class SandboxAccessError(PermissionError):
    """
    沙箱访问拒绝异常

    当路径在沙箱外且未被 allow_parent_dirs 允许时抛出。
    继承 PermissionError，兼容现有工具的 except PermissionError 捕获。

    【属性】
    - path: 被拒绝的路径
    - operation: 尝试的操作（read/write/delete 等）
    - sandbox_root: 沙箱根目录
    """

    def __init__(self, path: str, operation: str, sandbox_root: Path) -> None:
        self.path = path
        self.operation = operation
        self.sandbox_root = sandbox_root
        super().__init__(
            f"sandbox access denied: {operation} path '{path}' "
            f"is outside sandbox root '{sandbox_root}'. "
            f"If this access is intended, you can: "
            f"(1) move the file into the project directory, "
            f"(2) set sandbox.allow_parent_dirs=true in config, or "
            f"(3) set sandbox.enabled=false to disable sandbox."
        )


class SandboxManager:
    """
    沙箱管理器 - 管理路径级别的文件访问控制

    【核心职责】
    1. 路径检查：判断路径是否在沙箱内（或被 allow_parent_dirs 允许）
    2. 文件大小限制：单文件不超过 max_file_size
    3. 总配额限制：沙箱内所有文件总和不超过 max_total_size
    4. 搜索范围：search_limited 时限制搜索工具的根目录

    【路径检查逻辑】
    1. 路径 resolve() 解析真实路径（防 symlink 逃逸）
    2. 检查是否在 sandbox_root 内（relative_to 判断）
    3. 若 allow_parent_dirs=True，检查 sandbox_root 是否在路径内（祖先关系）
    """

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

    @property
    def allow_parent_dirs(self) -> bool:
        return self._config.allow_parent_dirs

    def _is_path_allowed(self, path: Path) -> bool:
        """
        内部方法：判断已 resolve 的路径是否被允许访问

        【判断逻辑】
        1. 路径在 sandbox_root 内 → 允许
        2. allow_parent_dirs=True 且路径在 sandbox_root 的父目录内 → 允许
           （monorepo 场景：sandbox_root=project/src，需要访问 project/shared）
        3. 其他 → 拒绝
        """
        # 检查路径是否在 sandbox_root 内
        try:
            path.relative_to(self._sandbox_root)
            return True
        except ValueError:
            pass

        # allow_parent_dirs：允许访问 sandbox_root 的父目录内的文件
        # 例如 sandbox_root=/project/src，父目录=/project，允许访问 /project/shared
        if self._config.allow_parent_dirs:
            try:
                path.relative_to(self._sandbox_root.parent)
                return True
            except ValueError:
                pass

        return False

    def is_path_allowed(self, path_str: str) -> bool:
        """
        判断路径是否在沙箱允许范围内（不抛异常）

        【参数】
        - path_str: 路径字符串（相对或绝对）

        【返回】
        - bool: True 表示允许访问，False 表示拒绝

        【注意】
        - 沙箱未启用时始终返回 True
        - 相对路径会基于 CWD 解析为绝对路径
        - symlink 会被 resolve() 解析为真实路径
        """
        if not self._config.enabled:
            return True

        path = Path(path_str)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()

        return self._is_path_allowed(path)

    def validate_path(self, path_str: str, operation: str = "access") -> Path:
        """
        验证路径是否在沙箱内，越界时抛出 SandboxAccessError

        【参数】
        - path_str: 路径字符串（相对或绝对）
        - operation: 操作类型（read/write/delete 等），用于错误信息

        【返回】
        - Path: resolve 后的绝对路径（在沙箱内时）

        【抛出】
        - SandboxAccessError: 路径在沙箱外时（始终抛出，不再静默放行）

        【注意】
        - 沙箱未启用时直接返回 resolve 后的路径
        - 相对路径基于 CWD 解析
        - symlink 被 resolve() 解析，防止 symlink 逃逸
        """
        if not self._config.enabled:
            path = Path(path_str)
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            else:
                path = path.resolve()
            return path

        path = Path(path_str)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()

        if self._is_path_allowed(path):
            return path

        # 路径越界，抛出异常（不再静默放行）
        raise SandboxAccessError(path_str, operation, self._sandbox_root)

    def check_file_size(self, content: str | bytes) -> None:
        """
        检查单个文件大小是否超过限制

        【参数】
        - content: 文件内容（字符串或字节）

        【抛出】
        - ValueError: 文件大小超过 max_file_size
        """
        if not self._config.enabled:
            return

        size = len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))
        if size > self._config.max_file_size:
            raise ValueError(
                f"file size {size} bytes exceeds sandbox limit of {self._config.max_file_size} bytes"
            )

    def get_total_used(self) -> int:
        """
        获取沙箱目录当前已使用的总字节数

        【返回】
        - int: 沙箱内所有文件的总大小（字节）
        """
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
        """
        检查写入 additional_bytes 后是否超过总配额

        【参数】
        - additional_bytes: 即将写入的字节数

        【抛出】
        - ValueError: 当前使用量 + 新增量超过 max_total_size
        """
        if not self._config.enabled:
            return

        current = self.get_total_used()
        if current + additional_bytes > self._config.max_total_size:
            raise ValueError(
                f"sandbox quota exceeded: current {current} + {additional_bytes} bytes > limit {self._config.max_total_size} bytes"
            )

    def ensure_sandbox_exists(self) -> None:
        """确保沙箱根目录存在（不存在则创建）"""
        if not self._sandbox_root.exists():
            self._sandbox_root.mkdir(parents=True, exist_ok=True)


# ======================================================================
# 模块级单例管理
# ======================================================================

_sandbox_manager: Optional[SandboxManager] = None


def init_sandbox(config: SandboxConfig) -> None:
    """
    初始化全局沙箱管理器

    在 AgentRunner.__init__ 中调用，用配置初始化沙箱。
    """
    global _sandbox_manager
    _sandbox_manager = SandboxManager(config)
    _sandbox_manager.ensure_sandbox_exists()


def _ensure_default_sandbox() -> None:
    """如果未初始化，创建一个禁用的默认沙箱（放行所有路径）"""
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = SandboxManager(SandboxConfig(enabled=False))


def get_sandbox() -> SandboxManager:
    """获取全局沙箱管理器实例"""
    _ensure_default_sandbox()
    return _sandbox_manager


# ======================================================================
# 便捷函数（委托给全局单例）
# ======================================================================


def is_path_allowed(path_str: str) -> bool:
    """判断路径是否在沙箱允许范围内（不抛异常）"""
    return get_sandbox().is_path_allowed(path_str)


def validate_path(path_str: str, operation: str = "access") -> Path:
    """
    验证路径是否在沙箱内，越界时抛出 SandboxAccessError

    所有文件操作工具在操作前应调用此函数。
    """
    return get_sandbox().validate_path(path_str, operation)


def check_file_size(content: str | bytes) -> None:
    """检查文件大小是否超过沙箱限制"""
    get_sandbox().check_file_size(content)


def check_total_quota(additional_bytes: int = 0) -> None:
    """检查总配额是否超限"""
    get_sandbox().check_total_quota(additional_bytes)


def get_search_root() -> Path:
    """
    获取搜索工具的根目录

    - search_limited=True 且沙箱启用 → 返回 sandbox_root
    - 否则 → 返回 CWD
    """
    sb = get_sandbox()
    if sb._config.search_limited and sb._config.enabled:
        return sb._sandbox_root
    return Path.cwd()
