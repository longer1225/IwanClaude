"""
权限存储模块 - 管理权限策略文件的加载和保存

【学习要点】
1. 策略文件格式：TOML 格式，只使用 [always] 节
2. 持久化机制：将用户的 always_allow/always_deny 决策保存到文件
3. 文件路径：默认 ~/.iwan/policy.toml
4. 手动编辑：支持手动编辑策略文件，格式正确即可生效

【核心功能】
- load_policy_file(): 加载策略文件
- save_policy_file(): 保存策略文件

【策略文件格式】
```toml
# ~/.iwan/policy.toml
# 由 iwan-core 自动管理，手动编辑生效但格式须正确

[always]
bash = "allow"
write_file = "deny"
```

【设计目的】
提供跨 session 的权限决策持久化，
用户的 always_allow/always_deny 决策在重启后仍然有效。
"""
from __future__ import annotations

from pathlib import Path

# 默认策略文件路径（用户 home 目录下的 .iwan/policy.toml）
_DEFAULT_POLICY_PATH = Path("~/.iwan/policy.toml")


def load_policy_file(path: Path | None = None) -> dict[str, str]:
    """
    加载 policy.toml 中 [always] 节，返回 {tool_name: "allow"/"deny"}

    【参数说明】
    - path: Path | None - 策略文件路径（默认 ~/.iwan/policy.toml）

    【返回值】
    - dict[str, str]: 工具名称到权限决策的映射（"allow" 或 "deny"）

    【执行流程】
    1. 解析文件路径（扩展 ~）
    2. 如果文件不存在，返回空字典
    3. 逐行读取文件
    4. 找到 [always] 节
    5. 解析节内的键值对（tool_name = "allow"/"deny"）
    6. 返回解析结果

    【文件格式】
    ```toml
    [always]
    bash = "allow"
    write_file = "deny"
    ```

    【注意事项】
    - 文件不存在时返回空字典
    - 注释行（以 # 开头）被忽略
    - 只读取 [always] 节的内容
    - 值必须为 "allow" 或 "deny"

    【示例】
    ```python
    policies = load_policy_file()
    # 返回: {"bash": "allow", "write_file": "deny"}
    ```
    """
    # 解析文件路径（扩展 ~）
    p = (path or _DEFAULT_POLICY_PATH).expanduser()
    # 如果文件不存在，返回空字典
    if not p.exists():
        return {}
    # 存储解析结果
    result: dict[str, str] = {}
    # 是否在 [always] 节内
    in_always = False
    # 逐行读取文件
    for line in p.read_text(encoding="utf-8").splitlines():
        # 去除首尾空白
        stripped = line.strip()
        # 找到 [always] 节开始
        if stripped == "[always]":
            in_always = True
            continue
        # 遇到其他节，退出 [always] 节
        if stripped.startswith("["):
            in_always = False
            continue
        # 在 [always] 节内，解析键值对
        if in_always and "=" in stripped and not stripped.startswith("#"):
            # 分割键值对
            k, _, v = stripped.partition("=")
            # 去除键的空白
            k = k.strip()
            # 去除值的空白和引号
            v = v.strip().strip('"')
            # 只接受 "allow" 或 "deny"
            if v in ("allow", "deny"):
                result[k] = v
    return result


def save_policy_file(always: dict[str, str], path: Path | None = None) -> None:
    """
    将 {tool_name: "allow"/"deny"} 写入 policy.toml，覆盖 [always] 节

    【参数说明】
    - always: dict[str, str] - 工具名称到权限决策的映射
    - path: Path | None - 策略文件路径（默认 ~/.iwan/policy.toml）

    【执行流程】
    1. 解析文件路径（扩展 ~）
    2. 创建父目录（如果不存在）
    3. 构建文件内容（头部注释 + [always] 节 + 键值对）
    4. 写入文件

    【文件格式】
    ```toml
    # ~/.iwan/policy.toml
    # 由 iwan-core 自动管理，手动编辑生效但格式须正确

    [always]
    bash = "allow"
    write_file = "deny"
    ```

    【注意事项】
    - 父目录不存在时自动创建
    - 使用 UTF-8 编码
    - 键值对按字母顺序排序
    - 覆盖现有 [always] 节的内容

    【示例】
    ```python
    save_policy_file({"bash": "allow", "write_file": "deny"})
    # 写入 ~/.iwan/policy.toml
    ```
    """
    # 解析文件路径（扩展 ~）
    p = (path or _DEFAULT_POLICY_PATH).expanduser()
    # 创建父目录（如果不存在）
    p.parent.mkdir(parents=True, exist_ok=True)
    # 构建文件内容
    lines = [
        "# ~/.iwan/policy.toml",
        "# 由 iwan-core 自动管理，手动编辑生效但格式须正确",
        "",
        "[always]",
    ]
    # 添加键值对（按字母顺序排序）
    for tool, decision in sorted(always.items()):
        lines.append(f'{tool} = "{decision}"')
    # 写入文件
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
