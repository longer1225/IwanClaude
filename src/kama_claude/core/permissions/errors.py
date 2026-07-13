# 导入 Python 3.7+ 的类型注解特性（让类可以在自身定义中引用自己）
from __future__ import annotations


# 权限被拒绝时抛出的异常类
# 继承自 Python 内置的 Exception 基类
class PermissionDeniedError(Exception):
    """Raised when a tool call is denied by the permission manager."""
    # 场景：当 PermissionManager.check_and_wait() 返回 (False, ...) 时，
    # 如果代码决定显式抛出异常，就会使用这个类
    # 例如：LLM 调用 bash 执行危险命令，用户拒绝了权限请求
