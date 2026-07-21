"""
权限错误模块 - 定义权限相关的异常类

【学习要点】
1. 异常定义：定义权限拒绝异常
2. 异常使用：在工具调用被拒绝时抛出

【核心异常】
- PermissionDeniedError: 权限拒绝异常

【使用场景】
当工具调用被权限管理器拒绝时抛出此异常，
上层代码可以捕获此异常并进行相应处理。

【示例】
```python
from iwan_claude.core.permissions.errors import PermissionDeniedError

def invoke_tool(tool_name, params):
    if not permission_manager.check(tool_name, params):
        raise PermissionDeniedError(f"Tool {tool_name} denied")
    # 执行工具调用
```
"""
from __future__ import annotations


class PermissionDeniedError(Exception):
    """
    权限拒绝异常 - 当工具调用被权限管理器拒绝时抛出

    【设计目的】
    提供统一的权限拒绝异常，便于上层代码捕获和处理。

    【使用场景】
    - 工具调用被 deny_patterns 匹配
    - 工具调用被用户拒绝
    - 权限审批超时

    【示例】
    ```python
    try:
        await tool.invoke(params)
    except PermissionDeniedError as e:
        print(f"权限被拒绝: {e}")
    ```
    """
    pass
