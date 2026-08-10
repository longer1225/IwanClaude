"""
配置模块 - 管理整个系统的配置加载和验证

【学习要点】
1. Dataclasses：Python 的数据类装饰器，用于创建简单的数据容器
2. 配置优先级：默认值 → TOML 文件 → .env → 环境变量（后者优先级最高）
3. 配置验证：在加载配置时进行类型检查和值范围验证
4. 配置热加载：通过环境变量可以动态覆盖配置，无需修改配置文件

【配置来源（优先级从低到高）】
1. 默认值：在 dataclass 字段定义中指定
2. 全局 TOML：~/.iwan/config.toml
3. 项目本地 TOML：./.iwan/config.toml
4. .env 文件：./.env 或包目录下的 .env
5. 环境变量：IWAN_* 格式的环境变量

【配置文件格式（TOML）】
```toml
[core]
host = "127.0.0.1"
port = 7437

[llm]
provider = "anthropic"
base_url = "https://api.deepseek.com/anthropic"
api_key_env = "DEEPSEEK_API_KEY"
default_model = "claude-sonnet-4-6"
```
"""
from __future__ import annotations

# os：操作系统相关功能，用于读取环境变量
# tomllib：TOML 文件解析（Python 3.11+ 内置）
# dataclasses：数据类装饰器
# pathlib：路径操作
# typing：类型提示
# dotenv：加载 .env 文件中的环境变量
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ===== 默认配置值 =====
# 这些常量定义了系统的默认配置值
_DEFAULT_HOST = "127.0.0.1"                    # 默认绑定地址（本地回环）
_DEFAULT_PORT = 7437                           # 默认端口号
_DEFAULT_LOG_LEVEL = "INFO"                    # 默认日志级别
_DEFAULT_LOG_FILE = "~/.iwan/logs/core.log"    # 默认日志文件路径
_DEFAULT_LOG_FORMAT = "text"                   # 默认日志格式（text 或 json）
_DEFAULT_CONFIG_PATH = "~/.iwan/config.toml"   # 默认配置文件路径
_DEFAULT_MAX_STEPS = 20                        # Agent 最大步骤数
_DEFAULT_MODEL = "claude-sonnet-4-6"           # 默认 LLM 模型
_DEFAULT_TRACE_FILE = "~/.iwan/traces/daemon.jsonl"  # 默认 trace 文件路径
_DEFAULT_AUTO_MODE = "off"                     # 默认自动模式（off / read_only / on）
_DEFAULT_EFFORT_LEVEL = "medium"               # 默认努力等级（minimal / low / medium / high / max）
_DEFAULT_MODEL_PRESET = "balanced"             # 默认模型预设（fast / balanced / powerful）


# ===== 配置数据类 =====
# 使用 @dataclass 装饰器创建配置类，每个类对应配置文件中的一个 section

@dataclass
class LoggingConfig:
    """
    日志配置类 - 对应 [logging] section
    
    属性：
        level: 日志级别（DEBUG, INFO, WARNING, ERROR）
        file: 日志文件路径（支持 ~ 表示用户主目录）
        format: 日志格式（text 或 json）
    """
    level: str = _DEFAULT_LOG_LEVEL
    file: str = _DEFAULT_LOG_FILE
    format: str = _DEFAULT_LOG_FORMAT  # "text" | "json"


@dataclass
class AgentConfig:
    """
    Agent 配置类 - 对应 [agent] section
    
    属性：
        max_steps: Agent 执行的最大步骤数（防止无限循环）
        engine: Agent 引擎类型（legacy / langgraph / plan_execute / debate / pipeline）
            - legacy: 简单循环实现（AgentLoop）
            - langgraph: LangGraph ReAct 引擎（chat→tools 循环）
            - plan_execute: LangGraph Plan & Execute 引擎（先规划再执行再反思）
            - debate: LangGraph Worker-Critic 辩论引擎（worker 回答 → critic 审查 → 改进循环）
            - pipeline: LangGraph 三角色流水线引擎（planner 规划 → executor 执行 → reviewer 审查）
        checkpoint_backend: 检查点存储后端（none, memory, sqlite）
        checkpoint_db_path: SQLite 检查点数据库路径
        auto_mode: 自动模式（off / read_only / on）
        effort_level: 努力等级（minimal / low / medium / high / max）
        model_preset: 模型预设（fast / balanced / powerful）
    """
    max_steps: int = _DEFAULT_MAX_STEPS
    engine: str = "legacy"
    checkpoint_backend: str = "none"  # "none" | "memory" | "sqlite"
    checkpoint_db_path: str = ".iwan/checkpoints.db"
    auto_mode: str = _DEFAULT_AUTO_MODE  # "off" | "read_only" | "on"
    effort_level: str = _DEFAULT_EFFORT_LEVEL  # "minimal" | "low" | "medium" | "high" | "max"
    model_preset: str = _DEFAULT_MODEL_PRESET  # "fast" | "balanced" | "powerful"


@dataclass
class LlmConfig:
    """
    LLM 配置类 - 对应 [llm] section
    
    核心配置说明：
    
    provider（提供商）：
        - "anthropic"：使用 Anthropic Messages SDK
          - DeepSeek 推荐使用此模式，官方提供 Anthropic 兼容端点
        - "openai_compatible"：使用 OpenAI /chat/completions 协议
          - 适用于通义、智谱、Ollama 等没有 Anthropic 端点的厂商
    
    base_url（基础 URL）：
        - provider=anthropic：填 Anthropic 兼容端点
          - DeepSeek: https://api.deepseek.com/anthropic
        - provider=openai_compatible：填 OpenAI 兼容端点（带 /v1 前缀）
          - DeepSeek: https://api.deepseek.com/v1
    
    api_key_env（API Key 环境变量名）：
        - DeepSeek: DEEPSEEK_API_KEY（anthropic 和 openai_compatible 都能用）
        - Anthropic: ANTHROPIC_API_KEY
        - 通义: DASHSCOPE_API_KEY
        - 智谱: ZHIPU_API_KEY
    
    router（模型路由策略）：
        - "static"：始终使用 default_model
        - "rule_based"：基于规则选择模型（如简单任务用 S4）
        - "cost_budget"：基于成本预算选择模型
    """
    # provider 类型
    provider: str = "anthropic"
    # base_url 用途取决于 provider
    base_url: str = ""
    # API Key 从哪个环境变量里读
    api_key_env: str = "ANTHROPIC_API_KEY"
    # 模型名
    default_model: str = _DEFAULT_MODEL
    # 模型 context window（用于计算 context_pct）
    context_window: int = 128_000
    # 模型路由策略
    router: str = "static"  # "static" | "rule_based" (S4) | "cost_budget" (S6)


@dataclass
class TraceConfig:
    """
    Trace 配置类 - 对应 [trace] section
    
    属性：
        enabled: 是否启用 trace（记录系统运行日志）
        file: trace 文件路径（JSONL 格式）
        include_llm_payload: 是否包含完整的 LLM 请求/响应（false 时只保留摘要）
    """
    enabled: bool = True
    file: str = _DEFAULT_TRACE_FILE
    include_llm_payload: bool = True  # false 时 LLM 记录只保留摘要


@dataclass
class PermissionConfig:
    """
    权限配置类 - 对应 [permission] section
    
    属性：
        timeout_s: 权限审批超时时间（秒），0 表示不超时
    """
    timeout_s: float = 60.0  # 审批超时秒数；0 表示不超时


@dataclass
class CompactionConfig:
    """
    会话压缩配置类 - 对应 [compaction] section
    
    属性：
        auto_threshold: context_pct 触发自动压缩的阈值（0 表示禁用，推荐用手动 /compact）
        tool_result_limit: tool_result 截断触发字符数
        tool_result_keep: 截断后保留的前缀字符数
    """
    auto_threshold: float = 0.0    # context_pct 触发自动压缩的阈值（0 表示禁用，推荐用手动 /compact）
    tool_result_limit: int = 8_000  # tool_result 截断触发字符数
    tool_result_keep: int = 4_000   # 截断后保留的前缀字符数


@dataclass
class McpServerConfig:
    """
    MCP 服务器配置类 - 对应 [mcp.servers] 数组中的每个元素
    
    MCP（Model Context Protocol）是 Anthropic 定义的协议，
    允许外部工具服务器向 LLM 暴露工具。
    
    属性：
        name: 服务器名称（用于标识）
        transport: 传输方式（stdio 或 tcp）
        command: stdio 模式下的可执行文件路径
        args: 命令行参数列表
        env: 额外的环境变量
        host: tcp 模式下的主机地址
        port: tcp 模式下的端口号
    """
    name: str
    transport: str = "stdio"       # "stdio" | "tcp"
    command: str = ""              # stdio 专用：可执行文件路径
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    host: str = "localhost"        # tcp 专用
    port: int = 3000               # tcp 专用


@dataclass
class McpConfig:
    """
    MCP 配置类 - 对应 [mcp] section
    
    属性：
        servers: MCP 服务器配置列表
    """
    servers: list[McpServerConfig] = field(default_factory=list)


# ===== 沙箱进程内强化默认常量 =====
# 命令黑名单正则（跨平台，命中则硬 DENY，不可被用户批准绕过）
# 覆盖：破坏性命令 + Windows 破坏性命令 + 凭证外传 + 凭证读取高敏路径
_DEFAULT_COMMAND_BLACKLIST: tuple[str, ...] = (
    # === Unix 破坏性命令 ===
    r"\brm\s+-rf?\s+/(?:\s|$)",               # rm -rf /
    r"\brm\s+-rf?\s+~",                        # rm -rf ~
    r"\brm\s+-rf?\s+\*",                       # rm -rf *
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;?\s*:",       # fork 炸弹 :(){:|:&};:
    r"\bmkfs\b",                               # 格式化文件系统
    r"\bdd\s+if=.*of=/dev/",                   # dd 写设备
    r"\bchmod\s+-R\s+777\s+/",                 # 递归改权限到根
    # === Windows 破坏性命令 ===
    r"(?i)\bformat\s+[A-Z]:",                  # format C:
    r"(?i)\bdiskpart\b",                       # 磁盘分区
    r"(?i)\brmdir\s+/s\s+/q",                  # rmdir /s /q
    r"(?i)\bdel\s+/f\s+/s\s+/q",               # del /f /s /q
    r"(?i)\breg\s+delete\s+/f",                # reg delete /f
    r"(?i)\btaskkill\s+/f\s+/im",              # taskkill /f /im
    r"(?i)\bshutdown\b",                       # 关机
    r"(?i)\bschtasks\s+/create",               # 计划任务（持久化后门）
    # === 凭证外传（远程执行）===
    r"(?i)\bcurl\s+.*\|\s*(?:sh|bash|pwsh)",   # curl | sh
    r"(?i)\bwget\s+.*\|\s*(?:sh|bash|pwsh)",   # wget | sh
    r"(?i)\biex\s*\(\s*irm\s",                 # PowerShell iex(irm) 远程执行
    r"(?i)\binvoke-expression.*net\.webclient", # PS 远程执行
    # === 凭证读取高敏路径 ===
    r"/etc/passwd",                            # Unix 密码文件
    r"/etc/shadow",                            # Unix 影子密码
    r"~/\.ssh/",                               # SSH 私钥
    r"(?i)%USERPROFILE%.*\\\.ssh",             # Windows SSH 私钥
    r"(?i)%USERPROFILE%.*\\\.aws",             # AWS 凭证
    r"(?i)%APPDATA%.*\\Microsoft\\Credentials", # Windows 凭证管理器
)

# 环境变量脱敏正则（匹配变量名，命中则从子进程 env 中移除）
# 覆盖：API key、密钥、令牌、密码、凭证等通用模式 + 已知厂商变量名
_DEFAULT_ENV_SCRUB_PATTERNS: tuple[str, ...] = (
    r"(?i).*_API_KEY$",          # ANTHROPIC_API_KEY, DASHSCOPE_API_KEY, OPENAI_API_KEY
    r"(?i).*_SECRET.*",          # AWS_SECRET_ACCESS_KEY, CLIENT_SECRET
    r"(?i).*_TOKEN$",            # GITHUB_TOKEN, GITLAB_TOKEN
    r"(?i).*_PASSWORD$",         # DB_PASSWORD, SMTP_PASSWORD
    r"(?i).*_CREDENTIAL.*",      # GOOGLE_APPLICATION_CREDENTIALS
    r"(?i)^AWS_ACCESS_KEY_ID$",
    r"(?i)^AWS_SESSION_TOKEN$",
    r"(?i)^ANTHROPIC_AUTH_TOKEN$",
    r"(?i)^DEEPSEEK_API_KEY$",
    r"(?i)^DASHSCOPE_API_KEY$",
)


@dataclass
class SandboxConfig:
    """
    沙箱配置类 - 对应 [sandbox] section

    沙箱是一个受限的文件系统环境，用于限制 Agent 的文件操作范围。
    默认 root 为 "."（当前工作目录/项目根），Agent 可操作项目文件但不能越界。

    属性：
        enabled: 是否启用沙箱
        root: 沙箱根目录（默认 "."=CWD，Agent 可操作项目文件但不能越界到 ~/.ssh、/etc 等）
        allow_parent_dirs: 是否允许访问 sandbox_root 的祖先目录（monorepo 场景）
        max_file_size: 单个文件最大大小（字节）
        max_total_size: 沙箱总大小限制（字节）
        search_limited: 是否限制搜索范围（在沙箱内）
        ask_on_access_denied: 访问被拒绝时的错误提示措辞（不再影响拦截行为，越界始终抛 SandboxAccessError）

    进程内强化属性（新增）：
        command_blacklist: 命令黑名单正则列表，命中则硬 DENY（不可被用户批准绕过）
        env_scrub_patterns: 环境变量脱敏正则列表，匹配变量名则从子进程 env 中移除
        block_network_commands: 是否阻断网络外传命令（curl/wget/nc/ssh/scp/ftp/telnet/PS iwr）
        audit_log: 是否启用审计日志
        audit_log_path: 审计日志文件路径（相对路径基于 CWD）
    """
    enabled: bool = True
    root: str = "."
    allow_parent_dirs: bool = False
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    max_total_size: int = 100 * 1024 * 1024  # 100MB
    search_limited: bool = False
    ask_on_access_denied: bool = True

    # ===== 进程内强化字段 =====
    # 命令黑名单（正则列表，命中则硬 DENY，不可被用户批准绕过）
    # 默认覆盖：破坏性命令 + 外传命令 + 凭证读取高敏路径
    command_blacklist: list[str] = field(
        default_factory=lambda: list(_DEFAULT_COMMAND_BLACKLIST)
    )
    # 环境变量脱敏：变量名匹配这些正则的 env 将被从子进程中移除
    # 默认覆盖：*_API_KEY、*_SECRET、*_TOKEN、*_PASSWORD、*_CREDENTIAL
    env_scrub_patterns: list[str] = field(
        default_factory=lambda: list(_DEFAULT_ENV_SCRUB_PATTERNS)
    )
    # 是否阻断网络外传命令（curl/wget/nc/ssh/scp/ftp/telnet/Invoke-WebRequest）
    block_network_commands: bool = True
    # 审计日志开关与路径
    audit_log: bool = True
    audit_log_path: str = ".iwan/audit.log"


@dataclass
class RagConfig:
    """
    RAG 配置类 - 对应 [rag] section
    
    RAG（Retrieval-Augmented Generation）是检索增强生成技术，
    允许 Agent 从本地文档中检索信息。
    
    属性：
        enabled: 是否启用 RAG
        embedding_model: 嵌入模型名称
        embedding_base_url: 嵌入模型 API 基础 URL
        embedding_api_key_env: 读取 Embedding API Key 的环境变量名（留空则启用兼容兜底）
        max_chunk_size: 文档分块最大大小（字符数）
        chunk_overlap: 分块重叠大小（字符数）
        top_k: 检索时返回的最相关文档数
        index_path: 向量索引存储路径
    """
    enabled: bool = False
    embedding_model: str = "text-embedding-v3"  # 通义 dashscope embedding 模型（DeepSeek 不提供 embedding 端点）
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 通义 OpenAI 兼容端点
    embedding_api_key_env: str = ""  # 留空则启用兼容列表：QIANWEN_API_KEY / QWEN_API_KEY / DASHSCOPE_API_KEY 等
    max_chunk_size: int = 512
    chunk_overlap: int = 64
    top_k: int = 5
    index_path: str = ".iwan/rag_index"


@dataclass
class IwanConfig:
    """
    主配置类 - 整合所有子配置类
    
    这是整个系统的配置入口，包含所有子系统的配置。
    使用 field(default_factory=...) 创建子配置实例，确保每个实例独立。
    
    属性：
        host: 服务绑定地址
        port: 服务绑定端口
        logging: 日志配置
        agent: Agent 配置
        llm: LLM 配置
        trace: Trace 配置
        permission: 权限配置
        compaction: 压缩配置
        mcp: MCP 配置
        sandbox: 沙箱配置
        rag: RAG 配置
    """
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
    rag: RagConfig = field(default_factory=RagConfig)


# 构建并返回运行时配置：默认值 → 全局 TOML → 项目本地 TOML → .env → 系统环境变量（后者优先级最高）
def get_config() -> IwanConfig:
    """
    加载并返回完整的运行时配置
    
    配置加载流程（优先级从低到高）：
    1. 创建默认配置（IwanConfig()）
    2. 加载 .env 文件（如果存在）
    3. 加载 TOML 配置文件（全局 → 项目本地）
    4. 应用环境变量覆盖
    
    返回：
        IwanConfig: 完整的配置对象
    """
    # 创建默认配置对象（所有字段使用默认值）
    config = IwanConfig()

    # ===== 加载 .env 文件 =====
    # .env 必须在读取 IWAN_CONFIG 之前加载，
    # 以便 .env 中的 IWAN_CONFIG 环境变量能影响 TOML 路径
    import iwan_claude
    pkg_dir = Path(iwan_claude.__file__).parent.parent.parent
    
    # 优先从当前工作目录加载 .env
    load_dotenv(".env", override=False)
    
    # 如果当前目录没有 .env，尝试从包目录加载
    if not os.path.exists(".env"):
        env_path = pkg_dir / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

    # ===== 确定 TOML 配置文件路径 =====
    # 如果显式指定了 IWAN_CONFIG 环境变量，只加载该文件
    # 否则按优先级加载：全局配置 → 项目本地配置（cwd → pkg_dir，避免 core 启动时 cwd 不对导致配置丢失）
    explicit = os.environ.get("IWAN_CONFIG")
    if explicit:
        config_paths = [Path(explicit).expanduser()]
    else:
        config_paths = [
            Path(_DEFAULT_CONFIG_PATH).expanduser(),            # 全局配置：~/.iwan/config.toml
            Path(".iwan/config.toml"),                          # 项目本地配置（cwd 下）
            Path(pkg_dir) / ".iwan" / "config.toml",            # 项目本地配置（package 根下，cwd 错时兜底）
        ]

    # ===== 加载 TOML 配置文件 =====
    for config_path in config_paths:
        if config_path.exists():
            try:
                # 以二进制模式打开，tomllib 需要字节输入
                with open(config_path, "rb") as f:
                    data = tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                # TOML 解析错误，退出进程并显示错误信息
                raise SystemExit(f"Config parse error ({config_path}): {e}") from e
            # 将 TOML 数据应用到配置对象
            _apply_toml(config, data)

    # ===== 应用环境变量覆盖 =====
    # 环境变量优先级最高，可以覆盖 TOML 配置
    _apply_env(config)
    
    # 返回完整配置
    return config


# 将已解析的 TOML 根表写入 config；未知小节或类型错误时退出进程
def _apply_toml(config: IwanConfig, data: dict[str, Any]) -> None:
    unknown = set(data.keys()) - {"core", "logging", "agent", "llm", "trace", "permission", "compaction", "mcp", "sandbox", "rag"}
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
        unknown_agent: set[str] = set(agent.keys()) - {
            "max_steps", "auto_mode", "effort_level", "model_preset",
            "engine", "checkpoint_backend", "checkpoint_db_path",
        }
        if unknown_agent:
            raise SystemExit(f"Unknown [agent] keys: {', '.join(sorted(unknown_agent))}")
        if "max_steps" in agent:
            val = agent["max_steps"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: agent.max_steps must be a positive integer")
            config.agent.max_steps = val
        if "auto_mode" in agent:
            val = agent["auto_mode"]
            if not isinstance(val, str) or val not in ("off", "read_only", "on"):
                raise SystemExit("Config error: agent.auto_mode must be 'off', 'read_only', or 'on'")
            config.agent.auto_mode = val
        if "effort_level" in agent:
            val = agent["effort_level"]
            if not isinstance(val, str) or val not in ("minimal", "low", "medium", "high", "max"):
                raise SystemExit("Config error: agent.effort_level must be 'minimal', 'low', 'medium', 'high', or 'max'")
            config.agent.effort_level = val
        if "model_preset" in agent:
            val = agent["model_preset"]
            if not isinstance(val, str) or val not in ("fast", "balanced", "powerful"):
                raise SystemExit("Config error: agent.model_preset must be 'fast', 'balanced', or 'powerful'")
            config.agent.model_preset = val
        if "engine" in agent:
            val = agent["engine"]
            valid_engines = ("legacy", "langgraph", "plan_execute", "debate", "pipeline")
            if not isinstance(val, str) or val not in valid_engines:
                raise SystemExit(f"Config error: agent.engine must be one of {valid_engines}")
            config.agent.engine = val
        if "checkpoint_backend" in agent:
            val = agent["checkpoint_backend"]
            if not isinstance(val, str) or val not in ("none", "memory", "sqlite"):
                raise SystemExit("Config error: agent.checkpoint_backend must be 'none', 'memory', or 'sqlite'")
            config.agent.checkpoint_backend = val
        if "checkpoint_db_path" in agent:
            val = agent["checkpoint_db_path"]
            if not isinstance(val, str):
                raise SystemExit("Config error: agent.checkpoint_db_path must be a string")
            config.agent.checkpoint_db_path = val

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
        unknown_sb: set[str] = set(sb.keys()) - {
            "enabled", "root", "allow_parent_dirs", "max_file_size", "max_total_size",
            "search_limited", "ask_on_access_denied",
            "block_network_commands", "audit_log", "audit_log_path",
        }
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
        if "block_network_commands" in sb:
            val = sb["block_network_commands"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: sandbox.block_network_commands must be a boolean")
            config.sandbox.block_network_commands = val
        if "audit_log" in sb:
            val = sb["audit_log"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: sandbox.audit_log must be a boolean")
            config.sandbox.audit_log = val
        if "audit_log_path" in sb:
            val = sb["audit_log_path"]
            if not isinstance(val, str):
                raise SystemExit("Config error: sandbox.audit_log_path must be a string")
            config.sandbox.audit_log_path = val

    if "rag" in data:
        rag = data["rag"]
        if not isinstance(rag, dict):
            raise SystemExit("Config error: [rag] must be a table")
        unknown_rag: set[str] = set(rag.keys()) - {"enabled", "embedding_model", "embedding_base_url", "embedding_api_key_env", "max_chunk_size", "chunk_overlap", "top_k", "index_path"}
        if unknown_rag:
            raise SystemExit(f"Unknown [rag] keys: {', '.join(sorted(unknown_rag))}")
        if "enabled" in rag:
            val = rag["enabled"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: rag.enabled must be a boolean")
            config.rag.enabled = val
        if "embedding_model" in rag:
            val = rag["embedding_model"]
            if not isinstance(val, str):
                raise SystemExit("Config error: rag.embedding_model must be a string")
            config.rag.embedding_model = val
        if "embedding_base_url" in rag:
            val = rag["embedding_base_url"]
            if not isinstance(val, str):
                raise SystemExit("Config error: rag.embedding_base_url must be a string")
            config.rag.embedding_base_url = val
        if "embedding_api_key_env" in rag:
            val = rag["embedding_api_key_env"]
            if not isinstance(val, str):
                raise SystemExit("Config error: rag.embedding_api_key_env must be a string")
            config.rag.embedding_api_key_env = val
        if "max_chunk_size" in rag:
            val = rag["max_chunk_size"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: rag.max_chunk_size must be a positive integer")
            config.rag.max_chunk_size = val
        if "chunk_overlap" in rag:
            val = rag["chunk_overlap"]
            if not isinstance(val, int) or val < 0:
                raise SystemExit("Config error: rag.chunk_overlap must be a non-negative integer")
            config.rag.chunk_overlap = val
        if "top_k" in rag:
            val = rag["top_k"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: rag.top_k must be a positive integer")
            config.rag.top_k = val
        if "index_path" in rag:
            val = rag["index_path"]
            if not isinstance(val, str):
                raise SystemExit("Config error: rag.index_path must be a string")
            config.rag.index_path = val


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

    rag_enabled = os.environ.get("IWAN_RAG_ENABLED")
    if rag_enabled is not None:
        config.rag.enabled = rag_enabled.lower() not in ("0", "false", "no")

    rag_embedding_model = os.environ.get("IWAN_RAG_EMBEDDING_MODEL")
    if rag_embedding_model is not None:
        config.rag.embedding_model = rag_embedding_model

    rag_max_chunk_size = os.environ.get("IWAN_RAG_MAX_CHUNK_SIZE")
    if rag_max_chunk_size is not None:
        try:
            val = int(rag_max_chunk_size)
            if val <= 0:
                raise SystemExit(
                    f"Config error: IWAN_RAG_MAX_CHUNK_SIZE must be a positive integer, got: {rag_max_chunk_size!r}"
                )
            config.rag.max_chunk_size = val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_RAG_MAX_CHUNK_SIZE must be an integer, got: {rag_max_chunk_size!r}"
            )

    rag_chunk_overlap = os.environ.get("IWAN_RAG_CHUNK_OVERLAP")
    if rag_chunk_overlap is not None:
        try:
            val = int(rag_chunk_overlap)
            if val < 0:
                raise SystemExit(
                    f"Config error: IWAN_RAG_CHUNK_OVERLAP must be a non-negative integer, got: {rag_chunk_overlap!r}"
                )
            config.rag.chunk_overlap = val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_RAG_CHUNK_OVERLAP must be an integer, got: {rag_chunk_overlap!r}"
            )

    rag_top_k = os.environ.get("IWAN_RAG_TOP_K")
    if rag_top_k is not None:
        try:
            val = int(rag_top_k)
            if val <= 0:
                raise SystemExit(
                    f"Config error: IWAN_RAG_TOP_K must be a positive integer, got: {rag_top_k!r}"
                )
            config.rag.top_k = val
        except ValueError:
            raise SystemExit(
                f"Config error: IWAN_RAG_TOP_K must be an integer, got: {rag_top_k!r}"
            )

    rag_index_path = os.environ.get("IWAN_RAG_INDEX_PATH")
    if rag_index_path is not None:
        config.rag.index_path = rag_index_path

    rag_embedding_base_url = os.environ.get("IWAN_RAG_EMBEDDING_BASE_URL")
    if rag_embedding_base_url is not None:
        config.rag.embedding_base_url = rag_embedding_base_url

    # 覆盖 Embedding API Key 读取的环境变量名（留空则启用兼容兜底）
    rag_embedding_api_key_env = os.environ.get("IWAN_RAG_EMBEDDING_API_KEY_ENV")
    if rag_embedding_api_key_env is not None:
        config.rag.embedding_api_key_env = rag_embedding_api_key_env

    agent_engine = os.environ.get("IWAN_AGENT_ENGINE")
    if agent_engine is not None:
        config.agent.engine = agent_engine

    checkpoint_backend = os.environ.get("IWAN_AGENT_CHECKPOINT_BACKEND")
    if checkpoint_backend is not None:
        config.agent.checkpoint_backend = checkpoint_backend

    checkpoint_db_path = os.environ.get("IWAN_AGENT_CHECKPOINT_DB_PATH")
    if checkpoint_db_path is not None:
        config.agent.checkpoint_db_path = checkpoint_db_path

    auto_mode = os.environ.get("IWAN_AUTO_MODE")
    if auto_mode is not None:
        if auto_mode not in ("off", "read_only", "on"):
            raise SystemExit(
                "Config error: IWAN_AUTO_MODE must be 'off', 'read_only', or 'on',"
                f" got: {auto_mode!r}"
            )
        config.agent.auto_mode = auto_mode

    # 努力等级环境变量覆盖（优先级最高）
    effort_level = os.environ.get("IWAN_EFFORT_LEVEL")
    if effort_level is not None:
        if effort_level not in ("minimal", "low", "medium", "high", "max"):
            raise SystemExit(
                "Config error: IWAN_EFFORT_LEVEL must be 'minimal', 'low', 'medium', 'high', or 'max',"
                f" got: {effort_level!r}"
            )
        config.agent.effort_level = effort_level

    # 模型预设环境变量覆盖（优先级最高）
    model_preset = os.environ.get("IWAN_MODEL_PRESET")
    if model_preset is not None:
        if model_preset not in ("fast", "balanced", "powerful"):
            raise SystemExit(
                "Config error: IWAN_MODEL_PRESET must be 'fast', 'balanced', or 'powerful',"
                f" got: {model_preset!r}"
            )
        config.agent.model_preset = model_preset
