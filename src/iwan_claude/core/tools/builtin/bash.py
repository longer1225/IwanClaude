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
import sys

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大输出字节数：64 KB，防止返回过多内容
_MAX_OUTPUT_BYTES = 64 * 1024
# 默认超时时间：60 秒
_DEFAULT_TIMEOUT = 60

# 检测当前平台：Windows 使用 PowerShell，其他平台使用默认 shell
IS_WINDOWS = sys.platform == "win32"


class BashParams(BaseModel):
    """
    Shell 命令参数模型

    【字段说明】
    - command: str - 要执行的 shell 命令
    - timeout: int - 超时时间（秒），范围 1-120，默认 60
    """
    model_config = ConfigDict(extra="ignore")
    command: str
    timeout: int = Field(default=_DEFAULT_TIMEOUT, ge=1, le=120)


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
                "description": f"Maximum seconds to wait (default {_DEFAULT_TIMEOUT}, max 120).",
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
                )
            else:
                # Linux / macOS：使用默认 shell 执行命令
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,  # 将 stderr 合并到 stdout
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
        truncated = len(stdout_bytes) > _MAX_OUTPUT_BYTES
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

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
