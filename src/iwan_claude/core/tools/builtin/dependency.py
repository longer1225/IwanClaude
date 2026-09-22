"""
依赖管理工具模块 - 提供 Python 包管理和依赖检查功能

【学习要点】
1. 异步子进程：使用 asyncio.create_subprocess_exec 执行 pip 命令
2. 超时控制：使用 asyncio.wait_for 防止命令执行过长时间
3. 错误处理：捕获 FileNotFoundError（pip 未安装）和其他异常
4. 输出合并：将 stdout 和 stderr 合并处理

【工具分类】
- PipManageTool：pip 包管理（安装、卸载、冻结、列出、升级）
- DependencyCheckTool：检查过时依赖

【支持的 pip 操作】
- install：安装包
- uninstall：卸载包（自动确认 -y）
- freeze：输出已安装包列表（requirements.txt 格式）
- list：列出已安装包
- upgrade：升级包

【安全注意事项】
- pip 操作可能影响系统环境，应配合权限管理器使用
- 建议在虚拟环境中使用，避免影响全局 Python 环境
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 最大输出字节数（兜底值）：64 KB，防止返回过多内容
_FALLBACK_OUTPUT_MAX_BYTES = 64 * 1024
# 默认超时时间（兜底值）：120 秒（2 分钟），pip 安装可能需要较长时间
_FALLBACK_TIMEOUT_S = 120


def _output_max_bytes() -> int:
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.dependency_output_max_bytes)
    except Exception:
        return _FALLBACK_OUTPUT_MAX_BYTES


def _timeout_s() -> int:
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.dependency_timeout_s)
    except Exception:
        return _FALLBACK_TIMEOUT_S


class PipManageParams(BaseModel):
    """
    pip 管理参数模型

    【字段说明】
    - action: str - 操作类型（install/uninstall/freeze/list/upgrade），必填
    - package: str | None - 包名（install/uninstall/upgrade 操作必需）

    【参数校验】
    - action 会被转换为小写
    - install/uninstall/upgrade 操作必须提供 package 参数
    """
    model_config = ConfigDict(extra="ignore")
    action: str = Field(description="Action: install, uninstall, freeze, list, upgrade")
    package: str | None = Field(default=None, description="Package name (for install/uninstall/upgrade)")


class PipManageTool(BaseTool):
    """
    pip 包管理工具 - 使用 pip 管理 Python 包

    【学习要点】
    1. 异步子进程管理：使用 asyncio.create_subprocess_exec 创建子进程
    2. 参数校验：使用 Pydantic 验证输入参数
    3. 条件分支：根据 action 执行不同的 pip 命令
    4. 错误处理：捕获 pip 未安装、超时、执行异常等情况

    【使用示例】
    ```python
    tool = PipManageTool()
    
    # 安装包
    result = await tool.invoke({"action": "install", "package": "requests"})
    
    # 卸载包
    result = await tool.invoke({"action": "uninstall", "package": "requests"})
    
    # 列出已安装包
    result = await tool.invoke({"action": "list"})
    
    # 生成 requirements.txt 格式
    result = await tool.invoke({"action": "freeze"})
    
    # 升级包
    result = await tool.invoke({"action": "upgrade", "package": "requests"})
    ```

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 参数校验（action 小写、package 必填检查）
    3. 根据 action 创建对应的 pip 子进程
    4. 等待命令完成（带超时控制）
    5. 合并 stdout 和 stderr
    6. 处理输出大小限制
    7. 返回结果

    【pip 命令说明】
    - pip install <pkg>: 安装指定包
    - pip uninstall -y <pkg>: 卸载指定包（-y 自动确认）
    - pip freeze: 输出已安装包（requirements.txt 格式）
    - pip list: 列出已安装包（友好格式）
    - pip install --upgrade <pkg>: 升级指定包
    """
    params_model = PipManageParams
    name = "pip_manage"
    description = (
        "Manage Python packages using pip. "
        "Supported actions: install, uninstall, freeze, list, upgrade."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action to perform: install, uninstall, freeze, list, upgrade",
            },
            "package": {
                "type": "string",
                "description": "Optional: package name (required for install, uninstall, upgrade)",
            },
        },
        "required": ["action"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行 pip 操作

        【参数说明】
        - params: dict - 工具调用参数，包含 action 和 package

        【执行流程详解】
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 验证输入参数（Pydantic）                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 2. 参数校验：                                                │
        │    ├─ action 转换为小写                                      │
        │    └─ install/uninstall/upgrade 必须提供 package             │
        ├─────────────────────────────────────────────────────────────┤
        │ 3. 根据 action 创建对应的 pip 子进程                          │
        │    ├─ install → pip install <pkg>                           │
        │    ├─ uninstall → pip uninstall -y <pkg>                    │
        │    ├─ freeze → pip freeze                                   │
        │    ├─ list → pip list                                       │
        │    └─ upgrade → pip install --upgrade <pkg>                 │
        ├─────────────────────────────────────────────────────────────┤
        │ 4. 等待命令完成（带超时控制）                                    │
        ├─────────────────────────────────────────────────────────────┤
        │ 5. 合并 stdout 和 stderr                                     │
        ├─────────────────────────────────────────────────────────────┤
        │ 6. 处理输出大小限制（超过 64KB 截断）                          │
        ├─────────────────────────────────────────────────────────────┤
        │ 7. 返回结果                                                  │
        └─────────────────────────────────────────────────────────────┘

        【异常处理】
        - FileNotFoundError: pip 命令未找到
        - asyncio.TimeoutError: 命令执行超时（由 wait_for 抛出）
        - Exception: 其他执行异常

        【返回值】
        - ToolResult: 包含命令输出或错误信息
        """
        # 1. 验证输入参数
        p = PipManageParams.model_validate(params)

        # 2. 参数校验
        action = p.action.lower()

        # 检查 install/uninstall/upgrade 操作是否提供了 package 参数
        if action in ["install", "uninstall", "upgrade"] and not p.package:
            return ToolResult(content="Package name is required for install, uninstall, and upgrade", is_error=True, error_type="schema_error")

        try:
            # 3. 根据 action 创建对应的 pip 子进程
            if action == "install":
                # 安装包：pip install <package>
                proc = await asyncio.create_subprocess_exec(
                    "pip", "install", p.package or "",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif action == "uninstall":
                # 卸载包：pip uninstall -y <package>（-y 自动确认）
                proc = await asyncio.create_subprocess_exec(
                    "pip", "uninstall", "-y", p.package or "",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif action == "freeze":
                # 输出已安装包：pip freeze（requirements.txt 格式）
                proc = await asyncio.create_subprocess_exec(
                    "pip", "freeze",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif action == "list":
                # 列出已安装包：pip list（友好格式）
                proc = await asyncio.create_subprocess_exec(
                    "pip", "list",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif action == "upgrade":
                # 升级包：pip install --upgrade <package>
                proc = await asyncio.create_subprocess_exec(
                    "pip", "install", "--upgrade", p.package or "",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                # 未知操作
                return ToolResult(content=f"Unknown action: {action}", is_error=True, error_type="schema_error")

            # 4. 等待命令完成（带超时控制）
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())

            # 5. 合并 stdout 和 stderr
            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\nSTDERR:\n" + stderr.decode("utf-8", errors="replace")

            # 6. 处理输出大小限制
            if len(output) > _output_max_bytes():
                output = output[:_output_max_bytes()] + "\n[truncated]"

            # 7. 返回结果
            return ToolResult(content=output)

        except FileNotFoundError:
            # pip 命令未找到
            return ToolResult(content="pip not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            # 其他异常（超时、执行错误等）
            return ToolResult(content=f"Error: {exc}", is_error=True, error_type="runtime_error")


class DependencyCheckParams(BaseModel):
    """
    依赖检查参数模型

    【字段说明】
    - requirements_file: str | None - requirements.txt 文件路径，默认 "requirements.txt"

    【设计说明】
    - 虽然参数包含 requirements_file，但实际检查使用 pip list --outdated
    - requirements_file 参数主要用于验证项目存在
    """
    model_config = ConfigDict(extra="ignore")
    requirements_file: str | None = Field(default=None, description="Path to requirements.txt")


class DependencyCheckTool(BaseTool):
    """
    依赖检查工具 - 检查过时的 Python 依赖

    【学习要点】
    1. pip 命令：使用 pip list --outdated 检查过时依赖
    2. 前置验证：先检查 requirements.txt 是否存在
    3. 异步执行：使用 asyncio.create_subprocess_exec 执行命令
    4. 输出处理：合并 stdout 和 stderr，处理大小限制

    【使用示例】
    ```python
    tool = DependencyCheckTool()
    
    # 使用默认路径
    result = await tool.invoke({})
    
    # 指定自定义路径
    result = await tool.invoke({"requirements_file": "requirements-dev.txt"})
    ```

    【pip 命令说明】
    - pip list --outdated：列出所有已安装但有新版本可用的包
    - 输出格式包含：包名、当前版本、最新版本、安装位置

    【执行流程】
    1. 验证输入参数（Pydantic）
    2. 检查 requirements.txt 是否存在
    3. 执行 pip list --outdated 命令
    4. 等待命令完成（带超时控制）
    5. 合并 stdout 和 stderr
    6. 处理输出大小限制
    7. 返回结果

    【注意事项】
    - 此工具不检查 requirements.txt 中的版本约束，只检查已安装包
    - 如果需要检查特定包，请使用 pip_manage 工具的 list 操作
    """
    params_model = DependencyCheckParams
    name = "dependency_check"
    description = (
        "Check for outdated dependencies. "
        "Compares installed packages with latest versions on PyPI."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "requirements_file": {
                "type": "string",
                "description": "Optional: path to requirements.txt (default: requirements.txt)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行依赖检查操作

        【参数说明】
        - params: dict - 工具调用参数，包含 requirements_file

        【返回值】
        - ToolResult: 包含过时依赖列表或错误信息
        """
        # 1. 验证输入参数
        p = DependencyCheckParams.model_validate(params)

        # 2. 检查 requirements.txt 是否存在（验证项目存在）
        req_file = p.requirements_file or "requirements.txt"
        req_path = Path(req_file).resolve()

        if not req_path.exists():
            return ToolResult(content=f"Requirements file not found: {req_path}", is_error=True, error_type="runtime_error")

        try:
            # 3. 执行 pip list --outdated 命令
            proc = await asyncio.create_subprocess_exec(
                "pip", "list", "--outdated",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # 4. 等待命令完成（带超时控制）
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())

            # 5. 合并 stdout 和 stderr
            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\nSTDERR:\n" + stderr.decode("utf-8", errors="replace")

            # 6. 处理输出大小限制
            if len(output) > _output_max_bytes():
                output = output[:_output_max_bytes()] + "\n[truncated]"

            # 7. 返回结果
            return ToolResult(content=output)

        except FileNotFoundError:
            # pip 命令未找到
            return ToolResult(content="pip not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            # 其他异常（超时、执行错误等）
            return ToolResult(content=f"Error: {exc}", is_error=True, error_type="runtime_error")