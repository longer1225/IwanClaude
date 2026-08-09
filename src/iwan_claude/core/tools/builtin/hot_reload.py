"""
工具热加载管理工具 - ReloadToolsTool / ListCustomToolsTool

【学习要点】
1. 工具管理工具：这两个工具本身是"管理其它工具的工具"
2. 延迟获取：用 lambda 延迟获取 registry（因为注册时 registry 还在构建中）
3. 依赖传递：deps dict 透传给 ToolLoader，保证 reload 时依赖一致

【使用场景】
- Agent 在对话中加了一个新工具文件到 .iwan/tools/，调用 reload_tools 立即生效
- 用 list_custom_tools 查看当前有哪些自定义工具
"""
from __future__ import annotations

from typing import Any, Callable

from iwan_claude.core.tools.base import BaseTool, ToolResult


class ReloadToolsTool(BaseTool):
    """
    重新扫描工具目录，加载/刷新自定义工具

    【工作流程】
    1. 调用 ToolLoader.load_all(deps) 扫描 .iwan/tools/ 和 ~/.iwan/tools/
    2. 将加载到的工具注册到 registry（同名覆盖）
    3. 返回加载结果摘要
    """

    name = "reload_tools"
    description = (
        "重新扫描 .iwan/tools/ 和 ~/.iwan/tools/ 目录，加载新增或修改的自定义工具。"
        "当你新增了工具文件后调用此工具使其立即生效，无需重启。"
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
    }

    def __init__(
        self,
        loader: Any,
        registry_getter: Callable[[], Any],
        deps: dict[str, Any],
    ) -> None:
        """
        参数：
            loader: ToolLoader 实例
            registry_getter: 返回 ToolRegistry 的 lambda（延迟获取，
                            因为注册此工具时 registry 还在构建中）
            deps: 运行时依赖字典，透传给 ToolLoader 做依赖注入
        """
        self._loader = loader
        self._registry_getter = registry_getter
        self._deps = deps

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """执行工具加载"""
        try:
            tools = self._loader.load_all(self._deps)
            registry = self._registry_getter()
            loaded_names: list[str] = []
            for tool in tools:
                registry.register(tool)
                loaded_names.append(tool.name)

            if not loaded_names:
                return ToolResult(
                    content="扫描完成，未发现自定义工具。"
                    "请将 .py 文件放到 .iwan/tools/ 或 ~/.iwan/tools/ 目录。"
                )

            return ToolResult(
                content=f"成功加载 {len(loaded_names)} 个自定义工具："
                f"{', '.join(loaded_names)}"
            )
        except Exception as exc:
            return ToolResult(
                content=f"重新加载工具失败：{exc}",
                is_error=True,
                error_type="runtime_error",
            )


class ListCustomToolsTool(BaseTool):
    """
    列出所有通过热加载机制加载的自定义工具
    """

    name = "list_custom_tools"
    description = (
        "列出所有通过热加载机制从 .iwan/tools/ 和 ~/.iwan/tools/ 加载的自定义工具。"
        "用于查看当前有哪些自定义工具可用。"
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, loader: Any, deps: dict[str, Any]) -> None:
        """
        参数：
            loader: ToolLoader 实例
            deps: 运行时依赖字典，透传给 ToolLoader 做依赖注入
        """
        self._loader = loader
        self._deps = deps

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """列出自定义工具"""
        try:
            tools = self._loader.load_all(self._deps)
            if not tools:
                return ToolResult(
                    content="当前没有自定义工具。"
                    "将 .py 文件放到 .iwan/tools/ 或 ~/.iwan/tools/ 目录，"
                    "然后调用 reload_tools 加载。"
                )

            lines = [f"- {t.name}: {t.description}" for t in tools]
            return ToolResult(
                content=f"共 {len(tools)} 个自定义工具：\n" + "\n".join(lines)
            )
        except Exception as exc:
            return ToolResult(
                content=f"列出自定义工具失败：{exc}",
                is_error=True,
                error_type="runtime_error",
            )
