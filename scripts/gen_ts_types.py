# -*- coding: utf-8 -*-  # noqa: UP009
# ---------------------------------------------------------------------------
# bus 协议模型 → TypeScript 类型生成器（gui/src/renderer/src/protocol/types.ts）
# ---------------------------------------------------------------------------
# 【学习要点】与 gen_protocol_doc.py 同源的思想：协议只有一份事实源（pydantic 模型），
# 文档和前端类型都是它的投影。前端手写消息定义必然漂移——改一个字段，
# daemon 正常、TS 悄悄失真，坏在运行时才暴露。本脚本 + --check 把失真拦在 CI。
#
# 生成物命名契约（渲染层按此 import）：
#   SessionCreateCommand -> interface SessionCreateParams（去掉 type 字段，非必填项加 ?）
#   SessionCreateResult  -> interface SessionCreateResult
#   事件模型             -> export type BusEvent = { type: 'llm.token'; ... } | ...（判别联合）
#   命令名               -> export type CommandMethod = "session.create" | ...
# ---------------------------------------------------------------------------
from __future__ import annotations

import inspect
import sys
import types as pytypes
import typing
from pathlib import Path
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from iwan_claude.core.bus import commands as cmds
from iwan_claude.core.bus import events as evts

# 输出目标路径（相对仓库根）
OUT_REL = Path("gui") / "src" / "renderer" / "src" / "protocol" / "types.ts"

_HEADER = (
    "// ⚠ AUTO-GENERATED - 由 scripts/gen_ts_types.py 从 pydantic 协议模型生成，勿手改。\n"
    "// 重新生成: uv run python scripts/gen_ts_types.py   校验: --check\n"
)


# 收集模块内定义的全部 pydantic 模型类（按定义顺序，稳定输出）
def _models_in(mod: Any) -> list[type[BaseModel]]:
    out: list[type[BaseModel]] = []
    for _name, obj in vars(mod).items():
        if (
            inspect.isclass(obj)
            and issubclass(obj, BaseModel)
            and getattr(obj, "__module__", "") == mod.__name__
        ):
            out.append(obj)
    return out


# 把 python 类型注解翻译成 TS 类型串；返回 (ts类型, 是否可能为 null)
def _ts_type(ann: Any) -> str:
    if ann is None or ann is type(None):
        return "null"
    if ann is Any:
        return "unknown"
    if ann is str:
        return "string"
    if ann in (int, float):
        return "number"
    if ann is bool:
        return "boolean"
    origin = get_origin(ann)
    if origin is typing.Literal:
        parts = []
        for v in get_args(ann):
            parts.append(f'"{"true" if v is True else "false" if v is False else v}"' if isinstance(v, (str, bool)) else str(v))
        return " | ".join(parts)
    # Optional[str] 与 str | None 是两种对象（typing.Union / types.UnionType），都要认
    if origin in (Union, pytypes.UnionType):
        args = [a for a in get_args(ann) if a is not type(None)]
        nullable = len(args) != len(get_args(ann))
        inner = " | ".join(_ts_type(a) for a in args)
        if len(args) > 1:
            inner = f"({inner})"
        return f"{inner} | null" if nullable else inner
    if origin in (list, set, frozenset):
        (arg,) = get_args(ann) or (Any,)
        return f"Array<{_ts_type(arg)}>"
    if origin is dict:
        args = get_args(ann)
        val = args[1] if len(args) == 2 else Any
        return f"Record<string, {_ts_type(val)}>"
    if inspect.isclass(ann) and issubclass(ann, BaseModel):
        return ann.__name__
    return "unknown"


# 生成一个模型的 interface 字段块（skip 里的字段名会被剔除，如判别字段 type）
def _fields_block(cls: type[BaseModel], indent: str = "  ", skip: tuple[str, ...] = ()) -> str:
    lines: list[str] = []
    for name, f in cls.model_fields.items():
        if name in skip:
            continue
        ts = _ts_type(f.annotation)
        opt = "" if f.is_required() else "?"
        lines.append(f"{indent}{name}{opt}: {ts}")
    return "\n".join(lines)


# 内联对象形态（事件联合成员用，成员以分号分隔，缩进两级）
def _inline_fields(cls: type[BaseModel], type_literal: str, indent: str = "  ") -> str:
    members = [f"{indent}  type: '{type_literal}';"]
    for name, f in cls.model_fields.items():
        if name == "type":
            continue
        ts = _ts_type(f.annotation)
        opt = "" if f.is_required() else "?"
        members.append(f"{indent}  {name}{opt}: {ts};")
    return "{\n" + "\n".join(members) + f"\n{indent}}}"


# 取模型的 type 判别字段字面量值
def _type_literal(cls: type[BaseModel]) -> str:
    f = cls.model_fields.get("type")
    if f is None or f.default is None:
        raise ValueError(f"{cls.__name__} 缺 type 判别值")
    return str(f.default)


# 主生成流程：拼出整个 types.ts 文本
def render() -> str:
    cmd_models = _models_in(cmds)
    evt_models = _models_in(evts)

    command_classes = [c for c in cmd_models if c.__name__.endswith("Command")]
    result_classes = {c.__name__[: -len("Command")]: c for c in cmd_models if c.__name__.endswith("Result")}
    plain_models = [
        c for c in cmd_models
        if not c.__name__.endswith(("Command", "Result"))
    ]

    out: list[str] = [_HEADER, ""]
    out.append("// ==================== 命令参数 ====================")
    for c in command_classes:
        base = c.__name__[: -len("Command")]
        out.append(f"export interface {base}Params {{")
        out.append(_fields_block(c, skip=("type",)))
        out.append("}")
        out.append("")
    out.append("// ==================== 命令结果与数据结构 ====================")
    for c in plain_models + list(result_classes.values()):
        out.append(f"export interface {c.__name__} {{")
        out.append(_fields_block(c))
        out.append("}")
        out.append("")
    out.append("// ==================== 命令名联合 ====================")
    methods = sorted(_type_literal(c) for c in command_classes)
    union = "\n  | ".join(f'"{m}"' for m in methods)
    out.append(f"export type CommandMethod =\n  | {union}")
    out.append("")
    out.append("// ==================== 事件判别联合 ====================")
    parts = ["  | " + _inline_fields(e, _type_literal(e)) for e in evt_models]
    out.append("export type BusEvent =")
    out.append("\n".join(parts))
    out.append("")
    return "\n".join(out)


# 写盘或校验（--check 模式给 CI 用：内容漂移即非零退出）
def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = root / OUT_REL
    content = render()
    if "--check" in sys.argv:
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            print(f"WARN {OUT_REL} 与协议不同步，请运行 scripts/gen_ts_types.py", file=sys.stderr)
            return 1
        print(f"OK {OUT_REL} 与协议同步")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    print(f"已生成 {OUT_REL}（{content.count(chr(10)) + 1} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
