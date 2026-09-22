"""git 工具沙箱路径校验与选项注入拒绝测试（任务 4）"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.config import SandboxConfig
from iwan_claude.core.sandbox import init_sandbox
from iwan_claude.core.tools.builtin.git import (
    GitCheckoutTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)


@pytest.fixture(autouse=True)
def reset_sandbox_state() -> Any:
    # 功能：每个测试前后清空沙箱全局单例与 contextvar
    # 设计：git 工具经 validate_path→get_sandbox 读取沙箱，若其他测试残留
    #       启用状态的会话沙箱会污染本文件的"未初始化=禁用"基线用例
    import iwan_claude.core.sandbox as sb_module
    sb_module._sandbox_manager = None
    token = sb_module._active_sandbox.set(None)
    sb_module._sandbox_by_session.clear()
    yield
    sb_module._active_sandbox.reset(token)
    sb_module._sandbox_manager = None
    sb_module._sandbox_by_session.clear()


class _FakeProc:
    """伪造 git 子进程：记录调用参数，立即返回成功"""

    def __init__(self, args: tuple[Any, ...]) -> None:
        self.args = args
        self.returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return (b"ok\n", b"")


# 记录被"执行"的 git 命令行，用于断言校验通过的调用确实走到了子进程阶段
_executed: list[tuple[Any, ...]] = []


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    # 功能：替换 asyncio.create_subprocess_exec，避免单测真的 fork git 进程
    # 设计：git 工具直接用 asyncio.create_subprocess_exec 这个名字；monkeypatch
    #       asyncio 模块属性即可全局拦截，并捕获 argv 供断言
    import asyncio

    _executed.clear()

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        _executed.append(args)
        return _FakeProc(args)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    return _executed


@pytest.fixture
def sandbox_proj(tmp_path: Path) -> Path:
    # 启用沙箱、根指向临时项目目录
    proj = tmp_path / "proj"
    proj.mkdir()
    init_sandbox(SandboxConfig(enabled=True, root=str(proj)))
    return proj


# ── 仓库路径越界拦截 ──────────────────────────────────────────────────────────


# 功能：验证 5 个 git 工具在仓库路径越出沙箱时返回 sandbox_violation 且不启动子进程
# 设计：沙箱根内/外各造一个目录，参数指向外部；断言 error_type 与 fake_git 未记录调用，
#       证明检查发生在 spawn 之前（硬边界，不依赖命令本身失败）
@pytest.mark.parametrize("tool_cls,extra", [
    (GitStatusTool, {}),
    (GitLogTool, {}),
    (GitDiffTool, {}),
    (GitCommitTool, {"message": "x"}),
    (GitCheckoutTool, {"target": "main"}),
])
async def test_repo_path_outside_sandbox_blocked(
    tool_cls: type, extra: dict[str, Any], sandbox_proj: Path, tmp_path: Path,
    fake_git: list[tuple[Any, ...]],
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    tool = tool_cls()
    result = await tool.invoke({"path": str(outside), **extra})
    assert result.is_error is True
    assert result.error_type == "sandbox_violation"
    assert fake_git == []  # 从未启动 git


# 功能：验证仓库路径在沙箱内时正常放行到子进程阶段
# 设计：同上用例的反向路径——用 fake_git 确认 argv 含 -C <repo>，
#       防止修复矫枉过正把合法调用也拦死
async def test_repo_path_inside_sandbox_allowed(
    sandbox_proj: Path, fake_git: list[tuple[Any, ...]]
) -> None:
    tool = GitStatusTool()
    result = await tool.invoke({"path": str(sandbox_proj)})
    assert result.is_error is not True
    # argv 形态：("git", "-C", <repo>, "status", ...)
    assert fake_git and fake_git[0][2] == str(sandbox_proj)


# ── 选项注入拒绝（--output / --git-dir / --work-tree 等） ─────────────────────


# 功能：验证 git_diff 的 file 参数以 '-' 开头时被 permission_denied 拒绝
# 设计：`git diff --output=/home/x/.ssh/authorized_keys` 类注入会让只读子命令获得
#       任意文件写能力；allowlist 思路下 diff 的目标文件不允许是选项形态
async def test_git_diff_file_option_injection_rejected(
    sandbox_proj: Path, fake_git: list[tuple[Any, ...]]
) -> None:
    tool = GitDiffTool()
    result = await tool.invoke({"path": str(sandbox_proj), "file": "--output=/tmp/evil.txt"})
    assert result.is_error is True
    assert result.error_type == "permission_denied"
    assert fake_git == []


# 功能：验证 git_diff 的 file 参数为沙箱外相对路径（../ 逃逸）时被 sandbox_violation 拒绝
# 设计：即使不以 '-' 开头，file 仍是一个路径参数，必须与仓库路径同标准做沙箱校验
async def test_git_diff_file_traversal_blocked(
    sandbox_proj: Path, fake_git: list[tuple[Any, ...]]
) -> None:
    tool = GitDiffTool()
    result = await tool.invoke({"path": str(sandbox_proj), "file": "../outside.txt"})
    assert result.is_error is True
    assert result.error_type == "sandbox_violation"
    assert fake_git == []


# 功能：验证 git_checkout 的 target 以 '-' 开头（-b / --orphan 等）被拒绝
# 设计：checkout target 直接拼进 argv，'-b newbranch' 会隐式创建分支，
#       '--git-dir=...' 可切换目标仓库；合法分支名/ref 均不以 '-' 开头
@pytest.mark.parametrize("bad_target", ["--orphan", "-b", "-bfeature", "  --foo=bar"])
async def test_git_checkout_target_option_rejected(
    bad_target: str, sandbox_proj: Path, fake_git: list[tuple[Any, ...]]
) -> None:
    tool = GitCheckoutTool()
    result = await tool.invoke({"path": str(sandbox_proj), "target": bad_target})
    assert result.is_error is True
    assert result.error_type == "permission_denied"
    assert fake_git == []


# 功能：验证 git_checkout 普通分支名不受选项检查影响，正常执行
# 设计：反向对照，确保 _reject_option_like 只拦 '-' 前缀而不是误伤常规 ref
async def test_git_checkout_normal_target_allowed(
    sandbox_proj: Path, fake_git: list[tuple[Any, ...]]
) -> None:
    tool = GitCheckoutTool()
    result = await tool.invoke({"path": str(sandbox_proj), "target": "main"})
    assert result.is_error is not True
    assert len(fake_git) == 1


# ── 沙箱禁用时的兼容性 ────────────────────────────────────────────────────────


# 功能：验证沙箱未启用（测试基线默认）时仓库路径检查直接放行
# 设计：get_sandbox 未初始化 → 禁用实例 → validate_path 不拦截；
#       保证未开启沙箱的用户升级后 git 工具行为不回退
async def test_path_check_noop_when_sandbox_disabled(
    tmp_path: Path, fake_git: list[tuple[Any, ...]]
) -> None:
    tool = GitStatusTool()
    result = await tool.invoke({"path": str(tmp_path)})
    assert result.is_error is not True
    assert len(fake_git) == 1
