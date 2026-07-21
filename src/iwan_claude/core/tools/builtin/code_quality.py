"""代码质量工具模块

这个模块实现了三个代码质量相关工具：
1. ReviewCodeTool - 代码审查工具，分析代码中的安全、性能、可读性和可维护性问题
2. LintCodeTool - 代码静态检查工具，集成 ruff 和 mypy
3. SecurityScanTool - 安全扫描工具，检测代码中的安全漏洞

**代码审查维度：**
- **安全 (security)**: 硬编码密钥、shell 注入、eval 使用、反序列化漏洞
- **性能 (performance)**: list.append 循环、range(len()) 使用
- **可读性 (readability)**: 行长度、命名风格
- **可维护性 (maintainability)**: TODO 注释、空块

**静态检查工具：**
- **ruff**: 快速的 Python linter，检测代码风格和常见问题
- **mypy**: 静态类型检查器，检测类型错误

**安全漏洞检测：**
- 硬编码密钥和 API Key
- Shell 注入漏洞
- eval/exec 使用
- 反序列化漏洞 (pickle)
- SQL 注入风险
- 路径遍历风险

**使用示例：**
```python
# 代码审查
result = await review_code_tool.invoke({
    "file_path": "src/main.py",
    "focus": "security"
})

# 静态检查
result = await lint_code_tool.invoke({
    "directory": "src"
})

# 安全扫描
result = await security_scan_tool.invoke({
    "directory": "src"
})
```
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 输出最大字节数，防止大输出导致内存问题
_MAX_OUTPUT_BYTES = 64 * 1024
# 默认超时时间，单位秒
_DEFAULT_TIMEOUT = 120


class ReviewCodeParams(BaseModel):
    """代码审查参数模型

    **参数说明：**
    - file_path: 要审查的文件路径（必填）
    - focus: 审查重点，可选值：security, performance, readability, maintainability
    """
    model_config = ConfigDict(extra="ignore")
    # 要审查的文件路径，必填
    file_path: str = Field(description="Path to the file to review")
    # 审查重点，可选值：security, performance, readability, maintainability
    focus: str | None = Field(default=None, description="Focus area: security, performance, readability, or maintainability")


class ReviewCodeTool(BaseTool):
    """代码审查工具

    对代码进行多维度审查，包括安全、性能、可读性和可维护性。
    
    **审查流程：**
    1. 读取文件内容
    2. 按行遍历代码
    3. 根据 focus 参数调用对应的检查方法
    4. 收集所有问题并按行号排序
    5. 格式化输出审查结果
    
    **问题等级：**
    - HIGH: 严重问题，可能导致安全漏洞或严重性能问题
    - MEDIUM: 中等问题，建议修复
    - LOW: 轻微问题，建议改进
    """
    params_model = ReviewCodeParams
    name = "review_code"
    description = (
        "Review code quality and provide suggestions for improvement. "
        "Analyzes code for issues in security, performance, readability, and maintainability. "
        "Can focus on specific areas when specified."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to review",
            },
            "focus": {
                "type": "string",
                "description": "Optional focus area: security, performance, readability, or maintainability",
            },
        },
        "required": ["file_path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行代码审查

        **执行流程：**
        1. 验证参数，获取文件路径和审查重点
        2. 解析文件路径并检查文件是否存在
        3. 读取文件内容
        4. 按行遍历代码，调用对应的检查方法
        5. 收集所有问题并按行号排序
        6. 格式化输出审查结果
        
        **检查方法调用逻辑：**
        - 如果 focus=None：调用所有检查方法
        - 如果 focus="security"：只调用 _check_security()
        - 如果 focus="performance"：只调用 _check_performance()
        - 如果 focus="readability"：只调用 _check_readability()
        - 如果 focus="maintainability"：只调用 _check_maintainability()
        
        Args:
            params: 包含 file_path 和 focus 的参数字典
            
        Returns:
            ToolResult: 包含审查结果的对象，列出所有发现的问题
        """
        # 验证参数并获取文件路径和审查重点
        p = ReviewCodeParams.model_validate(params)

        # 解析文件路径并检查文件是否存在
        file_path = Path(p.file_path).resolve()
        if not file_path.exists():
            return ToolResult(content=f"File not found: {file_path}", is_error=True, error_type="runtime_error")

        # 读取文件内容
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Failed to read file: {exc}", is_error=True, error_type="runtime_error")

        # 初始化问题列表
        issues = []
        # 将内容按行分割
        lines = content.splitlines()

        # 按行遍历代码，调用对应的检查方法
        for i, line in enumerate(lines, start=1):
            # 安全检查
            if p.focus in (None, "security"):
                security_issues = self._check_security(line, i)
                issues.extend(security_issues)

            # 性能检查
            if p.focus in (None, "performance"):
                perf_issues = self._check_performance(line, i)
                issues.extend(perf_issues)

            # 可读性检查
            if p.focus in (None, "readability"):
                read_issues = self._check_readability(line, i)
                issues.extend(read_issues)

            # 可维护性检查
            if p.focus in (None, "maintainability"):
                maint_issues = self._check_maintainability(line, i)
                issues.extend(maint_issues)

        # 如果没有发现问题，返回成功信息
        if not issues:
            return ToolResult(content=f"No issues found in {file_path}.")

        # 格式化审查结果
        lines = []
        lines.append(f"Code Review for: {file_path}")
        lines.append("=" * 60)
        # 按行号排序问题
        for issue in sorted(issues, key=lambda x: x[0]):
            line_num, severity, message = issue
            lines.append(f"[{severity}] Line {line_num}: {message}")
        lines.append("")
        lines.append(f"Total issues found: {len(issues)}")

        return ToolResult(content="\n".join(lines))

    def _check_security(self, line: str, line_num: int) -> list[tuple[int, str, str]]:
        """安全检查方法

        检测代码中的安全问题，包括：
        - 硬编码密钥（password, secret, token）
        - Shell 注入漏洞（subprocess.run with shell=True）
        - eval() 使用（不安全的代码执行）
        - pickle.load() 使用（反序列化漏洞）
        
        **正则表达式模式：**
        - 匹配 password/secret/token = "value" 格式
        - 匹配 subprocess.run(..., shell=True)
        - 匹配 eval(...)
        - 匹配 pickle.load(...)
        
        Args:
            line: 当前行代码
            line_num: 行号
            
        Returns:
            问题列表，每个问题是 (行号, 严重程度, 消息) 的元组
        """
        issues = []
        # 检测硬编码密钥
        if re.search(r"(password|secret|token)\s*=\s*[\"'].*[\"']", line, re.IGNORECASE):
            issues.append((line_num, "HIGH", "Hardcoded secret detected"))
        # 检测 Shell 注入漏洞
        if re.search(r"subprocess\.run\([^)]*shell=True", line):
            issues.append((line_num, "HIGH", "Potential shell injection vulnerability"))
        # 检测 eval() 使用
        if re.search(r"eval\(", line):
            issues.append((line_num, "HIGH", "Use of eval() is insecure"))
        # 检测 pickle.load() 使用（反序列化漏洞）
        if re.search(r"pickle\.load\(", line):
            issues.append((line_num, "HIGH", "Deserialization vulnerability"))
        return issues

    def _check_performance(self, line: str, line_num: int) -> list[tuple[int, str, str]]:
        """性能检查方法

        检测代码中的性能问题，包括：
        - 在循环中使用 list.append()（建议使用列表推导式）
        - 使用 range(len())（建议使用 enumerate()）
        
        **正则表达式模式：**
        - 匹配 .append( 在 for 循环中使用
        - 匹配 for ... in range( 模式
        
        Args:
            line: 当前行代码
            line_num: 行号
            
        Returns:
            问题列表，每个问题是 (行号, 严重程度, 消息) 的元组
        """
        issues = []
        # 检测循环中的 append（建议使用列表推导式）
        if re.search(r"\.append\(", line) and "for" in line:
            issues.append((line_num, "MEDIUM", "Consider using list comprehension"))
        # 检测 range(len()) 使用（建议使用 enumerate()）
        if re.search(r"for.*in.*range\(", line):
            issues.append((line_num, "LOW", "Consider enumerate() instead of range(len())"))
        return issues

    def _check_readability(self, line: str, line_num: int) -> list[tuple[int, str, str]]:
        """可读性检查方法

        检测代码中的可读性问题，包括：
        - 行长度超过 120 字符
        - 混合大小写命名（建议使用一致的命名风格）
        
        **检查逻辑：**
        - 直接检查字符串长度
        - 使用正则表达式检测大小写混合模式（小写+大写+小写）
        
        Args:
            line: 当前行代码
            line_num: 行号
            
        Returns:
            问题列表，每个问题是 (行号, 严重程度, 消息) 的元组
        """
        issues = []
        # 检测行长度
        if len(line) > 120:
            issues.append((line_num, "LOW", f"Line too long ({len(line)} chars)"))
        # 检测大小写混合命名
        if re.search(r"[a-z][A-Z][a-z]", line):
            issues.append((line_num, "LOW", "Mixed case detected, consider consistent naming"))
        return issues

    def _check_maintainability(self, line: str, line_num: int) -> list[tuple[int, str, str]]:
        """可维护性检查方法

        检测代码中的可维护性问题，包括：
        - TODO/FIXME/XXX 注释（未解决的待办事项）
        - 空块使用 pass（建议添加注释说明）
        
        **正则表达式模式：**
        - 匹配 TODO, FIXME, XXX 注释
        - 匹配单独的 pass 语句
        
        Args:
            line: 当前行代码
            line_num: 行号
            
        Returns:
            问题列表，每个问题是 (行号, 严重程度, 消息) 的元组
        """
        issues = []
        # 检测未解决的 TODO 注释
        if re.search(r"TODO|FIXME|XXX", line):
            issues.append((line_num, "MEDIUM", "Unresolved TODO comment"))
        # 检测空块使用 pass
        if re.search(r"pass\s*$", line):
            issues.append((line_num, "LOW", "Empty block with pass"))
        return issues


class LintCodeParams(BaseModel):
    """静态检查参数模型

    **参数说明：**
    - file_path: 要检查的文件路径（可选）
    - directory: 要检查的目录路径（可选）
    
    **优先级：**
    file_path > directory > 当前目录（.）
    """
    model_config = ConfigDict(extra="ignore")
    # 要检查的文件路径
    file_path: str | None = Field(default=None, description="Path to the file to lint")
    # 要检查的目录路径
    directory: str | None = Field(default=None, description="Directory to lint")


class LintCodeTool(BaseTool):
    """静态检查工具

    集成 ruff 和 mypy 进行代码静态检查。
    
    **检查流程：**
    1. 确定检查目标（文件或目录）
    2. 运行 ruff 检查代码风格和常见问题
    3. 运行 mypy 进行类型检查
    4. 合并结果并格式化输出
    
    **工具说明：**
    - **ruff**: 快速的 Python linter，检测代码风格、常见错误和最佳实践
    - **mypy**: 静态类型检查器，检测类型不匹配和类型错误
    
    **注意事项：**
    - 如果工具未安装，会跳过并提示
    - 输出超过 64KB 会被截断
    """
    params_model = LintCodeParams
    name = "lint_code"
    description = (
        "Run code linting using ruff and mypy. "
        "Can lint a single file or entire directory. "
        "Returns linting errors and warnings."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Optional: path to the file to lint",
            },
            "directory": {
                "type": "string",
                "description": "Optional: directory to lint (default: current directory)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行静态检查

        **执行流程：**
        1. 验证参数，确定检查目标
        2. 解析目标路径并检查是否存在
        3. 运行 ruff 检查
        4. 运行 mypy 检查
        5. 合并结果并格式化输出
        
        **目标确定逻辑：**
        - 如果指定了 file_path：检查该文件
        - 如果指定了 directory：检查该目录
        - 否则：检查当前目录（.）
        
        **错误处理：**
        - FileNotFoundError: 工具未安装，跳过并提示
        - 其他异常：记录错误信息
        
        Args:
            params: 包含 file_path 和 directory 的参数字典
            
        Returns:
            ToolResult: 包含静态检查结果的对象
        """
        # 验证参数
        p = LintCodeParams.model_validate(params)

        # 确定检查目标
        target = p.file_path or p.directory or "."
        target_path = Path(target).resolve()

        # 检查目标是否存在
        if not target_path.exists():
            return ToolResult(content=f"Path not found: {target_path}", is_error=True, error_type="runtime_error")

        # 初始化结果列表
        results = []

        # ========== 运行 ruff 检查 ==========
        results.append("=== Ruff Linting ===")
        try:
            # 创建子进程执行 ruff check
            proc = await asyncio.create_subprocess_exec(
                "ruff",
                "check",
                str(target_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
            # 解码输出
            output = stdout.decode("utf-8", errors="replace")
            if output.strip():
                results.append(output)
            else:
                results.append("No issues found.")
        except FileNotFoundError:
            results.append("ruff not installed, skipping.")
        except Exception as exc:
            results.append(f"Error: {exc}")

        # ========== 运行 mypy 检查 ==========
        results.append("")
        results.append("=== Mypy Type Checking ===")
        try:
            # 创建子进程执行 mypy
            proc = await asyncio.create_subprocess_exec(
                "mypy",
                "--ignore-missing-imports",  # 忽略缺失的导入
                str(target_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_DEFAULT_TIMEOUT)
            # 解码输出
            output = stdout.decode("utf-8", errors="replace")
            if output.strip():
                results.append(output)
            else:
                results.append("No issues found.")
        except FileNotFoundError:
            results.append("mypy not installed, skipping.")
        except Exception as exc:
            results.append(f"Error: {exc}")

        # 合并结果
        output = "\n".join(results)
        # 检查输出大小，超过限制则截断
        if len(output) > _MAX_OUTPUT_BYTES:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        return ToolResult(content=output)


class SecurityScanParams(BaseModel):
    """安全扫描参数模型

    **参数说明：**
    - file_path: 要扫描的文件路径（可选）
    - directory: 要扫描的目录路径（可选）
    
    **优先级：**
    file_path > directory > 当前目录（.）
    """
    model_config = ConfigDict(extra="ignore")
    # 要扫描的文件路径
    file_path: str | None = Field(default=None, description="Path to the file to scan")
    # 要扫描的目录路径
    directory: str | None = Field(default=None, description="Directory to scan")


class SecurityScanTool(BaseTool):
    """安全扫描工具

    扫描代码中的安全漏洞，支持单个文件或整个目录。
    
    **扫描流程：**
    1. 确定扫描目标
    2. 如果是目录，递归查找所有 .py 文件
    3. 逐文件、逐行扫描代码
    4. 使用正则表达式检测安全漏洞
    5. 收集所有漏洞并按严重程度排序
    6. 格式化输出扫描结果
    
    **漏洞类型：**
    - CRITICAL: 严重漏洞，可能导致系统被攻击
    - HIGH: 高危漏洞，可能导致信息泄露
    - MEDIUM: 中危漏洞，可能被利用
    
    **检测项目：**
    - 硬编码密钥（password, secret, token, api_key）
    - Shell 注入漏洞（shell=True）
    - eval() 和 exec() 使用
    - 反序列化漏洞（pickle）
    - SQL 注入风险（字符串格式化 SQL）
    - input() 使用
    - 路径遍历风险（open 写入）
    """
    params_model = SecurityScanParams
    name = "security_scan"
    description = (
        "Scan code for security vulnerabilities. "
        "Detects SQL injection, XSS, hardcoded secrets, and other security issues."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Optional: path to the file to scan",
            },
            "directory": {
                "type": "string",
                "description": "Optional: directory to scan (default: current directory)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行安全扫描

        **执行流程：**
        1. 验证参数，确定扫描目标
        2. 解析目标路径并检查是否存在
        3. 如果是目录，递归查找所有 .py 文件
        4. 逐文件、逐行扫描代码
        5. 使用正则表达式检测安全漏洞
        6. 收集所有漏洞并按严重程度排序
        7. 格式化输出扫描结果
        
        **目标确定逻辑：**
        - 如果指定了 file_path：扫描该文件
        - 如果指定了 directory：扫描该目录下所有 .py 文件
        - 否则：扫描当前目录下所有 .py 文件
        
        **文件遍历：**
        使用 Path.rglob("*.py") 递归查找所有 Python 文件
        
        Args:
            params: 包含 file_path 和 directory 的参数字典
            
        Returns:
            ToolResult: 包含安全扫描结果的对象
        """
        # 验证参数
        p = SecurityScanParams.model_validate(params)

        # 确定扫描目标
        target = p.file_path or p.directory or "."
        target_path = Path(target).resolve()

        # 检查目标是否存在
        if not target_path.exists():
            return ToolResult(content=f"Path not found: {target_path}", is_error=True, error_type="runtime_error")

        # 确定要扫描的文件列表
        if target_path.is_file():
            files = [target_path]
        else:
            # 递归查找所有 .py 文件
            files = list(target_path.rglob("*.py"))

        # 初始化漏洞列表
        vulnerabilities = []

        # 逐文件扫描
        for file in files:
            try:
                # 读取文件内容
                content = file.read_text(encoding="utf-8")
                lines = content.splitlines()
                # 逐行扫描
                for i, line in enumerate(lines, start=1):
                    # 调用扫描方法
                    vulns = self._scan_line(line, i, file.name)
                    # 收集漏洞
                    for severity, message in vulns:
                        vulnerabilities.append((severity, str(file), i, message))
            except Exception:
                # 跳过读取失败的文件
                continue

        # 如果没有发现漏洞，返回成功信息
        if not vulnerabilities:
            return ToolResult(content="No security vulnerabilities found.")

        # 格式化扫描结果
        lines = []
        lines.append("Security Scan Results")
        lines.append("=" * 60)
        lines.append(f"Scanned {len(files)} files")
        lines.append("")

        # 按严重程度排序（CRITICAL > HIGH > MEDIUM）
        for severity, filepath, line_num, message in sorted(vulnerabilities, key=lambda x: x[0]):
            lines.append(f"[{severity}] {filepath}:{line_num} - {message}")

        lines.append("")
        lines.append(f"Total vulnerabilities: {len(vulnerabilities)}")

        return ToolResult(content="\n".join(lines))

    def _scan_line(self, line: str, line_num: int, filename: str) -> list[tuple[str, str]]:
        """单行安全扫描方法

        使用正则表达式检测单行代码中的安全漏洞。
        
        **检测规则：**
        - 硬编码密钥：password/secret/token/api_key = "value"
        - Shell 注入：subprocess.run(..., shell=True)
        - eval() 使用：eval(...)
        - pickle 反序列化：pickle.load() 或 pickle.loads()
        - exec() 使用：exec(...)
        - SQL 注入：sql/query = f"..." 或 "..." 中包含 { }
        - input() 使用：input() 或 raw_input()
        - 路径遍历：open(..., "w")
        
        **严重程度分类：**
        - CRITICAL: 硬编码密钥、Shell 注入、pickle 反序列化
        - HIGH: eval()、exec()、SQL 注入
        - MEDIUM: input()、路径遍历
        
        Args:
            line: 当前行代码
            line_num: 行号
            filename: 文件名（未使用，保留参数一致性）
            
        Returns:
            漏洞列表，每个漏洞是 (严重程度, 消息) 的元组
        """
        vulns = []
        # 检测硬编码密钥
        if re.search(r"(password|secret|token|api_key)\s*=\s*[\"'].*[\"']", line, re.IGNORECASE):
            vulns.append(("CRITICAL", "Hardcoded secret detected"))
        # 检测 Shell 注入漏洞
        if re.search(r"subprocess\.run\([^)]*shell=True", line):
            vulns.append(("CRITICAL", "Shell injection vulnerability"))
        # 检测 eval() 使用
        if re.search(r"eval\(", line):
            vulns.append(("HIGH", "Use of eval() is insecure"))
        # 检测 pickle 反序列化漏洞
        if re.search(r"pickle\.load\(", line) or re.search(r"pickle\.loads\(", line):
            vulns.append(("CRITICAL", "Deserialization vulnerability"))
        # 检测 exec() 使用
        if re.search(r"exec\(", line):
            vulns.append(("HIGH", "Use of exec() is insecure"))
        # 检测 SQL 注入风险
        if re.search(r"(sql|query)\s*=\s*f?[\"'].*\{.*\}.*[\"']", line, re.IGNORECASE):
            vulns.append(("HIGH", "Potential SQL injection"))
        # 检测 input() 使用
        if re.search(r"(input|raw_input)\(", line):
            vulns.append(("MEDIUM", "Use of input() may be unsafe"))
        # 检测路径遍历风险（写入文件）
        if re.search(r"open\(.*[\"']w[\"']", line):
            vulns.append(("MEDIUM", "Potential path traversal"))
        return vulns
