# 启用 Python 3.7+ 的延迟注解评估（PEP 563）
from __future__ import annotations

# 导入 asyncio：用于异步执行子进程
import asyncio

# 导入工具基类：所有工具都继承自 BaseTool
from kama_claude.core.tools.base import BaseTool, ToolResult

# 输出大小限制：64 KB（防止输出过大导致 LLM 上下文溢出）
_MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB
# 默认超时时间：60 秒（防止命令长时间运行阻塞 agent）
_DEFAULT_TIMEOUT = 60


# BashTool：执行 shell 命令的工具
# 什么是 shell 命令？就是在终端中执行的命令（如 ls、cat、python 等）
# 为什么需要这个工具？因为 LLM 本身不能直接执行系统命令，需要通过工具间接执行
class BashTool(BaseTool):
    # 工具名称：供 LLM 调用时使用
    name = "bash"
    # 工具描述：告诉 LLM 这个工具的用途和使用方式
    description = (
        "Execute a shell command and return its output (stdout + stderr combined). "
        "Non-interactive only — commands requiring user input will hang and time out. "
        "Prefer short, focused commands. Output is truncated at 64 KB."
    )
    # 输入参数 schema：定义 LLM 需要提供的参数
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
        "required": ["command"],  # command 是必填参数
    }

    # 在子进程中执行 shell 命令，合并 stdout/stderr，超时或非零退出码时返回错误
    # 什么是子进程？就是在当前进程之外启动的另一个程序
    # 为什么用异步？因为执行命令可能需要时间，不能阻塞主事件循环
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        # 获取命令参数（转换为字符串）
        command = str(params["command"])
        # 获取超时参数（默认 60 秒，最大 120 秒）
        timeout = min(int(str(params.get("timeout") or _DEFAULT_TIMEOUT)), 120)

        try:
            # 创建子进程执行命令
            # stdout=asyncio.subprocess.PIPE：捕获标准输出
            # stderr=asyncio.subprocess.STDOUT：将标准错误合并到标准输出
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                # 等待命令执行完成，设置超时时间
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                # 如果超时，杀死进程并清理
                proc.kill()
                await proc.communicate()
                return ToolResult(
                    content=f"[timeout after {timeout}s]",
                    is_error=True,
                    error_type="timeout",
                )
        except Exception as exc:
            # 其他异常（如命令不存在）
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 将输出转换为字符串（处理编码错误）
        output = stdout_bytes.decode("utf-8", errors="replace")
        # 检查是否需要截断
        truncated = len(stdout_bytes) > _MAX_OUTPUT_BYTES
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        # 获取命令退出码（0 表示成功，非 0 表示失败）
        returncode = proc.returncode or 0
        if returncode != 0:
            # 非零退出码，返回错误
            return ToolResult(
                content=f"[exit {returncode}]\n{output}",
                is_error=True,
                error_type="runtime_error",
            )
        # 成功执行，返回输出
        return ToolResult(content=output or "[no output]")
