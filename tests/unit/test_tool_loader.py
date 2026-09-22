"""
工具热加载模块测试

测试内容：
1. ToolLoader 核心加载逻辑（无依赖工具、有依赖工具、依赖缺失）
2. 异常隔离（单个文件加载失败不影响其他）
3. 优先级覆盖（项目级覆盖用户级同名工具）
4. ReloadToolsTool / ListCustomToolsTool 管理工具
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from iwan_claude.core.tools.base import BaseTool, ToolResult
from iwan_claude.core.tools.builtin.hot_reload import (
    ListCustomToolsTool,
    ReloadToolsTool,
)
from iwan_claude.core.tools.loader import ToolLoader
from iwan_claude.core.tools.registry import ToolRegistry


# ======================================================================
# 辅助函数：在临时目录创建工具 .py 文件
# ======================================================================


def _write_tool_file(
    dir_path: Path,
    filename: str,
    code: str,
) -> Path:
    """在指定目录创建一个工具 .py 文件"""
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / filename
    file_path.write_text(textwrap.dedent(code), encoding="utf-8")
    return file_path


# 无依赖工具的代码模板
_SIMPLE_TOOL_CODE = """
from iwan_claude.core.tools.base import BaseTool, ToolResult


class SimpleTool(BaseTool):
    name = "simple_tool"
    description = "A simple test tool"
    input_schema = {"type": "object", "properties": {}}

    async def invoke(self, params):
        return ToolResult(content="simple result")
"""

# 有依赖工具的代码模板
_DEP_TOOL_CODE = """
from iwan_claude.core.tools.base import BaseTool, ToolResult


class DepTool(BaseTool):
    name = "dep_tool"
    description = "A tool with dependencies"
    input_schema = {"type": "object", "properties": {}}
    required_deps = ["bus", "provider"]

    def __init__(self, bus, provider):
        self._bus = bus
        self._provider = provider

    async def invoke(self, params):
        return ToolResult(content=f"bus={self._bus}, provider={self._provider}")
"""

# 语法错误的工具文件
_BROKEN_TOOL_CODE = """
class BrokenTool(
    # 语法错误：缺少括号闭合
"""

# 另一个无依赖工具（用于测试多文件加载）
_ANOTHER_TOOL_CODE = """
from iwan_claude.core.tools.base import BaseTool, ToolResult


class AnotherTool(BaseTool):
    name = "another_tool"
    description = "Another test tool"
    input_schema = {"type": "object", "properties": {}}

    async def invoke(self, params):
        return ToolResult(content="another result")
"""


# ======================================================================
# ToolLoader 核心测试
# ======================================================================


class TestToolLoaderBasic:
    """测试 ToolLoader 基础加载功能"""

    def test_load_simple_tool(self, tmp_path: Path) -> None:
        """测试加载无依赖工具"""
        _write_tool_file(tmp_path, "simple.py", _SIMPLE_TOOL_CODE)

        loader = ToolLoader()
        tools = loader.load_from_directory(tmp_path, deps={})

        assert len(tools) == 1
        assert tools[0].name == "simple_tool"
        assert tools[0].description == "A simple test tool"

    def test_load_multiple_files(self, tmp_path: Path) -> None:
        """测试从多个文件加载工具"""
        _write_tool_file(tmp_path, "simple.py", _SIMPLE_TOOL_CODE)
        _write_tool_file(tmp_path, "another.py", _ANOTHER_TOOL_CODE)

        loader = ToolLoader()
        tools = loader.load_from_directory(tmp_path, deps={})

        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"simple_tool", "another_tool"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        """测试空目录返回空列表"""
        loader = ToolLoader()
        tools = loader.load_from_directory(tmp_path, deps={})
        assert tools == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """测试不存在的目录返回空列表"""
        loader = ToolLoader()
        tools = loader.load_from_directory(tmp_path / "nonexistent", deps={})
        assert tools == []

    def test_skip_underscore_files(self, tmp_path: Path) -> None:
        """测试跳过下划线开头的文件（如 __init__.py）"""
        _write_tool_file(tmp_path, "__init__.py", _SIMPLE_TOOL_CODE)
        _write_tool_file(tmp_path, "_private.py", _SIMPLE_TOOL_CODE)
        _write_tool_file(tmp_path, "real.py", _SIMPLE_TOOL_CODE)

        loader = ToolLoader()
        tools = loader.load_from_directory(tmp_path, deps={})

        # 只加载了 real.py，__init__.py 和 _private.py 被跳过
        assert len(tools) == 1


class TestToolLoaderDeps:
    """测试依赖注入功能"""

    def test_load_tool_with_deps(self, tmp_path: Path) -> None:
        """测试加载有依赖的工具"""
        _write_tool_file(tmp_path, "dep.py", _DEP_TOOL_CODE)

        loader = ToolLoader()
        # 提供所需依赖
        deps = {"bus": "event_bus_instance", "provider": "llm_provider_instance"}
        tools = loader.load_from_directory(tmp_path, deps=deps)

        assert len(tools) == 1
        assert tools[0].name == "dep_tool"

    def test_skip_tool_with_missing_dep(self, tmp_path: Path) -> None:
        """测试依赖缺失时跳过工具"""
        _write_tool_file(tmp_path, "dep.py", _DEP_TOOL_CODE)

        loader = ToolLoader()
        # 只提供 bus，不提供 provider
        deps = {"bus": "event_bus_instance"}
        tools = loader.load_from_directory(tmp_path, deps=deps)

        # 依赖缺失，工具被跳过
        assert len(tools) == 0

    def test_skip_tool_with_none_dep(self, tmp_path: Path) -> None:
        """测试依赖值为 None 时跳过工具"""
        _write_tool_file(tmp_path, "dep.py", _DEP_TOOL_CODE)

        loader = ToolLoader()
        # provider 值为 None
        deps = {"bus": "event_bus_instance", "provider": None}
        tools = loader.load_from_directory(tmp_path, deps=deps)

        assert len(tools) == 0

    def test_tool_invoke_with_injected_deps(self, tmp_path: Path) -> None:
        """测试注入的依赖能被工具正确使用"""
        _write_tool_file(tmp_path, "dep.py", _DEP_TOOL_CODE)

        loader = ToolLoader()
        deps = {"bus": "my_bus", "provider": "my_provider"}
        tools = loader.load_from_directory(tmp_path, deps=deps)

        # 调用工具验证依赖被正确注入
        result = asyncio.run(tools[0].invoke({}))
        assert "my_bus" in result.content
        assert "my_provider" in result.content


class TestToolLoaderErrorHandling:
    """测试异常隔离"""

    def test_broken_file_doesnt_affect_others(self, tmp_path: Path) -> None:
        """测试语法错误的文件不影响其他文件加载"""
        _write_tool_file(tmp_path, "broken.py", _BROKEN_TOOL_CODE)
        _write_tool_file(tmp_path, "good.py", _SIMPLE_TOOL_CODE)

        loader = ToolLoader()
        tools = loader.load_from_directory(tmp_path, deps={})

        # broken.py 失败，good.py 正常加载
        assert len(tools) == 1
        assert tools[0].name == "simple_tool"

    def test_instantiation_failure_skipped(self, tmp_path: Path) -> None:
        """测试实例化失败的工具被跳过"""
        # 构造函数抛异常的工具
        bad_code = '''
from iwan_claude.core.tools.base import BaseTool, ToolResult

class BadTool(BaseTool):
    name = "bad_tool"
    description = "tool that fails to instantiate"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self):
        raise RuntimeError("intentional failure")

    async def invoke(self, params):
        return ToolResult(content="never reached")
'''
        _write_tool_file(tmp_path, "bad.py", bad_code)
        _write_tool_file(tmp_path, "good.py", _SIMPLE_TOOL_CODE)

        loader = ToolLoader()
        tools = loader.load_from_directory(tmp_path, deps={})

        # bad_tool 实例化失败被跳过，simple_tool 正常
        assert len(tools) == 1
        assert tools[0].name == "simple_tool"


class TestToolLoaderPriority:
    """测试优先级覆盖（项目级覆盖用户级）"""

    def test_project_overrides_user(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试项目级工具覆盖用户级同名工具"""
        user_dir = tmp_path / "user_tools"
        project_dir = tmp_path / "project_tools"

        # 用户级工具
        _write_tool_file(user_dir, "tool.py", '''
from iwan_claude.core.tools.base import BaseTool, ToolResult

class SharedTool(BaseTool):
    name = "shared_tool"
    description = "user version"
    input_schema = {"type": "object", "properties": {}}

    async def invoke(self, params):
        return ToolResult(content="from user")
''')

        # 项目级同名工具
        _write_tool_file(project_dir, "tool.py", '''
from iwan_claude.core.tools.base import BaseTool, ToolResult

class SharedTool(BaseTool):
    name = "shared_tool"
    description = "project version"
    input_schema = {"type": "object", "properties": {}}

    async def invoke(self, params):
        return ToolResult(content="from project")
''')

        loader = ToolLoader()
        # monkeypatch 目录路径
        monkeypatch.setattr(loader, "USER_TOOLS_DIR", user_dir)
        monkeypatch.setattr(loader, "PROJECT_TOOLS_DIR", project_dir)

        tools = loader.load_all(deps={})

        # 只有一个工具（同名去重），且是项目级版本
        assert len(tools) == 1
        assert tools[0].description == "project version"

    def test_load_all_combines_both_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 load_all 合并两个目录的不同工具"""
        user_dir = tmp_path / "user_tools"
        project_dir = tmp_path / "project_tools"

        _write_tool_file(user_dir, "user_tool.py", '''
from iwan_claude.core.tools.base import BaseTool, ToolResult

class UserOnlyTool(BaseTool):
    name = "user_only_tool"
    description = "user only"
    input_schema = {"type": "object", "properties": {}}

    async def invoke(self, params):
        return ToolResult(content="user")
''')

        _write_tool_file(project_dir, "project_tool.py", '''
from iwan_claude.core.tools.base import BaseTool, ToolResult

class ProjectOnlyTool(BaseTool):
    name = "project_only_tool"
    description = "project only"
    input_schema = {"type": "object", "properties": {}}

    async def invoke(self, params):
        return ToolResult(content="project")
''')

        loader = ToolLoader()
        monkeypatch.setattr(loader, "USER_TOOLS_DIR", user_dir)
        monkeypatch.setattr(loader, "PROJECT_TOOLS_DIR", project_dir)

        tools = loader.load_all(deps={})

        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"user_only_tool", "project_only_tool"}


# ======================================================================
# 管理工具测试
# ======================================================================


class TestReloadToolsTool:
    """测试 ReloadToolsTool"""

    def test_reload_loads_tools(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试 reload_tools 加载工具到 registry"""
        tools_dir = tmp_path / "tools"
        _write_tool_file(tools_dir, "simple.py", _SIMPLE_TOOL_CODE)

        loader = ToolLoader()
        monkeypatch.setattr(loader, "USER_TOOLS_DIR", tools_dir)
        monkeypatch.setattr(loader, "PROJECT_TOOLS_DIR", tmp_path / "empty")

        registry = ToolRegistry()
        tool = ReloadToolsTool(loader, lambda: registry, deps={})

        result = asyncio.run(tool.invoke({}))

        assert not result.is_error
        assert "1" in result.content
        assert "simple_tool" in result.content
        # 工具已注册到 registry
        assert registry.get("simple_tool") is not None

    def test_reload_no_tools(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试没有自定义工具时的提示"""
        loader = ToolLoader()
        monkeypatch.setattr(loader, "USER_TOOLS_DIR", tmp_path / "empty1")
        monkeypatch.setattr(loader, "PROJECT_TOOLS_DIR", tmp_path / "empty2")

        registry = ToolRegistry()
        tool = ReloadToolsTool(loader, lambda: registry, deps={})

        result = asyncio.run(tool.invoke({}))

        assert not result.is_error
        assert "未发现" in result.content or "没有" in result.content


class TestListCustomToolsTool:
    """测试 ListCustomToolsTool"""

    def test_list_with_tools(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试列出自定义工具"""
        tools_dir = tmp_path / "tools"
        _write_tool_file(tools_dir, "simple.py", _SIMPLE_TOOL_CODE)

        loader = ToolLoader()
        monkeypatch.setattr(loader, "USER_TOOLS_DIR", tools_dir)
        monkeypatch.setattr(loader, "PROJECT_TOOLS_DIR", tmp_path / "empty")

        tool = ListCustomToolsTool(loader, deps={})

        result = asyncio.run(tool.invoke({}))

        assert not result.is_error
        assert "simple_tool" in result.content
        assert "1" in result.content

    def test_list_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试没有自定义工具时的提示"""
        loader = ToolLoader()
        monkeypatch.setattr(loader, "USER_TOOLS_DIR", tmp_path / "empty1")
        monkeypatch.setattr(loader, "PROJECT_TOOLS_DIR", tmp_path / "empty2")

        tool = ListCustomToolsTool(loader, deps={})

        result = asyncio.run(tool.invoke({}))

        assert not result.is_error
        assert "没有" in result.content or "未" in result.content


class TestRequiredDepsAttribute:
    """测试 required_deps 类属性"""

    def test_base_tool_has_empty_required_deps(self) -> None:
        """测试 BaseTool 默认 required_deps 为空列表"""
        assert BaseTool.required_deps == []

    def test_custom_tool_inherits_default(self, tmp_path: Path) -> None:
        """测试自定义工具不声明 required_deps 时默认为空"""
        _write_tool_file(tmp_path, "simple.py", _SIMPLE_TOOL_CODE)
        loader = ToolLoader()
        tools = loader.load_from_directory(tmp_path, deps={})

        # 无 required_deps 声明的工具，默认空列表，无需依赖即可实例化
        assert len(tools) == 1
        assert tools[0].required_deps == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
