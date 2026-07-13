# 导入 Python 3.7+ 的类型注解特性
from __future__ import annotations

# 导入 pathlib 模块，用于处理文件路径（面向对象的文件系统操作）
from pathlib import Path

# 默认的策略文件路径：用户家目录下的 .kama/policy.toml
# ~ 表示用户家目录，在 Windows 上是 C:\Users\用户名，在 Linux 上是 /home/用户名
_DEFAULT_POLICY_PATH = Path("~/.kama/policy.toml")


# 加载 policy.toml 文件中的 [always] 节
# 参数 path: 可选的自定义文件路径，默认为 ~/.kama/policy.toml
# 返回值: 字典，key 是工具名，value 是 "allow" 或 "deny"
# 如果文件不存在，返回空字典（表示没有持久化的权限策略）
def load_policy_file(path: Path | None = None) -> dict[str, str]:
    # 使用提供的路径或默认路径，展开 ~ 为实际的家目录路径
    p = (path or _DEFAULT_POLICY_PATH).expanduser()
    
    # 如果文件不存在，直接返回空字典（表示没有持久化策略）
    if not p.exists():
        return {}
    
    # 初始化结果字典
    result: dict[str, str] = {}
    
    # 标记是否在 [always] 节内（TOML 文件的节用 [section_name] 表示）
    in_always = False
    
    # 逐行读取文件内容（按 UTF-8 编码）
    for line in p.read_text(encoding="utf-8").splitlines():
        # 去除行首行尾的空白字符
        stripped = line.strip()
        
        # 如果遇到 [always] 标记，开始解析该节
        if stripped == "[always]":
            in_always = True
            continue
        
        # 如果遇到其他节标记（以 [ 开头），退出 [always] 节
        if stripped.startswith("["):
            in_always = False
            continue
        
        # 如果在 [always] 节内，且行包含 =，且不是注释行（不以 # 开头）
        if in_always and "=" in stripped and not stripped.startswith("#"):
            # 按第一个 = 分割键值对（partition 返回 (key, separator, value)）
            k, _, v = stripped.partition("=")
            
            # 去除键的空白字符
            k = k.strip()
            
            # 去除值的空白字符和引号（可能是 "allow" 或 'allow'）
            v = v.strip().strip('"')
            
            # 只接受合法的决策值："allow" 或 "deny"
            if v in ("allow", "deny"):
                result[k] = v
    
    # 返回解析后的策略字典
    return result


# 将权限策略写入 policy.toml 文件，覆盖 [always] 节
# 参数 always: 字典，key 是工具名，value 是 "allow" 或 "deny"
# 参数 path: 可选的自定义文件路径，默认为 ~/.kama/policy.toml
# 返回值: 无（写入文件后直接返回）
def save_policy_file(always: dict[str, str], path: Path | None = None) -> None:
    # 使用提供的路径或默认路径，展开 ~ 为实际的家目录路径
    p = (path or _DEFAULT_POLICY_PATH).expanduser()
    
    # 创建父目录（如果不存在），parents=True 表示创建所有缺失的父目录
    # exist_ok=True 表示如果目录已存在也不会报错
    p.parent.mkdir(parents=True, exist_ok=True)
    
    # 构建要写入的文件内容，逐行存储在列表中
    lines = [
        "# ~/.kama/policy.toml",                     # 文件头注释
        "# 由 kama-core 自动管理，手动编辑生效但格式须正确",  # 使用说明注释
        "",                                          # 空行
        "[always]",                                  # [always] 节标记
    ]
    
    # 按工具名字母顺序遍历策略字典，写入每行配置
    for tool, decision in sorted(always.items()):
        lines.append(f'{tool} = "{decision}"')
    
    # 将列表拼接为字符串，每行用换行符分隔，最后加一个换行符
    # 按 UTF-8 编码写入文件
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
