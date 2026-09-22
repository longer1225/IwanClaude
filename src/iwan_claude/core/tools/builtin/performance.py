"""性能分析工具模块

这个模块实现了代码性能分析工具，使用 Python 内置的 cProfile 模块进行性能剖析。

**cProfile 工作原理：**
cProfile 是 Python 的内置性能分析器，通过统计函数调用次数、累计时间、单次调用时间等指标，帮助开发者定位性能瓶颈。

**性能分析指标：**
- ncalls: 函数调用次数
- tottime: 函数内部耗时（不包含子函数调用）
- percall: tottime / ncalls，平均每次调用耗时
- cumtime: 累计耗时（包含子函数调用）
- percall: cumtime / ncalls，平均每次调用累计耗时
- filename:lineno(function): 函数位置信息

**使用场景：**
- 识别执行时间最长的函数
- 找出调用次数过多的函数
- 定位性能瓶颈和优化点

**使用示例：**
```python
# 分析整个文件
result = await profile_code_tool.invoke({
    "file_path": "src/utils.py"
})

# 分析特定函数
result = await profile_code_tool.invoke({
    "file_path": "src/utils.py",
    "function_name": "process_data"
})
```
"""
from __future__ import annotations

import asyncio
import cProfile
import pstats
from io import StringIO
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from iwan_claude.core.tools.base import BaseTool, ToolResult

# 输出最大字节数，防止大输出导致内存问题
_MAX_OUTPUT_BYTES = 64 * 1024


class ProfileCodeParams(BaseModel):
    """性能分析参数模型

    **参数说明：**
    - file_path: 要分析的 Python 文件路径（必填）
    - function_name: 要分析的特定函数名称（可选）
    
    **分析模式：**
    - 如果指定了 function_name：只分析该函数的性能
    - 如果未指定 function_name：执行整个文件并分析所有函数
    """
    model_config = ConfigDict(extra="ignore")
    # 要分析的 Python 文件路径，必填
    file_path: str = Field(description="Path to the Python file to profile")
    # 要分析的特定函数名称，可选
    function_name: str | None = Field(default=None, description="Specific function to profile")


class ProfileCodeTool(BaseTool):
    """代码性能分析工具

    使用 cProfile 对 Python 代码进行性能分析，识别性能瓶颈。
    
    **分析流程：**
    1. 初始化 cProfile.Profile 对象
    2. 启用性能分析
    3. 执行目标代码（整个文件或特定函数）
    4. 禁用性能分析
    5. 生成性能报告（按时间排序，显示前 20 个函数）
    6. 处理输出截断
    7. 返回分析结果
    
    **两种分析模式：**
    - **文件模式**：执行整个文件，分析所有函数的性能
    - **函数模式**：导入模块并执行指定函数，分析该函数及其子函数的性能
    
    **注意事项：**
    - 输出超过 64KB 会被截断
    - 使用 pstats.SortKey.TIME 按累计时间排序
    - 显示前 20 个最耗时的函数
    """
    params_model = ProfileCodeParams
    name = "profile_code"
    description = (
        "Profile Python code performance. "
        "Uses cProfile to identify performance bottlenecks."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the Python file to profile",
            },
            "function_name": {
                "type": "string",
                "description": "Optional: specific function to profile",
            },
        },
        "required": ["file_path"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行性能分析

        **执行流程：**
        1. 验证参数，获取文件路径和函数名称
        2. 解析文件路径并检查文件是否存在
        3. 初始化 cProfile.Profile 对象
        4. 启用性能分析（pr.enable()）
        5. 根据 function_name 参数选择执行模式：
           - 指定函数：导入模块并执行指定函数
           - 未指定函数：执行整个文件
        6. 禁用性能分析（pr.disable()）
        7. 生成性能报告（按时间排序，显示前 20 个函数）
        8. 处理输出截断
        9. 返回分析结果
        
        **函数模式执行逻辑：**
        1. 使用 importlib.util.spec_from_file_location 创建模块规格
        2. 使用 importlib.util.module_from_spec 创建模块对象
        3. 使用 spec.loader.exec_module 执行模块
        4. 使用 getattr 获取指定函数
        5. 调用函数
        
        **文件模式执行逻辑：**
        使用 exec() 执行文件内容
        
        **性能报告生成：**
        1. 创建 StringIO 对象作为输出流
        2. 创建 pstats.Stats 对象并指定排序方式
        3. 使用 print_stats(20) 打印前 20 个函数
        4. 获取 StringIO 内容
        
        Args:
            params: 包含 file_path 和 function_name 的参数字典
            
        Returns:
            ToolResult: 包含性能分析报告的对象
        """
        # 验证参数并获取文件路径和函数名称
        p = ProfileCodeParams.model_validate(params)

        # 解析文件路径并检查文件是否存在
        file_path = Path(p.file_path).resolve()
        if not file_path.exists():
            return ToolResult(content=f"File not found: {file_path}", is_error=True, error_type="runtime_error")

        try:
            # 初始化 cProfile.Profile 对象
            pr = cProfile.Profile()
            # 启用性能分析
            pr.enable()

            # 根据 function_name 参数选择执行模式
            if p.function_name:
                # 函数模式：导入模块并执行指定函数
                import importlib.util
                
                # 创建模块规格
                spec = importlib.util.spec_from_file_location("profile_module", str(file_path))
                if spec is None:
                    return ToolResult(content=f"Failed to load module: {file_path}", is_error=True, error_type="runtime_error")
                
                # 创建模块对象
                module = importlib.util.module_from_spec(spec)
                if spec.loader is None:
                    return ToolResult(content=f"Module has no loader: {file_path}", is_error=True, error_type="runtime_error")
                
                # 执行模块
                spec.loader.exec_module(module)
                
                # 获取指定函数
                func = getattr(module, p.function_name, None)
                if func:
                    # 调用函数
                    func()
                else:
                    return ToolResult(content=f"Function '{p.function_name}' not found", is_error=True, error_type="runtime_error")
            else:
                # 文件模式：执行整个文件
                exec(file_path.read_text(encoding="utf-8"))

            # 禁用性能分析
            pr.disable()

            # 生成性能报告
            # 创建 StringIO 对象作为输出流
            s = StringIO()
            # 创建 pstats.Stats 对象，按累计时间排序
            ps = pstats.Stats(pr, stream=s).sort_stats(pstats.SortKey.TIME)
            # 打印前 20 个最耗时的函数
            ps.print_stats(20)

            # 获取报告内容
            output = s.getvalue()
            # 检查输出大小，超过限制则截断
            if len(output) > _MAX_OUTPUT_BYTES:
                output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

            return ToolResult(content=output)
        except Exception as exc:
            return ToolResult(content=f"Error profiling code: {exc}", is_error=True, error_type="runtime_error")
