# 功能：验证 core.token 的四级配置链入口（环境变量 / TOML）都被正确解析
# 设计：token 是鉴权唯一开关，空=不鉴权的默认值一旦漂移会让所有裸客户端连不上；
#       用 delenv/IWAN_CONFIG 隔离用户真实配置，分别只测 env 与 TOML 一条路径
from __future__ import annotations

from pathlib import Path

import pytest

from iwan_claude.core.config import get_config


# 功能：IWAN_TOKEN 环境变量覆盖默认空 token
# 设计：与 IWAN_PORT 同套 env 覆盖路径，setenv/delenv 成对排除宿主环境残留
def test_token_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IWAN_TOKEN", "sekrit")

    cfg = get_config()

    assert cfg.token == "sekrit"
    monkeypatch.delenv("IWAN_TOKEN")
    assert get_config().token == ""


# 功能：config.toml [core] token 键被解析进配置对象
# 设计：走 IWAN_CONFIG 显式文件路径（绕开全局 ~/.iwan），同时验证未知 core 键
#       白名单已放行 "token"——若漏加会触发 unknown 硬退出而 fail 在本断言前
def test_token_from_core_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = tmp_path / "cfg.toml"
    toml.write_text('[core]\ntoken = "from-toml"\n', encoding="utf-8")
    monkeypatch.setenv("IWAN_CONFIG", str(toml))
    monkeypatch.delenv("IWAN_TOKEN", raising=False)

    cfg = get_config()

    assert cfg.token == "from-toml"
