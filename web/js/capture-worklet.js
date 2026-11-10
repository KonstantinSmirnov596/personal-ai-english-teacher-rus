// AudioWorklet: forwards microphone samples (mono Float32) to the main thread while recording.

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.recording = false;
    this.port.onmessage = (ev) => {
      if (ev.data && ev.data.cmd === 'start') this.recording = true;
      if (ev.data && ev.data.cmd === 'stop') {
        this.recording = false;
        this.port.postMessage({ done: true });
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (this.recording && input && input.length > 0) {
      const channel = input[0];
      if (channel && channel.length) this.port.postMessage({ samples: channel.slice(0) });
    }
    return true;
  }
}

registerProcessor('capture-processor', CaptureProcessor);
