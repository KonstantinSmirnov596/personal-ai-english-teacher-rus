// WebSocket client: JSON text frames, audio as the binary frame right after its message.

export class CoreSocket {
  constructor(onMessage, onStatus) {
    this.onMessage = onMessage;
    this.onStatus = onStatus;
    this.ws = null;
    this.pendingAudioMsg = null;
    this.closedByUs = false;
    this.retryDelay = 500;
  }

  connect() {
    const url = `ws://${location.host}/ws`;
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    ws.addEventListener('open', () => {
      this.retryDelay = 500;
      this.onStatus('open');
    });
    ws.addEventListener('message', (ev) => this.#receive(ev.data));
    ws.addEventListener('close', (ev) => {
      this.onStatus('closed', ev.code);
      if (!this.closedByUs && ev.code !== 4001) {
        setTimeout(() => this.connect(), this.retryDelay);
        this.retryDelay = Math.min(this.retryDelay * 2, 5000);
      }
    });
  }

  #receive(data) {
    if (typeof data === 'string') {
      let msg;
      try {
        msg = JSON.parse(data);
      } catch {
        return;
      }
      if (msg && msg.audio && typeof msg.audio.bytes === 'number') {
        this.pendingAudioMsg = msg;
        return;
      }
      this.onMessage(msg, null);
      return;
    }
    const msg = this.pendingAudioMsg;
    this.pendingAudioMsg = null;
    if (msg) this.onMessage(msg, data);
  }

  get open() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  send(msg, binary) {
    if (!this.open) return false;
    this.ws.send(JSON.stringify(msg));
    if (binary) this.ws.send(binary);
    return true;
  }
}
