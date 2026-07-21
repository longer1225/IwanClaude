"""
Trace 命令模块 - 查看和过滤核心服务的 trace 日志

【学习要点】
1. JSONL 文件格式：每行一个 JSON 对象，适合流式日志存储
2. 终端彩色输出：使用 ANSI 转义码实现彩色文本
3. 文件实时监控：使用 seek(0, 2) 定位到文件末尾，实现类似 tail -f 的功能
4. 数据过滤：根据 run_id、layer、direction 等条件过滤日志
5. 数据摘要：从大型 payload 中提取关键信息，避免输出过多内容

【方向标识】
- CLIENT→CORE：客户端到核心服务的命令
- CORE→CLIENT：核心服务到客户端的响应
- CORE：核心服务内部日志
- CORE→LLM：核心服务到 LLM 的 API 调用
- LLM→CORE：LLM 到核心服务的 API 响应
"""
from __future__ import annotations

# json：JSON 序列化/反序列化
# sys：系统相关操作
# time：时间相关功能
# pathlib：路径操作
import json
import sys
import time
from pathlib import Path

# 导入配置和 Trace 记录模型
from iwan_claude.core.config import IwanConfig
from iwan_claude.core.trace.record import TraceRecord

# ANSI 转义码颜色映射：不同方向使用不同颜色，便于区分
_COLORS = {
    "CLIENT→CORE": "\033[36m",  # 青色：客户端到核心服务
    "CORE→CLIENT": "\033[33m",  # 黄色：核心服务到客户端
    "CORE":             "\033[32m",  # 绿色：核心服务内部
    "CORE→LLM":   "\033[35m",  # 紫色：核心服务到 LLM
    "LLM→CORE":   "\033[34m",  # 蓝色：LLM 到核心服务
}
_RESET = "\033[0m"  # 重置颜色
_BOLD = "\033[1m"   # 粗体


# iwan trace 子命令：从 daemon.jsonl 读取并展示 trace 记录
def cmd_trace(
    run_id: str | None,
    config: IwanConfig,
    *,
    layer: str | None = None,
    direction: str | None = None,
    raw: bool = False,
    follow: bool = False,
) -> None:
    """
    查看和过滤 trace 日志
    
    使用方式：iwan trace [run_id] [options]
    
    参数：
        run_id: 可选，只显示指定 run 的日志
        config: IwanConfig 配置对象
        layer: 可选，按层过滤（如 "api", "bus", "session"）
        direction: 可选，按方向过滤（如 "CLIENT→CORE", "CORE→LLM"）
        raw: 是否显示原始 JSON（默认 False，显示格式化输出）
        follow: 是否实时跟踪（类似 tail -f）
    
    工作流程：
    1. 定位 trace 文件
    2. 读取并处理已有日志
    3. 如果 follow=True，持续监控新日志
    """
    # 获取 trace 文件路径，展开用户目录（如 ~）
    trace_path = Path(config.trace.file).expanduser()
    
    # 检查文件是否存在
    if not trace_path.exists():
        print(f"trace file not found: {trace_path}", file=sys.stderr)
        sys.exit(1)

    # 读取已有日志
    with open(trace_path) as f:
        for line in f:
            _process_line(
                line.strip(),
                run_id=run_id,
                layer=layer,
                direction=direction,
                raw=raw,
            )

    # 如果需要实时跟踪（follow 模式）
    if follow:
        with open(trace_path) as f:
            # f.seek(0, 2)：定位到文件末尾
            # 0 表示偏移量，2 表示从文件末尾开始计算
            f.seek(0, 2)
            
            # 无限循环，持续读取新内容
            while True:
                # 读取一行
                line = f.readline()
                if line:
                    # 有新内容，处理并输出
                    _process_line(
                        line.strip(),
                        run_id=run_id,
                        layer=layer,
                        direction=direction,
                        raw=raw,
                    )
                else:
                    # 没有新内容，等待 50ms 再检查
                    time.sleep(0.05)


# 解析单行并根据过滤条件决定是否输出
def _process_line(
    line: str,
    *,
    run_id: str | None,
    layer: str | None,
    direction: str | None,
    raw: bool,
) -> None:
    """
    处理单行 trace 日志
    
    参数：
        line: JSONL 格式的日志行
        run_id: 可选，run_id 过滤条件
        layer: 可选，层过滤条件
        direction: 可选，方向过滤条件
        raw: 是否显示原始 JSON
    
    处理流程：
    1. 跳过空行
    2. 解析 JSON 为 TraceRecord 对象
    3. 根据过滤条件筛选
    4. 输出（原始或格式化）
    """
    # 跳过空行
    if not line:
        return
    
    try:
        # 将 JSON 字符串解析为 TraceRecord 对象
        # model_validate 会验证数据结构是否符合模型定义
        record = TraceRecord.model_validate(json.loads(line))
    except Exception:
        # 解析失败或验证失败，跳过该行
        return

    # ===== 过滤条件检查 =====
    # 如果指定了 run_id，只保留匹配的记录
    if run_id is not None and record.run_id != run_id:
        return
    # 如果指定了 layer，只保留匹配的记录
    if layer is not None and record.layer != layer:
        return
    # 如果指定了 direction，只保留匹配的记录
    if direction is not None and record.direction != direction:
        return

    # 输出结果
    if raw:
        # 原始模式：直接打印 JSON 字符串
        print(line)
    else:
        # 格式化模式：彩色输出
        _print_record(record)


# 将单条 TraceRecord 格式化为彩色单行输出
def _print_record(record: TraceRecord) -> None:
    """
    将 TraceRecord 格式化为彩色单行输出
    
    输出格式：
    [时间戳]  [方向]  [类型]  [run=xxx] [step=xxx] [摘要]
    
    参数：
        record: TraceRecord 对象
    """
    # 根据方向获取颜色
    color = _COLORS.get(record.direction, "")
    
    # 截取时间戳的时分秒毫秒部分（如 15:30:45.123456）
    # 原始时间戳格式类似：2024-01-15T15:30:45.123456Z
    ts = record.ts[11:23] if len(record.ts) >= 23 else record.ts

    # 方向字符串：带颜色和粗体，右对齐到 14 个字符
    direction_str = f"{color}{_BOLD}{record.direction:<14}{_RESET}"
    # 类型字符串：右对齐到 13 个字符
    kind_str = f"{record.kind:<13}"

    # 构建输出部分列表
    parts: list[str] = []
    if record.run_id:
        # 添加 run_id（只显示前 8 位）
        parts.append(f"run={record.run_id[:8]}")
    if record.step is not None:
        # 添加 step 编号
        parts.append(f"step={record.step}")
    # 添加数据摘要
    parts.append(_summarize(record))

    # 组合所有部分并打印
    print(f"{ts}  {direction_str}  {kind_str}  {'  '.join(parts)}")


# 从 data 字段提取关键摘要（不输出大型 payload）
def _summarize(record: TraceRecord) -> str:
    """
    根据记录类型提取关键摘要
    
    不同类型的记录有不同的数据结构，需要针对性提取关键信息：
    - command: 方法名和目标
    - response: 返回结果
    - error: 错误码和消息
    - push: 事件类型
    - event: 事件类型
    - api_call: 消息数量和工具数量
    - api_response: 停止原因、延迟、输出 token 数
    
    参数：
        record: TraceRecord 对象
    
    返回：
        摘要字符串
    """
    data = record.data
    kind = record.kind

    # ===== command 类型：客户端命令 =====
    if kind == "command":
        params = data.get("params", {})
        goal = str(params.get("goal", ""))
        # 如果有 goal，截取前 50 个字符
        suffix = f'  goal="{goal[:50]}"' if goal else ""
        return f"method={data.get('method')}{suffix}"

    # ===== response 类型：服务端响应 =====
    if kind == "response":
        result = data.get("result", {})
        # 如果是 run 创建响应，显示 run_id
        if isinstance(result, dict) and "run_id" in result:
            return f"run_id={result['run_id'][:8]}"
        # 其他响应，截取前 60 个字符
        return str(result)[:60]

    # ===== error 类型：错误信息 =====
    if kind == "error":
        err = data.get("error", {})
        return f"code={err.get('code')}  {err.get('message', '')}"

    # ===== push 类型：事件推送 =====
    if kind == "push":
        return f"event={data.get('event_type')}  sub={data.get('sub_id')}"

    # ===== event 类型：事件 =====
    if kind == "event":
        return f"type={data.get('type')}"

    # ===== api_call 类型：LLM API 调用 =====
    if kind == "api_call":
        msgs = data.get("messages")
        count = len(msgs) if isinstance(msgs, list) else data.get("message_count", "?")
        tools = data.get("tool_schemas")
        tc = len(tools) if isinstance(tools, list) else data.get("tool_count", "?")
        return f"msgs={count}  tools={tc}"

    # ===== api_response 类型：LLM API 响应 =====
    if kind == "api_response":
        usage = data.get("usage", {})
        return (
            f"stop={data.get('stop_reason')}  "
            f"latency={data.get('latency_ms')}ms  "
            f"out_tokens={usage.get('output_tokens', '?')}"
        )

    # 默认：截取前 60 个字符
    return str(data)[:60]
