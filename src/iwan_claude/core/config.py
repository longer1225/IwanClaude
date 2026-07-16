from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7437
_DEFAULT_LOG_LEVEL = "INFO"
_DEFAULT_LOG_FILE = "~/.iwan/logs/core.log"
_DEFAULT_LOG_FORMAT = "text"
_DEFAULT_CONFIG_PATH = "~/.iwan/config.toml"
_DEFAULT_MAX_STEPS = 20
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_TRACE_FILE = "~/.iwan/traces/daemon.jsonl"


@dataclass
class LoggingConfig:
    level: str = _DEFAULT_LOG_LEVEL
    file: str = _DEFAULT_LOG_FILE
    format: str = _DEFAULT_LOG_FORMAT  # "text" | "json"


@dataclass
class AgentConfig:
    max_steps: int = _DEFAULT_MAX_STEPS
    engine: str = "legacy"
    checkpoint_backend: str = "none"  # "none" | "memory" | "sqlite"
    checkpoint_db_path: str = ".iwan/checkpoints.db"


@dataclass
class LlmConfig:
    # provider 类型：
    #   "anthropic"       — 走 Anthropic Messages SDK；DeepSeek 用这个最简单（官方提供 Anthropic 兼容端点）
    #   "openai_compatible" — 走 OpenAI /chat/completions 协议；给通义兼容模式、智谱、Ollama 等没有 Anthropic 端点的厂商用
    provider: str = "anthropic"
    # base_url 用途取决于 provider：
    #   provider=anthropic：填 Anthropic 兼容端点。DeepSeek 填 https://api.deepseek.com/anthropic
    #   provider=openai_compatible：填 OpenAI 兼容端点 /v1 前缀，如 https://api.deepseek.com/v1
    base_url: str = ""
    # API Key 从哪个环境变量里读
    #   DeepSeek：建议 DEEPSEEK_API_KEY（anthropic 和 openai_compatible 都能用）
    #   Anthropic：ANTHROPIC_API_KEY
    #   通义：DASHSCOPE_API_KEY
    api_key_env: str = "ANTHROPIC_API_KEY"
    # 模型名
    default_model: str = _DEFAULT_MODEL
    # 模型 context window（用于算 context_pct）
    context_window: int = 128_000
    router: str = "static"  # "static" | "rule_based" (S4) | "cost_budget" (S6)


@dataclass
class TraceConfig:
    enabled: bool = True
    file: str = _DEFAULT_TRACE_FILE
    include_llm_payload: bool = True  # false 时 LLM 记录只保留摘要


@dataclass
class PermissionConfig:
    timeout_s: float = 60.0  # 审批超时秒数；0 表示不超时


@dataclass
class CompactionConfig:
    auto_threshold: float = 0.0    # context_pct 触发自动压缩的阈值（0 表示禁用，推荐用手动 /compact）
    tool_result_limit: int = 8_000  # tool_result 截断触发字符数
    tool_result_keep: int = 4_000   # 截断后保留的前缀字符数


@dataclass
class McpServerConfig:
    name: str
    transport: str = "stdio"       # "stdio" | "tcp"
    command: str = ""              # stdio 专用：可执行文件路径
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    host: str = "localhost"        # tcp 专用
    port: int = 3000               # tcp 专用


@dataclass
class McpConfig:
    servers: list[McpServerConfig] = field(default_factory=list)


@dataclass
class SandboxConfig:
    enabled: bool = True
    root: str = "./sandbox"
    allow_parent_dirs: bool = False
    max_file_size: int = 10 * 1024 * 1024
    max_total_size: int = 100 * 1024 * 1024
    search_limited: bool = False
    ask_on_access_denied: bool = True


@dataclass
class IwanConfig:
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    permission: PermissionConfig = field(default_factory=PermissionConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)


# 构建并返回运行时配置：默认值 → 全局 TOML → 项目本地 TOML → .env → 系统环境变量（后者优先级最高）
def get_config() -> IwanConfig:
    config = IwanConfig()

    # .env 必须在读取 IWAN_CONFIG 之前加载，以便 .env 中的 IWAN_CONFIG 能影响 TOML 路径
    load_dotenv(".env", override=False)

    # 若显式指定 IWAN_CONFIG，只读该文件；否则按优先级叠加：全局 → 项目本地
    explicit = os.environ.get("IWAN_CONFIG")
    if explicit:
        config_paths = [Path(explicit).expanduser()]
    else:
        config_paths = [
            Path(_DEFAULT_CONFIG_PATH).expanduser(),
            Path(".iwan/config.toml"),
        ]

    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path, "rb") as f:
                    data = tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                raise SystemExit(f"Config parse error ({config_path}): {e}") from e
            _apply_toml(config, data)

    _apply_env(config)
    return config


# 将已解析的 TOML 根表写入 config；未知小节或类型错误时退出进程
def _apply_toml(config: IwanConfig, data: dict[str, Any]) -> None:
    unknown = set(data.keys()) - {"core", "logging", "agent", "llm", "trace", "permission", "compaction", "mcp", "sandbox"}
    if unknown:
        raise SystemExit(f"Unknown top-level config keys: {', '.join(sorted(unknown))}")

    if "core" in data:
        core = data["core"]
        if not isinstance(core, dict):
            raise SystemExit("Config error: [core] must be a table")
        unknown_core: set[str] = set(core.keys()) - {"host", "port"}
        if unknown_core:
            raise SystemExit(f"Unknown [core] keys: {', '.join(sorted(unknown_core))}")
        if "host" in core:
            val = core["host"]
            if not isinstance(val, str):
                raise SystemExit("Config error: core.host must be a string")
            config.host = val
        if "port" in core:
            val = core["port"]
            if not isinstance(val, int):
                raise SystemExit("Config error: core.port must be an integer")
            config.port = val

    if "logging" in data:
        log = data["logging"]
        if not isinstance(log, dict):
            raise SystemExit("Config error: [logging] must be a table")
        unknown_log: set[str] = set(log.keys()) - {"level", "file", "format"}
        if unknown_log:
            raise SystemExit(f"Unknown [logging] keys: {', '.join(sorted(unknown_log))}")
        for key in ("level", "file", "format"):
            if key in log:
                val = log[key]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: logging.{key} must be a string")
                setattr(config.logging, key, val)

    if "agent" in data:
        agent = data["agent"]
        if not isinstance(agent, dict):
            raise SystemExit("Config error: [agent] must be a table")
        unknown_agent: set[str] = set(agent.keys()) - {"max_steps"}
        if unknown_agent:
            raise SystemExit(f"Unknown [agent] keys: {', '.join(sorted(unknown_agent))}")
        if "max_steps" in agent:
            val = agent["max_steps"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: agent.max_steps must be a positive integer")
            config.agent.max_steps = val

    if "llm" in data:
        llm = data["llm"]
        if not isinstance(llm, dict):
            raise SystemExit("Config error: [llm] must be a table")
        unknown_llm: set[str] = set(llm.keys()) - {
            "provider", "base_url", "api_key_env", "default_model", "context_window", "router",
        }
        if unknown_llm:
            raise SystemExit(f"Unknown [llm] keys: {', '.join(sorted(unknown_llm))}")
        if "provider" in llm:
            val = llm["provider"]
            if not isinstance(val, str) or val not in ("anthropic", "openai_compatible"):
                raise SystemExit("Config error: llm.provider must be 'anthropic' or 'openai_compatible'")
            config.llm.provider = val
        if "base_url" in llm:
            val = llm["base_url"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.base_url must be a string")
            config.llm.base_url = val
        if "api_key_env" in llm:
            val = llm["api_key_env"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.api_key_env must be a string")
            config.llm.api_key_env = val
        if "default_model" in llm:
            val = llm["default_model"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.default_model must be a string")
            config.llm.default_model = val
        if "context_window" in llm:
            val = llm["context_window"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: llm.context_window must be a positive integer")
            config.llm.context_window = val
        if "router" in llm:
            val = llm["router"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.router must be a string")
            config.llm.router = val

    if "trace" in data:
        trace = data["trace"]
        if not isinstance(trace, dict):
            raise SystemExit("Config error: [trace] must be a table")
        unknown_trace: set[str] = set(trace.keys()) - {"enabled", "file", "include_llm_payload"}
        if unknown_trace:
            raise SystemExit(f"Unknown [trace] keys: {', '.join(sorted(unknown_trace))}")
        if "enabled" in trace:
            val = trace["enabled"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: trace.enabled must be a boolean")
            config.trace.enabled = val
        if "file" in trace:
            val = trace["file"]
            if not isinstance(val, str):
                raise SystemExit("Config error: trace.file must be a string")
            config.trace.file = val
        if "include_llm_payload" in trace:
            val = trace["include_llm_payload"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: trace.include_llm_payload must be a boolean")
            config.trace.include_llm_payload = val

    if "permission" in data:
        perm = data["permission"]
        if not isinstance(perm, dict):
            raise SystemExit("Config error: [permission] must be a table")
        unknown_perm: set[str] = set(perm.keys()) - {"timeout_s"}
        if unknown_perm:
            raise SystemExit(f"Unknown [permission] keys: {', '.join(sorted(unknown_perm))}")
        if "timeout_s" in perm:
            val = perm["timeout_s"]
            if not isinstance(val, (int, float)) or val < 0:
                raise SystemExit("Config error: permission.timeout_s must be a non-negative number")
            config.permission.timeout_s = float(val)

    if "compaction" in data:
        comp = data["compaction"]
        if not isinstance(comp, dict):
            raise SystemExit("Config error: [compaction] must be a table")
        unknown_comp: set[str] = set(comp.keys()) - {"auto_threshold", "tool_result_limit", "tool_result_keep"}
        if unknown_comp:
            raise SystemExit(f"Unknown [compaction] keys: {', '.join(sorted(unknown_comp))}")
        if "auto_threshold" in comp:
            val = comp["auto_threshold"]
            if not isinstance(val, (int, float)) or not (0.0 <= val <= 1.0):
                raise SystemExit("Config error: compaction.auto_threshold must be between 0 and 1")
            config.compaction.auto_threshold = float(val)
        if "tool_result_limit" in comp:
            val = comp["tool_result_limit"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: compaction.tool_result_limit must be a positive integer")
            config.compaction.tool_result_limit = val
        if "tool_result_keep" in comp:
            val = comp["tool_result_keep"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: compaction.tool_result_keep must be a positive integer")
            config.compaction.tool_result_keep = val

    if "mcp" in data:
        mcp = data["mcp"]
        if not isinstance(mcp, dict):
            raise SystemExit("Config error: [mcp] must be a table")
        unknown_mcp: set[str] = set(mcp.keys()) - {"servers"}
        if unknown_mcp:
            raise SystemExit(f"Unknown [mcp] keys: {', '.join(sorted(unknown_mcp))}")
        servers_raw = mcp.get("servers", [])
        if not isinstance(servers_raw, list):
            raise SystemExit("Config error: mcp.servers must be an array of tables")
        for i, srv in enumerate(servers_raw):
            if not isinstance(srv, dict):
                raise SystemExit(f"Config error: mcp.servers[{i}] must be a table")
            name = srv.get("name")
            if not isinstance(name, str) or not name:
                raise SystemExit(f"Config error: mcp.servers[{i}].name must be a non-empty string")
            transport = srv.get("transport", "stdio")
            if transport not in ("stdio", "tcp"):
                raise SystemExit(f"Config error: mcp.servers[{i}].transport must be 'stdio' or 'tcp'")
            s = McpServerConfig(name=name, transport=transport)
            if "command" in srv:
                val = srv["command"]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: mcp.servers[{i}].command must be a string")
                s.command = val
            if "args" in srv:
                val = srv["args"]
                if not isinstance(val, list):
                    raise SystemExit(f"Config error: mcp.servers[{i}].args must be an array")
                s.args = [str(a) for a in val]
            if "env" in srv:
                val = srv["env"]
                if not isinstance(val, dict):
                    raise SystemExit(f"Config error: mcp.servers[{i}].env must be a table")
                s.env = {str(k): str(v) for k, v in val.items()}
            if "host" in srv:
                val = srv["host"]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: mcp.servers[{i}].host must be a string")
                s.host = val
            if "port" in srv:
                val = srv["port"]
                if not isinstance(val, int):
                    raise SystemExit(f"Config error: mcp.servers[{i}].port must be an integer")
                s.port = val
            config.mcp.servers.append(s)

    if "sandbox" in data:
        sb = data["sandbox"]
        if not isinstance(sb, dict):
            raise SystemExit("Config error: [sandbox] must be a table")
        unknown_sb: set[str] = set(sb.keys()) - {"enabled", "root", "allow_parent_dirs", "max_file_size", "max_total_size", "search_limited", "ask_on_access_denied"}
        if unknown_sb:
            raise SystemExit(f"Unknown [sandbox] keys: {', '.join(sorted(unknown_sb))}")
        if "enabled" in sb:
            val = sb["enabled"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: sandbox.enabled must be a boolean")
            config.sandbox.enabled = val
        if "root" in sb:
            val = sb["root"]
            if not isinstance(val, str):
                raise SystemExit("Config error: sandbox.root must be a string")
            config.sandbox.root = val
        if "allow_parent_dirs" in sb:
            val = sb["allow_parent_dirs"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: sandbox.allow_parent_dirs must be a boolean")
            config.sandbox.allow_parent_dirs = val
        if "max_file_size" in sb:
            val = sb["max_file_size"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: sandbox.max_file_size must be a positive integer")
            config.sandbox.max_file_size = val
        if "max_total_size" in sb:
            val = sb["max_total_size"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: sandbox.max_total_size must be a positive integer")
            config.sandbox.max_total_size = val
        if "search_limited" in sb:
            val = sb["search_limited"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: sandbox.search_limited must be a boolean")
            config.sandbox.search_limited = val
        if "ask_on_access_denied" in sb:
            val = sb["ask_on_access_denied"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: sandbox.ask_on_access_denied must be a boolean")
            config.sandbox.ask_on_access_denied = val


# 用 IWAN_* 环境变量覆盖 config 中对应字段（若变量已设置）
def _apply_env(config: IwanConfig) -> None:
    host = os.environ.get("IWAN_HOST")
    if host is not None:
        config.host = host

    port_str = os.environ.get("IWAN_PORT")
    if port_str is not None:
        try:
            config.port = int(port_str)
        except ValueError:
            raise SystemExit(f"Config error: IWAN_PORT must be an integer, got: {port_str!r}")

    log_level = os.environ.get("IWAN_LOG_LEVEL")
    if log_level is not None:
        config.logging.level = log_level

    log_file = os.environ.get("IWAN_LOG_FILE")
    if log_file is not None:
        config.logging.file = log_file

    log_format = os.environ.get("IWAN_LOG_FORMAT")
    if log_format is not None:
        config.logging.format = log_format

    max_steps_str = os.environ.get("IWAN_MAX_STEPS")
    if max_steps_str is not None:
        try:
            val = int(max_steps_str)
            if val <= 0:
                raise SystemExit(
                    "Config error: IWAN_MAX_STEPS must be a positive integer,"
                    f" got: {max_steps_str!r}"
                )
            config.agent.max_steps = val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_MAX_STEPS must be an integer, got: {max_steps_str!r}"
            )

    llm_provider = os.environ.get("IWAN_LLM_PROVIDER")
    if llm_provider is not None:
        if llm_provider not in ("anthropic", "openai_compatible"):
            raise SystemExit(
                "Config error: IWAN_LLM_PROVIDER must be 'anthropic' or 'openai_compatible',"
                f" got: {llm_provider!r}"
            )
        config.llm.provider = llm_provider

    llm_base_url = os.environ.get("IWAN_LLM_BASE_URL")
    if llm_base_url is not None:
        config.llm.base_url = llm_base_url

    llm_api_key_env = os.environ.get("IWAN_LLM_API_KEY_ENV")
    if llm_api_key_env is not None:
        config.llm.api_key_env = llm_api_key_env

    default_model = os.environ.get("IWAN_LLM_DEFAULT_MODEL")
    if default_model is not None:
        config.llm.default_model = default_model

    llm_ctx = os.environ.get("IWAN_LLM_CONTEXT_WINDOW")
    if llm_ctx is not None:
        try:
            val = int(llm_ctx)
            if val <= 0:
                raise SystemExit(
                    "Config error: IWAN_LLM_CONTEXT_WINDOW must be a positive integer,"
                    f" got: {llm_ctx!r}"
                )
            config.llm.context_window = val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_LLM_CONTEXT_WINDOW must be an integer, got: {llm_ctx!r}"
            )

    trace_enabled = os.environ.get("IWAN_TRACE_ENABLED")
    if trace_enabled is not None:
        config.trace.enabled = trace_enabled.lower() not in ("0", "false", "no")

    trace_file = os.environ.get("IWAN_TRACE_FILE")
    if trace_file is not None:
        config.trace.file = trace_file

    trace_payload = os.environ.get("IWAN_TRACE_INCLUDE_LLM_PAYLOAD")
    if trace_payload is not None:
        config.trace.include_llm_payload = trace_payload.lower() not in ("0", "false", "no")

    perm_timeout = os.environ.get("IWAN_PERMISSION_TIMEOUT_S")
    if perm_timeout is not None:
        try:
            perm_timeout_val = float(perm_timeout)
            if perm_timeout_val < 0:
                raise SystemExit(
                    f"Config error: IWAN_PERMISSION_TIMEOUT_S must be >= 0, got: {perm_timeout!r}"
                )
            config.permission.timeout_s = perm_timeout_val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_PERMISSION_TIMEOUT_S must be a number, got: {perm_timeout!r}"
            )

    compact_threshold = os.environ.get("IWAN_COMPACT_THRESHOLD")
    if compact_threshold is not None:
        try:
            compact_threshold_val = float(compact_threshold)
            if not (0.0 <= compact_threshold_val <= 1.0):
                raise SystemExit(
                    f"Config error: IWAN_COMPACT_THRESHOLD must be between 0 and 1, got: {compact_threshold!r}"
                )
            config.compaction.auto_threshold = compact_threshold_val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_COMPACT_THRESHOLD must be a number, got: {compact_threshold!r}"
            )

    compact_tool_limit = os.environ.get("IWAN_COMPACT_TOOL_LIMIT")
    if compact_tool_limit is not None:
        try:
            compact_tool_limit_val = int(compact_tool_limit)
            if compact_tool_limit_val <= 0:
                raise SystemExit(
                    f"Config error: IWAN_COMPACT_TOOL_LIMIT must be a positive integer, got: {compact_tool_limit!r}"
                )
            config.compaction.tool_result_limit = compact_tool_limit_val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_COMPACT_TOOL_LIMIT must be an integer, got: {compact_tool_limit!r}"
            )

    compact_tool_keep = os.environ.get("IWAN_COMPACT_TOOL_KEEP")
    if compact_tool_keep is not None:
        try:
            compact_tool_keep_val = int(compact_tool_keep)
            if compact_tool_keep_val <= 0:
                raise SystemExit(
                    f"Config error: IWAN_COMPACT_TOOL_KEEP must be a positive integer, got: {compact_tool_keep!r}"
                )
            config.compaction.tool_result_keep = compact_tool_keep_val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_COMPACT_TOOL_KEEP must be an integer, got: {compact_tool_keep!r}"
            )

    sandbox_enabled = os.environ.get("IWAN_SANDBOX_ENABLED")
    if sandbox_enabled is not None:
        config.sandbox.enabled = sandbox_enabled.lower() not in ("0", "false", "no")

    sandbox_root = os.environ.get("IWAN_SANDBOX_ROOT")
    if sandbox_root is not None:
        config.sandbox.root = sandbox_root

    sandbox_max_file_size = os.environ.get("IWAN_SANDBOX_MAX_FILE_SIZE")
    if sandbox_max_file_size is not None:
        try:
            val = int(sandbox_max_file_size)
            if val <= 0:
                raise SystemExit(
                    f"Config error: IWAN_SANDBOX_MAX_FILE_SIZE must be a positive integer, got: {sandbox_max_file_size!r}"
                )
            config.sandbox.max_file_size = val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_SANDBOX_MAX_FILE_SIZE must be an integer, got: {sandbox_max_file_size!r}"
            )

    sandbox_max_total_size = os.environ.get("IWAN_SANDBOX_MAX_TOTAL_SIZE")
    if sandbox_max_total_size is not None:
        try:
            val = int(sandbox_max_total_size)
            if val <= 0:
                raise SystemExit(
                    f"Config error: IWAN_SANDBOX_MAX_TOTAL_SIZE must be a positive integer, got: {sandbox_max_total_size!r}"
                )
            config.sandbox.max_total_size = val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_SANDBOX_MAX_TOTAL_SIZE must be an integer, got: {sandbox_max_total_size!r}"
            )

    sandbox_search_limited = os.environ.get("IWAN_SANDBOX_SEARCH_LIMITED")
    if sandbox_search_limited is not None:
        config.sandbox.search_limited = sandbox_search_limited.lower() not in ("0", "false", "no")

    sandbox_ask_on_access_denied = os.environ.get("IWAN_SANDBOX_ASK_ON_ACCESS_DENIED")
    if sandbox_ask_on_access_denied is not None:
        config.sandbox.ask_on_access_denied = sandbox_ask_on_access_denied.lower() not in ("0", "false", "no")
