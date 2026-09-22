from __future__ import annotations

from pathlib import Path

import pytest

from iwan_claude.core.config import get_config


def _write_env(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# 功能：验证 .env 文件中的值被正确加载并覆盖内建默认值
# 设计：写 .env 到临时目录并 chdir 进去，清除同名系统环境变量排除干扰，确认 .env 加载路径有效
def test_dotenv_base_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    _write_env(env_file, "IWAN_PORT=9999\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IWAN_PORT", raising=False)

    cfg = get_config()

    assert cfg.port == 9999


# 功能：验证系统环境变量的优先级高于 .env 文件中的值
# 设计：.env 写 9999，系统环境变量写 8888，确认最终值为 8888，对应四级优先链的顶层约束
def test_system_env_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    _write_env(env_file, "IWAN_PORT=9999\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IWAN_PORT", "8888")

    cfg = get_config()

    assert cfg.port == 8888


# 功能：验证 .env 文件不存在时静默跳过，使用内建默认值（不抛异常）
# 设计：chdir 到空目录，清除系统环境变量，确认 get_config() 不因 .env 缺失而崩溃，默认端口为 7437
def test_missing_env_file_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IWAN_PORT", raising=False)

    cfg = get_config()

    assert cfg.port == 7437


# 功能：验证 .env 中设置的 IWAN_CONFIG 能正确影响 TOML 配置文件的加载路径
# 设计：.env 指向自定义 TOML 文件，TOML 中写入不同端口，确认 .env 在 TOML 加载前被读取（优先级链的正确顺序）
def test_dotenv_before_toml_iwan_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml_path = tmp_path / "custom.toml"
    toml_path.write_bytes(b'[core]\nport = 5555\n')

    env_file = tmp_path / ".env"
    _write_env(env_file, f"IWAN_CONFIG={toml_path}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IWAN_CONFIG", raising=False)
    monkeypatch.delenv("IWAN_PORT", raising=False)

    cfg = get_config()

    assert cfg.port == 5555


# 功能：验证同一变量经过完整四级优先链后，最终值为最高优先级来源（系统环境变量）
# 设计：同时设置默认值(7437)/TOML(6000)/.env(7000)/系统环境变量(8000)，确认最终值为 8000，是优先级链的综合正确性验证
def test_priority_chain_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 默认值：7437
    # TOML：6000
    # .env：7000
    # 系统环境变量：8000（最高）
    toml_path = tmp_path / "iwan.toml"
    toml_path.write_bytes(b'[core]\nport = 6000\n')

    env_file = tmp_path / ".env"
    _write_env(env_file, "IWAN_PORT=7000\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IWAN_CONFIG", str(toml_path))
    monkeypatch.setenv("IWAN_PORT", "8000")

    cfg = get_config()

    assert cfg.port == 8000


# 功能：验证 [permission] mode 五态值经 TOML 与环境变量两级都能落入 config.permission.mode
# 设计：合法值取 bypassPermissions（最危险的一档）——若枚举校验漏了任何一档的
#       包含判断，这一档就会被误判非法；env 覆盖 toml 同时验证五态键参与优先级链
def test_permission_mode_loads_from_toml_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    toml_path = tmp_path / "iwan.toml"
    toml_path.write_bytes(b'[permission]\nmode = "acceptEdits"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IWAN_PERMISSION_MODE", raising=False)
    monkeypatch.setenv("IWAN_CONFIG", str(toml_path))

    cfg = get_config()
    assert cfg.permission.mode == "acceptEdits"

    monkeypatch.setenv("IWAN_PERMISSION_MODE", "bypassPermissions")
    cfg2 = get_config()
    assert cfg2.permission.mode == "bypassPermissions"


# 功能：验证五态之外的 mode 值（TOML 与环境变量两路）都拒绝启动
# 设计：拼错的档位若静默回退 default，用户以为开了 acceptEdits 实则每步弹窗；
#       与规则/hooks 同哲学——配置笔误当场 SystemExit 好过运行期行为漂移
def test_permission_mode_rejects_illegal_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    toml_bad = tmp_path / "bad.toml"
    toml_bad.write_bytes(b'[permission]\nmode = "turbo"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IWAN_PERMISSION_MODE", raising=False)
    monkeypatch.setenv("IWAN_CONFIG", str(toml_bad))
    with pytest.raises(SystemExit):
        get_config()

    toml_ok = tmp_path / "ok.toml"
    toml_ok.write_bytes(b'[permission]\nmode = "plan"\n')
    monkeypatch.setenv("IWAN_CONFIG", str(toml_ok))
    monkeypatch.setenv("IWAN_PERMISSION_MODE", "yolo")
    with pytest.raises(SystemExit):
        get_config()


# 功能：验证 config 层 permission.mode 默认值为 "default"（不启用任何豁免档）
# 设计：默认档必须是矩阵里最保守的"除只读外全弹窗"——这个不变式一旦破，
#       所有忘记配置 mode 的部署都静默升级了权限，单点断言永久看住
def test_permission_mode_default_is_conservative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IWAN_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("IWAN_CONFIG", raising=False)
    cfg = get_config()
    assert cfg.permission.mode == "default"
