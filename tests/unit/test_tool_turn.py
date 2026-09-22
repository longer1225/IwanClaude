"""
tool_turn 单元测试 - 引擎共享的"模型→工具→结果→模型"内循环

【学习要点】
1. 为什么这个测试文件重要：历史上的 P0 bug（工具结果不回喂、只把结果拼进
   答案文本）骗过了所有旧测试——旧测试的 stub provider 从不真正注册工具。
   这里的 EchoTool 会真实执行，断言的是 Anthropic 消息协议层面的形状：
   assistant.tool_use 后面必须紧跟 user.tool_result 配对块。
2. 轨迹（trajectory）断言用"消息序列形状"而非内容字符串：role + block 类型
   是协议的不变式，文本内容随模型版本变化。
"""
from __future__ import annotations

import asyncio

from iwan_claude.core import run_registry
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.llm.types import LlmResponse, ToolCallBlock
from iwan_claude.core.run_registry import add_steer
from iwan_claude.core.tool_turn import maybe_compact, run_tool_turn
from iwan_claude.core.tools.base import BaseTool, ToolResult
from iwan_claude.core.tools.registry import ToolRegistry


class _EchoTool(BaseTool):
    # 真实可执行的 echo 工具：返回入参回显，用于验证 tool_result 回喂内容
    name = "echo"
    description = "Echoes msg"
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        return ToolResult(content=f"echo:{params['msg']}")


class _ScriptedProvider:
    # 按脚本依次返回响应（越界重复最后一条）；可注入第 N 次调用后回调 / 第 M 次抛异常
    def __init__(
        self,
        responses: list[LlmResponse],
        *,
        exc_at: int | None = None,
        hook_after: int | None = None,
        hook: object = None,
    ) -> None:
        self._responses = responses
        self._exc_at = exc_at
        self._hook_after = hook_after
        self._hook = hook
        self.calls = 0

    # 模拟 LLMProvider.chat：脚本化响应，记录调用次数供断言
    async def chat(
        self,
        messages: object,
        tool_schemas: object,
        bus: object,
        run_id: str,
        *,
        step: int = 0,
        system: object = None,
    ) -> LlmResponse:
        idx = self.calls
        self.calls += 1
        if self._exc_at is not None and idx == self._exc_at:
            raise RuntimeError("boom")
        if self._hook_after is not None and idx == self._hook_after and callable(self._hook):
            self._hook()  # type: ignore[operator]
        return self._responses[min(idx, len(self._responses) - 1)]


class _BoomCompactor:
    # 假压缩器：compact 抛异常，验证降级路径
    async def compact(self, context: object, provider: object) -> None:
        raise RuntimeError("compact failed")


class _OkCompactor:
    # 假压缩器：把 ExecutionContext.messages 重写为单条摘要
    async def compact(self, context: object, provider: object) -> None:
        context.messages = [{"role": "user", "content": "SUMMARY"}]  # type: ignore[attr-defined]


# autouse：清空全局 run 注册表，避免用例间 steer 串台
async def _noop() -> None:
    return None


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_EchoTool())
    return reg


def _tool_use(uid: str = "t1", msg: str = "hi") -> LlmResponse:
    return LlmResponse(
        stop_reason="tool_use",
        tool_calls=[ToolCallBlock(id=uid, name="echo", input={"msg": msg})],
    )


def _msgs() -> list[dict[str, object]]:
    return [{"role": "user", "content": "do it"}]


# 给 steer 测试起一个可注册的占位 task（不 await，用例结束自动回收）
def _register_ghost_run(run_id: str) -> asyncio.Task[None]:
    task = asyncio.create_task(_noop())
    run_registry.register_run(run_id, "s1", task)
    return task


# 把轨迹消息归类成形状标签序列，供顺序断言
def _shape(trajectory: list[dict[str, object]]) -> list[str]:
    tags: list[str] = []
    for msg in trajectory:
        content = msg["content"]
        if isinstance(content, str):
            tags.append("steer" if "Steering" in content else "text")
        elif isinstance(content, list) and content:
            first = content[0]
            tags.append(str(first.get("type", "block")))
        else:
            tags.append("empty")
    return tags


# 功能：工具回合产出的轨迹符合 Anthropic 协议——assistant(tool_use) 后紧跟配对的 user(tool_result)
# 设计：stub provider 从"不注册工具的假响应"升级为真执行 EchoTool，正是旧测试
#       漏掉 P0 的盲区；断言 tool_use_id 配对与回喂内容，保证模型下一轮能"看见"结果
async def test_tool_result_is_paired_and_fed_back() -> None:
    provider = _ScriptedProvider([_tool_use(), LlmResponse(stop_reason="end_turn", text="all done")])
    result = await run_tool_turn(
        provider, _registry(), EventBus(),  # type: ignore[arg-type]
        system="s", messages=_msgs(), run_id="r-tool",
    )
    assert result.text == "all done"
    assert result.rounds == 2
    assert len(result.trajectory) == 3
    assistant_use, user_result, _final = result.trajectory
    assert assistant_use["role"] == "assistant"
    assert user_result["role"] == "user"
    block = user_result["content"][0]  # type: ignore[index]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "t1"
    assert "echo:hi" in str(block["content"])
    assert _shape(result.trajectory) == ["tool_use", "tool_result", "text"]


# 功能：steer 在回合边界注入——出现在 tool_result 之后、下一次模型响应的 assistant 之前
# 设计：通过 provider hook 在第一次响应返回后才入队修正，排除"跑之前就在队列里"
#       的干扰；断言形状序列而非仅存在性，钉死注入位置（协议合法性：不打断工具配对）
async def test_steer_consumed_at_turn_boundary() -> None:
    task = _register_ghost_run("r-steer")
    try:
        def _inject() -> None:
            add_steer("r-steer", "改用 asyncio")

        provider = _ScriptedProvider(
            [_tool_use(), LlmResponse(stop_reason="end_turn", text="ok switched")],
            hook_after=0, hook=_inject,
        )
        result = await run_tool_turn(
            provider, _registry(), EventBus(),  # type: ignore[arg-type]
            system="s", messages=_msgs(), run_id="r-steer",
        )
        assert result.text == "ok switched"
        assert _shape(result.trajectory) == ["tool_use", "tool_result", "steer", "text"]
    finally:
        run_registry.unregister_run("r-steer")
        task.cancel()


# 功能：LLM 调用异常时 error 透出，但异常前已产生的轨迹保留（错误不丢执行过程）
# 设计：第二轮才炸——第一轮的工具轨迹必须原样带回，这是"停在半路也能看到走了多远"的契约
async def test_error_preserves_partial_trajectory() -> None:
    provider = _ScriptedProvider([_tool_use()], exc_at=1)
    result = await run_tool_turn(
        provider, _registry(), EventBus(),  # type: ignore[arg-type]
        system="s", messages=_msgs(), run_id="r-err",
    )
    assert result.error == "boom"
    assert len(result.trajectory) == 2  # assistant(tool_use) + user(tool_result)


# 功能：模型持续调用工具时 max_rounds 保险丝生效，循环收敛不死转
# 设计：provider 无限返回 tool_use（脚本越界重复最后一条），轮数达上限即停；
#       断言 rounds 恰等于上限，确认 for-else 结构真的在"用尽轮数"时退出
async def test_max_rounds_fuse() -> None:
    provider = _ScriptedProvider([_tool_use()])
    result = await run_tool_turn(
        provider, _registry(), EventBus(),  # type: ignore[arg-type]
        system="s", messages=_msgs(), run_id="r-fuse", max_rounds=3,
    )
    assert result.rounds == 3
    assert provider.calls == 3


# 功能：maybe_compact 三种分支——未超阈值零开销、压缩异常降级、超阈值正常重写
# 设计：压缩失败绝不能拖垮整个 run（catch 后返回原历史），这条降级路径单独测；
#       用最小假压缩器替换真 LLM 摘要调用，测试只关心"换没换历史"
async def test_maybe_compact_branches() -> None:
    msgs = _msgs()
    out, did = await maybe_compact(None, None, msgs, 0.9, 0.5, "r-c")  # type: ignore[arg-type]
    assert out is msgs and did is False  # 无压缩器
    out, did = await maybe_compact(_OkCompactor(), None, msgs, 0.3, 0.5, "r-c")  # type: ignore[arg-type]
    assert out is msgs and did is False  # 未超阈值
    out, did = await maybe_compact(_BoomCompactor(), None, msgs, 0.9, 0.5, "r-c")  # type: ignore[arg-type]
    assert out == msgs and did is False  # 压缩异常 → 降级
    out, did = await maybe_compact(_OkCompactor(), None, msgs, 0.9, 0.5, "r-c")  # type: ignore[arg-type]
    assert did is True and out[0]["content"] == "SUMMARY"  # 正常压缩
