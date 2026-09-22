"""
Agent 角色配置加载器

该模块实现了 Agent 角色配置的加载和解析功能。

核心功能：
- 定义 AgentProfile 数据类，存储角色配置信息
- 实现 AgentProfileLoader，支持多级优先级查找角色配置

设计要点：
- 支持三级优先级：项目本地 > 用户全局 > 内建
- 配置文件使用 TOML 格式
- 角色配置包含名称、描述、系统提示词、允许的工具和模型
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentProfile:
    """
    Agent 角色配置数据类

    存储从 TOML 配置文件中解析出的角色配置信息。

    属性：
        name: 角色名称，如 "planner"、"executor"、"reviewer"
        description: 角色描述，用于展示和选择
        system_prompt: 系统提示词，定义角色的行为和能力
        allowed_tools: 允许使用的工具列表，为空表示允许所有工具
        model: 指定使用的模型名称，为空表示使用默认模型

    使用示例：
        >>> profile = AgentProfile(
        ...     name="planner",
        ...     description="规划师角色",
        ...     system_prompt="你是一个专业的项目规划师...",
        ...     allowed_tools=["read_file", "list_dir"],
        ...     model="claude-3-sonnet"
        ... )
    """
    name: str
    description: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""


class AgentProfileLoader:
    """
    Agent 角色配置加载器

    支持三级优先级查找角色配置文件：
    1. 项目本地：.iwan/agents/{name}.toml
    2. 用户全局：~/.iwan/agents/{name}.toml
    3. 内建：core/agents/builtin/{name}.toml

    工作原理：
    1. load() 方法按优先级顺序查找配置文件
    2. 找到第一个存在的文件后调用 _parse() 解析
    3. _parse() 读取 TOML 文件并转换为 AgentProfile 对象

    使用示例：
        >>> loader = AgentProfileLoader()
        >>> profile = loader.load("planner")
        >>> if profile:
        ...     print(profile.system_prompt)
    """

    # 内建角色配置目录，位于模块同级目录下的 builtin 文件夹
    _BUILTIN_DIR = Path(__file__).parent / "builtin"

    def load(self, name: str) -> AgentProfile | None:
        """
        加载指定名称的角色配置

        按优先级顺序查找配置文件，返回第一个存在的配置。

        参数：
            name: 角色名称，如 "planner"、"executor"、"reviewer"

        返回：
            AgentProfile | None: 解析后的角色配置，如果未找到或解析失败则返回 None

        实现步骤：
        1. 获取搜索路径列表（项目本地 > 用户全局 > 内建）
        2. 遍历路径，检查文件是否存在
        3. 如果存在，尝试解析并返回
        4. 如果解析失败，返回 None
        5. 如果所有路径都不存在，返回 None

        使用示例：
            >>> loader = AgentProfileLoader()
            >>> profile = loader.load("planner")
        """
        for path in self._search_paths(name):
            if path.exists():
                try:
                    return self._parse(path, name)
                except Exception:
                    return None
        return None

    def _search_paths(self, name: str) -> list[Path]:
        """
        获取搜索路径列表

        返回按优先级排序的配置文件路径列表。

        参数：
            name: 角色名称

        返回：
            list[Path]: 配置文件路径列表，按优先级从高到低排序

        优先级说明：
        - 项目本地：.iwan/agents/{name}.toml（最高优先级）
        - 用户全局：~/.iwan/agents/{name}.toml（次高优先级）
        - 内建：core/agents/builtin/{name}.toml（最低优先级）

        使用示例：
            >>> paths = loader._search_paths("planner")
            >>> print(paths)
            [Path(".iwan/agents/planner.toml"), Path("/home/user/.iwan/agents/planner.toml"), ...]
        """
        builtin = self._BUILTIN_DIR / f"{name}.toml"
        global_ = Path("~/.iwan/agents").expanduser() / f"{name}.toml"
        local = Path(".iwan/agents") / f"{name}.toml"
        return [local, global_, builtin]

    def _parse(self, path: Path, name: str) -> AgentProfile:
        """
        解析 TOML 角色配置文件

        将 TOML 格式的配置文件解析为 AgentProfile 对象。

        参数：
            path: 配置文件路径
            name: 角色名称

        返回：
            AgentProfile: 解析后的角色配置对象

        TOML 格式示例：
            [agent]
            description = "规划师角色"
            system_prompt = "你是一个专业的项目规划师..."
            allowed_tools = ["read_file", "list_dir"]
            model = "claude-3-sonnet"

        实现步骤：
        1. 以二进制模式打开文件
        2. 使用 tomllib.load() 解析 TOML 内容
        3. 从 "agent" 节中提取配置项
        4. 创建并返回 AgentProfile 对象
        """
        with open(path, "rb") as f:
            data = tomllib.load(f)
        agent = data.get("agent", {})
        return AgentProfile(
            name=name,
            description=agent.get("description", ""),
            system_prompt=agent.get("system_prompt", "").strip(),
            allowed_tools=agent.get("allowed_tools", []),
            model=agent.get("model", ""),
        )
