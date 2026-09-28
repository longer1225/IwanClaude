"""集成测试目录级 conftest：跨目录全局状态隔离"""
from __future__ import annotations

import pytest

from iwan_claude.core import sandbox as _sandbox_mod


# 功能：每个集成测试文件收尾时把沙箱模块的进程级全局复位为"未初始化"
# 设计：AgentRunner.__init__ 会调 init_sandbox 把全进程单例覆写成 config.sandbox
# 的根（默认 '.'=仓库目录）——test_s5_permission_flow 在进程内造 runner，若它
# 先于 tests/unit/test_builtin_tools 跑，tmp_path 会被"仓库根沙箱"判越界、
# 5 个用例假失败（HEAD 基线已确认存在的顺序污染）。复位三件套后
# get_sandbox 懒建"禁用"默认沙箱，与独立起 pytest 进程的首状态一致；
# module 级 scope 让每个集成文件出场即净，文件内部各用例互不影响、
# 也不影响 unit 里靠 init_sandbox→get_sandbox 同实例断言的用例（进程起点
# 本就未初始化，懒建语义不变）。contextvar 按 Task 拷贝天然自灭，
# set(None) 只是兜住同步上下文里可能的手滑。
@pytest.fixture(scope="module", autouse=True)
def _reset_sandbox_globals() -> None:
    yield
    _sandbox_mod._sandbox_manager = None
    _sandbox_mod._active_sandbox.set(None)
    _sandbox_mod._sandbox_by_session.clear()
