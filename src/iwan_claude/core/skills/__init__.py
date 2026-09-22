"""
Skill 模块 - 统一导出 Skill 相关的核心类

【学习要点】
1. 统一导出：将 Skill 模块的核心类统一导出，便于外部导入
2. Skill 数据类：封装可复用的工作流模板
3. SkillLoader：管理 Skill 的加载、匹配和安装

【导出内容】
- Skill: Skill 数据类，封装技能的名称、描述、系统提示词模板等
- SkillLoader: Skill 加载器，管理 Skill 的加载、匹配和安装

【使用示例】
```python
from iwan_claude.core.skills import Skill, SkillLoader

# 创建 SkillLoader
loader = SkillLoader()

# 解析 Skill
skill = loader.resolve("code_review")

# 渲染提示词
prompt = loader.render_prompt(skill, "请审查这个代码")
```
"""
from iwan_claude.core.skills.loader import Skill, SkillLoader

__all__ = ["Skill", "SkillLoader"]
