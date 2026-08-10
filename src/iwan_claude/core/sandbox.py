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


# 计算沙箱总配额时，需要排除的常见大体积非项目源文件目录
# 【原因】
#   sandbox_root 默认等于 CWD（项目根），项目目录里除了源码还有很多"环境产物"，
#   如虚拟环境 .venv（>100MB）、Git 历史 .git（>10MB）、Python 字节码缓存等，
#   这些都是开发者环境的一部分，不应该算到"Agent 写入沙箱"的配额里。
#   如果不排除，随便一个新项目配 100MB quota 都会立即超限。
_QUOTA_EXCLUDE_DIR_NAMES: frozenset[str] = frozenset(
    {
        # --- Python 生态 ---
        ".venv", "venv",                 # 虚拟环境（常见 > 100MB）
        "env", ".env_root",
        "__pycache__",                   # 字节码缓存
        ".pytest_cache",                 # pytest 缓存
        ".mypy_cache", ".pytype",        # 类型检查缓存
        ".ruff_cache",
        "site-packages",
        # --- 版本管理 ---
        ".git", ".svn", ".hg",
        # --- 前端生态 ---
        "node_modules",                  # 前端依赖（常见 > 500MB）
        ".next", ".nuxt", ".cache",
        "dist", "build",
        # --- IDE / 工具 ---
        ".idea", ".vscode",
        ".tox", ".nox",
        "__pycache__",
        # --- 本项目自身 ---
        ".iwan",                         # 项目本地：rag_index / sessions / audit.log 等
    }
)
# 排除的大体积文件扩展名（一般是二进制产物、数据库、日志轮转）
_QUOTA_EXCLUDE_SUFFIXES: frozenset[str] = frozenset(
    {".pyc", ".pyo", ".so", ".dll", ".pyd", ".o", ".a", ".lib", ".exe", ".log"}
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
        # 记录初始化根目录，便于后续判断是否是默认根
        self._default_root = self._sandbox_root

    def set_root(self, new_root: str | Path) -> None:
        """
        动态切换沙箱根目录（会话级隔离）

        【使用场景】
        每个会话可绑定不同的项目目录，类似 Claude Code 的 workspace 概念。
        - TUI 在 D:/project-a 启动 → 会话 A 沙箱根 = D:/project-a
        - TUI 在 E:/project-b 启动 → 会话 B 沙箱根 = E:/project-b
        - 切换会话时自动切换沙箱根

        【参数】
        - new_root: str | Path - 新的沙箱根目录路径

        【设计说明】
        - 路径会被 resolve() 规范化，消除 .. 和 symlink
        - 新根目录不存在时自动创建
        - 切换后立即生效，无需重启
        """
        self._sandbox_root = Path(new_root).resolve()
        self.ensure_sandbox_exists()
        import logging
        logging.getLogger(__name__).info(
            "sandbox root changed: %s", self._sandbox_root,
        )

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

    # ===== 进程内强化属性 =====

    @property
    def command_blacklist(self) -> list[str]:
        """命令黑名单正则列表（命中则硬 DENY）"""
        return self._config.command_blacklist

    @property
    def env_scrub_patterns(self) -> list[str]:
        """环境变量脱敏正则列表（匹配变量名）"""
        return self._config.env_scrub_patterns

    @property
    def block_network_commands(self) -> bool:
        """是否阻断网络外传命令（curl/wget/nc/ssh 等）"""
        return self._config.block_network_commands

    @property
    def audit_log_enabled(self) -> bool:
        """是否启用审计日志"""
        return self._config.audit_log

    @property
    def audit_log_path(self) -> str:
        """审计日志文件路径（相对路径基于 CWD）"""
        return self._config.audit_log_path

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
        获取沙箱目录当前已使用的总字节数（排除非项目源目录）

        【排除策略】
        - 目录级：跳过 .venv / .git / __pycache__ / node_modules 等环境产物
        - 后缀级：跳过 .pyc / .so / .dll / .log 等二进制/日志产物
        - 剪枝：os.walk topdown=True，从 dirnames 原地移除被排除项，避免进入子树

        【返回】
        - int: 沙箱内"用户/Agent真正写入的源码文件"的总大小（字节）
        """
        total = 0
        if not self._sandbox_root.exists() or not self._sandbox_root.is_dir():
            return 0

        # topdown=True：先拿到当前目录的子目录列表，再原地修改 dirnames 剪枝
        # os.walk 会跳过被移除的目录，提高 scan 速度，避免 .venv 里的几十万文件
        for dirpath, dirnames, filenames in os.walk(
            self._sandbox_root, topdown=True, followlinks=False
        ):
            # --- 子目录剪枝：原地移除需要排除的目录名 ---
            # 注意：必须用 dirnames[:] 切片赋值来修改"同一列表对象"，否则 os.walk 感知不到
            dirnames[:] = [
                d for d in dirnames if d not in _QUOTA_EXCLUDE_DIR_NAMES
            ]
            # --- 统计文件 ---
            for filename in filenames:
                # 跳过大体积后缀（如 .pyc 字节码）
                if Path(filename).suffix.lower() in _QUOTA_EXCLUDE_SUFFIXES:
                    continue
                filepath = Path(dirpath) / filename
                try:
                    st = filepath.stat()
                except OSError:
                    continue
                total += st.st_size
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
        limit = self._config.max_total_size
        if current + additional_bytes > limit:
            # --- 错误提示增强 ---
            # 1) current 异常大（>5GB 基本意味着剪枝还不够或 sandbox_root 指错到了用户目录）
            #    给出额外提示，不要只报天文数字让用户困惑
            extras: list[str] = []
            if current > 5 * 1024 * 1024 * 1024:  # > 5GB
                extras.append(
                    f"[warn] current_size is abnormally large ({current / (1024**3):.1f} GiB); "
                    "sandbox root may not be pointing at your project dir, or excludes need tuning"
                )
            # 2) 标准修复建议
            fix = (
                "Fix: increase [sandbox].max_total_size in D:\\IwanClaude\\.iwan\\config.toml "
                "or set env IWAN_SANDBOX_MAX_TOTAL_SIZE=3000000000 then restart core."
            )
            msg = (
                f"sandbox quota exceeded: current {current} + {additional_bytes} bytes "
                f"> limit {limit} bytes"
            )
            if extras:
                msg += "\n  " + "\n  ".join(extras)
            msg += "\n  " + fix
            raise ValueError(msg)

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
    # 关键日志：启动时明确记录沙箱根目录，便于排查 CWD 错误导致的配额问题
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        "sandbox initialized: root=%s  enabled=%s  max_total_size=%d",
        _sandbox_manager.root, config.enabled, config.max_total_size,
    )


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


def set_sandbox_root(new_root: str | Path) -> None:
    """
    动态切换全局沙箱根目录

    【使用场景】
    会话切换时调用此函数，将沙箱根目录绑定到新会话的项目目录。
    这是实现多会话多项目隔离的核心入口。

    【参数】
    - new_root: str | Path - 新的沙箱根目录路径（通常来自 TUI 启动时的 CWD）

    【示例】
    # TUI 在 D:/my-project 启动时
    set_sandbox_root("D:\\my-project")
    # 之后所有文件操作都会限制在 D:/my-project 内
    """
    get_sandbox().set_root(new_root)


def scrub_env(env: dict[str, str]) -> dict[str, str]:
    """
    脱敏环境变量：移除匹配 env_scrub_patterns 的敏感变量

    【安全目的】
    防止 API key、密码等敏感凭证泄露给 bash/run_python 子进程。
    恶意命令可通过 `env`、`echo $ANTHROPIC_API_KEY`、`printenv` 读取环境变量。

    【参数】
    - env: 原始环境变量字典（通常是 os.environ.copy()）

    【返回】
    - dict[str, str]: 脱敏后的环境变量字典（原 dict 不被修改）

    【设计要点】
    - 沙箱未启用时直接返回原 env（不脱敏）
    - 匹配规则按变量名（key）正则匹配，不检查 value
    - 被移除的变量名记录到审计日志（不记录值）
    - 使用延迟导入 audit 模块，避免循环导入
    """
    import re

    # 沙箱未初始化或未启用时，不脱敏
    if _sandbox_manager is None or not _sandbox_manager.enabled:
        return env

    patterns = _sandbox_manager.env_scrub_patterns
    if not patterns:
        return env

    compiled = [re.compile(p) for p in patterns]
    scrubbed: dict[str, str] = {}
    removed: list[str] = []
    for key, value in env.items():
        if any(pat.search(key) for pat in compiled):
            removed.append(key)
        else:
            scrubbed[key] = value

    # 记录审计日志（仅记录变量名，不记录值）
    if removed:
        try:
            from iwan_claude.core.audit import log_env_scrub
            log_env_scrub(removed_keys=removed)
        except Exception:
            # 审计日志写入失败不影响主流程
            pass

    return scrubbed
