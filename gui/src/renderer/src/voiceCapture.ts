// 语音采集（v2 会话式）：麦克风 → worklet 能量 VAD 自动断句 → 每句 16kHz
// 单声道 int16 PCM base64（speech.transcribe 的 GUI 进料口）
//
// 【学习要点】1) 不用 MediaRecorder：它产出 webm/opus，daemon 侧解码就得再引
// ffmpeg 依赖；AudioWorklet 直接拿裸 Float32 PCM，whisper 吃归一化数组，
// 两端零转码、零新增依赖。2) worklet 代码经 Blob URL 注入——为了 20 行采集
// 循环不引第三方音频库，也不在打包里多一个文件。3) 降采样用线性插值：
// 48k→16k 是整数比、采样天然对齐，语音识别不是 Hi-Fi，不值得上抗混叠滤波。
// 4) v2 断句是纯能量启发式（RMS 阈 + 尾静音窗），不是训练过的 VAD 模型——
// 噪声环境会把句子切碎、呼吸声可能被当人声。这是"轻量流式"的诚实边界：
// 换来的是 daemon 协议零改动，每句仍走 V1 的一次性 speech.transcribe。
// 5) MediaStreamSource 只连 worklet 不连 destination：把麦克风直通扬声器
// 就是回授啸叫的诞生现场。
// 6) 'end' 必有回话（尾段或 done）：主线程的 stop() 不悬死；自动停的尾段
// 走 onSegment 正常通道——到点断线也不吞掉用户最后一句。

// 能量 VAD 阈值（v2 的"大脑"全在这几个数上，集中定义免得散落在模板字符串里）：
// 人声 RMS 判定线——正常说话距麦克风 20~50cm 时 RMS 约 0.02~0.15，
// 安静房间底噪 <0.005，0.012 卡在两者之间偏保守（宁可漏一句不误切半句）
export const VAD_RMS_THRESHOLD = 0.012
// 一句至少要多长才算话：350ms 以下的"有声"多半是咔哒声/咳嗽起头
export const VAD_MIN_VOICED_MS = 350
// 说完判定：连续静音多久才算句号。650ms 比语言学停顿略长，防"嗯……"换气被切
export const VAD_TAIL_SILENCE_MS = 650
// 前导静音保留：从检测到有声再往前多捞 50ms，否则辅音起头（"是""吃"）会被吞
export const VAD_PREROLL_MS = 50

// worklet 处理器源码：滑动 RMS 断句。buf 攒当前句样本；未开工的纯静音期
// 把 buf 裁到只剩前导 50ms（防 5 分钟静默把 buf 撑爆），一旦"有声够长"
// 正式开工、"尾静音够长"就整句交回主线程并清零；收到 'end' 必有回话
const WORKLET_SRC = `
class PcmVad extends AudioWorkletProcessor {
  constructor(opts) {
    super()
    const o = (opts && opts.processorOptions) || {}
    this.win = Math.max(1, Math.round((o.windowMs || 100) * sampleRate / 1000))
    this.thresh = o.rmsThreshold || 0.012
    this.minVoiced = Math.round((o.minVoicedMs || 350) * sampleRate / 1000)
    this.tailSil = Math.round((o.tailSilenceMs || 650) * sampleRate / 1000)
    this.preroll = Math.round((o.prerollMs || 50) * sampleRate / 1000)
    this.maxSeg = Math.round((o.maxSegmentMs || 50000) * sampleRate / 1000)
    this.winBuf = []     // 当前 100ms 窗内的块，攒满算一次 RMS
    this.winSamples = 0
    this.buf = []        // 当前句的全部样本（含开工前的前导残留）
    this.bufLen = 0
    this.voicedRun = 0   // 连续有声样本数（咔哒声撑不过 350ms）
    this.silentRun = 0   // 连续静音样本数
    this.started = false // 本句是否已因 voicedRun 达标而开工
    this.over = false
    this.ending = false    // 收到过 'end'：此后交出的最后一段要标 final
    this.port.onmessage = (e) => {
      if (e.data === 'end' && !this.over) {
        this.over = true
        this.ending = true
        if (this.started) this.emit()
        else this.port.postMessage({ done: true })
      }
    }
  }
  // 交回当前句：buf 全量合并成一个 Float32Array；final 标记"这是散伙尾段"
  emit() {
    const out = new Float32Array(this.bufLen)
    let off = 0
    for (const c of this.buf) { out.set(c, off); off += c.length }
    this.port.postMessage({ segment: true, final: this.ending, frames: out })
    this.reset()
  }
  // 一句交账后清零，下一句从头攒
  reset() {
    this.buf = []; this.bufLen = 0
    this.voicedRun = 0; this.silentRun = 0; this.started = false
  }
  // 未开工且当前窗是静音：把 buf 头部扔掉，只留最近 preroll 长度的残留
  trimToPreroll() {
    while (this.bufLen > this.preroll && this.buf.length > 1) {
      this.bufLen -= this.buf.shift().length
    }
  }
  process(inputs) {
    if (this.over) return false
    const ch = inputs[0] && inputs[0][0]
    if (ch) {
      const copy = new Float32Array(ch)
      this.buf.push(copy)
      this.bufLen += copy.length
      this.winBuf.push(copy)
      this.winSamples += copy.length
      if (this.winSamples >= this.win) {
        let sq = 0, n = 0
        for (const c of this.winBuf) { for (let i = 0; i < c.length; i++) { sq += c[i] * c[i]; n++ } }
        const rms = Math.sqrt(sq / Math.max(1, n))
        if (rms >= this.thresh) {
          this.voicedRun += this.winSamples
          this.silentRun = 0
          if (!this.started && this.voicedRun >= this.minVoiced) this.started = true
          // 一口气 50 秒不换气：硬切兜底，别让单段撞上 daemon 的 60 秒闸
          if (this.bufLen >= this.maxSeg) this.emit()
        } else {
          this.silentRun += this.winSamples
          this.voicedRun = 0
          if (this.started && this.silentRun >= this.tailSil) this.emit()
          if (!this.started) this.trimToPreroll()
        }
        this.winBuf = []; this.winSamples = 0
      }
    }
    return true
  }
}
registerProcessor('pcm-vad', PcmVad)
`

// 语音统一目标采样率（whisper 原生期望 16k，省得 daemon 再重采样）
export const TARGET_RATE = 16000

export interface VoiceCapture {
  frames: Float32Array
  sampleRate: number
}

// 一次语音会话的句柄：start 拿麦克风并挂 VAD worklet（每断出一句回调一次），
// stop 只负责"发 end、要尾段、拆资源"
export class VoiceRecorder {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: AudioWorkletNode | null = null
  // 尾段兑现器：手动 stop 发出 'end' 后等 worklet 回话；没尾段就到 null
  private pending: ((cap: VoiceCapture | null) => void) | null = null
  private onAutoEnd: (() => void) | null = null
  // 自动停标记：到点路径不设 pending，尾段走 onSegment 正常通道
  private autoEnding = false
  private timer = 0

  // 开始会话监听；getUserMedia 被系统权限/无设备拒绝时原样抛出给调用方兜。
  // onSegment 每断出一句调一次；onAutoEnd 在会话上限到点、资源释放后调一次
  async start(
    onSegment: (cap: VoiceCapture) => void,
    onAutoEnd: () => void,
    maxSeconds = 300
  ): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
    })
    this.ctx = new AudioContext()
    const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: 'application/javascript' }))
    try {
      await this.ctx.audioWorklet.addModule(url)
    } finally {
      URL.revokeObjectURL(url)
    }
    const src = this.ctx.createMediaStreamSource(this.stream)
    this.node = new AudioWorkletNode(this.ctx, 'pcm-vad', {
      processorOptions: {
        windowMs: 100,
        rmsThreshold: VAD_RMS_THRESHOLD,
        minVoicedMs: VAD_MIN_VOICED_MS,
        tailSilenceMs: VAD_TAIL_SILENCE_MS,
        prerollMs: VAD_PREROLL_MS,
        maxSegmentMs: 50000
      }
    })
    const rate = this.ctx.sampleRate
    this.onAutoEnd = onAutoEnd
    this.node.port.onmessage = (e: MessageEvent) => {
      const d = e.data as { segment?: boolean; done?: boolean; final?: boolean; frames?: Float32Array }
      // 'end' 且无尾段：worklet 明确回话 done，stop 立刻兑现 null，不用等看门狗
      if (d.done) { this.finish(); return }
      if (!d.segment) return
      const cap = { frames: d.frames ?? new Float32Array(0), sampleRate: rate }
      if (this.pending) { const p = this.pending; this.pending = null; p(cap) }
      else onSegment(cap)
      // final 段 = 'end' 之后交出的尾句（手动停有人等 promise，到点停走 onSegment）
      if (d.final) this.finish()
    }
    src.connect(this.node) // 故意不接 ctx.destination：防回授啸叫
    // 会话到点自动停：不发 'end' 等回话的 promise——尾段挤进 onSegment 通道，
    // 用户的最后一句不至于被"到点断线"吞掉
    this.timer = window.setTimeout(() => {
      const node = this.node
      if (!node) return
      window.clearTimeout(this.timer)
      this.autoEnding = true
      node.port.postMessage('end')
      // 兜底：worklet 若病态不回话（驱动挂起），5 秒后强拆，指示灯不许常亮
      window.setTimeout(() => { this.finish() }, 5000)
    }, maxSeconds * 1000)
  }

  // 结束会话：若当前句已达标，worklet 把尾段交回来（resolve 到该段）；
  // 静音收尾或句太短则 resolve null。
  // 【学习要点】teardown 必须排在 worklet 回话之后——先关上下文等于把还在
  // 攒尾句的采集器当场杀了；5 秒看门狗兜住"end 发了但 worklet 永远不回"的
  // 病态路径（比如驱动挂起），宁可丢尾段也不让 promise 悬死
  stop(): Promise<VoiceCapture | null> {
    const node = this.node
    if (!node) return Promise.resolve(null)
    window.clearTimeout(this.timer)
    return new Promise((resolve) => {
      this.pending = resolve
      node.port.postMessage('end')
      window.setTimeout(() => { this.finish() }, 5000)
    })
  }

  // 放弃会话（组件卸载/出错路径）：不产生结果，只清资源
  cancel(): void {
    this.teardown()
  }

  // 终结收尾（尾段/done/看门狗共用）：兑现残留 pending（给 null）、拆资源；
  // 只有到点路径（autoEnding）才回调 onAutoEnd——手动停的调用方在 await 里
  // 自己管状态，重复通知会让 Composer 把"已停"再停一遍
  private finish(): void {
    if (this.pending) { const p = this.pending; this.pending = null; p(null) }
    const auto = this.autoEnding
    this.autoEnding = false
    const cb = this.onAutoEnd
    this.onAutoEnd = null
    this.teardown()
    if (auto && cb) cb()
  }

  // 释放全部音频资源：停 track、关 worklet 连接、关上下文、撤定时器。
  // 顺手兑现残留 pending（给 null）：teardown 与 stop 竞速时不让 promise 悬死
  private teardown(): void {
    window.clearTimeout(this.timer)
    this.stream?.getTracks().forEach((t) => t.stop())
    this.node?.disconnect()
    void this.ctx?.close().catch(() => undefined)
    this.stream = null
    this.node = null
    this.ctx = null
    if (this.pending) { const p = this.pending; this.pending = null; p(null) }
  }
}

// 任意采样率 → 16k：线性插值重采样（整数比场景下即等距抽样+两点加权）
export function downsample(frames: Float32Array, rate: number): Float32Array {
  if (rate === TARGET_RATE) return frames
  const ratio = rate / TARGET_RATE
  const n = Math.floor(frames.length / ratio)
  const out = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    const pos = i * ratio
    const lo = Math.floor(pos)
    const hi = Math.min(lo + 1, frames.length - 1)
    const w = pos - lo
    out[i] = frames[lo] * (1 - w) + frames[hi] * w
  }
  return out
}

// Float32 [-1,1] → int16 小端 PCM 的 base64（与 daemon 的解码约定互为镜像）
export function pcmToB64(f32: Float32Array): string {
  const i16 = new Int16Array(f32.length)
  for (let i = 0; i < f32.length; i++) {
    const v = Math.max(-1, Math.min(1, f32[i]))
    i16[i] = Math.round(v < 0 ? v * 32768 : v * 32767)
  }
  const bytes = new Uint8Array(i16.buffer)
  let bin = ''
  const CHUNK = 0x8000 // 分段拼串：一次 fromCharCode 十几万参数会爆栈
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK))
  }
  return btoa(bin)
}
