"""
工具热加载模块 - 从约定目录动态发现并加载 BaseTool 子类

【学习要点】
1. importlib 动态加载：用 spec_from_file_location 从文件路径加载 Python 模块
2. 插件架构：约定优于配置，扫描约定目录自动发现插件
3. 依赖注入：工具类声明 required_deps，loader 从 deps dict 注入
4. 异常隔离：单个插件加载失败不影响其他插件

【三级目录优先级】（与 Skills 系统一致）
1. 项目级：./.iwan/tools/  （跟项目走，优先级最高）
2. 用户级：~/.iwan/tools/   （跨项目共享）
同名工具：项目级覆盖用户级。

【使用方式】
将 .py 文件放到 .iwan/tools/ 目录，文件内定义 BaseTool 子类：

    from iwan_claude.core.tools.base import BaseTool, ToolResult

    class MyTool(BaseTool):
        name = "my_tool"
        description = "我的自定义工具"
        input_schema = {"type": "object", "properties": {}}

        async def invoke(self, params):
            return ToolResult(content="done")

启动 core 时自动加载，无需改 runner.py、无需重启。
运行时可通过 reload_tools 工具重新扫描加载新工具。

【依赖注入】
需要运行时依赖的工具，声明 required_deps 类属性：

    class MyRagTool(BaseTool):
        required_deps = ["index_manager"]
        def __init__(self, index_manager):
            self._index = index_manager

Loader 会从 deps dict 中取出 index_manager 传给构造函数。
若依赖缺失（不在 deps 中或值为 None），跳过该工具并记录警告。

【面试亮点】
"设计了工具热加载系统，基于 importlib 动态发现 + required_deps 依赖注入，
用户放 .py 文件到约定目录即可扩展 Agent 能力，支持项目级/用户级覆盖。"
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from iwan_claude.core.tools.base import BaseTool

log = logging.getLogger(__name__)


class ToolLoader:
    """
    工具热加载器 - 从约定目录动态发现并加载 BaseTool 子类

    【目录约定】
    - 项目级：./.iwan/tools/（优先级高，覆盖用户级同名工具）
    - 用户级：~/.iwan/tools/

    【加载流程】
    1. 扫描目录下所有 .py 文件（跳过 __init__.py 和下划线开头文件）
    2. 用 importlib 从文件路径动态加载模块
    3. 遍历模块成员，找 BaseTool 的非抽象子类（排除从其它模块 import 来的）
    4. 根据 required_deps 注入依赖，实例化工具类
    5. 单个文件/工具加载失败不影响其他

    【线程安全】
    本类无状态，可安全并发调用（每次 load_all 返回新列表）。
    """

    # 项目级工具目录（跟项目走，优先级高）
    PROJECT_TOOLS_DIR = Path(".iwan/tools")
    # 用户级工具目录（跨项目共享）
    USER_TOOLS_DIR = Path.home() / ".iwan" / "tools"

    def load_all(self, deps: dict[str, Any] | None = None) -> list[BaseTool]:
        """
        扫描项目级 + 用户级目录，加载所有自定义工具

        【加载顺序】
        1. 先加载用户级目录（低优先级）
        2. 再加载项目级目录（高优先级，覆盖同名工具）

        参数：
            deps: 运行时依赖字典（如 {"bus": bus, "provider": provider}）

        返回：
            list[BaseTool]: 加载成功的工具列表（按 name 去重，项目级覆盖用户级）
        """
        deps = deps or {}
        # 用 dict 按工具名去重，后加载的覆盖先加载的
        tools_by_name: dict[str, BaseTool] = {}

        # 先加载用户级（低优先级）
        for tool in self.load_from_directory(self.USER_TOOLS_DIR, deps):
            tools_by_name[tool.name] = tool

        # 再加载项目级（高优先级，覆盖同名）
        for tool in self.load_from_directory(self.PROJECT_TOOLS_DIR, deps):
            tools_by_name[tool.name] = tool

        return list(tools_by_name.values())

    def load_from_directory(
        self, dir_path: Path, deps: dict[str, Any]
    ) -> list[BaseTool]:
        """
        扫描单个目录下所有 .py 文件，加载其中的 BaseTool 子类

        参数：
            dir_path: 目录路径
            deps: 运行时依赖字典

        返回：
            list[BaseTool]: 该目录下加载成功的工具列表
        """
        if not dir_path.exists() or not dir_path.is_dir():
            return []

        tools: list[BaseTool] = []
        # 遍历目录下所有 .py 文件（按文件名排序，保证加载顺序稳定）
        for py_file in sorted(dir_path.glob("*.py")):
            # 跳过 __init__.py 和下划线开头的文件
            if py_file.name.startswith("_"):
                continue
            try:
                file_tools = self._load_from_file(py_file, deps)
                tools.extend(file_tools)
                if file_tools:
                    log.info(
                        "Loaded %d custom tool(s) from %s",
                        len(file_tools),
                        py_file,
                    )
            except Exception as exc:
                # 单个文件加载失败不影响其他文件
                log.error("Failed to load tools from %s: %s", py_file, exc)

        return tools

    def _load_from_file(
        self, py_file: Path, deps: dict[str, Any]
    ) -> list[BaseTool]:
        """
        从单个 .py 文件加载工具

        【加载流程】
        1. 用 importlib 从文件路径创建模块
        2. 执行模块代码
        3. 遍历模块成员，找 BaseTool 的非抽象子类
        4. 实例化每个工具类（带依赖注入）

        参数：
            py_file: .py 文件路径
            deps: 运行时依赖字典

        返回：
            list[BaseTool]: 该文件中加载成功的工具列表
        """
        # 生成唯一的模块名（避免重名冲突）
        module_name = f"_iwan_custom_tool_{py_file.stem}"

        # 用 importlib 从文件路径加载模块
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            log.warning("Cannot create module spec for %s", py_file)
            return []

        module = importlib.util.module_from_spec(spec)

        # 将模块加入 sys.modules（某些库在导入时需要模块在 sys.modules 中）
        sys.modules[module_name] = module

        try:
            # 执行模块代码（会触发 import 和顶层代码执行）
            spec.loader.exec_module(module)
        except Exception:
            # 执行失败，从 sys.modules 移除并重新抛出（由上层 load_from_directory 捕获）
            sys.modules.pop(module_name, None)
            raise

        # 遍历模块成员，找 BaseTool 的非抽象子类
        tools: list[BaseTool] = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            # 检查是否是类
            if not isinstance(attr, type):
                continue
            # 检查是否是 BaseTool 的子类（排除 BaseTool 本身）
            if not issubclass(attr, BaseTool) or attr is BaseTool:
                continue
            # 跳过抽象类（有未实现的抽象方法）
            if inspect.isabstract(attr):
                continue
            # 跳过定义在其他模块的类（避免重复加载从 builtin import 来的工具）
            if attr.__module__ != module_name:
                continue

            # 实例化工具类（带依赖注入）
            tool = self._instantiate(attr, deps)
            if tool is not None:
                tools.append(tool)

        return tools

    def _instantiate(
        self, tool_cls: type[BaseTool], deps: dict[str, Any]
    ) -> BaseTool | None:
        """
        实例化工具类，处理依赖注入

        【依赖注入逻辑】
        1. 读取工具类的 required_deps 类属性
        2. 从 deps dict 中取出对应依赖
        3. 若某个依赖缺失（不在 deps 中或值为 None），跳过并记录警告
        4. 用取出的依赖作为构造参数实例化工具

        参数：
            tool_cls: 工具类（BaseTool 的子类）
            deps: 运行时依赖字典

        返回：
            BaseTool | None: 实例化成功返回工具实例，失败返回 None
        """
        required = getattr(tool_cls, "required_deps", [])

        # 收集构造参数
        kwargs: dict[str, Any] = {}
        for dep_name in required:
            if dep_name not in deps or deps[dep_name] is None:
                log.warning(
                    "Skipping custom tool %s: missing required dependency %r",
                    tool_cls.__name__,
                    dep_name,
                )
                return None
            kwargs[dep_name] = deps[dep_name]

        try:
            return tool_cls(**kwargs)
        except Exception as exc:
            log.error(
                "Failed to instantiate custom tool %s: %s",
                tool_cls.__name__,
                exc,
            )
            return None
