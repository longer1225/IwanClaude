"""Git 工具模块

这个模块实现了常用的 Git 操作工具集，包括：
1. GitStatusTool - 查看工作区状态
2. GitLogTool - 查看提交历史
3. GitDiffTool - 查看代码差异
4. GitCommitTool - 创建提交
5. GitCheckoutTool - 切换分支或恢复文件

**技术要点：**
- 使用 asyncio.create_subprocess_exec 执行 Git 命令
- 通过 asyncio.wait_for 实现超时控制
- 使用 -C 参数指定仓库路径，避免切换工作目录
- 统一的错误处理和输出格式化

**安全注意事项：**
- 不提供 push/pull 功能，避免敏感操作
- 不提供强制操作（force push, reset --hard 等）
- 提交信息会被记录和审查

**使用示例：**
```python
# 查看状态
result = await git_status_tool.invoke({"path": "."})

# 查看提交历史
result = await git_log_tool.invoke({"path": ".", "limit": 20})

# 查看差异
result = await git_diff_tool.invoke({"path": ".", "staged": False})

# 创建提交
result = await git_commit_tool.invoke({
    "path": ".",
    "message": "fix: bug fix",
    "all": True
})

# 切换分支
result = await git_checkout_tool.invoke({"path": ".", "target": "feature-branch"})
```
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 兜底输出最大字节数，防止大输出导致内存问题
_FALLBACK_OUTPUT_MAX_BYTES = 64 * 1024
# 兜底默认超时时间，单位秒
_FALLBACK_TIMEOUT_S = 60


def _output_max_bytes() -> int:
    """从全局配置读取 git 工具输出截断字节数"""
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.git_output_max_bytes)
    except Exception:
        return _FALLBACK_OUTPUT_MAX_BYTES


def _timeout_s() -> int:
    """从全局配置读取 git 工具默认超时秒数"""
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.git_timeout_s)
    except Exception:
        return _FALLBACK_TIMEOUT_S

# 检测是否为 Windows 平台，用于跨平台兼容
IS_WINDOWS = sys.platform == "win32"


class GitStatusParams(BaseModel):
    """Git 状态参数模型

    可选参数：仓库路径，默认为当前目录。
    """
    model_config = ConfigDict(extra="ignore")
    # Git 仓库路径，默认为当前目录
    path: str = Field(default=".", description="Path to the git repository")


class GitStatusTool(BaseTool):
    """Git 状态工具

    用于查看 Git 仓库的工作区状态，包括：
    - 当前分支
    - 暂存的更改
    - 未暂存的更改
    - 未跟踪的文件
    
    **Git 命令：**
    git -C <path> status --porcelain -b
    
    **--porcelain 格式：**
    - 第一字符：暂存区状态（A=新增, M=修改, D=删除, R=重命名, ?=未跟踪）
    - 第二字符：工作区状态
    - -b：显示分支信息
    """
    params_model = GitStatusParams
    name = "git_status"
    description = (
        "Show the working tree status of a git repository. "
        "Returns staged/unstaged changes, untracked files, and branch information."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """查看 Git 仓库状态

        **执行流程：**
        1. 验证参数，获取仓库路径
        2. 解析路径并转换为绝对路径
        3. 创建子进程执行 git status 命令
        4. 使用 asyncio.wait_for 设置超时
        5. 检查命令返回码
        6. 处理特殊错误（git 命令不存在、非 git 仓库）
        7. 返回格式化的状态信息
        
        **错误处理：**
        - TimeoutError: 超时，返回 [timeout]
        - FileNotFoundError: git 命令不存在
        - 返回码非零：检查是否为非 git 仓库
        
        Args:
            params: 包含 path 的参数字典
            
        Returns:
            ToolResult: 包含状态信息的结果对象
        """
        # 验证参数并获取仓库路径
        p = GitStatusParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        try:
            # 创建子进程执行 git status 命令
            # -C 参数：在指定目录执行命令，避免切换工作目录
            # --porcelain：机器可读格式，便于解析
            # -b：显示分支信息
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "status",
                "--porcelain",
                "-b",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 检查命令返回码
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            # 特殊处理：非 git 仓库
            if "not a git repository" in err_msg:
                return ToolResult(content=f"Not a git repository: {repo_path}", is_error=True, error_type="runtime_error")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        # 解码输出
        output = stdout.decode("utf-8", errors="replace")
        # 检查是否为空输出（工作区干净）
        if not output.strip():
            return ToolResult(content="No changes in the working tree (clean)")
        return ToolResult(content=output)


class GitLogParams(BaseModel):
    """Git 日志参数模型

    可选参数：仓库路径、提交数量限制。
    """
    model_config = ConfigDict(extra="ignore")
    # Git 仓库路径，默认为当前目录
    path: str = Field(default=".", description="Path to the git repository")
    # 显示的提交数量，范围 1-50，默认为 10
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of commits to show")


class GitLogTool(BaseTool):
    """Git 日志工具

    用于查看 Git 仓库的提交历史，包括：
    - 提交哈希
    - 提交日期
    - 提交信息
    - 作者
    
    **Git 命令：**
    git -C <path> log --oneline -n<limit> --format=%h %ad %s (%an) --date=short
    
    **格式说明：**
    - %h: 短哈希
    - %ad: 提交日期
    - %s: 提交信息
    - %an: 作者姓名
    """
    params_model = GitLogParams
    name = "git_log"
    description = (
        "Show recent git commit history. "
        "Returns commit hash, author, date, and message for each commit."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of commits to show (default: 10, max: 50)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """查看 Git 提交历史

        **执行流程：**
        1. 验证参数，获取仓库路径和限制数量
        2. 解析路径并转换为绝对路径
        3. 创建子进程执行 git log 命令
        4. 使用 asyncio.wait_for 设置超时
        5. 检查命令返回码
        6. 返回格式化的提交历史
        
        Args:
            params: 包含 path 和 limit 的参数字典
            
        Returns:
            ToolResult: 包含提交历史的结果对象
        """
        # 验证参数并获取仓库路径和限制数量
        p = GitLogParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        try:
            # 创建子进程执行 git log 命令
            # --oneline: 单行显示
            # -n<limit>: 限制显示数量
            # --format: 自定义输出格式
            # --date=short: 简短日期格式
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "log",
                "--oneline",
                f"-n{p.limit}",
                "--format=%h %ad %s (%an)",
                "--date=short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 检查命令返回码
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        # 解码输出
        output = stdout.decode("utf-8", errors="replace")
        # 检查是否为空输出（无提交）
        if not output.strip():
            return ToolResult(content="No commits found")
        return ToolResult(content=output)


class GitDiffParams(BaseModel):
    """Git 差异参数模型

    可选参数：仓库路径、是否显示暂存区、指定文件。
    """
    model_config = ConfigDict(extra="ignore")
    # Git 仓库路径，默认为当前目录
    path: str = Field(default=".", description="Path to the git repository")
    # 是否显示暂存区的更改，默认为 False（显示工作区）
    staged: bool = Field(default=False, description="Show staged changes instead of working tree")
    # 指定文件，显示特定文件的差异
    file: str | None = Field(default=None, description="Show diff for a specific file")


class GitDiffTool(BaseTool):
    """Git 差异工具

    用于查看代码差异，支持：
    - 工作区与暂存区的差异
    - 暂存区与提交的差异
    - 指定文件的差异
    
    **Git 命令：**
    git -C <path> diff [--cached] [file]
    
    **--cached：**
    显示暂存区与最后一次提交之间的差异
    
    **输出限制：**
    输出被截断在 64 KB，防止大输出导致内存问题
    """
    params_model = GitDiffParams
    name = "git_diff"
    description = (
        "Show changes between commits, commit and working tree, or staged changes. "
        "Output is truncated at 64 KB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
            "staged": {
                "type": "boolean",
                "description": "Show staged changes (default: false)",
            },
            "file": {
                "type": "string",
                "description": "Optional: show diff for a specific file",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """查看代码差异

        **执行流程：**
        1. 验证参数，获取仓库路径、staged 标志和文件
        2. 解析路径并转换为绝对路径
        3. 构建 git diff 命令参数
        4. 创建子进程执行命令
        5. 使用 asyncio.wait_for 设置超时
        6. 检查命令返回码
        7. 处理输出截断（超过 64KB）
        8. 返回差异信息
        
        **命令构建：**
        - 默认：git diff
        - staged=True：git diff --cached
        - 指定文件：git diff [--cached] <file>
        
        Args:
            params: 包含 path、staged、file 的参数字典
            
        Returns:
            ToolResult: 包含差异信息的结果对象
        """
        # 验证参数并获取仓库路径、staged 标志和文件
        p = GitDiffParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        # 构建命令参数
        args = ["-C", str(repo_path), "diff"]
        # 如果指定了 staged，添加 --cached 参数
        if p.staged:
            args.append("--cached")
        # 如果指定了文件，添加文件名参数
        if p.file:
            args.append(p.file)

        try:
            # 创建子进程执行 git diff 命令
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 检查命令返回码
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        # 解码输出
        output = stdout.decode("utf-8", errors="replace")
        # 检查输出大小，超过限制则截断
        max_bytes = _output_max_bytes()
        truncated = len(stdout) > max_bytes
        if truncated:
            output = output[:max_bytes] + "\n[truncated]"
        # 检查是否为空输出（无差异）
        if not output.strip():
            return ToolResult(content="No changes to show")
        return ToolResult(content=output)


class GitCommitParams(BaseModel):
    """Git 提交参数模型

    必填参数：提交消息。
    可选参数：仓库路径、是否自动暂存。
    """
    model_config = ConfigDict(extra="ignore")
    # Git 仓库路径，默认为当前目录
    path: str = Field(default=".", description="Path to the git repository")
    # 提交消息，必填
    message: str = Field(description="Commit message")
    # 是否自动暂存所有修改和删除的文件，默认为 True
    all: bool = Field(default=True, description="Stage all modified and deleted files")


class GitCommitTool(BaseTool):
    """Git 提交工具

    用于创建 Git 提交，支持自动暂存功能。
    
    **执行流程：**
    1. 如果 all=True，先执行 git add -A 暂存所有更改
    2. 执行 git commit -m <message> 创建提交
    
    **安全注意事项：**
    - 不支持 amend（修改最后一次提交）
    - 不支持空提交
    - 提交消息会被记录
    
    **常见错误：**
    - "nothing to commit"：工作区干净，无更改可提交
    """
    params_model = GitCommitParams
    name = "git_commit"
    description = (
        "Create a new git commit with staged changes. "
        "By default stages all modified/deleted files before committing."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
            "message": {
                "type": "string",
                "description": "Commit message",
            },
            "all": {
                "type": "boolean",
                "description": "Stage all modified and deleted files (default: true)",
            },
        },
        "required": ["message"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """创建 Git 提交

        **执行流程：**
        1. 验证参数，获取仓库路径、提交消息和 all 标志
        2. 解析路径并转换为绝对路径
        3. 如果 all=True，执行 git add -A 暂存所有更改
        4. 执行 git commit -m <message> 创建提交
        5. 检查命令返回码
        6. 处理特殊错误（无内容可提交）
        7. 返回提交结果
        
        **暂存逻辑：**
        git add -A：暂存所有修改、删除和新增的文件
        
        **提交逻辑：**
        git commit -m <message>：使用指定消息创建提交
        
        Args:
            params: 包含 path、message、all 的参数字典
            
        Returns:
            ToolResult: 包含提交结果的结果对象
        """
        # 验证参数并获取仓库路径、提交消息和 all 标志
        p = GitCommitParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        # 如果 all=True，先执行 git add -A 暂存所有更改
        if p.all:
            try:
                # 创建子进程执行 git add -A
                add_proc = await asyncio.create_subprocess_exec(
                    "git",
                    "-C",
                    str(repo_path),
                    "add",
                    "-A",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                # 等待命令完成，设置超时
                await asyncio.wait_for(add_proc.communicate(), timeout=_timeout_s())
                # 检查暂存命令返回码
                if add_proc.returncode != 0:
                    return ToolResult(
                        content=f"Failed to stage files: exit code {add_proc.returncode}",
                        is_error=True,
                        error_type="runtime_error",
                    )
            except Exception as exc:
                return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 执行 git commit 创建提交
        try:
            # 创建子进程执行 git commit
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "commit",
                "-m",
                p.message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 检查命令返回码
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            # 特殊处理：无内容可提交
            if "nothing to commit" in err_msg:
                return ToolResult(content="Nothing to commit (working tree clean)", is_error=True, error_type="runtime_error")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        # 解码输出并返回
        output = stdout.decode("utf-8", errors="replace")
        return ToolResult(content=output)


class GitCheckoutParams(BaseModel):
    """Git 检出参数模型

    必填参数：目标分支或提交哈希。
    可选参数：仓库路径。
    """
    model_config = ConfigDict(extra="ignore")
    # Git 仓库路径，默认为当前目录
    path: str = Field(default=".", description="Path to the git repository")
    # 要检出的分支名称或提交哈希，必填
    target: str = Field(description="Branch name or commit hash to checkout")


class GitCheckoutTool(BaseTool):
    """Git 检出工具

    用于切换分支或恢复工作区文件。
    
    **Git 命令：**
    git -C <path> checkout <target>
    
    **功能：**
    - 切换到指定分支
    - 检出指定提交（会进入 detached HEAD 状态）
    
    **安全注意事项：**
    - 不支持强制检出（--force）
    - 不支持创建新分支（-b）
    - 会保留未提交的更改（可能导致冲突）
    """
    params_model = GitCheckoutParams
    name = "git_checkout"
    description = (
        "Switch branches or restore working tree files. "
        "Can checkout a branch name or commit hash."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: current directory)",
            },
            "target": {
                "type": "string",
                "description": "Branch name or commit hash to checkout",
            },
        },
        "required": ["target"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """切换分支或检出提交

        **执行流程：**
        1. 验证参数，获取仓库路径和目标
        2. 解析路径并转换为绝对路径
        3. 创建子进程执行 git checkout 命令
        4. 使用 asyncio.wait_for 设置超时
        5. 检查命令返回码
        6. 返回检出结果
        
        **注意事项：**
        - 如果检出提交哈希，会进入 detached HEAD 状态
        - 如果有未提交的更改，可能会导致冲突
        
        Args:
            params: 包含 path 和 target 的参数字典
            
        Returns:
            ToolResult: 包含检出结果的结果对象
        """
        # 验证参数并获取仓库路径和目标
        p = GitCheckoutParams.model_validate(params)
        repo_path = Path(p.path).resolve()

        try:
            # 创建子进程执行 git checkout 命令
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "checkout",
                p.target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())
        except TimeoutError:
            return ToolResult(content="[timeout]", is_error=True, error_type="timeout")
        except FileNotFoundError:
            return ToolResult(content="git command not found", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True, error_type="runtime_error")

        # 检查命令返回码
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            return ToolResult(content=f"[exit {proc.returncode}]\n{err_msg}", is_error=True, error_type="runtime_error")

        # 解码输出
        output = stdout.decode("utf-8", errors="replace")
        # 如果输出为空，返回默认提示
        return ToolResult(content=output or f"Checked out to: {p.target}")
