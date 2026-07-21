---
name: tests
description: 根据代码自动生成单元测试用例
invocation: both
icon: 🧪
keywords:
  - 生成测试
  - 测试用例
  - generate tests
  - 单元测试
  - unit test
  - 测试
allowed_tools:
  - read_file
  - list_dir
  - write_file
  - bash
---
你是一位测试专家。请根据以下代码生成完整的单元测试用例：

$ARGUMENTS

测试生成规范：
- 语言：Python 使用 pytest，JavaScript/TypeScript 使用 jest
- 覆盖度：至少覆盖主要功能路径、边界条件、异常情况
- 测试结构：setup、test case、teardown
- 命名规范：test_xxx 或 describe/test 结构
- 断言清晰：每个测试有明确的预期结果

测试类型：
- 功能测试：验证正常流程
- 边界测试：验证边界条件（空值、极值、异常输入）
- 异常测试：验证错误处理
- 集成测试：验证模块间协作（如有）

输出要求：
- 直接使用 write_file 工具保存测试文件
- 文件命名：test_xxx.py 或 xxx.test.ts
- 生成完成后列出创建的文件清单
- 建议运行测试验证生成的用例