"""Python 代码执行工具模块

这个模块实现了一个安全的 Python 代码执行工具，允许 Agent 在隔离环境中执行 Python 代码。

**核心特性：**
1. **沙箱隔离**：通过正则表达式检测代码中的文件操作，防止越权访问
2. **虚拟环境**：支持在独立的虚拟环境中运行代码，避免依赖冲突
3. **超时控制**：可配置的执行超时时间（1-300秒）
4. **依赖安装**：支持在运行前安装指定的 pip 依赖
5. **路径验证**：严格的路径遍历检测，禁止 ".." 访问

**安全机制详解：**
1. **文件操作检测**：通过正则表达式扫描代码中的 open()、Path()、os.path.* 等文件操作
2. **路径验证**：使用 validate_path() 验证文件路径是否在沙箱内
3. **工作目录限制**：work_dir 必须在沙箱内，且不能包含路径遍历
4. **临时文件清理**：执行完成后自动清理临时脚本文件和虚拟环境

**虚拟环境策略：**
- 可复用 venv：通过 venv_dir 指定可复用的虚拟环境目录
- 临时 venv：如果不指定 venv_dir，会创建临时目录并在执行后删除
- 环境变量配置：正确设置 VIRTUAL_ENV 和 PATH，移除 PYTHONHOME

**执行流程：**
1. 参数验证和安全检查
2. 创建或复用虚拟环境
3. 安装依赖（如果有）
4. 将代码写入临时文件
5. 执行 Python 子进程
6. 捕获输出并截断
7. 清理临时文件

**使用示例：**
```python
# 简单执行
result = await run_python_tool.invoke({
    "code": "print('Hello, World!')"
})

# 带参数和依赖
result = await run_python_tool.invoke({
    "code": "import numpy as np; print(np.array([1, 2, 3]))",
    "args": ["arg1", "arg2"],
    "requirements": ["numpy"],
    "timeout": 60,
    "work_dir": "."
})
```
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import venv
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.sandbox import validate_path
from iwan_claude.core.tools.base import BaseTool, ToolResult

# 输出最大字节数：128 KB，防止大输出导致内存问题
_MAX_OUTPUT_BYTES = 128 * 1024
# 默认超时时间，单位秒
_DEFAULT_TIMEOUT = 60
# 检测是否为 Windows 平台，用于跨平台兼容
_IS_WINDOWS = sys.platform == "win32"

# 文件操作函数名称集合，用于安全检查
_FILE_OP_FUNCTIONS = {
    "open", "open_file", "file",
    "__import__('os').open", "__import__('pathlib').Path",
}

# 文件操作模式匹配正则表达式，用于检测代码中的文件路径
_FILE_OP_PATTERNS = [
    # 匹配 open("path") 或 open('path')
    re.compile(r'\bopen\s*\(\s*["\']([^"\']+)["\']'),
    # 匹配 Path("path") 或 Path('path')
    re.compile(r'\bPath\s*\(\s*["\']([^"\']+)["\']'),
    # 匹配 os.path.*("path")
    re.compile(r'\bos\.path\.\w+\s*\(\s*["\']([^"\']+)["\']'),
    # 匹配 os.mkdir, os.makedirs, os.rmdir, os.remove, os.unlink, os.rename
    re.compile(r'\bos\.(?:mkdir|makedirs|rmdir|remove|unlink|rename)\s*\(\s*["\']([^"\']+)["\']'),
]


class RunPythonParams(BaseModel):
    """Python 代码执行参数模型

    **参数说明：**
    - code: 要执行的 Python 代码（必填）
    - args: 传递给脚本的命令行参数，作为 sys.argv[1:]
    - stdin: 传递给脚本的标准输入内容
    - requirements: 需要安装的 pip 依赖列表
    - timeout: 执行超时时间，范围 1-300 秒
    - use_venv: 是否使用虚拟环境，默认 True（推荐）
    - venv_dir: 可复用的虚拟环境目录路径（可选）
    - work_dir: 子进程的工作目录（可选）
    - capture_stderr: 是否捕获 stderr 输出，默认 True
    """
    model_config = ConfigDict(extra="ignore")
    
    # 要执行的 Python 代码，必填字段
    code: str
    # 命令行参数列表，传递给 sys.argv[1:]
    args: list[str] = []
    # 标准输入内容
    stdin: str = ""
    # 需要安装的 pip 依赖列表
    requirements: list[str] = []
    # 执行超时时间，范围 1-300 秒
    timeout: int = Field(default=_DEFAULT_TIMEOUT, ge=1, le=300)
    # 是否使用虚拟环境，推荐开启以实现隔离
    use_venv: bool = True
    # 可复用的虚拟环境目录路径
    venv_dir: str | None = None
    # 子进程的工作目录
    work_dir: str | None = None
    # 是否将 stderr 合并到输出中
    capture_stderr: bool = True


class RunPythonTool(BaseTool):
    """Python 代码执行工具

    提供安全隔离的 Python 代码执行能力，支持虚拟环境和依赖安装。
    
    **安全检查流程：**
    1. 使用正则表达式扫描代码中的文件操作
    2. 对检测到的文件路径进行沙箱验证
    3. 检查 work_dir 和 venv_dir 是否包含路径遍历
    4. 验证 work_dir 是否在沙箱内
    
    **执行流程：**
    1. 安全检查
    2. 创建或复用虚拟环境
    3. 安装依赖（如果有）
    4. 将代码写入临时文件
    5. 创建子进程执行代码
    6. 捕获输出并处理超时
    7. 清理临时文件和环境
    """
    params_model = RunPythonParams
    name = "run_python"
    description = (
        "Execute a Python code snippet in an isolated subprocess. Optional pip "
        "requirements are installed into a fresh or reused venv (default on). "
        "Use work_dir to set CWD (relative to project CWD). Supports stdin, argv, "
        "and configurable timeout. Captured stdout (and optionally stderr) is "
        "truncated at 128 KB. Path traversal is forbidden."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source code to execute."},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "String arguments passed as sys.argv[1:].",
            },
            "stdin": {
                "type": "string",
                "default": "",
                "description": "Text piped to the script on stdin.",
            },
            "requirements": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Pip requirement specifiers to install before running.",
            },
            "timeout": {
                "type": "integer",
                "default": _DEFAULT_TIMEOUT,
                "description": "Seconds before the process is killed (max 300).",
            },
            "use_venv": {
                "type": "boolean",
                "default": True,
                "description": "If true, run inside a venv (recommended for isolation).",
            },
            "venv_dir": {
                "type": "string",
                "description": (
                    "Optional relative path of a reusable venv. If omitted a temp "
                    "directory is used and deleted after execution."
                ),
            },
            "work_dir": {
                "type": "string",
                "description": "Optional relative working directory for the process.",
            },
            "capture_stderr": {
                "type": "boolean",
                "default": True,
                "description": "Merge stderr into the captured output.",
            },
        },
        "required": ["code"],
    }

    # 工具元数据，用于分类和过滤
    metadata: ClassVar[dict[str, str]] = {"category": "execute"}

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行 Python 代码

        **安全检查阶段：**
        1. 使用正则表达式扫描代码中的文件操作路径
        2. 对每个检测到的路径调用 validate_path() 进行沙箱验证
        3. 检查 work_dir 是否包含路径遍历（".."）
        4. 验证 work_dir 是否在沙箱内
        5. 检查 work_dir 是否存在
        
        **虚拟环境创建阶段：**
        1. 如果 use_venv=True：
           - 如果指定了 venv_dir：检查路径安全，不存在则创建
           - 如果未指定 venv_dir：创建临时目录和虚拟环境
        2. 如果 use_venv=False：使用系统 Python
        
        **依赖安装阶段：**
        1. 如果有 requirements：调用 _pip_install() 安装依赖
        2. 安装失败则返回错误
        
        **执行阶段：**
        1. 将代码写入临时 .py 文件
        2. 构建命令行参数（Python 解释器 + 脚本路径 + args）
        3. 创建子进程执行代码
        4. 使用 asyncio.wait_for 设置超时
        5. 超时则杀死进程并返回错误
        
        **清理阶段：**
        1. 删除临时脚本文件
        2. 如果使用临时虚拟环境，清理临时目录
        
        Args:
            params: 包含执行参数的字典
            
        Returns:
            ToolResult: 包含执行结果的对象，包括输出和返回码
        """
        # 使用 pydantic 验证并转换参数
        p = RunPythonParams.model_validate(params)

        # ========== 安全检查：扫描代码中的文件操作 ==========
        # 遍历所有文件操作模式，检测代码中的文件路径
        for pattern in _FILE_OP_PATTERNS:
            for match in pattern.finditer(p.code):
                file_path = match.group(1)
                # 对检测到的路径进行沙箱验证
                try:
                    validate_path(file_path, "execute")
                except PermissionError:
                    return ToolResult(
                        content=f"sandbox access denied: Python code attempts to access file outside sandbox: '{file_path}'",
                        is_error=True,
                        error_type="permission_denied",
                    )

        # ========== 安全检查：验证工作目录 ==========
        work_path: Path | None = None
        if p.work_dir is not None:
            # 检查工作目录是否包含路径遍历
            if ".." in Path(p.work_dir).parts:
                return ToolResult(
                    content=f"work_dir path traversal not allowed: {p.work_dir}",
                    is_error=True,
                    error_type="permission_denied",
                )
            # 验证工作目录是否在沙箱内
            try:
                validate_path(p.work_dir, "execute")
            except PermissionError:
                return ToolResult(
                    content=f"sandbox access denied: work_dir '{p.work_dir}' is outside sandbox",
                    is_error=True,
                    error_type="permission_denied",
                )
            # 解析工作目录路径
            work_path = Path(p.work_dir)
            # 检查工作目录是否存在
            if not work_path.exists():
                return ToolResult(
                    content=f"work_dir does not exist: {p.work_dir}",
                    is_error=True,
                    error_type="runtime_error",
                )

        # ========== 虚拟环境创建/复用 ==========
        venv_path: Path | None = None
        owns_temp_venv = False  # 是否拥有临时虚拟环境（需要清理）
        temp_root: tempfile.TemporaryDirectory[str] | None = None
        
        try:
            if p.use_venv:
                if p.venv_dir is not None:
                    # 使用指定的虚拟环境目录
                    # 检查路径是否包含遍历
                    if ".." in Path(p.venv_dir).parts:
                        return ToolResult(
                            content=f"venv_dir path traversal not allowed: {p.venv_dir}",
                            is_error=True,
                            error_type="permission_denied",
                        )
                    venv_path = Path(p.venv_dir)
                    # 如果目录不存在，创建虚拟环境
                    if not venv_path.exists():
                        try:
                            venv.create(str(venv_path), with_pip=True, clear=False)
                        except Exception as exc:
                            return ToolResult(
                                content=f"failed to create venv at {p.venv_dir}: {exc}",
                                is_error=True,
                                error_type="runtime_error",
                            )
                else:
                    # 创建临时虚拟环境
                    temp_root = tempfile.TemporaryDirectory(prefix="iwan_py_")
                    venv_path = Path(temp_root.name)
                    owns_temp_venv = True
                    try:
                        venv.create(str(venv_path), with_pip=True, clear=False)
                    except Exception as exc:
                        return ToolResult(
                            content=f"failed to create temp venv: {exc}",
                            is_error=True,
                            error_type="runtime_error",
                        )

            # 解析 Python 解释器路径
            interpreter = _resolve_python(venv_path)

            # ========== 依赖安装 ==========
            if p.requirements:
                install_ok = await _pip_install(
                    interpreter, list(p.requirements), timeout=max(300, p.timeout)
                )
                if not install_ok.success:
                    return ToolResult(
                        content=(
                            f"pip install failed for {p.requirements}\n"
                            f"---\n{install_ok.output}"
                        ),
                        is_error=True,
                        error_type="runtime_error",
                    )

            # ========== 代码执行 ==========
            script_path: Path
            # 创建临时脚本文件
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as fh:
                fh.write(p.code)
                script_path = Path(fh.name)
            
            try:
                # 构建子进程工作目录
                proc_cwd = str(work_path.resolve()) if work_path is not None else None
                # 构建命令行参数
                argv = [interpreter, str(script_path), *list(p.args)]
                # 配置 stderr 处理方式
                stderr_target = (
                    asyncio.subprocess.STDOUT  # 合并到 stdout
                    if p.capture_stderr
                    else asyncio.subprocess.DEVNULL  # 丢弃
                )
                
                # 创建子进程
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *argv,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=stderr_target,
                        stdin=asyncio.subprocess.PIPE,
                        cwd=proc_cwd,
                        env=_child_env(os.environ.copy(), venv_path),
                    )
                except Exception as exc:
                    return ToolResult(
                        content=f"failed to spawn python subprocess: {exc}",
                        is_error=True,
                        error_type="runtime_error",
                    )

                # 执行代码并等待结果，设置超时
                try:
                    stdout_bytes, _ = await asyncio.wait_for(
                        proc.communicate(
                            p.stdin.encode("utf-8") if p.stdin else None
                        ),
                        timeout=p.timeout,
                    )
                except TimeoutError:
                    # 超时处理：杀死进程并清理
                    proc.kill()
                    await proc.communicate()
                    return ToolResult(
                        content=f"[timeout after {p.timeout}s]",
                        is_error=True,
                        error_type="timeout",
                    )
            finally:
                # 清理临时脚本文件
                try:
                    script_path.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            # 清理临时虚拟环境
            if owns_temp_venv and temp_root is not None:
                try:
                    temp_root.cleanup()
                except Exception:
                    pass

        # ========== 输出处理 ==========
        # 解码输出
        output = (stdout_bytes or b"").decode("utf-8", errors="replace")
        # 检查输出大小，超过限制则截断
        truncated = len(stdout_bytes or b"") > _MAX_OUTPUT_BYTES
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        # 检查返回码
        returncode = proc.returncode or 0
        if returncode != 0:
            return ToolResult(
                content=f"[exit {returncode}]\n{output}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=output or "[no output]")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _resolve_python(venv_path: Path | None) -> str:
    """解析 Python 解释器路径

    根据虚拟环境路径返回对应的 Python 解释器路径。
    
    **跨平台处理：**
    - Windows: venv_path/Scripts/python.exe
    - 其他平台: venv_path/bin/python
    
    Args:
        venv_path: 虚拟环境路径，None 表示使用系统 Python
        
    Returns:
        Python 解释器的完整路径
    """
    if venv_path is None:
        return sys.executable
    if _IS_WINDOWS:
        candidate = venv_path / "Scripts" / "python.exe"
    else:
        candidate = venv_path / "bin" / "python"
    # 如果虚拟环境中的 Python 不存在，回退到系统 Python
    return str(candidate) if candidate.exists() else sys.executable


def _child_env(env: dict[str, str], venv_path: Path | None) -> dict[str, str]:
    """配置子进程的环境变量

    设置虚拟环境所需的环境变量，确保子进程使用正确的 Python 环境。
    
    **环境变量设置：**
    - VIRTUAL_ENV: 虚拟环境路径
    - PATH: 将虚拟环境的 bin/Scripts 目录添加到 PATH 最前面
    - 移除 PYTHONHOME: 避免与虚拟环境冲突
    
    Args:
        env: 当前环境变量字典的副本
        venv_path: 虚拟环境路径，None 表示不修改环境变量
        
    Returns:
        配置好的环境变量字典
    """
    if venv_path is None:
        return env
    # 设置 VIRTUAL_ENV 环境变量
    env["VIRTUAL_ENV"] = str(venv_path.resolve())
    # 将虚拟环境的可执行目录添加到 PATH 最前面
    if _IS_WINDOWS:
        scripts = str(venv_path.resolve() / "Scripts")
        env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    else:
        bin_dir = str(venv_path.resolve() / "bin")
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    # 移除 PYTHONHOME，避免与虚拟环境冲突
    env.pop("PYTHONHOME", None)
    return env


async def _pip_install(
    interpreter: str, requirements: list[str], timeout: int
) -> _InstallResult:
    """安装 pip 依赖

    使用指定的 Python 解释器安装依赖包。
    
    **执行流程：**
    1. 检查是否有依赖需要安装
    2. 创建子进程执行 pip install 命令
    3. 设置超时和输出捕获
    4. 返回安装结果（成功/失败 + 输出）
    
    Args:
        interpreter: Python 解释器路径
        requirements: 需要安装的依赖列表
        timeout: 超时时间，单位秒
        
    Returns:
        _InstallResult 对象，包含成功状态和输出
    """
    if not requirements:
        return _InstallResult(success=True, output="")
    try:
        # 创建子进程执行 pip install
        proc = await asyncio.create_subprocess_exec(
            interpreter,
            "-m",
            "pip",
            "install",
            "--quiet",  # 安静模式，减少输出
            "--disable-pip-version-check",  # 禁用版本检查
            *requirements,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # 合并 stderr 到 stdout
        )
    except Exception as exc:
        return _InstallResult(success=False, output=str(exc))
    
    # 等待安装完成，设置超时
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        # 超时处理：杀死进程并清理
        proc.kill()
        await proc.communicate()
        return _InstallResult(success=False, output="[pip install timed out]")
    
    # 解码输出
    out = (stdout_bytes or b"").decode("utf-8", errors="replace")
    # 检查返回码
    if (proc.returncode or 0) != 0:
        return _InstallResult(success=False, output=out)
    return _InstallResult(success=True, output=out)


class _InstallResult:
    """pip 安装结果对象

    用于封装 pip 安装的结果，包含成功状态和输出信息。
    
    **字段说明：**
    - success: 布尔值，表示安装是否成功
    - output: 字符串，包含安装过程的输出
    """
    def __init__(self, success: bool, output: str) -> None:
        self.success = success
        self.output = output
