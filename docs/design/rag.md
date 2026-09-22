# iwanclaude RAG 设计思路复盘文档

> 写作背景：2026-09-21 对 RAG 子系统做了一次全面代码评审并落地修复。
> 本文档记录**每一层为什么存在、为什么选这条路线、好处和代价各是什么**，
> 供以后复盘时回答"当初为什么这么写"而不是重新读代码倒推。
> 配套回归测试：`tests/unit/test_rag.py::TestRagReviewFixes202609`（每个已修复 bug 一条钉死用例）。

---

## 0. 全景：数据流与分层

```
                 离线索引路径
  源文件 ──► DocumentChunker ──► Chunk(text, source_path(绝对), symbol/section, parent_id, context)
                 │                        │
                 │                        ▼
                 │              EmbeddingProvider ──► OpenAI 兼容 /embeddings（默认 dashscope）
                 │                        │
                 │                        ▼
                 └────────────► MemoryVectorStore（chunks.json + vectors.json 持久化到目录）
                                       │
                 在线检索路径            ▼
  query ──► AdaptiveRetriever（LLM 分类: direct / grep / rag）
              │ direct → 不检索，直接回答
              │ grep   → keyword_search（零 embedding 成本）
              │ rag    → KnowledgeIndexManager.hybrid_search
              │            ├─ 查询重写（同义词变体，一次批量 embed）
              │            ├─ 语义腿：向量余弦 top_k*2
              │            ├─ 关键词腿：全库文本命中，独立引入候选，并集
              │            ├─ 综合分 = 0.7*语义 + 0.3*关键词（原始语义分随 metadata 带出）
              │            └─ Parent-Child 上下文回填
              ├─ CRAG 质量评估（correct≥0.6 / ambiguous≥0.3 → 改写重检 / incorrect → 降级 grep）
              └─ 可选 LLM rerank（[rag] rerank_enabled 开关）──► 结果给 Agent
```

依赖方向严格单向：`adaptive → index → (chunker, embedding, vectorstore)`，
`tools` 只做协议适配，`eval` 站在最外面当裁判。任何一层都可以被换掉
（vectorstore 是 ABC，chunker/embedding 都是构造器注入），这是全模块最重要的结构约束。

---

## 1. 分块层（chunker.py）

### 为什么按格式分策略，而不是统一滑动窗口
检索质量的天花板在分块：块切坏了，后面再聪明也救不回来。

| 格式 | 策略 | 好处 | 代价 |
|---|---|---|---|
| Python | AST 解析，函数/类为边界 | 块=语义完整单元，行号精确，能带 symbol 元数据 | 语法错误文件需回退；`end_lineno` 依赖版本行为 |
| Markdown | 标题层级切分 + section_path | 章节结构保留，引用可定位到小节 | 无标题长文退化 |
| JSON/YAML | 结构化路径切分 | 配置文件按键取值检索 | 超大扁平 JSON 仍会走兜底 |
| 其他 | 滑动窗口（512/64 重叠） | 通用兜底，永不失败 | 可能拦腰切断语义；重叠区检索时重复命中 |

### 关键决策与代价
- **Parent-Child**：方法块携带 `parent_id` 指向类块。检索命中"半个方法"时把整个类文本
  回填进 `metadata["parent_context"]`。好处：精确命中 + 完整上下文两头占；
  缺点：一次额外的 `get_by_ids` 查询、给 LLM 的上下文变长（token 成本）。
- **Contextual Retrieval**（有 LLM 时）：让 LLM 给每块写 50-100 token 的"它属于哪、干什么"，
  embedding 时拼在正文前。这是 Anthropic 实证过显著提召回的做法。
  缺点很硬：**每个块索引时多一次 LLM 调用**——大目录首次索引成本线性膨胀，所以无 llm_client 时整层静默降级。
- **编码兜底**：`read_text_safely` 按 utf-8 → gbk → errors=replace 依次尝试。
  这是本项目从 Linux 迁到 Windows 的直接后果——仓库里混着 GBK 老文件，
  单一编码会让整个目录索引崩掉。replace 兜底保证"检索永不因单文件编码失败"，
  代价是坏字节静默变 `?`，这类块检索质量不可保证（宁可召回差也不炸全局）。

---

## 2. Embedding 层（embedding.py）

- **走 OpenAI 兼容协议而非 dashscope SDK**：换供应商只改 `base_url/model`，零代码。
  默认值指向 dashscope 是因为已有 key，不是架构承诺。
- **超时接线**（本轮修复）：`llm.embedding_timeout_s` 此前是死配置，现在真正传给
  httpx，且连接超时 `min(20, max(5, total/6))`——慢的批量请求不该被连接阶段掐死。
  缺点：没有重试层；上游抖动时一次索引直接失败（由调用方的 per-file try/except 兜住，只跳该文件）。
- **响应排序**（本轮修复）：OpenAI 协议**不保证** `data[]` 与输入顺序一致，
  按 `item["index"]` 排序回填；无 index 字段时才信任枚举顺序。
  不修的后果是文本↔向量错位——检索在完全错误的数据上打分，且不报任何错。
- **批量接口**：一次 HTTP 带一批文本（内部按 batch_size 切）。
  批量是混合检索成本可控的前提（见 §4）。
- 缺点/风险：维度一致性只做"首块基准 + `_check_dim` 拦截"，换 embedding 模型
  必须清库重建，混维度向量会直接 raise（fail-closed，故意的）。

## 3. 向量存储层（vectorstore.py）

### 为什么用 MemoryVectorStore + JSON 文件，而不是 FAISS/sqlite-vec/chromadb
这是本项目最"离经叛道"的一层，也是最想记清楚的一层。

**理由（诚实版）**：
1. 这是学习/对标项目，手写余弦检索把向量检索的原理全部摊开在自己眼前——
   `_check_dim`、filter-before-cutoff、load 校验这些修复之所以能写出来，正是因为代码是自己的。
2. 本地知识库规模在几千~几万 chunk 内，O(n) 余弦在这个量级是毫秒级，
   引入 ANN 库换来的是 Windows 平台编译依赖地狱（本项目在原生 Windows 上跑，
   很多向量库的首选平台不是它）。
3. 零外部进程/零原生扩展，`chunks.json + vectors.json` 肉眼可读、git 可 diff，调试成本极低。

**代价（同样诚实）**：
- **O(n) 全量扫描**：十万 chunk 级会明显变慢，且每次 load 全量进内存——内存占用 = 全库向量。
- **JSON 不是数据库**：无事务。写一半崩溃 = 文件损坏（load 已改成拒绝损坏并 raise，
  而不是静默丢数据，但写侧仍可能留下半截文件）。
- 无增量持久化：任何 add/delete 后整库重写。
- 没有真正的索引结构，`search_by_text` 是逐块字符串匹配——万级库时关键词腿会先于向量腿成为瓶颈。

**何时该换**：单库超过 ~5 万 chunk、或需要并发写、或需要持久化事务时，
按 `VectorStore` ABC 实现一个 sqlite-vec 后端即可整体替换，上层零改动——
ABC 就是为了这一刻留的缝（本轮把 `filters`、`size()` 补进 ABC 就是这个原因）。

**本轮修复**：`search()` 的 filters 必须在 top_k 截断**之前**应用（后置过滤会"说好过滤却少给结果"）；
`add()` 的 chunks/vectors 长度不匹配从 zip 静默截断改为 raise；
同 `chunk_id` 重复 add 改为覆盖（否则列表错位产生孤儿向量）。

## 4. 索引/检索编排层（index.py）—— 策略最多的地方

### 4.1 混合检索（hybrid_search）
语义腿管"意思相近"，关键词腿管"字面精确"（函数名、报错串、配置键——恰恰是代码问答的多数查询）。

- **关键词腿独立产候选**（本轮修复）：旧实现关键词只对语义池内结果"加分"，
  等于混合检索退化成语义重排，精确标识符一旦语义分低就永远进不来。
  现在全库文本命中与语义结果**并集**打分，无语义分的候选记 0 参与竞争。
  缺点：纯关键词候选综合分上限只有 0.3，排名天然吃亏（权重即立场，可按场景调 `keyword_weight`）。
- **一次批量 embed 所有查询变体**（本轮修复）：逐变体 embed 是 5-10 次 HTTP 往返/每次检索，
  合并后 1 次。检索延迟和限流风险同时下降。
- **综合分与原始分分离**（本轮修复）：返回的 score 是综合分，但
  `chunk.metadata["semantic_score"]` 携带**纯语义分**。CRAG 阈值（0.6/0.3）语义上
  对照的是相似度，误用综合分会让关键词加分虚抬置信度，把"实际没找到"判成"回答可靠"。

### 4.2 查询重写
- 有 LLM：让模型生成变体（理解语义，覆盖广），缺点是一次额外 LLM 往返。
- 无 LLM：硬编码同义词表 + **词边界正则**（本轮修复：旧版子串包含让 "profile" 命中
  "file"、"default" 命中 "def"——每个误变体都是一次真金白银的 embedding 调用，
  还稀释排序信号）。词表法召回有限是明确接受的代价。

### 4.3 增量索引与元数据
- `_meta["sources"]` 记录 `{abs_path: {mtime, chunk_count}}`，mtime 没变就跳过。
- **key 统一为绝对路径**（本轮修复）：旧版写入用相对 key、删除查绝对 key，两把钥匙永不相交，
  删除的文件在 meta 里永远残留。**代价：升级后所有旧相对 key 记录作废，首次运行全量重索引一遍**（一次性）。
- **exclude 的 `dir/**` 语义**（本轮修复）：Python 3.12 的 `Path.match()` 不支持 `**` 递归
  （3.13 才有），旧版 `.git/**` 实际只挡一层，`.git/hooks/*.py` 照样被送到外部 embedding API——
  这是本轮唯一的**隐私/成本双重 P0**。修复后按顶层目录名精确比对 + fnmatch 兜底。
- **单文件失败跳过大目录**（本轮修复）：逐文件 try/except + warning，
  一个坏文件不该毁掉整个目录的索引作业；代价是坏文件只有日志可见，聚合层需要看 `notes`。
- 缺点：mtime 无内容哈希——touch 过的未改动文件会白白重嵌一遍（换 hash 要读全文件，
  省不了多少，暂不做）。

### 4.4 备份/恢复
`backup_index` 只拷 meta + store 的 json 文件；备份前 `mkdir(parents)`，
meta 不存在时跳过。缺点：不是原子快照，大库备份中途崩溃可能留半套备份（未修，债务）。

## 5. 自适应检索层（adaptive.py）—— LLM 当路由器

**动机**：三类问题（常识/直答型、精确标识符型、语义知识型）的最优路径完全不同；
全走 RAG 浪费 embedding 成本且被无意义检索污染上下文，全走直答则丧失知识库价值。

流程：LLM 分类 `direct | grep | rag` → 对应路径 → CRAG 质量评估 → 可选 rerank。

| 策略 | 好处 | 代价 |
|---|---|---|
| LLM 路由 | 每类查询走最优通道；direct 零检索成本 | 每次检索前置一次 LLM 往返（延迟）；分类错了靠 CRAG 兜底 |
| grep 通道 | 精确标识符零 embedding、零向量计算 | 完全不懂同义改写 |
| CRAG（correct≥0.6 / ambiguous≥0.3 / incorrect） | 检索质量差不硬答，ambiguous 改写重检、incorrect 降级关键词——质量下限有保险 | 阈值经验值（未用 eval 数据标定，见债务）；最坏路径 = 分类+检索+评估+重检，多次 LLM/embedding 往返 |
| LLM rerank | 对 top_k 精排，收益在"候选对但顺序错"场景 | **整轮 LLM 调用，时延大头**；现受 `[rag] rerank_enabled`（默认 True 保持旧行为）控制，可一键关 |

本轮接线：`IWAN_RAG_RERANK_ENABLED` / `[rag] rerank_enabled` 四层级配置全通；
`retrieve()` 的 `filters` 从工具层一路透传（旧版工具 schema 声明了 filters、实现里静默吞掉——
"接口上存在的谎言"类 bug）。

## 6. 工具层与配置（tools.py / runner.py / app.py / config.py)

- 工具层只做参数翻译 + 结果排版，不承载检索逻辑（评审时它确实夹带了：空 paths NameError、
  计数硬编码、不存在的文件静默消失——现全部修复并把 `skipped (not found)` 写进结果让模型可见）。
- `index_path` 实际是**目录**（内含 chunks.json/vectors.json），历史配置值长得像 .json 文件名
  ——app.py 里加了【设计】注释说明，避免下一个读代码的人再困惑一次。
- 旧机器上 `~/.iwan_claude/memory/vector_memory.json` 这个"名字带 .json 的目录"是历史遗留，
  可手动删除（新布局在其旁边重新生成）。

## 7. 评估层（eval.py）—— 没有它前面全是玄学

- Recall/Precision/MRR/Hit@K 四指标 + 分类分解 + 分块参数消融实验。
- **Agent 场景 Hit@K > Recall@K**：top_k 里有一个对的，LLM 就能答对；不追求"全找回来"。
- 本轮修复：
  - 相关性比对两侧统一 `resolve()` 成绝对路径再求交集——chunk 侧自 2026-09 起恒绝对、
    标注侧恒仓库相对，旧版交集恒空，**所有指标静默为 0 且报告"看起来在跑"**。
    这是本轮最阴险的 bug：坏掉的评估比没有评估更糟，它会给你虚假的安全感。
  - 消融实验的规模列改读 `status().total_chunks`（旧键 `_meta["total_chunks"]` 从未被写入，恒 0）。
  - `--generate` 测试集生成器（本轮新实现：ast 提取顶层符号生成"xxx 定义在哪里"弱标注查询）。
    **弱标注的局限要写死在这**：查询全部源自字面标识符，天然偏袒关键词腿，
    适合当回归基线（分数掉了=真退步），不适合当语义能力的证明书。
  - 内置测试集 `BUILTIN_TESTSET` 标注的是本仓库文件——重构搬文件后要记得同步标注，
    否则指标下滑是测试集腐烂，不是检索退步（已知易腐点）。

## 8. 成本模型（谁在花钱）

| 动作 | LLM 调用 | embedding 调用 |
|---|---|---|
| 索引每文件（Contextual 开） | +1/块 | 1 批/块 |
| 混合检索每次 | 1（分类）+ 0~1（重写）+ 0~1（CRAG 重检） | 1 批（全变体合并） |
| rerank 每次检索 | +1 | 0 |
| grep 通道 | 1（仅分类） | 0 |

省钱开关：`[rag] rerank_enabled=false`；llm_client 传 None 时 Contextual/重写全降级。
隐私红线（本轮已焊死）：exclude 语义正确后，`.git`/`.venv`/`node_modules` 不再外发。

## 9. 2026-09-21 评审修复记录（问题 → 根因 → 修复）

| # | 级别 | 症状 | 根因 | 修复 | 遗留风险 |
|---|---|---|---|---|---|
| 1 | P0 | 任何 `index_directory(incremental=False)` 必崩 | mtime 只在增量分支赋值 | mtime 无条件先取 | — |
| 2 | P0 | `.git/hooks/*.py` 被发到外部 embedding API | 3.12 `Path.match` 不支持 `**` | `dir/**` 顶层目录精确匹配 + fnmatch 兜底 | 自定义 pattern 仍需按新语义书写 |
| 3 | P0 | filters 检索"说好用却少给" | 过滤在 top_k 截断之后 | filter-before-cutoff（进 ABC） | — |
| 4 | P0 | 文本↔向量错位、打分在错误数据上 | 信任 `data[]` 顺序 | 按 `index` 排序回填 + 数量/空向量校验 | 兼容端点若乱序**且**不带 index 仍会错位（协议上不可能同时发生，接受了） |
| 5 | P0 | add 长度不匹配静默截断、重复 id 孤儿向量 | zip 语义 | raise + 同 id 覆盖 | — |
| 6 | P0 | meta 幽灵记录删不掉 | 写相对 key、删查绝对 key | key 统一绝对路径 | **首次运行全量重索引**（一次性成本） |
| 7 | P0 | CRAG 置信度虚高放行垃圾结果 | 拿混合分对照相似度阈值 | metadata 带出原始语义分，`_quality_score` 优先取它 | grep 腿无语义分可取时回退综合分（无 better signal，注释说明） |
| 8 | P0 | eval 指标恒 0 假运行 | 绝对 vs 相对路径永不交集 | 两侧 resolve 后比对 | 测试集标注腐烂（见 §7） |
| 9 | P1 | 关键词腿永不引入新候选 | 只在语义池内加分 | 全库文本命中并入候选 | 纯关键词候选分数封顶 0.3 |
| 10 | P1 | 检索时 5-10 次 embedding 往返 | 逐变体 embed | 批量一次 | — |
| 11 | P1 | profile→file 误改写 | 子串包含 + str.replace | 词边界正则 | 词表召回有限 |
| 12 | P1 | rerank 无开关、embedding 超时死配置 | 配置层未接线 | `rerank_enabled` + `embedding_timeout_s` 全链接通 | — |
| 13 | P1 | 工具层：空 paths 崩 / 计数恒 1 / 幽灵路径静默 | 手滑逻辑 | 全部修正 + 结果携带 notes | — |
| 14 | P2 | GBK 文件崩索引 / 可变默认参数 / 坏 JSON 静默清空 / 备份目录缺失 / 编码缺失 | 各种小凿 | 逐一加固 | 备份非原子（债务） |
| 15 | P2 | `status().total_chunks`、消融规模列恒 0 | 读了不存在的键 | 真实计数 | — |

## 10. 已知债务清单（下次动 RAG 先看这里）

1. **O(n) 向量扫描 + 全量 JSON 重写**：库过 ~5 万 chunk 时按 `VectorStore` ABC 换 sqlite-vec。
2. **无 BM25**：`search_by_text` 是命中计数，长文档/常见词会高估；接 rank_bm25 即可填进关键词腿。
3. **CRAG 阈值 0.6/0.3 未标定**：应该用 eval 的标注集扫一遍分数分布再定，现在是拍的。
4. **`delete_by_session`（memory 层）仍直接摸 `_store._chunks`**：绕过 ABC 的私处接触，
   换后端会立刻炸——迁移时第一个要收口的点。
5. **backup 非原子**；**mtime 无内容哈希**；**换 embedding 模型必须手动清库**（无版本号戳记）。
6. `documentation.py:228` 还有一处 `\s` docstring 未做双反斜杠转义（非 RAG 域，顺手时清）。
7. `index_knowledge` 工具按安全模型应挂 **default-ASK**（它会驱动数据外发第三方 API）；
   permissions 侧接线待做。
8. 内置测试集与仓库文件路径强耦合，重命名文件需同步 `BUILTIN_TESTSET` 标注。
