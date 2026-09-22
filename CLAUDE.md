# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install / sync dependencies
uv sync

# Lint
uv run ruff check src tests scripts
uv run mypy src

# Tests
uv run pytest tests/unit -v           # unit only (fast, no daemon)
uv run pytest tests/integration -v    # needs no running daemon; fixture spawns one
uv run pytest tests/ -v               # all

# Single test
uv run pytest tests/unit/test_envelope.py::test_request_roundtrip -v

# Regenerate WIRE_PROTOCOL.md after changing bus models
uv run python scripts/gen_protocol_doc.py

# Verify WIRE_PROTOCOL.md is in sync (used in CI equivalent)
uv run python scripts/gen_protocol_doc.py --check

# Run daemon manually
uv run iwan-core                        # foreground; Ctrl+C to stop
IWAN_PORT=8000 uv run iwan-core        # override port

# Send a ping
uv run iwan ping
uv run iwan --version
```

## Architecture

This is a **dual-process** local AI agent system. `iwan-core` is a persistent daemon; `kama` and `iwan-tui` are clients that connect to it over a Unix domain socket.

```
iwan-core (daemon)
  └─ listens on 127.0.0.1:7437 (TCP)
       ↑ JSON-RPC 2.0 NDJSON
iwan (CLI)   iwan-tui (TUI, S2+)
```

**`iwan-tui` is the primary frontend.** All user-facing work on task management, observability, and interaction should be designed for and validated in the TUI first. The `iwan` CLI exists only for quick scripted testing and debugging — it is not a product surface. When implementing features that touch the user interface, invest in the TUI layout, event rendering, and keyboard interactions. Do not shortcut TUI work by pointing to the CLI as an alternative.

### Protocol layer (`src/iwan_claude/core/bus/`)

All IPC messages are typed pydantic v2 models with a **discriminated union on the `type` field**. This is the contract boundary — adding a new command or event means adding a new model class to `commands.py` or `events.py` and extending the `Command`/`Event` union.

- `envelope.py` — `JsonRpcRequest`, `JsonRpcSuccess`, `JsonRpcError`, error code constants, `make_error()`
- `commands.py` — `Command` union; currently only `PingCommand` + `PongResult`
- `events.py` — `Event` union; currently only `CoreStartedEvent`

`WIRE_PROTOCOL.md` is **generated** from these models by `scripts/gen_protocol_doc.py`. Always regenerate and commit it after changing bus models.

### Transport layer (`src/iwan_claude/core/transport/`)

- `socket_server.py` — TCP server (`asyncio.start_server`); reads NDJSON lines, dispatches to registered `CommandHandler`s, handles JSON-RPC error cases. On `start()`, probes `host:port` first — errors if another daemon is already listening. Handlers registered via `server.register("method.name", handler_fn)`.

### Config (`src/iwan_claude/core/config.py`)

Four-tier priority: **built-in defaults → `~/.iwan/config.toml` → `.env` → env vars**.

S0 keys: `host` (default `127.0.0.1`), `port` (default `7437`), `log_level`, `log_file`. Config file is silently skipped if absent; unknown keys cause a hard exit.

Relevant env vars: `IWAN_CONFIG`, `IWAN_HOST`, `IWAN_PORT`, `IWAN_LOG_LEVEL`, `IWAN_LOG_FILE`, `IWAN_LOG_FORMAT`.

### Daemon entry (`src/iwan_claude/core/app.py`)

`CoreApp.run()` is the single async entry point: loads config → sets up logging → creates `SocketServer` → registers handlers → waits for `SIGINT`/`SIGTERM` → calls `server.stop()`. Adding new handlers: instantiate a handler method on `CoreApp` and call `server.register()`.

### Testing

Integration tests in `tests/conftest.py` spawn a real daemon subprocess using a random free port (via `free_port` fixture). The fixture finds a free port, releases it, passes it to the daemon via `IWAN_PORT`, then polls `asyncio.open_connection` until the daemon is ready.

### Code style

All functions must have a **single-line Chinese comment** immediately above the `def` line explaining what the function does. Example:

```python
# 发送 JSON-RPC 响应并刷新写缓冲区
async def _send(self, writer: asyncio.StreamWriter, msg: BaseModel) -> None:
    ...
```

Do not write multi-line docstrings; one concise Chinese line is enough.

**Test functions** require **two Chinese comment lines** immediately above the `def` line:

```python
# 功能：验证 publish 后订阅者能收到事件对象
# 设计：用内联 handler 收集事件引用，断言 is 而非 ==，排除序列化中间步骤的干扰
async def test_publish_reaches_subscriber() -> None:
    ...
```

- `# 功能：` — 该测试验证的具体行为或不变式，一句话说清楚"测什么"
- `# 设计：` — 为什么选择这种测试方式：覆盖了什么边界条件、为什么用这个 stub/fixture、这种断言方式相比其他方式的优势

两行注释缺一不可。功能行让读者 5 秒内判断测试意图；设计行让读者理解测试背后的决策，而非只看到操作步骤。

## Security model — Claude Code as the benchmark

iwanclaude 的长期目标是持续升级本项目，安全/权限体系以 Claude Code 官方实现为对标（调研存档见 `docs/learning/claudecode-sandbox-research-2026-09.md`）。改动 `permissions/`、`sandbox.py`、`tools/builtin/` 前必须遵守以下事实模型：

**规则评估序（不可动摇）**：deny → ask → allow，先匹配先赢。deny 盖过一切 allow 例外；ask 盖过 allow（预确认 ≠ 免确认）。宽 deny 永远不可被窄 allow 例外——这是与"黑名单漏着放"相反的方向，我们所有规则引擎必须按这个序实现。

**规则语义**：工具级规则（`Bash`）盖过整个工具；`Tool(specifier)` 的 specifier 匹配规则：无 `*` = **整串精确匹配**（不是前缀）；`:*` 是**遗留前缀语法**，等价于尾部 ` *`；`*` 可匹配含空格的任意文本但**前面必须有空格**（`git*` 不合法）；文件类工具（`Read`/`Edit`）用 **gitignore 语法**（`Read(./.env)`）；`WebFetch(domain:*)` ≠ 裸 `WebFetch`。未匹配任何规则的工具调用默认弹 ASK（default-ask）。`rm -rf` 之类破坏性形态在官方实现中无硬编码规则——靠 default-ask + 模型保守性 + hooks。

**权限模式**（5 种，我们应支持等价概念）：`default`（每次工具调用决策）、`acceptEdits`（项目内编辑/创建免批，**不绕 ask 规则**）、`plan`（权限覆盖层不执行：编辑/多步读/插件启用全被拒）、`auto`（分类器模型判断非规则严格性，需 `autoMode` 配置）、`bypassPermissions`（跳规则但 managed deny/ask 仍生效）。`dontAsk` 非模式，是 setting 值。

**hooks 是用户不可见的强制层**：PreToolUse 在任何权限评估前触发；exit 2 = 强制阻断+stderr 回灌模型；`bypassPermissions`/`--dangerously-skip-permissions` 也跳不过（Claude 4.5+）；企业 managed hooks 连 `disableAllHooks` 都跳不过。PolicyHooks（allow 直批 / ask 强制弹窗）可改权限结果。我们补 hooks 体系时语义对齐这里。

**OS 沙箱**：macOS 用 `sandbox-exec`（Seatbelt）、Linux/WSL2 用 bubblewrap，**原生 Windows 不支持**（官方指引走 WSL2，issue #46740 开放）——我们在 Windows 上做 `CreateRestrictedToken`/ACL 是自研路线，无官方先例，必须 fail-closed 设计。沙箱默认值：写 = cwd + 会话临时目录（`filesystem.allowWrite` 增补），网络 = **默认全拒**（域名进 `network.allowedDomains`；本地代理按 hostname 决策、不解 TLS；绕过代理的工具在内核层结构性不可达）。与审批耦合的三个开关值得对齐：`autoAllowBashIfSandboxed`（可沙箱化的命令免弹问）、`allowUnsandboxedCommands:false`（关 `dangerouslyDisableSandbox` 逃逸口）、`failIfUnavailable`（沙箱不可用=硬失败）。

**后台进程绕沙箱网络限制是官方设计行为**——不是漏洞。我们的 Job Object 只兜进程树击杀，网络管控要在 spawn 前强制代理。

## Design docs (outside the repo)

The planning documents live in `../docs/` (sibling of this repo, not committed here):
- `agent_development_plan.md` — staged development roadmap S0–S8
- `s0_implementation_plan.md` — detailed S0 decisions and rationale
- `agent_functional_outline.md` — full feature catalogue
