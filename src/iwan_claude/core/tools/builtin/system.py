"""
系统工具 - 提供进程列表等系统信息查询功能

【学习要点】
1. 跨平台支持：Windows 使用 PowerShell，其他平台使用标准命令
2. 进程信息：获取进程 ID、名称、内存使用、启动时间等
3. 过滤功能：支持按进程名称过滤
4. 异步执行：使用 asyncio 创建子进程

【安全注意事项】
- 此工具只读，不执行任何修改操作
- 返回系统敏感信息（进程列表），应配合权限管理器使用

【输出格式】
Windows：
```
 Id ProcessName WorkingSet64 StartTime
 -- ----------- ----------- ---------
 1234 python     123456789   2024-01-15 10:30:45
```

Linux/macOS：
```
USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root         1  0.0  0.1 168768  8192 ?        Ss   Jan15   0:02 /sbin/init
```
"""
from __future__ import annotations

import asyncio
import sys

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大输出字节数：64 KB
_MAX_OUTPUT_BYTES = 64 * 1024
# 默认超时时间：30 秒
_DEFAULT_TIMEOUT = 30

# 检测当前平台：Windows 使用 PowerShell，其他平台使用标准命令
IS_WINDOWS = sys.platform == "win32"


class ProcessListParams(BaseModel):
    """
    进程列表参数模型

    【字段说明】
    - filter: str | None - 进程名称过滤（不区分大小写），可选
    - limit: int - 最大返回进程数，范围 1-200，默认 50
    """
    model_config = ConfigDict(extra="ignore")
    filter: str | None = Field(default=None, description="Filter processes by name")
    limit: int = Field(default=50, ge=1, le=200, description="Maximum number of processes to show")


class ProcessListTool(BaseTool):
    """
    进程列表工具 - 获取系统运行中的进程列表

    【学习要点】
    1. 跨平台命令：
       - Windows：使用 PowerShell 的 Get-Process 命令
       - Linux/macOS：使用 ps aux 命令
    2. 进程排序：按内存使用量降序排列
    3. 结果限制：通过 Select-Object -First 或 head 限制数量
    4. 后期过滤：在获取结果后，按名称过滤（客户端过滤）

    【使用示例】
    ```python
    tool = ProcessListTool()
    
    # 获取所有进程（最多 50 个）
    result = await tool.invoke({})
    
    # 获取特定进程
    result = await tool.invoke({"filter": "python"})
    
    # 获取前 10 个进程
    result = await tool.invoke({"limit": 10})
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 根据平台构建命令
       - Windows：PowerShell Get-Process 命令
       - Linux/macOS：ps aux 命令
    3. 创建子进程并执行命令
    4. 等待命令完成（带超时控制）
    5. 处理输出（解码、截断）
    6. 如果指定了 filter，进行客户端过滤
    7. 返回结果
    """
    params_model = ProcessListParams
    name = "process_list"
    description = (
        "List running processes on the system. "
        "Returns process ID, name, and memory usage. "
        "Can filter by process name and limit results."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "Optional: filter processes by name (case-insensitive)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of processes to show (default: 50, max: 200)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        获取进程列表

        【执行流程详解】
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 验证输入参数（Pydantic）                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 2. 根据平台构建命令                                           │
        │    ├─ Windows → PowerShell Get-Process                      │
        │    └─ Linux/macOS → ps aux                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 3. 创建子进程并执行命令                                        │
        ├─────────────────────────────────────────────────────────────┤
        │ 4. 等待命令完成（带超时控制）                                    │
        ├─────────────────────────────────────────────────────────────┤
        │ 5. 处理输出（解码、截断）                                       │
        ├─────────────────────────────────────────────────────────────┤
        │ 6. 如果指定了 filter，进行客户端过滤                             │
        ├─────────────────────────────────────────────────────────────┤
        │ 7. 返回结果                                                  │
        └─────────────────────────────────────────────────────────────┘
        """
        # 1. 验证输入参数
        p = ProcessListParams.model_validate(params)

        try:
            if IS_WINDOWS:
                # Windows：使用 PowerShell 获取进程列表
                # Select-Object Id,Name,WorkingSet64,StartTime：选择需要的字段
                # Sort-Object WorkingSet64 -Descending：按内存使用量降序排列
                # Select-Object -First {p.limit}：限制结果数量
                # Format-Table -AutoSize：自动调整列宽
                command = (
                    f"Get-Process | Select-Object Id,Name,WorkingSet64,StartTime | "
                    f"Sort-Object WorkingSet64 -Descending | Select-Object -First {p.limit} | "
                    f"Format-Table -AutoSize"
                )
                ps_command = _quote_powershell(command)
                proc = await asyncio.create_subprocess_exec(
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    ps_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            else:
                # Linux / macOS：使用 ps aux 获取进程列表
                # --sort=-%mem：按内存使用百分比降序排列
                # head -{p.limit}：限制结果数量
                command = f"ps aux --sort=-%mem | head -{p.limit}"
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

            # 等待命令完成（带超时控制）
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)

        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 处理输出（解码、截断）
        output = stdout_bytes.decode("utf-8", errors="replace")
        truncated = len(stdout_bytes) > _MAX_OUTPUT_BYTES
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        # 如果指定了 filter，进行客户端过滤
        if p.filter:
            lines = output.splitlines()
            # 保留表头（第一行）
            filtered = [lines[0]] if lines else []
            for line in lines[1:]:
                if p.filter.lower() in line.lower():
                    filtered.append(line)
            output = "\n".join(filtered)

        # 处理空结果
        if not output.strip():
            return ToolResult(content="No processes found")

        return ToolResult(content=output)


def _quote_powershell(command: str) -> str:
    """
    将命令字符串包装成 PowerShell -Command 能正确解析的形式

    【参数说明】
    - command: str - 原始命令字符串

    【返回值】
    - str: 转义后的 PowerShell 命令

    【转义规则】
    - 单引号 ' 需要转义为两个单引号 ''
    - 命令需要用 & {} 格式包装

    【示例】
    - "Get-Process" → "& {Get-Process}"
    """
    escaped = command.replace("'", "''")
    return f"& {{{escaped}}}"