"""
权限管理模块 - 管理工具调用的权限审批

【学习要点】
1. 权限决策：ALLOW（允许）、DENY（拒绝）、ASK（询问用户）
2. 策略评估：4 层静态策略评估 + 2 层缓存评估
3. 用户审批：通过事件机制向客户端发送权限请求
4. 缓存机制：session 级缓存（重启丢失）和持久化缓存（跨 session）

【核心类】
- PermissionManager: 权限管理器
- PermissionDecision: 权限决策枚举（ALLOW/DENY/ASK）
- ToolPolicy: 工具策略数据类
- PermissionDeniedError: 权限拒绝异常

【策略评估流程】
1. deny_patterns: 拒绝模式匹配（bash only）
2. OUTSIDE_CWD_HEURISTICS: 检测操作 cwd 之外路径（强制 ASK）
3. sandbox path check: 沙箱路径检查（强制 ASK）
4. allow_patterns: 允许模式匹配（bash only）
5. tool default: 工具默认策略

【缓存机制】
- session_always: session 级缓存，重启丢失
- persistent_always: 持久化缓存，从 policy.toml 加载，跨 session

【文件结构】
- __init__.py: 统一导出
- errors.py: 权限相关异常
- policy.py: 策略定义和评估逻辑
- storage.py: 策略文件的加载和保存
- manager.py: 权限管理器主类
"""
from iwan_claude.core.permissions.errors import PermissionDeniedError
from iwan_claude.core.permissions.manager import PermissionManager
from iwan_claude.core.permissions.policy import PermissionDecision, ToolPolicy
from iwan_claude.core.permissions.storage import load_policy_file, save_policy_file

# 统一导出的公共 API
__all__ = [
    "PermissionDecision",
    "PermissionDeniedError",
    "PermissionManager",
    "ToolPolicy",
    "load_policy_file",
    "save_policy_file",
]
