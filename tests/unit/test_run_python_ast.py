"""run_python AST 静态写分析测试（任务 6）

覆盖：analyze_python_writes 的常量/动态路径判定、正则时代的经典绕过（f-string、
变量路径、子进程/exec）、以及工具层"静态越界写=硬拒绝 / 动态写=交由权限层"的分工。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from iwan_claude.core.config import SandboxConfig
from iwan_claude.core.sandbox import init_sandbox
from iwan_claude.core.tools.builtin.run_python import (
    RunPythonTool,
    analyze_python_writes,
)


@pytest.fixture(autouse=True)
def reset_sandbox_state() -> Any:
    # 功能：测试前后清空沙箱全局单例与 contextvar
    # 设计：validate_path 走 get_sandbox()，前序测试残留的启用沙箱会改变拒绝路径的预期
    import iwan_claude.core.sandbox as sb_module
    sb_module._sandbox_manager = None
    token = sb_module._active_sandbox.set(None)
    sb_module._sandbox_by_session.clear()
    yield
    sb_module._active_sandbox.reset(token)
    sb_module._sandbox_manager = None
    sb_module._sandbox_by_session.clear()


# ── analyze_python_writes 纯函数 ──────────────────────────────────────────────


# 功能：验证纯计算代码不含任何写操作（静态路径为空、无动态写）
# 设计：作为放行基线——若连 print 都被判危险，用户会被无意义 ASK 淹没
def test_no_writes_in_pure_computation() -> None:
    paths, dynamic = analyze_python_writes("print(1 + 1)\nx = [i*i for i in range(10)]")
    assert paths == []
    assert dynamic is False


# 功能：验证 open() 写模式常量路径被收集为静态路径
# 设计：w/a/x/+ 四种写模式与 mode= 关键字形式都要识别；只读 'r'/'rb' 不应误报
@pytest.mark.parametrize("code,expected", [
    ("open('out.txt', 'w')", ["out.txt"]),
    ("open('out.txt', 'a')", ["out.txt"]),
    ("open('out.txt', 'x')", ["out.txt"]),
    ("open('out.txt', 'r+')", ["out.txt"]),
    ("open('out.txt', mode='w')", ["out.txt"]),
    ("io.open('out.txt', 'wb')", ["out.txt"]),
])
def test_open_write_constant_paths_collected(code: str, expected: list[str]) -> None:
    paths, dynamic = analyze_python_writes(code)
    assert paths == expected
    assert dynamic is False


# 功能：验证只读 open 与读取模式不产生写路径
# 设计：open('f') 默认 mode='r'；'rb' 也无写字符——两分支都必须放行
def test_open_read_only_not_flagged() -> None:
    assert analyze_python_writes("open('data.csv', 'r').read()") == ([], False)
    assert analyze_python_writes("open('data.csv', 'rb').read()") == ([], False)


# 功能：验证 f-string / 变量 / 拼接路径的写操作被判为动态（正则时代的三类绕过）
# 设计：这三类正是旧正则字面量扫描的盲区——字符串能扫到但语义拼不出完整路径；
#       AST 下 JoinedStr/Name/BinOp 常量性判定失败 → has_dynamic=True → 强制 ASK
@pytest.mark.parametrize("code", [
    "open(f'{d}/x.txt', 'w')",
    "p = input()\nopen(p, 'w')",
    "open('a' + ext, 'w')",
    "Path(user_dir).write_text('x')",
    "Path('logs').joinpath(name).touch()",
])
def test_dynamic_write_paths_detected(code: str) -> None:
    _paths, dynamic = analyze_python_writes(code)
    assert dynamic is True


# 功能：验证 pathlib 常量写（write_text/mkdir/unlink 等）被静态收集
# 设计：Path('lit').method() 形态接收者可解析为字面量 → 静态；
#       接收者再包一层（joinpath 变量）即失去常量性 → 动态
def test_pathlib_constant_writes() -> None:
    paths, dynamic = analyze_python_writes(
        "Path('report.md').write_text('x')\nPath('tmp').mkdir()\nPath('old.log').unlink()"
    )
    assert sorted(paths) == ["old.log", "report.md", "tmp"]
    assert dynamic is False


# 功能：验证 os.* 与 shutil.* 写/删操作的路径收集，含 rename 双端参数
# 设计：rename/replace/link/symlink 有源+目标两个路径，只查源会漏"沙箱内→外"的搬移；
#       任一端非常量则整体降为动态
def test_os_shutil_writes() -> None:
    paths, dynamic = analyze_python_writes("os.remove('a.tmp')\nos.makedirs('d/e')")
    assert sorted(paths) == ["a.tmp", "d/e"]
    assert dynamic is False

    paths2, dyn2 = analyze_python_writes("os.rename('inside.txt', 'target.txt')")
    assert sorted(paths2) == ["inside.txt", "target.txt"]
    assert dyn2 is False

    _p3, dyn3 = analyze_python_writes("os.rename(src, 'dst.txt')")
    assert dyn3 is True

    _p4, dyn4 = analyze_python_writes("shutil.rmtree(name)")
    assert dyn4 is True


# 功能：验证子进程/exec/eval/os.system 等"可写任意文件"的调用一律判为动态
# 设计：这些入口内部行为不可静态分析（rm 命令、pickle 反序列化都算），
#       与其穷举参数形态，不如按调用名一刀切标记动态——fail-closed
@pytest.mark.parametrize("code", [
    "subprocess.run(['rm', '-rf', 'x'])",
    "os.system('del C:\\\\*')",
    "exec(user_code)",
    "eval('open(\"a\", \"w\")')",
    "__import__('os').remove('a')",
])
def test_always_dynamic_calls_flagged(code: str) -> None:
    _paths, dynamic = analyze_python_writes(code)
    assert dynamic is True


# 功能：验证语法错误代码返回 (空, True)，按存在动态写处理
# 设计：解析失败意味着无法证明任何写操作安全，必须交给 ASK 人工过目而非静默放行
def test_syntax_error_fail_closed() -> None:
    assert analyze_python_writes("def broken(") == ([], True)


# ── 工具层职责：静态越界硬拒绝 / 动态写不拦截（由权限层 ASK） ─────────────────


# 功能：验证沙箱启用时，代码里的常量越界写路径在 spawn 前被工具直接拒绝
# 设计：绝对路径指向沙箱外 + '../' 逃逸两种形态；断言 error_type=permission_denied
#       且没有子进程被创建（沙箱外的 /etc/passwd 类路径是硬边界，不依赖审批）
@pytest.mark.parametrize("code", [
    "open('/etc/shadow', 'w')",
    "open('../escape.txt', 'w')",
    "Path('../../outside.log').write_text('x')",
])
async def test_static_outside_write_denied_by_tool(
    code: str, tmp_path: Path
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    init_sandbox(SandboxConfig(enabled=True, root=str(proj)))
    result = await RunPythonTool().invoke({"code": code})
    assert result.is_error is True
    assert result.error_type == "permission_denied"
    assert "outside sandbox" in result.content


# 功能：验证动态写代码不被工具层静态拦截（审批在权限层完成，工具不重复拦）
# 设计：input() 变量路径 + use_venv=False 快速子进程；子进程因 EOF 报错退出，
#       关键断言是错误类型不是 permission_denied——证明"ASK 后放行"的分工成立
async def test_dynamic_write_not_blocked_by_tool(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    init_sandbox(SandboxConfig(enabled=True, root=str(proj)))
    result = await RunPythonTool().invoke({
        "code": "p = input()\nopen(p, 'w')",
        "use_venv": False,
        "timeout": 30,
    })
    assert "outside sandbox" not in result.content
    assert result.error_type != "permission_denied"


# 功能：验证沙箱内常量写 + 纯计算代码正常执行并返回输出
# 设计：use_venv=False 直连系统解释器，避免 venv 创建拖慢单测；
#       print 输出内容回传即整链路（校验→spawn→捕获）贯通
async def test_safe_code_executes(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    init_sandbox(SandboxConfig(enabled=True, root=str(proj)))
    result = await RunPythonTool().invoke({
        "code": "print(6 * 7)",
        "use_venv": False,
        "timeout": 30,
    })
    assert result.is_error is not True
    assert "42" in result.content


# 功能：验证 work_dir 越出沙箱或含路径遍历时被拒绝
# 设计：work_dir 与写路径同级敏感（决定相对路径解析基准），必须走同一硬校验；
#       '..' 遍历单独有专属错误信息，便于模型自我纠正
async def test_work_dir_outside_denied(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    init_sandbox(SandboxConfig(enabled=True, root=str(proj)))

    r1 = await RunPythonTool().invoke({"code": "print(1)", "work_dir": str(outside)})
    assert r1.is_error is True and r1.error_type == "permission_denied"

    r2 = await RunPythonTool().invoke({"code": "print(1)", "work_dir": "../x"})
    assert r2.is_error is True and r2.error_type == "permission_denied"
