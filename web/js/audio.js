// Audio in the window: microphone capture with browser echo/noise processing, level metering,
// and sentence-by-sentence playback of the tutor's PCM (24 kHz).

export function rmsToLevel(rms) {
  if (!(rms > 0)) return 0;
  const db = 20 * Math.log10(rms);
  return Math.max(0, Math.min(100, Math.round(((db + 60) / 60) * 100)));
}

function rmsOf(analyser, buf) {
  analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

export class AudioEngine {
  constructor() {
    this.ctx = null;
    this.stream = null;
    this.micAnalyser = null;
    this.outAnalyser = null;
    this.worklet = null;
    this.chunks = [];
    this.recording = false;
    this.sources = [];
    this.nextTime = 0;
    this.playing = false;
    this.onPlaybackDrained = null;
    this._buf = new Float32Array(2048);
  }

  get sampleRate() {
    return this.ctx ? this.ctx.sampleRate : 48000;
  }

  // Must be called from a user gesture (the "start conversation" click).
  async init(constraints = {}) {
    if (!this.ctx) {
      this.ctx = new AudioContext({ sampleRate: 48000, latencyHint: 'interactive' });
      this.outAnalyser = this.ctx.createAnalyser();
      this.outAnalyser.fftSize = 2048;
      this.outGain = this.ctx.createGain();
      this.outGain.connect(this.outAnalyser);
      this.outAnalyser.connect(this.ctx.destination);
      await this.ctx.audioWorklet.addModule('/js/capture-worklet.js');
    }
    if (this.ctx.state !== 'running') await this.ctx.resume();
    const key = JSON.stringify(constraints);
    if (!this.stream || this.micKey !== key) {
      await this.openMic(constraints);
      this.micKey = key;
    }
  }

  async openMic({ deviceId = '', noiseSuppression = true, autoGainControl = true } = {}) {
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    const audio = {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression,
      autoGainControl,
    };
    if (deviceId) audio.deviceId = { exact: deviceId };
    this.stream = await navigator.mediaDevices.getUserMedia({ audio });
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.micAnalyser = this.ctx.createAnalyser();
    this.micAnalyser.fftSize = 2048;
    source.connect(this.micAnalyser);
    this.worklet = new AudioWorkletNode(this.ctx, 'capture-processor');
    this.worklet.port.onmessage = (ev) => {
      if (ev.data.samples && this.recording) this.chunks.push(ev.data.samples);
    };
    source.connect(this.worklet);
    // Keep the worklet pulled by the graph without making it audible.
    const sink = this.ctx.createGain();
    sink.gain.value = 0;
    this.worklet.connect(sink);
    sink.connect(this.ctx.destination);
  }

  micLevel() {
    return this.micAnalyser ? rmsToLevel(rmsOf(this.micAnalyser, this._buf)) : 0;
  }

  outputLevel() {
    return this.outAnalyser ? rmsToLevel(rmsOf(this.outAnalyser, this._buf)) : 0;
  }

  startRecording() {
    this.chunks = [];
    this.recording = true;
    this.worklet.port.postMessage({ cmd: 'start' });
  }

  // Returns {pcm: ArrayBuffer (s16le), sampleRate, seconds} or null when nothing was captured.
  stopRecording() {
    this.recording = false;
    if (this.worklet) this.worklet.port.postMessage({ cmd: 'stop' });
    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    if (!total) return null;
    const pcm = new Int16Array(total);
    let offset = 0;
    for (const chunk of this.chunks) {
      for (let i = 0; i < chunk.length; i++) {
        const s = Math.max(-1, Math.min(1, chunk[i]));
        pcm[offset++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
    }
    this.chunks = [];
    return { pcm: pcm.buffer, sampleRate: this.sampleRate, seconds: total / this.sampleRate };
  }

  cancelRecording() {
    this.recording = false;
    this.chunks = [];
    if (this.worklet) this.worklet.port.postMessage({ cmd: 'stop' });
  }

  // Queue one sentence (PCM s16le). Returns true when this sentence starts playback from silence.
  enqueue(arrayBuffer, sampleRate, speed = 1) {
    const int16 = new Int16Array(arrayBuffer);
    const buffer = this.ctx.createBuffer(1, int16.length, sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < int16.length; i++) data[i] = int16[i] / 32768;
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.playbackRate.value = speed;
    src.connect(this.outGain);
    const now = this.ctx.currentTime;
    const startsFresh = this.sources.length === 0;
    const at = Math.max(now + 0.02, this.nextTime);
    src.start(at);
    this.nextTime = at + buffer.duration / speed;
    this.sources.push(src);
    this.playing = true;
    src.addEventListener('ended', () => {
      if (!this.sources.includes(src)) return; // stopped by stopPlayback()
      this.sources = this.sources.filter((s) => s !== src);
      if (this.sources.length === 0) {
        this.playing = false;
        if (this.onPlaybackDrained) this.onPlaybackDrained();
      }
    });
    return startsFresh;
  }

  stopPlayback() {
    const srcs = this.sources;
    this.sources = [];
    this.playing = false;
    this.nextTime = 0;
    for (const s of srcs) {
      try {
        s.stop();
      } catch {
        // already stopped
      }
    }
  }
}
