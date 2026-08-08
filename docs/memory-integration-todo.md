# Memory System 集成记录

> ✅ 集成已完成（2026-08-08）。三层记忆已接入 Agent 执行流程。
> 核心功能 + 集成共 549 个单元测试通过，无回归。
> 端到端验证（真实对话写入/检索记忆）待手动运行确认。

## 当前状态

| 组件 | 实现状态 | 集成状态 |
|------|---------|---------|
| LongTermMemory | ✅ 完成（long_term.py） | ✅ 已集成（app.py 初始化） |
| VectorMemory | ✅ 完成（vector_memory.py） | ✅ 已集成（app.py 初始化，无 key 降级） |
| MemoryManager | ✅ 完成（manager.py） | ✅ 已集成（runner recall + session 存储） |

## 待集成的 3 个点

### 1. 在 CoreApp 中初始化 MemoryManager

**文件**：`src/iwan_claude/core/app.py`

在 CoreApp 的 `__init__` 或初始化方法中创建 MemoryManager：

```python
from iwan_claude.core.memory import MemoryManager, LongTermMemory, VectorMemory

# 在 CoreApp 初始化中
long_term = LongTermMemory(Path.home() / ".iwan_claude" / "memory" / "long_term.jsonl")
vector_memory = VectorMemory(
    vector_store=get_vector_store(),
    embedding_provider=get_embedding_provider(config.rag, config.llm.base_url),
    index_path=str(Path.home() / ".iwan_claude" / "memory" / "vector_memory.json"),
)
self._memory = MemoryManager(
    long_term=long_term,
    vector_memory=vector_memory,
    project_context=render_claude_md_prompt(load_claude_md()),
)
self._memory.load()
```

### 2. 在 system_prompt 中注入记忆

**文件**：`src/iwan_claude/core/system_prompt.py`

在 `build_base_system_prompt` 中添加记忆检索：

```python
# 在构建系统提示词时，检索相关记忆
if memory_manager:
    memory_context = await memory_manager.recall(user_query)
    if memory_context:
        prompt += f"\n\n## Memory\n{memory_context}"
```

或者更精细的方式：在每次 LLM 调用前，把记忆注入到 messages 中。

### 3. 在会话结束后存储对话

**文件**：`src/iwan_claude/core/session/manager.py`

在 `send_message` 完成后，把对话存入向量记忆：

```python
# Agent 回答完后
await self._memory.remember_conversation(
    user_msg=content,
    assistant_msg=response_text,
    session_id=sid,
)
```

## 注意事项

1. **Embedding 依赖**：VectorMemory 需要 EmbeddingProvider，没有 API Key 时自动降级为空（不影响长期记忆）
2. **存储路径**：`~/.iwan_claude/memory/` 目录需要自动创建
3. **性能**：recall() 是异步的（向量搜索），不应阻塞主流程；可以考虑缓存或后台预加载
4. **隐私**：长期记忆包含用户偏好，需注意不要把敏感信息（密码、密钥）存入

## 测试验证

集成后需要验证：
- [ ] 启动时不报错（无 API Key 也能正常工作）
- [ ] 对话后向量记忆有数据
- [ ] 新会话能检索到旧会话的相关对话
- [ ] /memory 命令能列出长期记忆
- [ ] 遗忘功能正常工作
