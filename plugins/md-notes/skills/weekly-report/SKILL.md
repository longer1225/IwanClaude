---
name: weekly-report
description: 从流水账/提交记录生成结构化周报：进展（带数据）/ 问题 / 下周计划
invocation: both
icon: 📈
keywords:
  - 周报
  - 写周报
  - weekly report
  - 本周总结
  - 工作汇报
allowed_tools:
  - read_file
  - list_dir
  - bash
---
根据下面的素材写一份周报（可直接贴进文档）：

$ARGUMENTS

素材不足时：先跑 `git log --since=1.week --oneline --author=$(git config user.name)` 补齐本周提交。

模板：
```
# 周报（<起>–<止>）

## 本周进展
- <事项>：<一句话结果，能量化就量化（数字/状态/上线）>

## 问题与风险
- <阻塞点或隐患> → <当前应对或需要的支持>

## 下周计划
- <可验收的目标，动词开头>
```

纪律：
1. 进展写"结果"不写"过程"——"重构了鉴权模块"要升级成"鉴权模块收敛为单入口，删掉 3 处旁路"
2. 没进展就直说没进展，用问题节解释去向；不灌水
3. 下周计划 ≤5 条，超过说明没做取舍
