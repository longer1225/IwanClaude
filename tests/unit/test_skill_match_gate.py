"""自动技能匹配的"命令性门控"回归测试（针对通用关键词劫持）"""
from __future__ import annotations

from iwan_claude.core.skills.loader import SkillLoader


# 功能：验证通用短词（总结/文档/测试）埋在长任务句里不触发技能，请求交回 agent
# 设计：用真实内置技能（summarize 含关键词"总结"），构造"读项目结构+总结"这种明确要
#       agent 干活的长句，断言 match_skill 返回 None——这是"总结架构被 summarize 劫持
#       且回显空白"事故的源头回归钉；误差方向刻意偏向抑制（漏报只少个建议，误报才是打扰）
def test_generic_keyword_in_long_task_sentence_does_not_match() -> None:
    loader = SkillLoader()
    skill, _score = loader.match_skill("读一下这个项目的文件结构 总结一下里面的内容")
    assert skill is None
    skill2, _ = loader.match_skill("帮我实现登录功能然后做好测试再修复失败用例")
    assert skill2 is None


# 功能：验证短命令仍建议技能、多字专有关键词即便在长句里也放行、无关句不匹配
# 设计：三档输入卡住门控两端——裸命令(总结一下)命中证明没误伤正常路径；含专有词
#       "代码审查"(≥4字)的长句命中，证明抑制只针对通用短词而非一刀切；闲聊句不匹配
#       证明不会到处乱弹技能卡
def test_short_and_distinctive_inputs_still_match() -> None:
    loader = SkillLoader()
    assert loader.match_skill("总结一下")[0] is not None
    assert loader.match_skill("帮我写代码审查 src/app.py 这个文件")[0] is not None
    assert loader.match_skill("随便聊聊今天的天气到底怎么样")[0] is None
