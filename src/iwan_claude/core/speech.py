# 语音转写模块：本地 faster-whisper 离线 ASR（语音输入功能的 daemon 侧）
#
# 【学习要点】1) 重依赖懒加载：faster-whisper 不在模块导入期 import，而是首次
# 真要用时才 import——依赖没装/模型没下载时 daemon 照常起，RPC 返回 ok=False +
# 中文指引。功能不可用 ≠ 整个应用起不来，这是可选能力的通用装配纪律。
# 2) 转写是纯 CPU 阻塞运算，所以本模块只暴露同步函数 transcribe_sync，
# 由 handler 用 asyncio.to_thread 丢进线程池——事件循环一旦被语音掐住，
# 所有审批卡/心跳/广播全部延迟，代价远超一次推理。
# 3) 校验全部前置于模型加载：空音频/坏 base64/超长这些"垃圾进"场景
# 不该付 150MB 模型加载的学费，也让集成测试能绕开真模型验证协议往返。
from __future__ import annotations

import base64
import binascii
import os
from typing import Any

# 单次转写的音频时长上限（秒）：超过说明 GUI 侧失控，daemon 拒绝硬扛
_MAX_DURATION_S = 60.0
# 采样率合法区间：8k 太低无法识别中文，48k 以上纯属浪费带宽（GUI 固定送 16k）
_MIN_SR = 8000
_MAX_SR = 48000


# whisper 模型规模（tiny/base/small/medium），env 可覆盖以适配离线缓存/低配机器
def model_size() -> str:
    return os.environ.get("IWAN_SPEECH_MODEL", "base").strip() or "base"


# 识别语言提示（zh/en/...），空=自动检测；env 注入而非配置表：单字段不值当开一节
def language_hint() -> str:
    return os.environ.get("IWAN_SPEECH_LANG", "zh").strip()


class SpeechTranscriber:
    """
    语音转写器：懒加载 whisper 模型 + 同步转写入口

    【设计目的】
    单实例挂在 CoreApp 上终身复用——模型加载秒级起步，每条语音都重载
    等于把功能做成幻灯片。transcribe_sync 非异步是刻意的：调用方
    （handler）负责用 to_thread 包，模块自己绝不 import asyncio 装懂并发。
    """

    def __init__(self) -> None:
        # 模型对象 Any：faster-whisper 无类型标记，接口只用到 transcribe() 一点
        self._model: Any | None = None

    # 首次调用时加载模型并缓存；依赖缺失/下载失败都转成中文 RuntimeError
    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise RuntimeError(
                    "语音识别未安装：请在项目根执行 `uv add faster-whisper` 后重启 daemon"
                ) from e
            size = model_size()
            try:
                # int8 CPU 推理：精度换体积，base 模型下中文听写质量损失可忽略
                self._model = WhisperModel(size, device="cpu", compute_type="int8")
            except Exception as e:
                raise RuntimeError(
                    f"加载语音模型（{size}）失败：{e}。首次使用需联网下载；"
                    "若访问不到 huggingface.co，可设 HF_ENDPOINT=https://hf-mirror.com "
                    "或改用 IWAN_SPEECH_MODEL=tiny"
                ) from e
        return self._model

    # PCM → 文字（同步）：入参是 int16 小端单声道裸字节的 base64；校验先于模型加载
    def transcribe_sync(self, audio_b64: str, sample_rate: int) -> str:
        if not _MIN_SR <= sample_rate <= _MAX_SR:
            raise ValueError(f"采样率超出合法区间 {_MIN_SR}..{_MAX_SR}：{sample_rate}")
        try:
            raw = base64.b64decode(audio_b64, validate=True)
        except binascii.Error as e:
            raise ValueError("音频数据不是合法的 base64 编码") from e
        if not raw:
            raise ValueError("音频为空：请先录一段语音再发送")
        if len(raw) % 2:
            raise ValueError("PCM 字节数必须是偶数（int16 样本对）")
        import numpy as np

        n_samples = len(raw) // 2
        dur_s = n_samples / sample_rate
        if dur_s > _MAX_DURATION_S:
            raise ValueError(f"音频超过 {_MAX_DURATION_S:.0f} 秒上限（{dur_s:.1f} 秒）")
        # whisper 约定输入是 [-1,1] 归一化 float32；int16 满量程 32768
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        model = self._ensure_model()
        lang = language_hint()
        segments, _info = model.transcribe(audio, language=lang or None, vad_filter=True)
        return "".join(seg.text for seg in segments).strip()
