# 功能：验证语音转写器的入参校验、依赖缺失报错与假模型下的 PCM 归一化/拼接路径
# 设计：全部绕开真模型——校验类用例依赖"校验前置于 _ensure_model"的函数内顺序
# （垃圾输入绝不该付模型加载的学费）；缺依赖用 sys.modules 塞 None 触发
# ImportError 分支，不真卸载包；成功路径注入假模型捕获音频数组，断言
# int16→float32/32768 的换算数值（16384→0.5、-32768→-1.0），这步若错，
# 真模型只会"听不见"却难定位，所以必须在单测锁死。
from __future__ import annotations

import base64
import sys
from typing import Any

import numpy as np
import pytest

from iwan_claude.core.speech import SpeechTranscriber, model_size, prewarm_enabled


# 功能：采样率越界（低于 8k / 高于 48k）都在加载模型前被 ValueError 拦下
# 设计：取边界外侧一格（7999/48001）而非离谱值——边界判定最容易off-by-one
def test_sample_rate_out_of_range() -> None:
    t = SpeechTranscriber()
    for sr in (7999, 48001):
        with pytest.raises(ValueError, match="采样率"):
            t.transcribe_sync("AAA=", sr)


# 功能：空音频 / 非法 base64 / 奇数字节三种垃圾输入各得中文 ValueError
# 设计：三条断言共用"必须在校验段抛出"这一不变式，模型加载函数若被调用
# 会去下载真模型——测试环境网络不可控，故用 _ensure_model 打桩成 raise
# AssertionError 的方式反证校验确实前置
def test_garbage_inputs_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    t = SpeechTranscriber()

    def _boom() -> Any:
        raise AssertionError("校验段失守：垃圾输入走到了模型加载")

    monkeypatch.setattr(t, "_ensure_model", _boom)
    with pytest.raises(ValueError, match="音频为空"):
        t.transcribe_sync("", 16000)
    with pytest.raises(ValueError, match="base64"):
        t.transcribe_sync("!!!not-base64!!!", 16000)
    with pytest.raises(ValueError, match="偶数"):
        t.transcribe_sync(base64.b64encode(b"\x01\x02\x03").decode(), 16000)


# 功能：超过 60 秒时长上限的音频被拒（用最低合法采样率压缩构造成本）
# 设计：8000Hz×61s×2字节的 base64 仍有一百多 KB，但断言只需匹配"上限"
# 文案——构造成本换真实性，不去测 59.9s 的贴边通过（那是 whisper 的事）
def test_duration_clamped() -> None:
    t = SpeechTranscriber()
    raw = b"\x00\x00" * (8000 * 61)
    with pytest.raises(ValueError, match="上限"):
        t.transcribe_sync(base64.b64encode(raw).decode(), 8000)


# 功能：依赖未安装时报中文指引（含 uv add 命令），而非裸 ImportError
# 设计：sys.modules 塞 None 让 from-import 直接 ImportError——不真动虚拟环境；
# 这是 daemon 缺包时的用户体验面，文案丢了"怎么修"就只够用户骂街
def test_missing_dependency_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    t = SpeechTranscriber()
    with pytest.raises(RuntimeError, match="uv add faster-whisper"):
        t._ensure_model()


# 功能：假模型成功路径——int16 样本正确归一化成 float32、分段文本正确拼接
# 设计：fake.transcribe 捕获音频数组并断言 16384→0.5、-32768→-1.0（whisper
# 约定满量程 32768），segments 用带 .text 属性的简单对象；返回 "".join 后再
# strip，验证"你好"+"世界"两段合成"你好世界"的拼接无粘连空格
def test_fake_model_normalizes_and_joins(monkeypatch: pytest.MonkeyPatch) -> None:
    t = SpeechTranscriber()
    captured: dict[str, Any] = {}

    class _Seg:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeModel:
        def transcribe(self, audio: np.ndarray, **kw: Any) -> tuple[Any, Any]:
            captured["audio"] = audio
            captured["kw"] = kw
            return iter([_Seg("你好"), _Seg("世界")]), object()

    monkeypatch.setattr(t, "_ensure_model", lambda: _FakeModel())
    pcm = np.array([0, 16384, -32768], dtype=np.int16).tobytes()
    out = t.transcribe_sync(base64.b64encode(pcm).decode(), 8000)
    assert out == "你好世界"
    got = captured["audio"]
    assert got.dtype == np.float32
    assert np.allclose(got, [0.0, 0.5, -1.0])
    assert captured["kw"]["language"] == "zh"  # 默认语言提示来自 IWAN_SPEECH_LANG


# 功能：vad_filter 判定全程无人声时返回空串而不是报错
# 设计：ok=True + 空 text 是合法组合（GUI 侧据此提示"没听清"），
# 与校验失败的 ok=False 通道严格区分——两者混用会让界面无法措辞
def test_silence_yields_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    t = SpeechTranscriber()

    class _FakeModel:
        def transcribe(self, audio: np.ndarray, **kw: Any) -> tuple[Any, Any]:
            return iter([]), object()

    monkeypatch.setattr(t, "_ensure_model", lambda: _FakeModel())
    pcm = np.zeros(800, dtype=np.int16).tobytes()
    assert t.transcribe_sync(base64.b64encode(pcm).decode(), 8000) == ""


# 功能：IWAN_SPEECH_MODEL 环境变量可覆盖模型规模，空值回退 base
# 设计：两条各测一半语义（覆盖/回退）；不测 tiny/medium 加载——那是
# 真模型的领域，本模块只保证名字传得对
def test_model_size_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IWAN_SPEECH_MODEL", "tiny")
    assert model_size() == "tiny"
    monkeypatch.setenv("IWAN_SPEECH_MODEL", "  ")
    assert model_size() == "base"


# 功能：prewarm 加载模型后用 0.1 秒静音空跑一遍，并把惰性生成器消费到底，返回模型规模
# 设计：假模型的 transcribe 记录传入音频并返回"迭代过才置位"的生成器——
# 若实现忘了 for 消费（ctranslate2 的推理在迭代时才真正发生），consumed 保持
# False，测试即红；这正是预热比"只 load"多出来的那一截价值，必须锁死。
# 音频断言 1600 点 float32（=0.1 秒 @16k），语言参数走 language_hint 默认 zh
def test_prewarm_runs_silence_and_drains_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    t = SpeechTranscriber()
    captured: dict[str, Any] = {}
    consumed = {"drained": False}

    def _lazy_segments() -> Any:
        yield from ()
        consumed["drained"] = True

    class _FakeModel:
        def transcribe(self, audio: np.ndarray, **kw: Any) -> tuple[Any, Any]:
            captured["audio"] = audio
            captured["kw"] = kw
            return _lazy_segments(), object()

    t._model = _FakeModel()  # 预置缓存：prewarm 不该再触发真加载
    monkeypatch.setenv("IWAN_SPEECH_MODEL", "small")
    assert t.prewarm() == "small"
    assert captured["audio"].dtype == np.float32
    assert len(captured["audio"]) == 1600
    assert consumed["drained"] is True
    assert captured["kw"]["language"] == "zh"


# 功能：IWAN_SPEECH_PREWARM 默认开（未设/空值都算开），显式设 "0" 才关
# 设计：开关的消费端（app.py 建 task 与否）属集成面，单测只锁读端语义——
# 用 "0 "/" 0" 两个带空格变体验证 strip 生效，防 env 传值带尾空格时预热赖着不走
def test_prewarm_enabled_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IWAN_SPEECH_PREWARM", raising=False)
    assert prewarm_enabled() is True
    monkeypatch.setenv("IWAN_SPEECH_PREWARM", "")
    assert prewarm_enabled() is True
    monkeypatch.setenv("IWAN_SPEECH_PREWARM", "0")
    assert prewarm_enabled() is False
    monkeypatch.setenv("IWAN_SPEECH_PREWARM", " 0 ")
    assert prewarm_enabled() is False
    monkeypatch.setenv("IWAN_SPEECH_PREWARM", "1")
    assert prewarm_enabled() is True
