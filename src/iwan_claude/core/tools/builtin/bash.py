"""
Shell 命令执行工具 - 在子进程中执行 shell 命令

【学习要点】
1. 跨平台支持：Windows 使用 PowerShell，其他平台使用默认 shell
2. 异步子进程：使用 asyncio.create_subprocess_exec 和 asyncio.create_subprocess_shell
3. 超时控制：使用 asyncio.wait_for 防止命令执行过长时间
4. 输出合并：将 stderr 合并到 stdout，方便统一处理
5. PowerShell 兼容性：自动处理 PowerShell 的命令格式和转义

【安全注意事项】
- 此工具具有潜在危险，应配合权限管理器使用
- 命令执行在沙箱外，需谨慎使用
- 禁止交互式命令（如 vim、ssh），会导致超时

【PowerShell 参数说明】
- -NoProfile：不加载用户配置文件，避免环境变量污染
- -NonInteractive：非交互模式，防止命令等待用户输入
- -Command：指定要执行的命令
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult
from iwan_claude.core.sandbox import get_sandbox, scrub_env

# 兜底最大输出字节数：64 KB，防止返回过多内容
_FALLBACK_OUTPUT_MAX_BYTES = 64 * 1024
# 兜底默认超时时间：60 秒
_FALLBACK_TIMEOUT_S = 60


def _output_max_bytes() -> int:
    """从全局配置读取 bash 工具输出截断字节数"""
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.bash_output_max_bytes)
    except Exception:
        return _FALLBACK_OUTPUT_MAX_BYTES


def _timeout_s() -> int:
    """从全局配置读取 bash 工具默认超时秒数"""
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.bash_timeout_s)
    except Exception:
        return _FALLBACK_TIMEOUT_S

# 检测当前平台：Windows 使用 PowerShell，其他平台使用默认 shell
IS_WINDOWS = sys.platform == "win32"

# 日志记录器
logger = logging.getLogger(__name__)

# Windows 绝对路径正则：匹配 C:\xxx 或 D:/xxx 等形式
# 提取完整的绝对路径（盘符 + 路径部分）
_WIN_ABS_PATH_RE = re.compile(
    r'([A-Za-z]):[\\/]([^\s"\'|&;()\[\]{}<>,`]*)'
)
# Unix 绝对路径正则：匹配 /etc/xxx 或 /usr/local 等形式
_UNIX_ABS_PATH_RE = re.compile(
    r'(?:^|[\s;|&])(/[^\s"\'|&;()\[\]{}<>,`]+)'
)


def _check_bash_paths(command: str, sandbox_root: Path, allow_parent: bool) -> list[str]:
    """
    检查 bash 命令中是否包含指向沙箱外的绝对路径

    【安全目的】
    防止通过 bash 工具绕过沙箱的路径级文件访问限制。
    即使 write_file/read_file 被沙箱正确拦截，bash 仍可能通过
    绝对路径访问系统任意文件（如 type C:\\Windows\\win.ini）。

    【检测策略】
    1. 提取命令中所有 Windows 绝对路径（C:\\xxx）
    2. 提取命令中所有 Unix 绝对路径（/etc/xxx）
    3. resolve() 每个路径并检查是否在沙箱根内
    4. 收集所有越界路径并返回

    【参数】
    - command: str - 要执行的 shell 命令
    - sandbox_root: Path - 沙箱根目录（已 resolve）
    - allow_parent: bool - 是否允许访问沙箱根的父目录

    【返回】
    - list[str]: 越界路径列表（空列表表示全部合法）

    【示例】
    >>> _check_bash_paths("type C:\\\\Windows\\\\win.ini", Path("D:\\\\Test"), False)
    ["C:\\\\Windows\\\\win.ini"]  # 越界，应被拦截
    >>> _check_bash_paths("type file.txt", Path("D:\\\\Test"), False)
    []  # 相对路径，合法
    """
    violations: list[str] = []
    root_lower = str(sandbox_root).lower()

    def _is_within(abs_path: str) -> bool:
        """检查路径是否在沙箱根内"""
        try:
            resolved = str(Path(abs_path).resolve()).lower()
            # 路径在沙箱根内
            if resolved.startswith(root_lower):
                return True
            # allow_parent 模式：允许访问沙箱根的父目录
            if allow_parent:
                parent = str(sandbox_root.parent.resolve()).lower()
                if resolved.startswith(parent):
                    return True
            return False
        except Exception:
            # resolve 失败（路径不存在等）时，保守判断为越界
            return False

    # 检测 Windows 绝对路径
    for m in _WIN_ABS_PATH_RE.finditer(command):
        drive = m.group(1).upper()
        rest = m.group(2)
        abs_path = f"{drive}:\\{rest}"
        if not _is_within(abs_path):
            violations.append(abs_path)
            logger.debug("bash sandbox check: blocked Windows path %s", abs_path)

    # 检测 Unix 绝对路径
    for m in _UNIX_ABS_PATH_RE.finditer(command):
        abs_path = m.group(1)
        if not _is_within(abs_path):
            violations.append(abs_path)
            logger.debug("bash sandbox check: blocked Unix path %s", abs_path)

    return violations


class BashParams(BaseModel):
    """
    Shell 命令参数模型

    【字段说明】
    - command: str - 要执行的 shell 命令
    - timeout: int - 超时时间（秒），范围 1-120，默认 60
    """
    model_config = ConfigDict(extra="ignore")
    command: str
    timeout: int = Field(default_factory=_timeout_s, ge=1, le=120)


class BashTool(BaseTool):
    """
    Shell 命令执行工具 - 在子进程中执行 shell 命令

    【学习要点】
    1. 异步子进程管理：使用 asyncio 管理子进程的创建和通信
    2. 平台适配：根据操作系统选择不同的执行方式
    3. 超时处理：超时后终止进程并清理资源
    4. 输出处理：合并 stdout 和 stderr，处理编码和截断

    【使用示例】
    ```python
    tool = BashTool()
    
    # 执行简单命令
    result = await tool.invoke({"command": "ls -la"})
    
    # 执行带超时的命令
    result = await tool.invoke({"command": "sleep 10", "timeout": 5})
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 根据平台选择执行方式
       - Windows：使用 PowerShell
       - Linux/macOS：使用默认 shell
    3. 创建子进程并执行命令
    4. 等待命令完成（带超时控制）
    5. 处理输出（解码、截断）
    6. 根据退出码返回结果或错误
    """
    params_model = BashParams
    name = "bash"
    description = (
        "Execute a shell command and return its output (stdout + stderr combined). "
        "On Windows this uses PowerShell, on macOS/Linux the system shell. "
        "Non-interactive only — commands requiring user input will hang and time out. "
        "Prefer short, focused commands. Output is truncated at 64 KB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum seconds to wait (default from tools.bash_timeout_s, max 120).",
            },
        },
        "required": ["command"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行 shell 命令

        【执行流程详解】
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 验证输入参数（Pydantic）                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 2. 根据平台选择执行方式                                       │
        │    ├─ Windows → PowerShell (create_subprocess_exec)         │
        │    └─ Linux/macOS → 默认 shell (create_subprocess_shell)    │
        ├─────────────────────────────────────────────────────────────┤
        │ 3. 创建子进程并执行命令                                        │
        │    ├─ stdout=PIPE：捕获标准输出                              │
        │    └─ stderr=STDOUT：将标准错误合并到标准输出                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 4. 等待命令完成（带超时控制）                                    │
        │    ├─ 正常完成 → 继续处理输出                                 │
        │    └─ 超时 → 终止进程，返回超时错误                           │
        ├─────────────────────────────────────────────────────────────┤
        │ 5. 处理输出（解码、截断）                                       │
        ├─────────────────────────────────────────────────────────────┤
        │ 6. 根据退出码返回结果                                         │
        │    ├─ returncode=0 → 成功结果                                │
        │    └─ returncode≠0 → 错误结果（包含退出码）                     │
        └─────────────────────────────────────────────────────────────┘
        """
        # 1. 验证输入参数
        p = BashParams.model_validate(params)
        command = p.command
        timeout = p.timeout

        # 获取沙箱根目录作为子进程工作目录
        # 沙箱启用时，命令在项目目录内执行（相对路径基于项目根）
        # 沙箱未启用时，使用 None（继承父进程 CWD）
        sandbox = get_sandbox()
        cwd = str(sandbox.root) if sandbox.enabled else None

        # ===== 沙箱路径级硬拦截 =====
        # 检测命令中是否包含指向沙箱外的绝对路径
        # 这是强制安全限制：即使权限系统通过也会拦截
        # 防止 Agent 通过 bash 绕过 write_file/read_file 的路径限制
        if sandbox.enabled:
            violations = _check_bash_paths(
                command, sandbox.root, sandbox.allow_parent_dirs
            )
            if violations:
                logger.warning(
                    "bash sandbox block: command=%s  violations=%s",
                    command[:100], violations,
                )
                # 记录审计日志
                try:
                    from iwan_claude.core.audit import log_sandbox_block
                    log_sandbox_block(
                        tool="bash",
                        reason="path_outside_sandbox",
                        command=command[:200],
                    )
                except Exception:
                    pass
                return ToolResult(
                    content=(
                        f"[blocked by sandbox] command accesses path(s) outside "
                        f"the project directory: {', '.join(violations)}\n\n"
                        f"To fix: use relative paths or move the file into "
                        f"the project root ({sandbox.root})."
                    ),
                    is_error=True,
                    error_type="sandbox_violation",
                )

        # ===== 进程内强化：网络命令阻断 =====
        # block_network_commands=True 时，阻断 curl/wget/nc/ssh 等网络外传命令
        # 防止 Agent 通过 bash 子进程外传沙箱内文件（如 .env、密钥配置）
        # 可通过 sandbox.block_network_commands=false 关闭
        if sandbox.enabled and sandbox.block_network_commands:
            from iwan_claude.core.permissions.policy import matches_network_command
            if matches_network_command(command):
                # 记录审计日志
                try:
                    from iwan_claude.core.audit import log_sandbox_block
                    log_sandbox_block(
                        tool="bash",
                        reason="network_command_blocked",
                        command=command[:200],  # 截断防止日志膨胀
                    )
                except Exception:
                    pass
                return ToolResult(
                    content=(
                        "[blocked] network command detected in sandbox mode. "
                        "To allow network egress, set sandbox.block_network_commands=false "
                        "or use the http_request tool (which has its own safety controls)."
                    ),
                    is_error=True,
                    error_type="permission_denied",
                )

        # ===== 进程内强化：环境变量脱敏 =====
        # 移除 ANTHROPIC_API_KEY、DASHSCOPE_API_KEY 等敏感变量
        # 防止子进程通过 env/echo $KEY/printenv 读取密钥
        # 沙箱未启用时不脱敏（child_env=None 表示继承父进程 env）
        child_env = scrub_env(dict(os.environ)) if sandbox.enabled else None

        try:
            if IS_WINDOWS:
                # Windows：使用 PowerShell 执行命令
                # PowerShell 参数说明：
                # -NoProfile：不加载用户配置文件，避免环境变量污染
                # -NonInteractive：非交互模式，防止命令等待用户输入
                # -Command：指定要执行的命令（需要特殊转义）
                ps_command = _quote_powershell(command)
                proc = await asyncio.create_subprocess_exec(
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    ps_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,  # 将 stderr 合并到 stdout
                    cwd=cwd,  # 沙箱根目录作为工作目录
                    env=child_env,  # 脱敏后的环境变量（沙箱启用时）
                )
            else:
                # Linux / macOS：使用默认 shell 执行命令
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,  # 将 stderr 合并到 stdout
                    cwd=cwd,  # 沙箱根目录作为工作目录
                    env=child_env,  # 脱敏后的环境变量（沙箱启用时）
                )

            # 2. 等待命令完成（带超时控制）
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                # 超时处理：终止进程并清理资源
                proc.kill()
                await proc.communicate()  # 清理残留输出
                return ToolResult(
                    content=f"[timeout after {timeout}s]",
                    is_error=True,
                    error_type="timeout",
                )

        except Exception as exc:
            # 其他异常（如命令不存在、权限不足等）
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 3. 处理输出
        output = stdout_bytes.decode("utf-8", errors="replace")

        # 4. 处理输出大小限制
        max_bytes = _output_max_bytes()
        truncated = len(stdout_bytes) > max_bytes
        if truncated:
            output = output[:max_bytes] + "\n[truncated]"

        # 5. 根据退出码返回结果
        returncode = proc.returncode or 0
        if returncode != 0:
            return ToolResult(
                content=f"[exit {returncode}]\n{output}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=output or "[no output]")


def _quote_powershell(command: str) -> str:
    """
    将命令字符串包装成 PowerShell -Command 能正确解析的形式

    【学习要点】
    1. PowerShell 命令格式：`& {command}`
    2. 转义规则：单引号 `'` 需要转义为两个单引号 `''`
    3. 括号包裹：命令需要用大括号 `{}` 包裹

    【参数说明】
    - command: str - 原始命令字符串

    【返回值】
    - str: 转义后的 PowerShell 命令

    【示例】
    - "ls -la" → "& {ls -la}"
    - "echo 'hello'" → "& {echo ''hello''}"

    【PowerShell 语法说明】
    - `&` 是调用操作符，用于执行命令
    - `{}` 是脚本块，用于包裹命令
    - 单引号在脚本块内需要转义为两个单引号
    """
    # 转义单引号：将 ' 替换为 ''
    escaped = command.replace("'", "''")
    # 使用 & {} 格式包装命令
    return f"& {{{escaped}}}"
