# AGENTS.md

<!--
  ============================================================
  AGENTS.md — 项目级 Agent 行为指导文件
  ============================================================
  这个文件会被 IwanClaude 读取并注入到 system prompt 末尾。
  你可以在这里写自定义规则、代码风格、架构约定等。
  优先级：低于内置规则，用于补充项目特定指导。

  【怎么写好 AGENTS.md — 5 个核心原则】

  1. 角色明确：开头说明项目是什么，agent 在做什么
  2. 流程具体：给出步骤顺序，不要写"尽量做好"
  3. 工具指南：说明项目特有的工具使用建议
  4. 边界清晰：列出禁止行为和需要询问的场景
  5. 示例驱动：用具体例子说明"好"和"坏"

  【反模式避坑】
  ✗ 啰嗦重复（LLM 会困惑）
  ✗ 模糊形容词（"小心""尽力"）
  ✗ 矛盾指令（A 又要 B）
  ✗ 过长（超 2000 字 LLM 抓不住重点）

  实战口诀：身份 → 流程 → 工具 → 边界 → 例子。
  每条不超过 2 行，能用列表就不用段落。
  ============================================================
-->

# IwanClaude 项目 Agent 指导

## 项目概述
IwanClaude 是一个本地优先的 AI 编码助手，双进程架构：
- `iwan-core`：持久化 daemon
- `iwan-tui`：Textual TUI 客户端
- 通过 JSON-RPC 2.0 NDJSON 通信

## 代码风格
- Python 3.11+，使用 `from __future__ import annotations`
- 类型提示必须完整（mypy 严格模式）
- 异步优先（async/await），工具调用走 asyncio
- 日志使用 `logging.getLogger(__name__)`，不要 print

## 工具使用建议
- 编辑 Python 文件前先用 `read_file` 看现有结构
- 改工具实现时，检查 `_build_registry` 是否注册
- 新工具加在 `src/iwan_claude/core/tools/builtin/`，并在 `builtin.py` 导出
- 测试加在 `tests/unit/`，命名 `test_<模块>.py`

## 安全边界
- 不要修改 `.env`、`pyproject.toml` 的依赖版本
- 不要 force push 或重写 git 历史
- 改架构（如引擎切换）前先在对话中说明方案

## 常用命令
```bash
uv sync                          # 安装依赖
uv run ruff check src tests      # lint
uv run mypy src                  # 类型检查
uv run pytest tests/unit -v      # 单元测试
```

## 自定义规则（你可以在这里加项目特定规则）
<!-- 示例： -->
<!-- - 数据库迁移必须先写 down 脚本 -->
<!-- - API 改动必须更新 WIRE_PROTOCOL.md -->
