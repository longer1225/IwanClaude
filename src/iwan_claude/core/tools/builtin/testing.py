"""测试工具模块

这个模块实现了三个测试相关工具：
1. GenerateTestsTool - 测试代码生成工具，根据 Python 文件生成 pytest 测试用例
2. RunTestsTool - 测试运行工具，执行 pytest 测试并返回结果
3. TestCoverageTool - 测试覆盖率工具，运行测试并生成覆盖率报告

**测试生成逻辑：**
1. 使用正则表达式提取文件中的函数和类
2. 排除特殊方法（__init__, __str__, __repr__, __eq__）
3. 为每个函数生成测试用例
4. 为每个类生成实例化测试用例

**测试运行工具：**
- 使用 pytest 执行测试
- 支持指定测试文件或目录
- 支持详细输出模式

**测试覆盖率工具：**
- 使用 coverage 模块运行测试
- 生成覆盖率报告
- 显示未覆盖的行号

**使用示例：**
```python
# 生成测试
result = await generate_tests_tool.invoke({
    "file_path": "src/utils.py",
    "output_path": "tests/test_utils.py"
})

# 运行测试
result = await run_tests_tool.invoke({
    "test_path": "tests/",
    "verbose": True
})

# 测试覆盖率
result = await test_coverage_tool.invoke({
    "source_path": "src/"
})
```
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 输出最大字节数兜底值，防止大输出导致内存问题
_FALLBACK_OUTPUT_MAX_BYTES = 64 * 1024
# 默认超时时间兜底值，单位秒（测试可能需要较长时间）
_FALLBACK_TIMEOUT_S = 180


def _output_max_bytes() -> int:
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.testing_output_max_bytes)
    except Exception:
        return _FALLBACK_OUTPUT_MAX_BYTES


def _timeout_s() -> int:
    try:
        from iwan_claude.core.config import get_config
        return int(get_config().tools.testing_timeout_s)
    except Exception:
        return _FALLBACK_TIMEOUT_S


class GenerateTestsParams(BaseModel):
    """测试生成参数模型

    **参数说明：**
    - file_path: 要生成测试的文件路径（必填）
    - output_path: 测试文件输出路径（可选，默认为 tests/test_<filename>.py）
    """
    model_config = ConfigDict(extra="ignore")
    # 要生成测试的文件路径，必填
    file_path: str = Field(description="Path to the file to generate tests for")
    # 测试文件输出路径，可选
    output_path: str | None = Field(default=None, description="Path to save test file")


class GenerateTestsTool(BaseTool):
    '''测试代码生成工具

    根据 Python 文件自动生成 pytest 测试用例。
    
    **生成流程：**
    1. 读取源文件内容
    2. 使用正则表达式提取函数和类
    3. 排除特殊方法（__init__, __str__, __repr__, __eq__）
    4. 为每个函数生成测试用例
    5. 为每个类生成实例化测试用例
    6. 将测试代码写入指定路径
    
    **测试代码模板：**
    ```python
    import pytest
    from module_name import func1, func2, Class1, Class2

    def test_func1():
        """Test func1 function"""
        result = func1()
        assert result is not None

    def test_Class1_instantiation():
        """Test Class1 class instantiation"""
        instance = Class1()
        assert isinstance(instance, Class1)
    ```
    
    **注意事项：**
    - 生成的测试用例是基础模板，需要手动完善断言
    - 不支持生成复杂的参数化测试
    - 不支持生成异步函数的测试
    '''
    params_model = GenerateTestsParams
    name = "generate_tests"
    description = (
        "Generate unit tests for a Python file. "
        "Analyzes the code and generates comprehensive test cases using pytest."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to generate tests for",
            },
            "output_path": {
                "type": "string",
                "description": "Optional: path to save the test file (defaults to tests/test_<filename>.py)",
            },
        },
        "required": ["file_path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """生成测试代码

        **执行流程：**
        1. 验证参数，获取文件路径和输出路径
        2. 解析文件路径并检查文件是否存在
        3. 读取文件内容
        4. 提取函数和类
        5. 生成测试代码
        6. 确定输出路径
        7. 创建输出目录并写入测试文件
        8. 返回生成结果
        
        **输出路径确定逻辑：**
        - 如果指定了 output_path：使用指定路径
        - 否则：使用 tests/test_<filename>.py
        
        Args:
            params: 包含 file_path 和 output_path 的参数字典
            
        Returns:
            ToolResult: 包含生成结果和测试代码的对象
        """
        # 验证参数并获取文件路径和输出路径
        p = GenerateTestsParams.model_validate(params)

        # 解析文件路径并检查文件是否存在
        file_path = Path(p.file_path).resolve()
        if not file_path.exists():
            return ToolResult(content=f"File not found: {file_path}", is_error=True, error_type="runtime_error")

        # 读取文件内容
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(content=f"Failed to read file: {exc}", is_error=True, error_type="runtime_error")

        # 提取函数和类
        functions, classes = self._extract_functions_and_classes(content)

        # 如果没有找到函数或类，返回提示信息
        if not functions and not classes:
            return ToolResult(content="No functions or classes found to test.")

        # 生成测试代码
        test_content = self._generate_test_code(file_path.stem, functions, classes)

        # 确定输出路径
        if p.output_path:
            output_path = Path(p.output_path).resolve()
        else:
            # 默认输出到 tests/test_<filename>.py
            output_path = Path("tests") / f"test_{file_path.stem}.py"

        # 创建输出目录（如果不存在）
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # 写入测试文件
        output_path.write_text(test_content, encoding="utf-8")

        return ToolResult(content=f"Tests generated at: {output_path}\n\n{test_content}")

    def _extract_functions_and_classes(self, content: str) -> tuple[list[str], list[str]]:
        r"""提取函数和类

        使用正则表达式从代码中提取函数名和类名。
        
        **正则表达式模式：**
        - 函数模式：def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(
        - 类模式：class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*
        
        **排除规则：**
        - 排除特殊方法：__init__, __str__, __repr__, __eq__
        - 这些方法通常不需要单独测试
        
        Args:
            content: 文件内容
            
        Returns:
            (函数列表, 类列表) 的元组
        """
        functions = []
        classes = []
        import re

        # 函数匹配模式：def func_name(
        func_pattern = r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
        # 类匹配模式：class ClassName
        class_pattern = r"class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*"

        # 提取函数
        for match in re.finditer(func_pattern, content):
            func_name = match.group(1)
            # 排除特殊方法
            if func_name not in ["__init__", "__str__", "__repr__", "__eq__"]:
                functions.append(func_name)

        # 提取类
        for match in re.finditer(class_pattern, content):
            class_name = match.group(1)
            classes.append(class_name)

        return functions, classes

    def _generate_test_code(self, module_name: str, functions: list[str], classes: list[str]) -> str:
        """生成测试代码

        根据提取的函数和类生成 pytest 测试代码。
        
        **代码结构：**
        1. import pytest
        2. from module_name import func1, func2, Class1, Class2
        3. 为每个函数生成 test_func_name()
        4. 为每个类生成 test_ClassName_instantiation()
        
        **测试用例模板：**
        - 函数测试：调用函数并断言结果不为 None
        - 类测试：实例化类并断言是正确类型
        
        Args:
            module_name: 模块名称（不含 .py 扩展名）
            functions: 函数列表
            classes: 类列表
            
        Returns:
            生成的测试代码字符串
        """
        lines = []
        # 添加 pytest 导入
        lines.append("import pytest")
        # 添加模块导入
        lines.append(f"from {module_name} import {', '.join(functions + classes)}")
        lines.append("")
        lines.append("")

        # 为每个函数生成测试用例
        for func in functions:
            lines.append(f"def test_{func}():")
            lines.append(f"    \"\"\"Test {func} function\"\"\"")
            lines.append(f"    result = {func}()")
            lines.append(f"    assert result is not None")
            lines.append("")

        # 为每个类生成测试用例
        for cls in classes:
            lines.append(f"def test_{cls}_instantiation():")
            lines.append(f"    \"\"\"Test {cls} class instantiation\"\"\"")
            lines.append(f"    instance = {cls}()")
            lines.append(f"    assert isinstance(instance, {cls})")
            lines.append("")

        return "\n".join(lines)


class RunTestsParams(BaseModel):
    """测试运行参数模型

    **参数说明：**
    - test_path: 测试文件或目录路径（可选，默认为 tests/）
    - verbose: 是否详细输出（可选，默认为 False）
    """
    model_config = ConfigDict(extra="ignore")
    # 测试文件或目录路径
    test_path: str | None = Field(default=None, description="Path to test file or directory")
    # 是否详细输出
    verbose: bool = Field(default=False, description="Verbose output")


class RunTestsTool(BaseTool):
    """测试运行工具

    使用 pytest 执行测试并返回结果。
    
    **执行流程：**
    1. 确定测试路径
    2. 检查路径是否存在
    3. 构建 pytest 命令参数
    4. 创建子进程执行 pytest
    5. 捕获输出并处理
    6. 返回测试结果
    
    **命令参数：**
    - 默认：pytest <test_path>
    - verbose=True：pytest <test_path> -v
    
    **注意事项：**
    - 需要安装 pytest
    - 输出超过 64KB 会被截断
    """
    params_model = RunTestsParams
    name = "run_tests"
    description = (
        "Run pytest tests. "
        "Can run tests in a specific file or entire test directory."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "test_path": {
                "type": "string",
                "description": "Optional: path to test file or directory (default: tests/)",
            },
            "verbose": {
                "type": "boolean",
                "description": "Optional: verbose output (default: false)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """运行测试

        **执行流程：**
        1. 验证参数，获取测试路径和详细模式
        2. 确定测试路径（默认 tests/）
        3. 检查路径是否存在
        4. 构建 pytest 命令参数
        5. 创建子进程执行 pytest
        6. 等待命令完成，设置超时
        7. 合并 stdout 和 stderr
        8. 处理输出截断
        9. 返回测试结果
        
        **路径确定逻辑：**
        - 如果指定了 test_path：使用指定路径
        - 否则：使用 tests/
        
        **错误处理：**
        - FileNotFoundError: pytest 未安装
        - 其他异常：记录错误信息
        
        Args:
            params: 包含 test_path 和 verbose 的参数字典
            
        Returns:
            ToolResult: 包含测试结果的对象
        """
        # 验证参数并获取测试路径和详细模式
        p = RunTestsParams.model_validate(params)

        # 确定测试路径
        test_path = p.test_path or "tests"
        target_path = Path(test_path).resolve()

        # 检查路径是否存在
        if not target_path.exists():
            return ToolResult(content=f"Path not found: {target_path}", is_error=True, error_type="runtime_error")

        try:
            # 构建 pytest 命令参数
            args = ["pytest", str(target_path)]
            if p.verbose:
                args.append("-v")

            # 创建子进程执行 pytest
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())

            # 解码输出
            output = stdout.decode("utf-8", errors="replace")
            # 如果有 stderr，追加到输出
            if stderr:
                output += "\nSTDERR:\n" + stderr.decode("utf-8", errors="replace")

            # 检查输出大小，超过限制则截断
            max_bytes = _output_max_bytes()
            if len(output) > max_bytes:
                output = output[:max_bytes] + "\n[truncated]"

            return ToolResult(content=output)
        except FileNotFoundError:
            return ToolResult(content="pytest not installed, skipping.", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=f"Error running tests: {exc}", is_error=True, error_type="runtime_error")


class TestCoverageParams(BaseModel):
    """测试覆盖率参数模型

    **参数说明：**
    - source_path: 源代码路径（可选，默认为 src/）
    """
    model_config = ConfigDict(extra="ignore")
    # 源代码路径
    source_path: str | None = Field(default=None, description="Path to source code")


class TestCoverageTool(BaseTool):
    """测试覆盖率工具

    使用 coverage 模块运行测试并生成覆盖率报告。
    
    **执行流程：**
    1. 确定源代码路径
    2. 检查路径是否存在
    3. 运行 coverage run -m pytest tests/
    4. 运行 coverage report -m
    5. 返回覆盖率报告
    
    **覆盖率报告内容：**
    - 每个文件的覆盖率百分比
    - 未覆盖的行号
    - 总覆盖率统计
    
    **注意事项：**
    - 需要安装 coverage 模块
    - 输出超过 64KB 会被截断
    """
    params_model = TestCoverageParams
    name = "test_coverage"
    description = (
        "Run tests with coverage analysis. "
        "Generates a coverage report showing which lines are covered by tests."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Optional: path to source code (default: src/)",
            },
        },
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """生成测试覆盖率报告

        **执行流程：**
        1. 验证参数，获取源代码路径
        2. 确定源代码路径（默认 src/）
        3. 检查路径是否存在
        4. 运行 coverage run -m pytest tests/
        5. 运行 coverage report -m
        6. 合并输出并处理截断
        7. 返回覆盖率报告
        
        **路径确定逻辑：**
        - 如果指定了 source_path：使用指定路径
        - 否则：使用 src/
        
        **错误处理：**
        - FileNotFoundError: coverage 未安装
        - 其他异常：记录错误信息
        
        Args:
            params: 包含 source_path 的参数字典
            
        Returns:
            ToolResult: 包含覆盖率报告的对象
        """
        # 验证参数并获取源代码路径
        p = TestCoverageParams.model_validate(params)

        # 确定源代码路径
        source_path = p.source_path or "src"
        target_path = Path(source_path).resolve()

        # 检查路径是否存在
        if not target_path.exists():
            return ToolResult(content=f"Path not found: {target_path}", is_error=True, error_type="runtime_error")

        try:
            # 第一步：运行 coverage run -m pytest tests/
            proc = await asyncio.create_subprocess_exec(
                "coverage",
                "run",
                "-m",
                "pytest",
                "tests/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())

            # 第二步：运行 coverage report -m 生成详细报告
            proc2 = await asyncio.create_subprocess_exec(
                "coverage",
                "report",
                "-m",  # 显示未覆盖的行号
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 等待命令完成，设置超时
            stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=_timeout_s())

            # 解码覆盖率报告
            output = stdout2.decode("utf-8", errors="replace")
            # 如果有 stderr，追加到输出
            if stderr2:
                output += "\nSTDERR:\n" + stderr2.decode("utf-8", errors="replace")

            # 检查输出大小，超过限制则截断
            max_bytes = _output_max_bytes()
            if len(output) > max_bytes:
                output = output[:max_bytes] + "\n[truncated]"

            return ToolResult(content=output)
        except FileNotFoundError:
            return ToolResult(content="coverage not installed, skipping.", is_error=True, error_type="runtime_error")
        except Exception as exc:
            return ToolResult(content=f"Error running coverage: {exc}", is_error=True, error_type="runtime_error")
