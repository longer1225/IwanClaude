# IwanClaude 项目可做内容清单

> 面向简历展示与面试准备的"数据化"内容清单。每一项都标注了目标、方法、产出数据、面试价值。
> 按"面试权重 × 实现成本"排序，从高到低。

---

## 一、项目现状盘点（已完成）

在补充新内容前，先明确已有的"资产"，这些是简历和面试的基本盘。

### 1.1 已实现的核心功能

| 模块 | 功能 | 代码位置 | 测试 |
|------|------|----------|------|
| Agent 架构 | CoreApp 中央调度 + EventBus 发布订阅 | [app.py](file:///d:/IwanClaude/src/iwan_claude/core/app.py) | ✅ test_event_bus.py |
| 双进程 IPC | JSON-RPC over TCP + 事件广播器 | [ipc_broadcaster.py](file:///d:/IwanClaude/src/iwan_claude/core/transport/ipc_broadcaster.py) | ✅ test_socket_*.py |
| RAG 系统 | 5 种分块 + 混合检索 + 增量索引 + 查询重写 | [rag/](file:///d:/IwanClaude/src/iwan_claude/core/rag/) | ✅ test_rag.py |
| 权限管理 | 6 级优先级 + auto_mode 状态机 + 异步审批 | [manager.py](file:///d:/IwanClaude/src/iwan_claude/core/permissions/manager.py) | ✅ test_permission_*.py |
| 多会话 TUI | Textual + 流式输出 + 内联权限 UI + 斜杠命令 | [tui/](file:///d:/IwanClaude/src/iwan_claude/tui/) | ✅ test_tui_app.py |
| 子 Agent 并行 | SpawnAgent/SpawnAgents + Semaphore + 任务注册表 | [tool.py](file:///d:/IwanClaude/src/iwan_claude/core/subagent/tool.py) | ✅ test_spawn_agent_tool.py |
| 上下文管理 | compact 压缩 + checkpoint 检查点恢复 | [compactor.py](file:///d:/IwanClaude/src/iwan_claude/core/compact/compactor.py) | ✅ test_compactor.py |
| 工程化 | 四级配置优先级 + Trace 追踪 + 49 个测试 | [config.py](file:///d:/IwanClaude/src/iwan_claude/core/config.py) | ✅ |

### 1.2 已有的数据/指标素材

| 素材 | 数值 | 来源 |
|------|------|------|
| 测试用例数 | 49 个（unit + integration） | tests/ 目录 |
| 代码规模 | ~15000 行 Python | src/ 目录 |
| 分块策略数 | 6 种（Python AST / Markdown / JSON / YAML / XML / 滑动窗口） | [chunker.py](file:///d:/IwanClaude/src/iwan_claude/core/rag/chunker.py) |
| 权限优先级层级 | 6 级 | [manager.py](file:///d:/IwanClaude/src/iwan_claude/core/permissions/manager.py) |
| 支持的文件格式 | 7 种（.py .md .json .yaml .xml .csv .txt） | chunker.py |
| 工具数量 | 15+ 内置工具 | [tools/builtin/](file:///d:/IwanClaude/src/iwan_claude/core/tools/builtin/) |

---

## 二、可补充的数据化内容（按优先级排序）

### 🔴 P0：RAG 检索质量评估（面试必问）

**面试场景**：面试官一定会问"你的 RAG 效果怎么样？怎么验证的？"没有数据 = 答不上来。

#### 2.1 检索质量指标评估

| 项 | 说明 |
|----|------|
| **目标** | 量化 RAG 检索质量，证明"能找到正确答案" |
| **方法** | 用 [eval.py](file:///d:/IwanClaude/src/iwan_claude/core/rag/eval.py) 的 16 条内置测试集，跑出 Recall@K / Precision@K / MRR / Hit Rate |
| **产出数据** | 表格：`K=1 / K=3 / K=5 / K=10` × `Recall / Precision / MRR / HitRate` |
| **面试话术** | "我构建了 16 条标注测试集，覆盖 7 个分类。混合检索的 Recall@5 达到 X%，MRR 为 Y" |
| **实现成本** | 低（脚本已写好，配置 API Key 即可运行，~1 小时） |
| **依赖** | 需要真实的 Embedding API Key（DeepSeek / OpenAI） |

**运行方式**：
```python
from iwan_claude.core.rag import (
    DocumentChunker, EmbeddingProvider,
    MemoryVectorStore, KnowledgeIndexManager,
)
from iwan_claude.core.rag.eval import RAGEvaluator, BUILTIN_TESTSET, print_summary
import asyncio, os

chunker = DocumentChunker(chunk_size=512, chunk_overlap=64)
store = MemoryVectorStore()
embedder = EmbeddingProvider(
    model="text-embedding-v3",
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ["DEEPSEEK_API_KEY"],
)
index_mgr = KnowledgeIndexManager(store, embedder, chunker)
index_mgr.index_directory("src/")

evaluator = RAGEvaluator(index_mgr, BUILTIN_TESTSET)
summary = asyncio.run(evaluator.evaluate())
print_summary(summary)
```

#### 2.2 分块参数消融实验

| 项 | 说明 |
|----|------|
| **目标** | 证明 chunk_size / overlap 的选择有数据支撑，不是拍脑袋 |
| **方法** | 对比 7 组配置：chunk_size ∈ {256, 512, 1024} × overlap ∈ {0, 64, 128} |
| **产出数据** | 表格：每组配置的 Recall@5 / MRR / 总分块数 |
| **面试话术** | "我做了消融实验，chunk_size=512 + overlap=64 的 Recall@5 比 256 高 X%，比 1024 只低 Y%，是最优平衡点" |
| **实现成本** | 中（脚本已写好 `run_chunk_ablation`，需要多次调用 API，~2-3 小时） |
| **依赖** | 同上，需要 API Key，且会产生 API 费用（7 次完整索引） |

#### 2.3 检索方法对比

| 项 | 说明 |
|----|------|
| **目标** | 证明"混合检索"比单一方法好，体现设计取舍 |
| **方法** | 对同一测试集分别跑：纯语义检索 / 纯关键词检索 / 混合检索 |
| **产出数据** | 三组指标的对比表 + 提升百分比 |
| **面试话术** | "混合检索在 Recall@5 上比纯语义提升 X%，比纯关键词提升 Y%。语义擅长理解意图，关键词擅长精确匹配，两者互补" |
| **实现成本** | 低（复用 eval.py，切换检索方法即可，~1 小时） |

---

### 🟡 P1：性能测试（加分项）

**面试场景**：面试官可能问"你的系统性能怎么样？能支撑多少并发？"

#### 2.4 多会话并发吞吐量测试

| 项 | 说明 |
|----|------|
| **目标** | 证明 asyncio 架构的并发能力 |
| **方法** | 同时创建 N 个会话并发送消息，测量总耗时和平均延迟 |
| **产出数据** | 折线图：并发数(1/2/4/8/16) × 平均响应时间 / 吞吐量 |
| **面试话术** | "基于 asyncio 的单线程并发架构，8 个会话并发时平均延迟仅增加 X ms，证明事件循环调度高效" |
| **实现成本** | 中（需要写并发测试脚本 + 模拟 LLM 响应，~3 小时） |

#### 2.5 流式输出性能测试

| 项 | 说明 |
|----|------|
| **目标** | 证明 Token 级流式渲染 + Markdown 延迟渲染的性能优势 |
| **方法** | 对比两种渲染策略：①每 Token 渲染 Markdown ②累积后延迟渲染 |
| **产出数据** | 表格：渲染次数 / 总耗时 / CPU 占用 / 帧率 |
| **面试话术** | "延迟渲染策略将渲染次数从 N 次（每 Token 一次）降到 1 次，CPU 占用降低 X%，用户感知延迟不变" |
| **实现成本** | 中（需要 mock Token 流 + 性能计时，~2 小时） |

#### 2.6 权限审批端到端延迟测试

| 项 | 说明 |
|----|------|
| **目标** | 证明内联权限 UI 的响应速度 |
| **方法** | 测量从"工具调用请求"到"审批结果回传"的完整链路延迟 |
| **产出数据** | 延迟分解：IPC 传输 / UI 渲染 / 用户操作 / 结果回传 |
| **面试话术** | "内联 UI 设计避免了 Modal 弹窗的焦点切换开销，端到端延迟 < X ms" |
| **实现成本** | 低（利用现有的 trace 追踪系统，~1.5 小时） |

---

### 🟢 P2：扩展性验证（锦上添花）

**面试场景**：高级岗位或技术深挖时可能问到。

#### 2.7 向量存储后端对比

| 项 | 说明 |
|----|------|
| **目标** | 证明架构的可扩展性（VectorStore 抽象层的设计价值） |
| **方法** | 对比 MemoryVectorStore vs FAISS vs Chroma 的检索速度和内存占用 |
| **产出数据** | 表格：存储后端 × 索引时间 / 检索延迟 / 内存占用 / 召回率 |
| **面试话术** | "我设计了 VectorStore 抽象层，切换到 FAISS 后检索延迟从 X ms 降到 Y ms，且代码零改动" |
| **实现成本** | 高（需要集成 FAISS/Chroma 依赖，~4-6 小时） |

#### 2.8 Embedding 模型对比

| 项 | 说明 |
|----|------|
| **目标** | 证明对 Embedding 模型选型的理解 |
| **方法** | 对比不同模型（text-embedding-v3 / bge-large / m3e）在测试集上的召回率 |
| **产出数据** | 表格：模型 × 维度 / Recall@5 / MRR / API 成本 |
| **面试话术** | "对比了 3 个 Embedding 模型，bge-large 在中文场景下 Recall@5 比 v3 高 X%，但延迟增加 Y ms" |
| **实现成本** | 中（需要多个模型的 API Key，~3 小时） |

#### 2.9 上下文压缩收益测试

| 项 | 说明 |
|----|------|
| **目标** | 证明 compact 机制的实际价值 |
| **方法** | 构造长对话（50+ 轮），对比压缩前后的 Token 数 / 响应质量 / 成本 |
| **产出数据** | 图表：对话轮数 × 压缩前 Token / 压缩后 Token / 压缩率 |
| **面试话术** | "compact 机制在 50 轮对话后将 Token 数从 X 降到 Y，压缩率 Z%，且关键信息保留率 > 90%" |
| **实现成本** | 中（需要构造长对话数据 + 人工评估质量，~3 小时） |

---

## 三、实施优先级与时间规划

### 第一阶段：必做（面试前完成，~4 小时）

| # | 任务 | 时间 | 产出 |
|---|------|------|------|
| 1 | 配置 Embedding API Key，跑 RAG 检索质量评估 | 1h | Recall/Precision/MRR 数据表 |
| 2 | 跑分块参数消融实验（7 组配置） | 2h | chunk_size/overlap 最优配置结论 |
| 3 | 跑检索方法对比（语义/关键词/混合） | 1h | 混合检索优势证明 |

**产出**：一份 RAG 评估报告，含 3 张数据表，可直接贴简历/面试展示。

### 第二阶段：建议做（有时间再做，~6 小时）

| # | 任务 | 时间 | 产出 |
|---|------|------|------|
| 4 | 多会话并发吞吐量测试 | 3h | 并发性能折线图 |
| 5 | 流式输出渲染性能对比 | 2h | 延迟渲染优势数据 |
| 6 | 权限审批延迟分解 | 1.5h | 端到端延迟数据 |

**产出**：性能测试报告，证明系统在实际场景下的表现。

### 第三阶段：选做（冲刺高级岗，~10 小时）

| # | 任务 | 时间 | 产出 |
|---|------|------|------|
| 7 | 向量存储后端对比（FAISS/Chroma） | 5h | 可扩展性证明 |
| 8 | Embedding 模型对比 | 3h | 模型选型依据 |
| 9 | 上下文压缩收益测试 | 3h | compact 价值证明 |

**产出**：扩展性验证报告，体现技术深度。

---

## 四、简历可用的数据化表述模板

完成上述实验后，简历可以这样写（把 X/Y/Z 替换为实际数据）：

### RAG 部分

> 实现 6 种文档分块策略（Python AST、Markdown 标题、JSON/YAML/XML/CSV 结构感知、
> 纯文本滑动窗口），通过消融实验对比 7 组 chunk_size/overlap 配置，
> 选定 chunk_size=512 + overlap=64（Recall@5=X%，较 256 提升 Y%）。
> 设计语义+关键词混合检索，在 16 条标注测试集上 Recall@5 比纯语义提升 Z%，
> MRR 达到 0.X。

### 性能部分

> 基于 asyncio 单线程并发架构，8 会话并发时平均延迟仅增加 X ms。
> 流式输出采用 Token 累积 + Markdown 延迟渲染，渲染次数降低 Y%，
> CPU 占用降低 Z%。权限审批内联 UI 端到端延迟 < X ms。

### 工程化部分

> 编写 49 个单元/集成测试覆盖核心模块。设计四级配置优先级体系
>（环境变量 > .env > 本地 TOML > 全局 TOML）。
> Trace 追踪系统支持运行回放，便于问题排查。

---

## 五、面试高频问题预判

基于上述内容，预判面试官可能问的问题及应对：

| 问题 | 应对策略 | 需要的数据 |
|------|---------|-----------|
| "你的 RAG 分块策略为什么这么设计？" | 讲消融实验数据 | chunk_size 对比表 |
| "检索效果怎么样？怎么验证的？" | 讲 Recall@K / MRR | 评估报告 |
| "混合检索比纯语义好多少？" | 讲对比实验 | 检索方法对比表 |
| "系统并发性能如何？" | 讲 asyncio 吞吐量 | 并发测试图 |
| "为什么用 Textual 不用 Web？" | 讲终端场景优势 + 流式渲染优化 | 渲染性能数据 |
| "权限审批为什么不用弹窗？" | 讲内联 UI 延迟优势 | 延迟分解数据 |
| "向量存储为什么用内存？" | 讲抽象层设计 + 可切换 FAISS | 存储对比表 |
| "compact 压缩会不会丢信息？" | 讲压缩收益 + 信息保留率 | 压缩测试数据 |

---

## 六、下一步行动建议

1. **立即做**：配置 API Key，跑 P0 的 3 项实验（~4 小时）
2. **本周做**：跑 P1 的性能测试（~6 小时）
3. **简历更新**：用实验数据替换简历中模糊的描述
4. **面试准备**：对照第五节的问题预判，准备 2-3 分钟的技术深讲

> 💡 关键原则：**没有数据的"我实现了 X"不如有数据的"我验证了 X 的效果是 Y"**。
> 面试官看重的是工程决策背后的数据支撑，而不是功能列表。
