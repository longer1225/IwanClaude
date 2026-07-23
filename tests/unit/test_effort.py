"""
Effort Level 模块单元测试

测试内容：
1. EffortLevel 枚举值正确性
2. EFFORT_PRESETS 预设参数正确性
3. get_effort_params() 函数行为
4. EffortParams 不可变性
5. 参数随等级递增的规律
"""
from __future__ import annotations

import pytest

from iwan_claude.core.effort import (
    EFFORT_LEVELS,
    EFFORT_PRESETS,
    EffortLevel,
    EffortParams,
    get_effort_params,
)


class TestEffortLevel:
    """测试 EffortLevel 枚举"""

    def test_effort_levels_count(self) -> None:
        # 应该有 5 个等级
        assert len(EFFORT_LEVELS) == 5

    def test_effort_levels_values(self) -> None:
        # 验证枚举值
        assert EffortLevel.MINIMAL == "minimal"
        assert EffortLevel.LOW == "low"
        assert EffortLevel.MEDIUM == "medium"
        assert EffortLevel.HIGH == "high"
        assert EffortLevel.MAX == "max"

    def test_effort_levels_order(self) -> None:
        # 验证等级顺序
        assert EFFORT_LEVELS == ("minimal", "low", "medium", "high", "max")


class TestEffortPresets:
    """测试 EFFORT_PRESETS 预设参数"""

    def test_all_levels_have_presets(self) -> None:
        # 每个等级都应该有预设参数
        for level in EFFORT_LEVELS:
            assert level in EFFORT_PRESETS, f"Missing preset for level: {level}"

    def test_minimal_params(self) -> None:
        params = EFFORT_PRESETS["minimal"]
        assert params.max_files_read == 3
        assert params.max_verify_rounds == 0
        assert params.max_steps_override == 5
        assert params.auto_run_tests is False
        assert params.read_full_file is False

    def test_low_params(self) -> None:
        params = EFFORT_PRESETS["low"]
        assert params.max_files_read == 10
        assert params.max_verify_rounds == 0
        assert params.auto_run_tests is False
        assert params.read_full_file is True

    def test_medium_params(self) -> None:
        params = EFFORT_PRESETS["medium"]
        assert params.max_files_read == 20
        assert params.max_verify_rounds == 1
        assert params.max_steps_override == 0  # 使用全局配置
        assert params.auto_run_tests is False

    def test_high_params(self) -> None:
        params = EFFORT_PRESETS["high"]
        assert params.max_files_read == 50
        assert params.max_verify_rounds == 2
        assert params.auto_run_tests is True
        assert params.verbose_summary is True

    def test_max_params(self) -> None:
        params = EFFORT_PRESETS["max"]
        assert params.max_files_read == 0  # 不限制
        assert params.max_verify_rounds == 3
        assert params.auto_run_tests is True
        assert params.verbose_summary is True

    def test_files_read_increases_with_level(self) -> None:
        # 等级越高，读取文件数越多（max 为 0 表示不限制，单独处理）
        low = EFFORT_PRESETS["low"].max_files_read
        medium = EFFORT_PRESETS["medium"].max_files_read
        high = EFFORT_PRESETS["high"].max_files_read
        assert low < medium < high

    def test_verify_rounds_increases_with_level(self) -> None:
        # 等级越高，验证轮数越多
        minimal = EFFORT_PRESETS["minimal"].max_verify_rounds
        low = EFFORT_PRESETS["low"].max_verify_rounds
        medium = EFFORT_PRESETS["medium"].max_verify_rounds
        high = EFFORT_PRESETS["high"].max_verify_rounds
        max_ = EFFORT_PRESETS["max"].max_verify_rounds
        assert minimal <= low <= medium <= high <= max_

    def test_auto_tests_turns_on_at_high(self) -> None:
        # high 和 max 自动跑测试
        assert EFFORT_PRESETS["high"].auto_run_tests is True
        assert EFFORT_PRESETS["max"].auto_run_tests is True
        # medium 及以下不自动跑测试
        assert EFFORT_PRESETS["medium"].auto_run_tests is False
        assert EFFORT_PRESETS["low"].auto_run_tests is False
        assert EFFORT_PRESETS["minimal"].auto_run_tests is False


class TestGetEffortParams:
    """测试 get_effort_params() 函数"""

    def test_valid_level_returns_params(self) -> None:
        for level in EFFORT_LEVELS:
            params = get_effort_params(level)
            assert isinstance(params, EffortParams)

    def test_invalid_level_falls_back_to_medium(self) -> None:
        params = get_effort_params("invalid")
        medium = EFFORT_PRESETS["medium"]
        assert params == medium

    def test_empty_string_falls_back_to_medium(self) -> None:
        params = get_effort_params("")
        medium = EFFORT_PRESETS["medium"]
        assert params == medium


class TestEffortParamsImmutability:
    """测试 EffortParams 不可变性"""

    def test_frozen_dataclass(self) -> None:
        params = EFFORT_PRESETS["medium"]
        with pytest.raises(Exception):
            params.max_files_read = 999  # type: ignore[misc]
