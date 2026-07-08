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
_DEFAULT_LOG_FILE = "~/.kama/logs/core.log"
_DEFAULT_LOG_FORMAT = "text"
_DEFAULT_CONFIG_PATH = "~/.kama/config.toml"


@dataclass
class LoggingConfig:
    level: str = _DEFAULT_LOG_LEVEL
    file: str = _DEFAULT_LOG_FILE
    format: str = _DEFAULT_LOG_FORMAT  # "text" | "json"


@dataclass
class KamaConfig:
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# 构建并返回运行时配置：默认值 → TOML → .env → 系统环境变量（后者优先级最高）
# 函数作用：加载并合并多层配置来源，返回一个完整的 KamaConfig 对象
# 返回值：KamaConfig 配置对象，包含 host、port、logging 等所有配置项
def get_config() -> KamaConfig:
    # 创建 KamaConfig 实例，使用所有字段的默认值初始化
    config = KamaConfig()

    # 加载当前目录下的 .env 文件，override=False 表示不覆盖已存在的环境变量
    # 注意：必须在读取 KAMA_CONFIG 环境变量之前加载，以便 .env 中的 KAMA_CONFIG 能影响 TOML 路径
    load_dotenv(".env", override=False)

    # 获取配置文件路径：优先使用 KAMA_CONFIG 环境变量，否则使用默认路径 ~/.kama/config.toml
    # expanduser() 将路径中的 ~ 转换为实际的用户主目录路径
    config_path = Path(os.environ.get("KAMA_CONFIG", _DEFAULT_CONFIG_PATH)).expanduser()

    # 判断配置文件是否存在
    if config_path.exists():
        # 使用 try-except 块捕获 TOML 解析错误
        try:
            # 以二进制只读模式打开配置文件
            with open(config_path, "rb") as f:
                # 使用 tomllib 解析 TOML 文件内容，返回字典数据
                data = tomllib.load(f)
        # 捕获 TOML 语法错误
        except tomllib.TOMLDecodeError as e:
            # 抛出 SystemExit 异常终止程序，提示配置文件解析错误及具体原因
            raise SystemExit(f"Config parse error ({config_path}): {e}") from e
        # 调用 _apply_toml 函数，将 TOML 解析结果应用到 config 对象
        _apply_toml(config, data)

    # 调用 _apply_env 函数，用环境变量覆盖 config 中对应的配置项
    _apply_env(config)
    # 返回最终的配置对象
    return config


# 将已解析的 TOML 根表写入 config；未知小节或类型错误时退出进程
def _apply_toml(config: KamaConfig, data: dict[str, Any]) -> None:
    unknown = set(data.keys()) - {"core", "logging"}
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


# 用 KAMA_* 环境变量覆盖 config 中对应字段（若变量已设置）
def _apply_env(config: KamaConfig) -> None:
    host = os.environ.get("KAMA_HOST")
    if host is not None:
        config.host = host

    port_str = os.environ.get("KAMA_PORT")
    if port_str is not None:
        try:
            config.port = int(port_str)
        except ValueError:
            raise SystemExit(f"Config error: KAMA_PORT must be an integer, got: {port_str!r}")

    log_level = os.environ.get("KAMA_LOG_LEVEL")
    if log_level is not None:
        config.logging.level = log_level

    log_file = os.environ.get("KAMA_LOG_FILE")
    if log_file is not None:
        config.logging.file = log_file

    log_format = os.environ.get("KAMA_LOG_FORMAT")
    if log_format is not None:
        config.logging.format = log_format
