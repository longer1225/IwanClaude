"""SSH 子系统（M4）— 连接库 / 密钥助手 / 终端会话 / agent 工具

【学习要点】分包而不塞平铺文件：ssh 有四块彼此独立又互相引用的职责
（connections 存储、keys 文件操作、session 进程池、后续 exec 工具参数拼装
复用 session 的 argv 逻辑），单文件会很快破 500 行。工具（tools/builtin/
ssh_exec.py）不放在这里——工具注册表在 tools 侧，这里只留被工具调用的
纯逻辑。
"""
