from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iwan_claude.core.config import SandboxConfig
from iwan_claude.core.sandbox import (
    SandboxManager,
    check_file_size,
    check_total_quota,
    get_sandbox,
    init_sandbox,
    is_path_allowed,
    validate_path,
)


@pytest.fixture(autouse=True)
def reset_sandbox() -> None:
    import iwan_claude.core.sandbox as sb_module
    sb_module._sandbox_manager = None


class TestSandboxManager:
    def test_is_path_allowed(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path))
        sb = SandboxManager(config)

        assert sb.is_path_allowed(str(tmp_path / "test.txt"))
        assert sb.is_path_allowed(str(tmp_path / "subdir" / "file.txt"))
        assert not sb.is_path_allowed(str(tmp_path.parent / "outside.txt"))

    def test_is_path_allowed_disabled(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=False, root=str(tmp_path))
        sb = SandboxManager(config)

        assert sb.is_path_allowed(str(tmp_path / "test.txt"))
        assert sb.is_path_allowed(str(tmp_path.parent / "outside.txt"))

    def test_validate_path(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path))
        sb = SandboxManager(config)

        result = sb.validate_path(str(tmp_path / "test.txt"))
        assert result.is_absolute()

    def test_validate_path_denied(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path), ask_on_access_denied=False)
        sb = SandboxManager(config)

        with pytest.raises(PermissionError, match="sandbox access denied"):
            sb.validate_path(str(tmp_path.parent / "outside.txt"))

    def test_validate_path_denied_asks(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path), ask_on_access_denied=True)
        sb = SandboxManager(config)

        result = sb.validate_path(str(tmp_path.parent / "outside.txt"))
        assert result.is_absolute()

    def test_check_file_size(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path), max_file_size=100)
        sb = SandboxManager(config)

        sb.check_file_size("short content")

        with pytest.raises(ValueError, match="exceeds sandbox limit"):
            sb.check_file_size("x" * 200)

    def test_check_file_size_disabled(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=False, root=str(tmp_path), max_file_size=100)
        sb = SandboxManager(config)

        sb.check_file_size("x" * 200)

    def test_check_total_quota(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path), max_total_size=100)
        sb = SandboxManager(config)

        sb.check_total_quota(50)

        with pytest.raises(ValueError, match="quota exceeded"):
            sb.check_total_quota(150)

    def test_get_total_used(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path))
        sb = SandboxManager(config)

        (tmp_path / "test.txt").write_text("hello world")
        assert sb.get_total_used() == 11

    def test_ensure_sandbox_exists(self, tmp_path: Path) -> None:
        sandbox_dir = tmp_path / "sandbox"
        config = SandboxConfig(enabled=True, root=str(sandbox_dir))
        sb = SandboxManager(config)

        assert not sandbox_dir.exists()
        sb.ensure_sandbox_exists()
        assert sandbox_dir.exists()


class TestSandboxModuleFunctions:
    def test_init_and_get_sandbox(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path))
        init_sandbox(config)

        sb = get_sandbox()
        assert sb.root == tmp_path.resolve()

    def test_get_sandbox_not_initialized(self) -> None:
        with patch("iwan_claude.core.sandbox._sandbox_manager", None):
            sb = get_sandbox()
            assert not sb.enabled

    def test_is_path_allowed_function(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path))
        init_sandbox(config)

        assert is_path_allowed(str(tmp_path / "test.txt"))
        assert not is_path_allowed(str(tmp_path.parent / "outside.txt"))

    def test_validate_path_function(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path), ask_on_access_denied=False)
        init_sandbox(config)

        validate_path(str(tmp_path / "test.txt"))

        with pytest.raises(PermissionError):
            validate_path(str(tmp_path.parent / "outside.txt"))

    def test_validate_path_function_asks(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path), ask_on_access_denied=True)
        init_sandbox(config)

        validate_path(str(tmp_path / "test.txt"))
        result = validate_path(str(tmp_path.parent / "outside.txt"))
        assert result.is_absolute()

    def test_check_file_size_function(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path), max_file_size=100)
        init_sandbox(config)

        check_file_size("short")

        with pytest.raises(ValueError):
            check_file_size("x" * 200)

    def test_check_total_quota_function(self, tmp_path: Path) -> None:
        config = SandboxConfig(enabled=True, root=str(tmp_path), max_total_size=100)
        init_sandbox(config)

        check_total_quota(50)

        with pytest.raises(ValueError):
            check_total_quota(150)