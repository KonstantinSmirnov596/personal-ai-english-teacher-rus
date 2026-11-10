// Waveform: 48 bars of RMS level (~50 ms each), newest on the right.
// Exposes the current level and source through data-level / data-source for tests.

const BARS = 48;
const SAMPLE_MS = 50;

export class Wave {
  constructor(canvas, getLevel) {
    this.canvas = canvas;
    this.getLevel = getLevel; // () => {level: 0..100, source: 'mic'|'tutor', recording: bool}
    this.values = new Array(BARS).fill(0);
    this.color = 'faint';
    this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.colors = {};
    this.lowSince = performance.now();
    this.onSilence = null; // (silent: boolean) => void
    this.silent = false;
    this.timer = setInterval(() => this.sample(), SAMPLE_MS);
    this.draw = this.draw.bind(this);
    requestAnimationFrame(this.draw);
  }

  readColors() {
    const css = getComputedStyle(document.documentElement);
    for (const name of ['faint', 'accent', 'muted']) {
      this.colors[name] = css.getPropertyValue(`--${name}`).trim();
    }
  }

  sample() {
    const { level, source, recording, micOpen } = this.getLevel();
    this.values.push(level);
    if (this.values.length > BARS) this.values.shift();
    this.canvas.dataset.level = String(level);
    this.canvas.dataset.source = source;
    this.color = source === 'tutor' ? 'muted' : recording ? 'accent' : 'faint';
    if (micOpen && source === 'mic') {
      const now = performance.now();
      if (level >= 5) this.lowSince = now;
      const silent = now - this.lowSince > 5000;
      if (silent !== this.silent) {
        this.silent = silent;
        if (this.onSilence) this.onSilence(silent);
      }
    }
  }

  draw() {
    if (!this.colors.faint) this.readColors();
    const c = this.canvas;
    const ratio = window.devicePixelRatio || 1;
    const w = c.clientWidth;
    const h = c.clientHeight;
    if (c.width !== Math.round(w * ratio) || c.height !== Math.round(h * ratio)) {
      c.width = Math.round(w * ratio);
      c.height = Math.round(h * ratio);
    }
    const g = c.getContext('2d');
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    g.clearRect(0, 0, w, h);
    g.fillStyle = this.colors[this.color] || this.colors.faint;
    const step = w / BARS;
    const barW = Math.max(2, Math.min(4, step * 0.45));
    for (let i = 0; i < BARS; i++) {
      const v = this.values[i] / 100;
      const bh = Math.max(2, v * h);
      const x = i * step + (step - barW) / 2;
      const y = (h - bh) / 2;
      g.beginPath();
      g.roundRect(x, y, barW, bh, barW / 2);
      g.fill();
    }
    if (this.reduced) setTimeout(() => requestAnimationFrame(this.draw), 100);
    else requestAnimationFrame(this.draw);
  }
}
