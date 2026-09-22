"""
会话模块 - 统一导出会话相关的核心类和类型

【学习要点】
1. 统一导出：将会话模块的核心类统一导出，便于外部导入
2. 类型定义：SessionMode 和 SessionStatus 是字面量类型
3. 核心类：Session（会话数据模型）、SessionManager（会话管理器）、SessionStore（会话存储）

【导出内容】
- MessageContent: 消息内容数据类
- Session: 会话数据模型
- SessionManager: 会话管理器
- SessionMode: 会话模式类型（one_shot / chat）
- SessionStatus: 会话状态类型（active / waiting_for_input / closed）
- SessionStore: 会话存储管理器

【使用示例】
```python
from iwan_claude.core.session import Session, SessionManager, SessionMode
```
"""
from iwan_claude.core.session.manager import SessionManager
from iwan_claude.core.session.model import Session, SessionMode, SessionStatus
from iwan_claude.core.session.store import MessageContent, SessionStore

__all__ = [
    "MessageContent",
    "Session",
    "SessionManager",
    "SessionMode",
    "SessionStatus",
    "SessionStore",
]
