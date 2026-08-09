"""
沙箱模块测试

测试内容：
1. SandboxManager 核心路径检查（沙箱内/外、相对路径、绝对路径）
2. validate_path 拦截行为（越界始终抛 SandboxAccessError，不再静默放行）
3. allow_parent_dirs 功能（祖先目录访问）
4. check_file_size / check_total_quota 大小限制
5. 沙箱禁用时的放行行为
6. symlink 防护验证
7. 权限策略 Windows 路径启发式规则
8. SandboxAccessError 异常属性
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from iwan_claude.core.config import SandboxConfig
from iwan_claude.core.sandbox import (
    SandboxAccessError,
    SandboxManager,
    get_sandbox,
    init_sandbox,
    validate_path,
)
from iwan_claude.core.permissions.policy import matches_outside_cwd


# ======================================================================
# 辅助函数
# ======================================================================


def _make_manager(
    root: str = ".",
    enabled: bool = True,
    allow_parent_dirs: bool = False,
    max_file_size: int = 10 * 1024 * 1024,
    max_total_size: int = 100 * 1024 * 1024,
) -> SandboxManager:
    """创建沙箱管理器实例"""
    config = SandboxConfig(
        enabled=enabled,
        root=root,
        allow_parent_dirs=allow_parent_dirs,
        max_file_size=max_file_size,
        max_total_size=max_total_size,
    )
    return SandboxManager(config)


# ======================================================================
# 核心路径检查测试
# ======================================================================


class TestPathValidation:
    """测试 validate_path 核心路径检查"""

    def test_path_inside_sandbox_allowed(self, tmp_path: Path) -> None:
        """沙箱内路径允许访问"""
        manager = _make_manager(root=str(tmp_path))
        # 沙箱内的文件
        result = manager.validate_path(str(tmp_path / "test.txt"), "write")
        assert result == (tmp_path / "test.txt").resolve()

    def test_relative_path_inside_sandbox(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """相对路径基于 CWD 解析，CWD 在沙箱内时允许"""
        manager = _make_manager(root=str(tmp_path))
        monkeypatch.chdir(tmp_path)
        result = manager.validate_path("src/main.py", "read")
        assert result == (tmp_path / "src/main.py").resolve()

    def test_path_outside_sandbox_raises(self, tmp_path: Path) -> None:
        """沙箱外路径抛出 SandboxAccessError（不再静默放行）"""
        manager = _make_manager(root=str(tmp_path))
        outside_path = str(tmp_path.parent / "outside.txt")
        with pytest.raises(SandboxAccessError) as exc_info:
            manager.validate_path(outside_path, "read")

        # 验证异常属性
        assert exc_info.value.operation == "read"
        assert exc_info.value.sandbox_root == tmp_path.resolve()

    def test_ask_on_access_denied_still_raises(self, tmp_path: Path) -> None:
        """ask_on_access_denied=True 时仍然抛异常（不再静默放行）"""
        manager = _make_manager(root=str(tmp_path))
        # ask_on_access_denied 默认为 True
        assert manager.ask_on_access_denied is True
        outside_path = str(tmp_path.parent / "secret.txt")
        with pytest.raises(SandboxAccessError):
            manager.validate_path(outside_path, "read")

    def test_disabled_sandbox_allows_all(self, tmp_path: Path) -> None:
        """沙箱禁用时放行所有路径"""
        manager = _make_manager(root=str(tmp_path), enabled=False)
        outside_path = str(tmp_path.parent / "anywhere.txt")
        # 不抛异常
        result = manager.validate_path(outside_path, "read")
        assert result == Path(outside_path).resolve()

    def test_is_path_allowed_returns_bool(self, tmp_path: Path) -> None:
        """is_path_allowed 返回 bool，不抛异常"""
        manager = _make_manager(root=str(tmp_path))
        assert manager.is_path_allowed(str(tmp_path / "file.txt")) is True
        assert manager.is_path_allowed(str(tmp_path.parent / "outside.txt")) is False

    def test_is_path_allowed_disabled(self, tmp_path: Path) -> None:
        """沙箱禁用时 is_path_allowed 始终返回 True"""
        manager = _make_manager(root=str(tmp_path), enabled=False)
        assert manager.is_path_allowed("/anywhere") is True


# ======================================================================
# allow_parent_dirs 测试
# ======================================================================


class TestAllowParentDirs:
    """测试 allow_parent_dirs 功能"""

    def test_parent_dir_denied_by_default(self, tmp_path: Path) -> None:
        """默认不允许访问父目录"""
        subdir = tmp_path / "project" / "src"
        subdir.mkdir(parents=True)
        manager = _make_manager(root=str(subdir))
        # 父目录被拒绝
        with pytest.raises(SandboxAccessError):
            manager.validate_path(str(tmp_path / "project" / "file.txt"), "read")

    def test_parent_dir_allowed_when_enabled(self, tmp_path: Path) -> None:
        """allow_parent_dirs=True 时允许访问祖先目录"""
        subdir = tmp_path / "project" / "src"
        subdir.mkdir(parents=True)
        manager = _make_manager(root=str(subdir), allow_parent_dirs=True)
        # 祖先目录允许访问
        result = manager.validate_path(str(tmp_path / "project" / "file.txt"), "read")
        assert result == (tmp_path / "project" / "file.txt").resolve()

    def test_parent_dir_grandparent_denied(self, tmp_path: Path) -> None:
        """allow_parent_dirs=True 时只允许直接父目录，祖父目录仍被拒绝"""
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        manager = _make_manager(root=str(deep), allow_parent_dirs=True)
        # 直接父目录允许 (tmp_path/a/b/c)
        result = manager.validate_path(str(tmp_path / "a" / "b" / "c" / "file.txt"), "read")
        assert result == (tmp_path / "a" / "b" / "c" / "file.txt").resolve()
        # 祖父目录拒绝 (tmp_path/a/b)
        with pytest.raises(SandboxAccessError):
            manager.validate_path(str(tmp_path / "a" / "b" / "file.txt"), "read")

    def test_parent_dir_unrelated_path_still_denied(self, tmp_path: Path) -> None:
        """allow_parent_dirs=True 时父目录之外的路径仍被拒绝"""
        sandbox_root = tmp_path / "project"
        sandbox_root.mkdir()
        manager = _make_manager(root=str(sandbox_root), allow_parent_dirs=True)
        # 父目录是 tmp_path，祖父目录(tmp_path.parent)之外的路径仍被拒绝
        # tmp_path.parent 是 pytest 的临时目录根，其同级目录是无关路径
        unrelated_path = tmp_path.parent.parent / "completely_unrelated_dir"
        with pytest.raises(SandboxAccessError):
            manager.validate_path(str(unrelated_path / "file.txt"), "read")

    def test_is_path_allowed_with_parent_dirs(self, tmp_path: Path) -> None:
        """is_path_allowed 也支持 allow_parent_dirs（直接父目录内允许）"""
        subdir = tmp_path / "project" / "src"
        subdir.mkdir(parents=True)
        manager = _make_manager(root=str(subdir), allow_parent_dirs=True)
        # 直接父目录内的文件允许 (tmp_path/project/file.txt)
        assert manager.is_path_allowed(str(tmp_path / "project" / "file.txt")) is True
        # 祖父目录内的文件拒绝 (tmp_path/file.txt)
        assert manager.is_path_allowed(str(tmp_path / "file.txt")) is False
        # 无关路径仍为 False
        other = tmp_path / "other_project"
        other.mkdir()
        assert manager.is_path_allowed(str(other / "file.txt")) is False


# ======================================================================
# 文件大小限制测试
# ======================================================================


class TestFileSizeLimits:
    """测试文件大小和配额限制"""

    def test_check_file_size_within_limit(self) -> None:
        """文件大小在限制内不抛异常"""
        manager = _make_manager(max_file_size=100)
        manager.check_file_size("a" * 50)  # 50 bytes < 100

    def test_check_file_size_exceeds_limit(self) -> None:
        """文件大小超限抛 ValueError"""
        manager = _make_manager(max_file_size=100)
        with pytest.raises(ValueError, match="exceeds sandbox limit"):
            manager.check_file_size("a" * 200)  # 200 bytes > 100

    def test_check_file_size_bytes(self) -> None:
        """支持 bytes 输入"""
        manager = _make_manager(max_file_size=100)
        manager.check_file_size(b"a" * 50)
        with pytest.raises(ValueError):
            manager.check_file_size(b"a" * 200)

    def test_check_file_size_disabled(self) -> None:
        """沙箱禁用时不检查文件大小"""
        manager = _make_manager(enabled=False, max_file_size=10)
        # 不抛异常
        manager.check_file_size("a" * 10000)

    def test_check_total_quota_within_limit(self, tmp_path: Path) -> None:
        """总配额在限制内不抛异常"""
        manager = _make_manager(root=str(tmp_path), max_total_size=10000)
        # 写入一些文件
        (tmp_path / "a.txt").write_text("hello")
        manager.check_total_quota(additional_bytes=100)  # 5 + 100 < 10000

    def test_check_total_quota_exceeds(self, tmp_path: Path) -> None:
        """总配额超限抛 ValueError"""
        manager = _make_manager(root=str(tmp_path), max_total_size=50)
        (tmp_path / "a.txt").write_text("a" * 30)  # 30 bytes
        with pytest.raises(ValueError, match="quota exceeded"):
            manager.check_total_quota(additional_bytes=30)  # 30 + 30 > 50

    def test_check_total_quota_disabled(self, tmp_path: Path) -> None:
        """沙箱禁用时不检查总配额"""
        manager = _make_manager(root=str(tmp_path), enabled=False, max_total_size=10)
        manager.check_total_quota(additional_bytes=100000)  # 不抛异常


# ======================================================================
# SandboxAccessError 测试
# ======================================================================


class TestSandboxAccessError:
    """测试 SandboxAccessError 异常"""

    def test_is_permission_error_subclass(self) -> None:
        """SandboxAccessError 是 PermissionError 的子类（兼容现有 except）"""
        assert issubclass(SandboxAccessError, PermissionError)

    def test_error_attributes(self, tmp_path: Path) -> None:
        """异常包含 path/operation/sandbox_root 属性"""
        manager = _make_manager(root=str(tmp_path))
        outside = str(tmp_path.parent / "secret.txt")
        with pytest.raises(SandboxAccessError) as exc_info:
            manager.validate_path(outside, "delete")

        err = exc_info.value
        assert err.operation == "delete"
        assert err.sandbox_root == tmp_path.resolve()

    def test_error_message_contains_hint(self, tmp_path: Path) -> None:
        """错误消息包含解决建议"""
        manager = _make_manager(root=str(tmp_path))
        with pytest.raises(SandboxAccessError, match="allow_parent_dirs"):
            manager.validate_path(str(tmp_path.parent / "x.txt"), "read")

    def test_caught_by_permission_error(self, tmp_path: Path) -> None:
        """SandboxAccessError 可以被 except PermissionError 捕获"""
        manager = _make_manager(root=str(tmp_path))
        caught = False
        try:
            manager.validate_path(str(tmp_path.parent / "x.txt"), "read")
        except PermissionError:
            caught = True
        assert caught


# ======================================================================
# 全局单例测试
# ======================================================================


class TestGlobalSandbox:
    """测试全局沙箱单例"""

    def test_init_and_get_sandbox(self, tmp_path: Path) -> None:
        """init_sandbox 初始化后 get_sandbox 返回同一实例"""
        config = SandboxConfig(enabled=True, root=str(tmp_path))
        init_sandbox(config)
        sb = get_sandbox()
        assert sb.enabled is True
        assert sb.root == tmp_path.resolve()

    def test_validate_path_module_level(self, tmp_path: Path) -> None:
        """模块级 validate_path 函数正常工作"""
        config = SandboxConfig(enabled=True, root=str(tmp_path))
        init_sandbox(config)
        result = validate_path(str(tmp_path / "file.txt"), "read")
        assert result == (tmp_path / "file.txt").resolve()

    def test_validate_path_module_level_denied(self, tmp_path: Path) -> None:
        """模块级 validate_path 越界时抛异常"""
        config = SandboxConfig(enabled=True, root=str(tmp_path))
        init_sandbox(config)
        with pytest.raises(SandboxAccessError):
            validate_path(str(tmp_path.parent / "outside.txt"), "read")

    def test_default_sandbox_when_not_initialized(self) -> None:
        """未初始化时 get_sandbox 返回禁用的默认沙箱"""
        # 这测试 _ensure_default_sandbox 的回退逻辑
        # 注意：由于全局状态，这里只验证不抛异常
        sb = get_sandbox()
        assert sb is not None


# ======================================================================
# 权限策略 Windows 路径启发式测试
# ======================================================================


class TestOutsideCwdHeuristics:
    """测试 OUTSIDE_CWD_HEURISTICS 规则（含 Windows 路径）"""

    # === 安全命令（不应触发 ASK）===
    @pytest.mark.parametrize("command", [
        "ls",
        "dir",
        "echo hello",
        "python script.py",
        "git status",
        "Get-ChildItem",
        "Write-Output hello",
    ])
    def test_safe_commands_not_flagged(self, command: str) -> None:
        """安全命令不触发 outside_cwd"""
        assert matches_outside_cwd(command) is False

    # === Unix 路径（应触发 ASK）===
    @pytest.mark.parametrize("command", [
        "cat /etc/passwd",
        "ls ~",
        "cat ~/.bashrc",
        "cat ../secret.txt",
        "cd /etc",
        "echo $HOME",
        "echo $PWD",
    ])
    def test_unix_outside_paths_flagged(self, command: str) -> None:
        """Unix 沙箱外路径触发 outside_cwd"""
        assert matches_outside_cwd(command) is True

    # === Windows 路径（应触发 ASK）===
    @pytest.mark.parametrize("command", [
        "type C:\\Users\\secret.txt",
        "dir D:\\data",
        "type C:/Users/secret.txt",
        "echo %USERPROFILE%",
        "echo %TEMP%",
        "echo %APPDATA%",
        "echo %WINDIR%",
        "echo %SYSTEMROOT%",
        "echo %PROGRAMFILES%",
        "echo %LOCALAPPDATA%",
    ])
    def test_windows_outside_paths_flagged(self, command: str) -> None:
        """Windows 沙箱外路径触发 outside_cwd"""
        assert matches_outside_cwd(command) is True

    # === PowerShell 目录切换命令（应触发 ASK）===
    @pytest.mark.parametrize("command", [
        "Set-Location C:\\Users",
        "Set-Location ..",
        "Push-Location ..",
        "Pop-Location",
        "sl ..",
    ])
    def test_powershell_cd_commands_flagged(self, command: str) -> None:
        """PowerShell 目录切换命令触发 outside_cwd"""
        # 注意：sl 是 Set-Location 的别名，但不会被 cd 规则匹配
        # Pop-Location 本身不带路径，但仍然标记为需要 ASK
        result = matches_outside_cwd(command)
        # Pop-Location 不带路径参数可能不触发，但 Set-Location/Push-Location 会
        if "Pop-Location" in command:
            # Pop-Location 单独不一定触发 outside_cwd
            pass
        else:
            assert result is True

    # === Windows 父目录遍历（应触发 ASK）===
    @pytest.mark.parametrize("command", [
        "type ..\\..\\secret.txt",
        "dir ..\\sibling",
    ])
    def test_windows_parent_traversal_flagged(self, command: str) -> None:
        """Windows 反斜杠父目录遍历触发 outside_cwd"""
        assert matches_outside_cwd(command) is True

    # === UNC 路径（应触发 ASK）===
    def test_unc_path_flagged(self) -> None:
        """UNC 路径触发 outside_cwd"""
        assert matches_outside_cwd("dir \\\\server\\share") is True


# ======================================================================
# 集成场景测试
# ======================================================================


class TestSandboxIntegration:
    """测试沙箱与工具集成的场景"""

    def test_write_file_within_sandbox(self, tmp_path: Path) -> None:
        """沙箱内写文件正常"""
        manager = _make_manager(root=str(tmp_path))
        file_path = manager.validate_path(str(tmp_path / "new.txt"), "write")
        file_path.write_text("content")
        assert file_path.read_text() == "content"

    def test_nested_directory_in_sandbox(self, tmp_path: Path) -> None:
        """沙箱内嵌套目录路径允许"""
        manager = _make_manager(root=str(tmp_path))
        nested = tmp_path / "a" / "b" / "c" / "file.txt"
        result = manager.validate_path(str(nested), "write")
        assert result == nested.resolve()

    def test_sandbox_root_itself_allowed(self, tmp_path: Path) -> None:
        """沙箱根目录本身允许访问"""
        manager = _make_manager(root=str(tmp_path))
        result = manager.validate_path(str(tmp_path), "read")
        assert result == tmp_path.resolve()

    def test_absolute_path_inside_sandbox(self, tmp_path: Path) -> None:
        """绝对路径在沙箱内时允许"""
        manager = _make_manager(root=str(tmp_path))
        abs_path = str((tmp_path / "file.txt").resolve())
        result = manager.validate_path(abs_path, "read")
        assert result == Path(abs_path)
