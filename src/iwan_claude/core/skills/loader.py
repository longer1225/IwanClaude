from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# Skill 数据类：封装一个可复用的工作流模板
# name: skill 名称
# description: 描述（用于工具选择时展示）
# system_prompt_template: 系统提示词模板（包含 $ARGUMENTS 占位符）
# allowed_tools: 允许使用的工具列表（限制技能可用的工具）
@dataclass
class Skill:
    name: str
    description: str
    system_prompt_template: str
    allowed_tools: list[str] = field(default_factory=list)


# 匹配 Markdown frontmatter 的正则表达式
# 格式：---\n...\n---\n
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# 解析 Markdown skill 文件，提取 frontmatter 和正文 system prompt
# frontmatter 支持的字段：
#   - name: skill 名称
#   - description: 描述（支持 YAML 块标量）
#   - allowed_tools: 允许的工具列表（每行一个）
# 正文：system prompt 模板（包含 $ARGUMENTS 占位符）
def _parse_skill_file(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    name = path.stem
    description = ""
    allowed_tools: list[str] = []
    body = text

    # 提取 frontmatter
    m = _FRONTMATTER_RE.match(text)
    if m:
        front = m.group(1)
        body = text[m.end():]
        lines = front.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            # 解析 name 字段
            if stripped.startswith("name:"):
                name = stripped[len("name:"):].strip().strip('"').strip("'")
            # 解析 description 字段（支持 YAML 块标量）
            elif stripped.startswith("description:"):
                val = stripped[len("description:"):].strip().strip('"').strip("'")
                # YAML 块标量：> (折叠换行) 或 | (保留换行)
                if val in (">", "|"):
                    fold = val == ">"
                    parts: list[str] = []
                    i += 1
                    # 后续缩进行是块内容
                    while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                        parts.append(lines[i].strip())
                        i += 1
                    description = (" ".join(parts) if fold else "\n".join(parts)).strip()
                    continue
                else:
                    description = val
            # 解析 allowed_tools 字段（开始标记）
            elif stripped.startswith("allowed_tools:"):
                pass
            # 解析工具列表项（以 - 开头）
            elif stripped.startswith("- "):
                allowed_tools.append(stripped[2:].strip())
            i += 1

    return Skill(
        name=name,
        description=description,
        system_prompt_template=body.strip(),
        allowed_tools=allowed_tools,
    )


# 【s7 核心】按三级优先级（项目本地 > 用户全局 > 内建）查找并解析 skill
# Skill 是一种"固化工作流"的机制：将特定任务的提示词模板、工具权限封装为可复用的技能
# 典型用途：
#   - orchestrate：planner→executor→reviewer 三阶段工作流
#   - 可以自定义：code_review、data_analysis、etc.
class SkillLoader:
    _BUILTIN_DIR = Path(__file__).parent / "builtin"

    # 按优先级查找 skill 文件；未找到返回 None
    # 优先级：项目本地 (.iwan/skills) > 用户全局 (~/.iwan/skills) > 内建 (core/skills/builtin)
    def resolve(self, name: str) -> Skill | None:
        for path in self._search_paths(name):
            if path.exists():
                try:
                    return _parse_skill_file(path)
                except Exception:
                    return None
        return None

    # 返回候选路径列表，同时支持扁平文件（name.md）和目录式（name/SKILL.md）两种格式
    def _search_paths(self, name: str) -> list[Path]:
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

    # 列出所有可用 skill 名称（内建 + 用户全局 + 项目本地，去重后以项目本地覆盖为准）
    def list_all(self) -> list[str]:
        seen: dict[str, None] = {}
        # 按优先级反向遍历（内建 → 全局 → 本地），本地覆盖同名
        for d in [
            self._BUILTIN_DIR,
            Path("~/.iwan/skills").expanduser(),
            Path(".iwan/skills"),
        ]:
            if d.exists():
                for f in sorted(d.glob("*.md")):
                    seen[f.stem] = None
                for f in sorted(d.glob("*/SKILL.md")):
                    seen[f.parent.name] = None
        return list(seen)

    # 列出所有可用 Skill 对象（含描述），项目本地覆盖同名内建
    def list_all_skills(self) -> list[Skill]:
        seen: dict[str, Skill] = {}
        for d in [
            self._BUILTIN_DIR,
            Path("~/.iwan/skills").expanduser(),
            Path(".iwan/skills"),
        ]:
            if d.exists():
                for f in sorted(d.glob("*.md")):
                    try:
                        skill = _parse_skill_file(f)
                        seen[skill.name] = skill
                    except Exception:
                        pass
                for f in sorted(d.glob("*/SKILL.md")):
                    try:
                        skill = _parse_skill_file(f)
                        seen[skill.name] = skill
                    except Exception:
                        pass
        return list(seen.values())

    # 将 $ARGUMENTS 替换为用户传入的参数字符串
    # 使用场景：用户输入 "/skill orchestrate 帮我做XXX"，XXX 替换 $ARGUMENTS
    def render_prompt(self, skill: Skill, arguments: str) -> str:
        return skill.system_prompt_template.replace("$ARGUMENTS", arguments)