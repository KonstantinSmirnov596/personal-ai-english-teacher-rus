// Auto turn mode: Silero VAD (vad-web) on the same microphone stream and AudioContext.
// Library files are served by the core from /vendor; nothing is loaded from the network.

let loading = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`failed to load ${src}`));
    document.head.append(s);
  });
}

async function loadLibraries() {
  if (!loading) {
    loading = (async () => {
      await loadScript('/vendor/ort/ort.wasm.min.js');
      window.ort.env.wasm.numThreads = 1; // no cross-origin isolation: single-threaded WASM
      window.ort.env.wasm.wasmPaths = '/vendor/ort/';
      window.ort.env.logLevel = 'error';
      await loadScript('/vendor/vad/bundle.min.js');
    })();
  }
  return loading;
}

export class AutoTurns {
  // callbacks: {onStart(), onEnd(Float32Array16k), onMisfire()}
  constructor(audio, callbacks) {
    this.audio = audio;
    this.callbacks = callbacks;
    this.vad = null;
    this.running = false;
    this.pauseMs = 1500;
  }

  async start(pauseMs) {
    this.pauseMs = pauseMs;
    await loadLibraries();
    if (!this.vad) {
      const stream = this.audio.stream;
      this.vad = await window.vad.MicVAD.new({
        model: 'v5',
        baseAssetPath: '/vendor/vad/',
        onnxWASMBasePath: '/vendor/ort/',
        audioContext: this.audio.ctx,
        getStream: async () => stream,
        pauseStream: async () => {},
        resumeStream: async () => stream,
        startOnLoad: false,
        redemptionMs: pauseMs,
        minSpeechMs: 300,
        preSpeechPadMs: 300,
        positiveSpeechThreshold: 0.5,
        negativeSpeechThreshold: 0.35,
        submitUserSpeechOnPause: false,
        onSpeechStart: () => this.callbacks.onStart(),
        onSpeechEnd: (audio) => this.callbacks.onEnd(audio),
        onVADMisfire: () => this.callbacks.onMisfire(),
      });
    } else {
      this.vad.setOptions({ redemptionMs: pauseMs });
    }
    await this.vad.start();
    this.running = true;
  }

  setPause(pauseMs) {
    this.pauseMs = pauseMs;
    if (this.vad) this.vad.setOptions({ redemptionMs: pauseMs });
  }

  async pause() {
    if (this.vad && this.running) {
      this.running = false;
      await this.vad.pause();
    }
  }

  async resume() {
    if (this.vad && !this.running) {
      await this.vad.start();
      this.running = true;
    }
  }

  async stop() {
    await this.pause();
  }
}

export function floatToPcm16(samples) {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return pcm.buffer;
}
