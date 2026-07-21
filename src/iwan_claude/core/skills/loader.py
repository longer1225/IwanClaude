"""
Skill 加载器模块 - 管理 Skill 的加载、匹配和安装

【学习要点】
1. Skill 数据类：使用 dataclass 封装技能的属性
2. Markdown 解析：解析 Skill 文件的 frontmatter 和正文
3. 优先级查找：按项目本地 > 用户全局 > 内建的顺序查找 Skill
4. 自动匹配：根据用户输入自动匹配最合适的 Skill
5. 远程安装：从 URL 下载并安装 Skill

【Skill 文件格式】
Skill 文件是 Markdown 格式，包含 frontmatter 和正文：
```markdown
---
name: code_review
description: 代码审查技能，帮助审查代码质量和安全性
allowed_tools:
  - read_file
  - search_knowledge
invocation: auto
icon: 🔍
keywords:
  - 代码审查
  - code review
---

你是一位专业的代码审查专家。请审查以下代码：

$ARGUMENTS
```

【frontmatter 字段】
- name: Skill 名称（必填）
- description: 描述（用于工具选择和自动匹配）
- allowed_tools: 允许使用的工具列表
- invocation: 触发方式（manual/auto/both）
- icon: 图标标识（用于 TUI 显示）
- keywords: 用于自动匹配的关键词列表

【优先级查找】
1. 项目本地：.iwan/skills/
2. 用户全局：~/.iwan/skills/
3. 内建：core/skills/builtin/

【设计特点】
- 支持扁平格式（name.md）和目录格式（name/SKILL.md）
- 本地 Skill 覆盖同名内建 Skill
- 支持从 GitHub 仓库、ZIP 文件、SKILL.md 文件安装
"""
from __future__ import annotations

import asyncio
import re
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx


class InvocationType(Enum):
    """
    Skill 触发类型枚举

    【枚举值】
    - MANUAL: 手动触发，用户需要输入 "/" + Skill 名称
    - AUTO: 自动触发，系统根据用户输入自动匹配
    - BOTH: 两者皆可，既支持手动触发也支持自动触发

    【使用示例】
    ```python
    from iwan_claude.core.skills.loader import InvocationType
    
    # 创建自动触发的 Skill
    skill = Skill(
        name="code_review",
        description="代码审查",
        system_prompt_template="...",
        invocation=InvocationType.AUTO
    )
    ```
    """
    MANUAL = "manual"
    AUTO = "auto"
    BOTH = "both"


@dataclass
class Skill:
    """
    Skill 数据类 - 封装一个可复用的工作流模板

    【学习要点】
    1. dataclass 使用：使用 dataclass 装饰器自动生成构造函数
    2. 默认值设置：使用 field(default_factory=list) 设置列表默认值
    3. 字段说明：每个字段都有特定的用途

    【字段说明】
    - name: str - Skill 名称（必填）
    - description: str - 描述（用于工具选择和自动匹配）
    - system_prompt_template: str - 系统提示词模板（包含 $ARGUMENTS 占位符）
    - allowed_tools: list[str] - 允许使用的工具列表（限制技能可用的工具）
    - invocation: InvocationType - 触发方式（manual/auto/both），默认为 MANUAL
    - icon: str - 图标标识（用于 TUI 显示），默认为 "⚡"
    - keywords: list[str] - 用于自动匹配的关键词列表

    【系统提示词模板】
    模板中可以包含 $ARGUMENTS 占位符，在使用时会被用户传入的参数替换：
    ```
    你是一位专业的代码审查专家。请审查以下代码：

    $ARGUMENTS
    ```

    【使用示例】
    ```python
    from iwan_claude.core.skills.loader import Skill, InvocationType
    
    # 创建代码审查 Skill
    skill = Skill(
        name="code_review",
        description="代码审查技能，帮助审查代码质量和安全性",
        system_prompt_template="你是一位专业的代码审查专家。请审查以下代码：\n\n$ARGUMENTS",
        allowed_tools=["read_file", "search_knowledge"],
        invocation=InvocationType.AUTO,
        icon="🔍",
        keywords=["代码审查", "code review"]
    )
    ```
    """
    name: str
    description: str
    system_prompt_template: str
    allowed_tools: list[str] = field(default_factory=list)
    invocation: InvocationType = InvocationType.MANUAL
    icon: str = "⚡"
    keywords: list[str] = field(default_factory=list)


# 匹配 Markdown frontmatter 的正则表达式
# 格式：---\n...\n---\n
# re.DOTALL 使 . 匹配换行符
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_skill_file(path: Path) -> Skill:
    """
    解析 Markdown Skill 文件，提取 frontmatter 和正文

    【参数说明】
    - path: Path - Skill 文件路径

    【返回值】
    - Skill: 解析后的 Skill 对象

    【frontmatter 支持的字段】
    - name: Skill 名称
    - description: 描述（支持 YAML 块标量，用于工具选择和自动匹配）
    - allowed_tools: 允许的工具列表（每行一个，以 - 开头）
    - invocation: 触发方式（manual/auto/both，默认 manual）
    - icon: 图标标识（用于 TUI 显示）
    - keywords: 关键词列表（用于自动匹配，每行一个，以 - 开头）

    【正文】
    正文作为 system prompt 模板，包含 $ARGUMENTS 占位符，在使用时会被替换

    【YAML 块标量支持】
    description 字段支持 YAML 块标量格式：
    ```yaml
    description: |
      这是第一行
      这是第二行
      
    description: >
      这是一行，会被折叠为单行
    ```

    【执行流程】
    1. 读取文件内容
    2. 使用正则表达式提取 frontmatter
    3. 逐行解析 frontmatter 字段
    4. 处理列表字段（allowed_tools、keywords）
    5. 返回 Skill 对象

    【设计特点】
    - 使用正则表达式提取 frontmatter
    - 手动解析 YAML 格式（不依赖第三方库）
    - 支持 YAML 块标量格式
    - 支持列表字段的多行格式
    """
    # 1. 读取文件内容
    text = path.read_text(encoding="utf-8")
    
    # 2. 初始化默认值
    # 默认名称为文件名（不含扩展名）
    name = path.stem
    description = ""
    allowed_tools: list[str] = []
    invocation = InvocationType.MANUAL
    icon = "⚡"
    keywords: list[str] = []
    # 默认正文为整个文件内容
    body = text
    # 当前正在解析的列表字段
    current_list: list[str] | None = None

    # 3. 提取 frontmatter
    m = _FRONTMATTER_RE.match(text)
    if m:
        # 获取 frontmatter 内容（第一个捕获组）
        front = m.group(1)
        # 获取正文内容（frontmatter 之后的部分）
        body = text[m.end():]
        # 按行分割 frontmatter
        lines = front.splitlines()
        i = 0
        # 逐行解析 frontmatter
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # 如果正在解析列表字段（allowed_tools 或 keywords）
            if current_list is not None:
                if stripped.startswith("- "):
                    # 添加列表项
                    current_list.append(stripped[2:].strip())
                    i += 1
                    continue
                else:
                    # 列表结束
                    current_list = None
            
            # 解析 name 字段
            if stripped.startswith("name:"):
                # 去除字段名和引号
                name = stripped[len("name:"):].strip().strip('"').strip("'")
            # 解析 description 字段（支持 YAML 块标量）
            elif stripped.startswith("description:"):
                val = stripped[len("description:"):].strip().strip('"').strip("'")
                # 检测 YAML 块标量（| 或 >）
                if val in (">", "|"):
                    # fold=True 表示折叠换行（>），fold=False 表示保留换行（|）
                    fold = val == ">"
                    parts: list[str] = []
                    i += 1
                    # 读取缩进的内容行
                    while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                        parts.append(lines[i].strip())
                        i += 1
                    # 根据块标量类型拼接内容
                    description = (" ".join(parts) if fold else "\n".join(parts)).strip()
                    continue
                else:
                    # 普通字符串值
                    description = val
            # 解析 invocation 字段
            elif stripped.startswith("invocation:"):
                val = stripped[len("invocation:"):].strip().strip('"').strip("'")
                try:
                    # 转换为枚举值
                    invocation = InvocationType(val.lower())
                except ValueError:
                    # 无效值，使用默认值
                    invocation = InvocationType.MANUAL
            # 解析 icon 字段
            elif stripped.startswith("icon:"):
                icon = stripped[len("icon:"):].strip().strip('"').strip("'")
            # 解析 allowed_tools 字段（开始列表）
            elif stripped.startswith("allowed_tools:"):
                current_list = allowed_tools
            # 解析 keywords 字段（开始列表）
            elif stripped.startswith("keywords:"):
                current_list = keywords
            # 解析列表项（以 - 开头）
            elif stripped.startswith("- "):
                if current_list is None:
                    # 如果没有指定列表字段，默认添加到 allowed_tools
                    allowed_tools.append(stripped[2:].strip())
                else:
                    # 添加到当前列表字段
                    current_list.append(stripped[2:].strip())
            i += 1

    # 4. 返回 Skill 对象
    return Skill(
        name=name,
        description=description,
        system_prompt_template=body.strip(),
        allowed_tools=allowed_tools,
        invocation=invocation,
        icon=icon,
        keywords=keywords,
    )


class SkillLoader:
    """
    Skill 加载器 - 管理 Skill 的加载、匹配和安装

    【学习要点】
    1. 优先级查找：按项目本地 > 用户全局 > 内建的顺序查找 Skill
    2. 多格式支持：支持扁平格式（name.md）和目录格式（name/SKILL.md）
    3. 自动匹配：根据用户输入自动匹配最合适的 Skill
    4. 远程安装：从 URL 下载并安装 Skill

    【核心功能】
    - resolve(): 解析指定名称的 Skill
    - list_all(): 列出所有可用 Skill 名称
    - list_all_skills(): 列出所有可用 Skill 对象
    - render_prompt(): 渲染系统提示词模板
    - match_skill(): 根据用户输入自动匹配 Skill
    - install_from_url(): 从 URL 下载并安装 Skill

    【优先级查找】
    1. 项目本地：.iwan/skills/
    2. 用户全局：~/.iwan/skills/
    3. 内建：core/skills/builtin/

    【文件格式支持】
    - 扁平格式：name.md
    - 目录格式：name/SKILL.md

    【设计特点】
    - 本地 Skill 覆盖同名内建 Skill
    - 使用简单的 YAML 解析（不依赖第三方库）
    - 支持从 GitHub 仓库、ZIP 文件、SKILL.md 文件安装
    """
    # 内建 Skill 目录路径
    _BUILTIN_DIR = Path(__file__).parent / "builtin"

    def resolve(self, name: str) -> Skill | None:
        """
        按优先级查找并解析 Skill

        【参数说明】
        - name: str - Skill 名称

        【返回值】
        - Skill | None: 解析后的 Skill 对象，未找到返回 None

        【优先级】
        1. 项目本地：.iwan/skills/
        2. 用户全局：~/.iwan/skills/
        3. 内建：core/skills/builtin/

        【执行流程】
        1. 获取候选路径列表
        2. 遍历路径，检查文件是否存在
        3. 如果存在，解析文件并返回 Skill 对象
        4. 如果解析失败，返回 None
        5. 遍历完所有路径都未找到，返回 None

        【注意事项】
        - 优先返回高优先级目录中的 Skill
        - 解析失败时返回 None（不抛出异常）
        """
        # 遍历候选路径
        for path in self._search_paths(name):
            if path.exists():
                try:
                    # 解析 Skill 文件
                    return _parse_skill_file(path)
                except Exception:
                    # 解析失败，返回 None
                    return None
        # 未找到 Skill
        return None

    def _search_paths(self, name: str) -> list[Path]:
        """
        返回候选路径列表

        【参数说明】
        - name: str - Skill 名称

        【返回值】
        - list[Path]: 候选路径列表，按优先级排序

        【支持的格式】
        - 扁平格式：{directory}/{name}.md
        - 目录格式：{directory}/{name}/SKILL.md

        【优先级顺序】
        1. 项目本地：.iwan/skills/
        2. 用户全局：~/.iwan/skills/
        3. 内建：core/skills/builtin/

        【设计目的】
        生成所有可能的 Skill 文件路径，按优先级排序
        """
        # 搜索目录列表（按优先级排序）
        dirs = [
            Path(".iwan/skills"),              # 项目本地
            Path("~/.iwan/skills").expanduser(),  # 用户全局
            self._BUILTIN_DIR,                 # 内建
        ]
        paths: list[Path] = []
        for d in dirs:
            paths.append(d / f"{name}.md")      # 扁平格式
            paths.append(d / name / "SKILL.md")  # 目录格式
        return paths

    def list_all(self) -> list[str]:
        """
        列出所有可用 Skill 名称

        【返回值】
        - list[str]: Skill 名称列表

        【优先级规则】
        按优先级反向遍历（内建 → 全局 → 本地），本地覆盖同名 Skill
        这样返回的列表中，本地 Skill 会覆盖同名内建 Skill

        【执行流程】
        1. 按优先级反向遍历目录
        2. 收集所有 .md 文件（扁平格式）
        3. 收集所有 */SKILL.md 文件（目录格式）
        4. 使用字典去重（后面的覆盖前面的）
        5. 返回名称列表

        【设计目的】
        返回所有可用 Skill 的名称，供用户选择
        """
        seen: dict[str, None] = {}
        # 按优先级反向遍历（内建 → 全局 → 本地），本地覆盖同名
        for d in [
            self._BUILTIN_DIR,
            Path("~/.iwan/skills").expanduser(),
            Path(".iwan/skills"),
        ]:
            if d.exists():
                # 收集扁平格式的 Skill
                for f in sorted(d.glob("*.md")):
                    seen[f.stem] = None
                # 收集目录格式的 Skill
                for f in sorted(d.glob("*/SKILL.md")):
                    seen[f.parent.name] = None
        return list(seen)

    def list_all_skills(self) -> list[Skill]:
        """
        列出所有可用 Skill 对象（含描述）

        【返回值】
        - list[Skill]: Skill 对象列表

        【优先级规则】
        按优先级反向遍历（内建 → 全局 → 本地），本地覆盖同名 Skill

        【执行流程】
        1. 按优先级反向遍历目录
        2. 解析所有 .md 文件（扁平格式）
        3. 解析所有 */SKILL.md 文件（目录格式）
        4. 使用字典去重（后面的覆盖前面的）
        5. 返回 Skill 对象列表

        【设计目的】
        返回所有可用 Skill 的完整信息，供自动匹配使用
        """
        seen: dict[str, Skill] = {}
        # 按优先级反向遍历（内建 → 全局 → 本地），本地覆盖同名
        for d in [
            self._BUILTIN_DIR,
            Path("~/.iwan/skills").expanduser(),
            Path(".iwan/skills"),
        ]:
            if d.exists():
                # 收集扁平格式的 Skill
                for f in sorted(d.glob("*.md")):
                    try:
                        skill = _parse_skill_file(f)
                        seen[skill.name] = skill
                    except Exception:
                        pass
                # 收集目录格式的 Skill
                for f in sorted(d.glob("*/SKILL.md")):
                    try:
                        skill = _parse_skill_file(f)
                        seen[skill.name] = skill
                    except Exception:
                        pass
        return list(seen.values())

    def render_prompt(self, skill: Skill, arguments: str) -> str:
        """
        渲染系统提示词模板

        【参数说明】
        - skill: Skill - Skill 对象
        - arguments: str - 用户传入的参数

        【返回值】
        - str: 渲染后的系统提示词

        【模板替换】
        将模板中的 $ARGUMENTS 占位符替换为用户传入的参数

        【使用场景】
        用户输入 "/skill orchestrate 帮我做XXX"，XXX 替换 $ARGUMENTS

        【示例】
        ```python
        skill = Skill(
            name="code_review",
            system_prompt_template="你是一位专业的代码审查专家。请审查以下代码：\n\n$ARGUMENTS"
        )
        prompt = loader.render_prompt(skill, "def hello(): print('hello')")
        # 输出："你是一位专业的代码审查专家。请审查以下代码：\n\ndef hello(): print('hello')"
        ```
        """
        return skill.system_prompt_template.replace("$ARGUMENTS", arguments)

    def match_skill(self, user_input: str) -> tuple[Skill | None, float]:
        """
        根据用户输入自动匹配 Skill

        【参数说明】
        - user_input: str - 用户输入的文本

        【返回值】
        - tuple[Skill | None, float]: (匹配的 Skill 对象, 匹配度分数)

        【匹配规则】
        1. 仅匹配 invocation=auto 或 invocation=both 的 Skill
        2. 优先匹配 keywords 中的关键词（匹配度 +2.0，词匹配 +1.0）
        3. 其次匹配 description 中的关键词（每个重叠词 +0.5）
        4. 返回匹配度最高的 Skill，匹配度低于阈值（2.0）返回 None

        【评分算法】
        - keywords 完整匹配：+2.0
        - keywords 词匹配：+1.0
        - description 词重叠：+0.5/词

        【阈值】
        匹配度 >= 2.0 才返回匹配结果

        【设计目的】
        根据用户输入自动识别并触发合适的 Skill，提升用户体验
        """
        # 将用户输入转换为小写
        user_lower = user_input.lower()
        # 将用户输入分割为词集合（支持中文标点）
        user_tokens = set(user_lower.replace("，", " ").replace("。", " ").split())
        
        # 初始化最佳匹配
        best_skill: Skill | None = None
        best_score = 0.0
        
        # 遍历所有可用 Skill
        for skill in self.list_all_skills():
            # 跳过手动触发的 Skill
            if skill.invocation == InvocationType.MANUAL:
                continue
            
            # 计算匹配度分数
            score = 0.0
            
            # 匹配 keywords
            for keyword in skill.keywords:
                kw_lower = keyword.lower()
                # 完整匹配（在字符串中）
                if kw_lower in user_lower:
                    score += 2.0
                # 词匹配（在词集合中）
                if kw_lower in user_tokens:
                    score += 1.0
            
            # 匹配 description
            if skill.description:
                # 将描述分割为词集合
                desc_tokens = set(skill.description.lower().replace("，", " ").replace("。", " ").split())
                # 计算词重叠数
                overlap = user_tokens & desc_tokens
                score += len(overlap) * 0.5
            
            # 更新最佳匹配
            if score > best_score:
                best_score = score
                best_skill = skill
        
        # 判断是否超过阈值
        threshold = 2.0
        if best_score >= threshold:
            return best_skill, best_score
        return None, 0.0

    async def install_from_url(self, url: str, global_install: bool = False) -> tuple[str, bool]:
        """
        从 URL 下载并安装 Skill

        【参数说明】
        - url: str - Skill 的 URL
        - global_install: bool - 是否全局安装（默认 False，安装到项目本地）

        【返回值】
        - tuple[str, bool]: (安装结果消息, 是否成功)

        【支持的 URL 格式】
        - GitHub 仓库: https://github.com/username/repo
        - GitHub 仓库指定分支: https://github.com/username/repo/tree/branch
        - ZIP 文件: https://example.com/skill.zip
        - SKILL.md 文件: https://example.com/SKILL.md

        【安装位置】
        - 全局安装：~/.iwan/skills/
        - 项目本地：.iwan/skills/

        【执行流程】
        1. 确定安装目录并创建
        2. 标准化 URL
        3. 下载内容
        4. 根据内容类型选择安装方式
        5. 返回安装结果

        【错误处理】
        安装失败时返回错误消息和 False
        """
        # 确定安装目录
        target_dir = Path("~/.iwan/skills").expanduser() if global_install else Path(".iwan/skills")
        # 创建安装目录（如果不存在）
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 标准化 URL
            url = self._normalize_url(url)
            # 下载内容并识别类型
            content, content_type = await self._download_content(url)

            # 根据内容类型选择安装方式
            if content_type == "zip":
                return await self._install_from_zip(content, target_dir)
            elif content_type == "skill_md":
                return await self._install_from_skill_md(content, url, target_dir)
            else:
                return "Unsupported content type", False
        except Exception as exc:
            return f"Failed to install skill: {exc}", False

    def _normalize_url(self, url: str) -> str:
        """
        标准化 Skill URL

        【参数说明】
        - url: str - 原始 URL

        【返回值】
        - str: 标准化后的 URL

        【处理逻辑】
        1. 去除首尾空白和尾部斜杠
        2. 处理 GitHub URL：
           - github.com/username/repo → https://github.com/username/repo
           - git@github.com:username/repo → https://github.com/username/repo
           - https://github.com/username/repo/tree/branch → https://github.com/username/repo/archive/refs/heads/branch.zip
           - https://github.com/username/repo → https://github.com/username/repo/archive/refs/heads/main.zip
        3. 其他 URL 保持不变

        【设计目的】
        将各种格式的 GitHub URL 统一转换为 ZIP 下载 URL
        """
        # 去除首尾空白和尾部斜杠
        url = url.strip().rstrip("/")

        # 处理缺少协议的 GitHub URL
        if url.startswith("github.com"):
            url = "https://" + url
        # 处理 SSH 格式的 GitHub URL
        elif url.startswith("git@github.com:"):
            url = url.replace("git@github.com:", "https://github.com/")

        # 处理 GitHub 分支 URL（转换为 ZIP 下载 URL）
        if "github.com" in url and "/tree/" in url:
            parts = url.split("/tree/")
            branch = parts[1].split("/")[0]
            repo_path = parts[0]
            return f"{repo_path}/archive/refs/heads/{branch}.zip"
        # 处理 GitHub 仓库 URL（默认下载 main 分支）
        elif "github.com" in url and not url.endswith(".zip"):
            return f"{url}/archive/refs/heads/main.zip"

        return url

    async def _download_content(self, url: str) -> tuple[bytes, str]:
        """
        下载 URL 内容并识别类型

        【参数说明】
        - url: str - 要下载的 URL

        【返回值】
        - tuple[bytes, str]: (下载的内容, 内容类型)

        【内容类型】
        - "zip": ZIP 文件
        - "skill_md": SKILL.md 文件
        - "unknown": 未知类型

        【判断依据】
        1. URL 扩展名（.zip, .md, SKILL.md）
        2. Content-Type 响应头

        【超时设置】
        - 总超时：30 秒
        - 连接超时：10 秒

        【错误处理】
        下载失败时抛出异常
        """
        # 创建异步 HTTP 客户端
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            # 发送 GET 请求
            response = await client.get(url)
            # 检查 HTTP 状态码（非 200 会抛出异常）
            response.raise_for_status()

            # 获取 Content-Type
            content_type = response.headers.get("content-type", "")
            # 根据 URL 扩展名和 Content-Type 判断内容类型
            if url.endswith(".zip") or "zip" in content_type:
                return response.content, "zip"
            elif url.endswith(".md") or url.endswith("SKILL.md"):
                return response.content, "skill_md"
            else:
                return response.content, "unknown"

    async def _install_from_zip(self, content: bytes, target_dir: Path) -> tuple[str, bool]:
        """
        从 ZIP 文件安装 Skill

        【参数说明】
        - content: bytes - ZIP 文件内容
        - target_dir: Path - 安装目录

        【返回值】
        - tuple[str, bool]: (安装结果消息, 是否成功)

        【支持的 ZIP 结构】
        1. 包含 SKILL.md 文件的目录：name/SKILL.md
        2. 在 skills/ 目录下的 .md 文件：skills/name.md

        【执行流程】
        1. 将 ZIP 内容加载到内存缓冲区
        2. 遍历 ZIP 中的文件
        3. 如果是 SKILL.md 文件，创建目录并复制
        4. 如果是 skills/ 目录下的 .md 文件，创建目录并复制
        5. 返回安装结果

        【设计目的】
        支持从 GitHub 仓库 ZIP 下载并安装多个 Skill
        """
        # 将 ZIP 内容加载到内存缓冲区
        zip_buffer = BytesIO(content)
        installed_skills: list[str] = []

        # 打开 ZIP 文件
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            # 遍历 ZIP 中的所有文件
            for info in zf.infolist():
                # 跳过目录
                if info.is_dir():
                    continue

                # 获取文件路径
                path = Path(info.filename)
                # 情况 1：SKILL.md 文件（目录格式）
                if path.name == "SKILL.md":
                    # Skill 名称为父目录名称
                    skill_name = path.parent.name
                    # 创建目标目录
                    dest_dir = target_dir / skill_name
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    # 创建目标文件路径
                    dest_path = dest_dir / "SKILL.md"
                    # 复制文件
                    with zf.open(info) as src, open(dest_path, "wb") as dst:
                        dst.write(src.read())
                    # 记录已安装的 Skill
                    installed_skills.append(skill_name)
                # 情况 2：skills/ 目录下的 .md 文件
                elif path.suffix == ".md" and path.parent.parent.name == "skills":
                    # Skill 名称为文件名（不含扩展名）
                    skill_name = path.stem
                    # 创建目标目录
                    dest_dir = target_dir / skill_name
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    # 创建目标文件路径
                    dest_path = dest_dir / "SKILL.md"
                    # 复制文件
                    with zf.open(info) as src, open(dest_path, "wb") as dst:
                        dst.write(src.read())
                    # 记录已安装的 Skill
                    installed_skills.append(skill_name)

        # 检查是否安装了任何 Skill
        if not installed_skills:
            return "No SKILL.md files found in the zip", False

        return f"Installed skills: {', '.join(installed_skills)}", True

    async def _install_from_skill_md(self, content: bytes, url: str, target_dir: Path) -> tuple[str, bool]:
        """
        从 SKILL.md 文件安装 Skill

        【参数说明】
        - content: bytes - SKILL.md 文件内容
        - url: str - 原始 URL（用于提取 Skill 名称）
        - target_dir: Path - 安装目录

        【返回值】
        - tuple[str, bool]: (安装结果消息, 是否成功)

        【Skill 名称提取】
        1. 优先从 frontmatter 的 name 字段提取
        2. 如果没有，从 URL 路径提取（文件名，不含扩展名）

        【执行流程】
        1. 解码文件内容
        2. 提取 frontmatter 中的 name 字段
        3. 如果没有 name，从 URL 提取
        4. 创建目标目录
        5. 写入 SKILL.md 文件
        6. 返回安装结果

        【设计目的】
        支持直接从 URL 安装单个 SKILL.md 文件
        """
        try:
            # 解码文件内容
            text = content.decode("utf-8")
            # 提取 frontmatter
            m = _FRONTMATTER_RE.match(text)
            # 默认 Skill 名称为 unknown
            skill_name = "unknown"

            # 从 frontmatter 提取 name 字段
            if m:
                front = m.group(1)
                for line in front.splitlines():
                    if line.strip().startswith("name:"):
                        skill_name = line.strip()[len("name:"):].strip().strip('"').strip("'")
                        break

            # 如果仍然是 unknown，从 URL 提取
            if skill_name == "unknown":
                # 导入放在函数内部避免循环导入
                from urllib.parse import urlparse

                parsed = urlparse(url)
                # 使用路径的文件名（不含扩展名）作为 Skill 名称
                skill_name = Path(parsed.path).stem

            # 创建目标目录
            dest_dir = target_dir / skill_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            # 创建目标文件路径
            dest_path = dest_dir / "SKILL.md"
            # 写入文件
            dest_path.write_bytes(content)

            return f"Installed skill: {skill_name}", True
        except Exception as exc:
            return f"Failed to install skill: {exc}", False