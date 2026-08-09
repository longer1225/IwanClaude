"""
工具基础模块 - 定义工具系统的核心数据结构和抽象接口

【学习要点】
1. ABC（抽象基类）：使用 abc 模块定义抽象类，强制子类实现 invoke 方法
2. dataclass：使用 @dataclass 简化数据类定义，自动生成 __init__、__repr__ 等方法
3. ClassVar：标记类变量，不参与实例化，用于存储共享的 Pydantic 模型
4. 异步接口设计：所有工具的 invoke 方法都是 async，支持异步 I/O 操作

【设计模式】
- 模板方法模式：BaseTool 定义工具的基本结构，子类实现具体逻辑
- 策略模式：不同工具实现不同的 invoke 策略
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel


@dataclass
class ToolResult:
    """
    工具执行结果 - 所有工具返回的统一格式

    【字段说明】
    - content: 工具执行的输出内容，可以是文本、JSON 等格式
    - is_error: 是否为错误结果，True 表示执行失败
    - error_type: 错误类型，可选值包括：
      - "runtime_error": 运行时异常
      - "timeout": 执行超时
      - "schema_error": 参数校验失败
      - "permission_denied": 权限被拒绝

    【使用示例】
    ```python
    # 成功结果
    return ToolResult(content="执行成功")
    
    # 失败结果
    return ToolResult(content="操作失败", is_error=True, error_type="runtime_error")
    ```
    """
    content: str
    is_error: bool = False
    error_type: str | None = None


class BaseTool(ABC):
    """
    工具抽象基类 - 所有自定义工具都必须继承此类

    【学习要点】
    1. ABCMeta：通过继承 ABC 使类成为抽象类，不能直接实例化
    2. 抽象方法：@abstractmethod 标记的方法必须在子类中实现
    3. 类属性约定：子类必须定义以下属性：
       - name: 工具名称，用于在注册表中标识（必须唯一）
       - description: 工具描述，用于 LLM 理解工具用途
       - input_schema: 参数的 JSON Schema，用于 LLM 生成正确的调用参数
       - params_model: 可选的 Pydantic 模型，用于参数校验

    【子类实现示例】
    ```python
    class MyTool(BaseTool):
        name = "my_tool"
        description = "这是一个示例工具"
        input_schema = {
            "type": "object",
            "properties": {
                "arg1": {"type": "string", "description": "参数1"}
            },
            "required": ["arg1"]
        }
        params_model = MyToolParams
        
        async def invoke(self, params: dict[str, object]) -> ToolResult:
            # 实现工具逻辑
            return ToolResult(content="结果")
    ```
    """
    name: str
    description: str
    input_schema: dict[str, object]
    params_model: ClassVar[type[BaseModel] | None] = None
    # 热加载依赖声明：工具类列出需要的运行时依赖名（如 ["bus", "provider"]），
    # ToolLoader 会从 deps dict 中取出对应依赖传给构造函数。
    # 空列表表示无依赖，可直接无参实例化。详见 tools/loader.py。
    required_deps: ClassVar[list[str]] = []

    @abstractmethod
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """
        执行工具调用 - 子类必须实现此方法

        【参数说明】
        - params: 工具调用参数，字典形式，键为参数名，值为参数值

        【返回值】
        - ToolResult: 包含执行结果或错误信息

        【注意事项】
        - 方法必须是 async def，支持异步操作
        - 不要直接抛出异常，应返回 ToolResult(is_error=True)
        - 参数校验由 invoke_tool 统一处理，此方法可假设参数已校验通过
        """
        ...
