// «Чтение»: read a phrase aloud, get every sound marked (верно / неточно / ошибка),
// open a word to see what was heard instead and how to fix it.

import { ru } from '../i18n/ru.js';
import { ApiError, get, post } from './api.js';
import { clear, el, icons, isTextField } from './dom.js';
import { Wave } from './wave.js';

const R = ru.reading;
const LEVEL_CLASS = { error: 'mark-pron-error', approx: 'mark-pron-approx', ok: 'read-word-ok' };

export function wavBlob(pcm, sampleRate) {
  const header = new DataView(new ArrayBuffer(44));
  const text = (offset, s) => [...s].forEach((ch, i) => header.setUint8(offset + i, ch.charCodeAt(0)));
  text(0, 'RIFF');
  header.setUint32(4, 36 + pcm.byteLength, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  header.setUint32(16, 16, true);
  header.setUint16(20, 1, true); // PCM
  header.setUint16(22, 1, true); // mono
  header.setUint32(24, sampleRate, true);
  header.setUint32(28, sampleRate * 2, true);
  header.setUint16(32, 2, true);
  header.setUint16(34, 16, true);
  text(36, 'data');
  header.setUint32(40, pcm.byteLength, true);
  return new Blob([header, pcm], { type: 'audio/wav' });
}

async function postAudio(path, body, contentType) {
  const init = { method: 'POST', body, credentials: 'same-origin' };
  if (contentType) init.headers = { 'Content-Type': contentType };
  const resp = await fetch(path, init);
  const data = await resp.json().catch(() => null);
  if (!resp.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(resp.status, err.code || 'internal', err.message_ru || '');
  }
  return data;
}

export class ReadingView {
  constructor(app) {
    this.app = app;
    this.root = el('div', { class: 'talk reading' });
    this.phrases = [];
    this.index = 0;
    this.result = null; // assessment of the current phrase
    this.selected = null; // index into result.words
    this.recording = null; // null | 'phrase' | 'word'
    this.busy = false;
    this.lastBlob = null;
    this.player = null;
    this.micReady = false;
    this.message = '';
    this.build();
    document.addEventListener('keydown', (e) => {
      if (!this.isActive() || isTextField(e.target)) return;
      if (e.code === 'Space') {
        e.preventDefault();
        if (!e.repeat) this.toggle('phrase');
      } else if (e.key === 'Escape' && this.recording) {
        this.app.audio.cancelRecording();
        this.recording = null;
        this.update();
      }
    });
  }

  isActive() {
    return this.app.current === 'reading' && document.body.contains(this.root);
  }

  build() {
    this.phraseBox = el('section', { class: 'read-phrase', 'aria-label': R.phrase });
    this.detailBox = el('div');
    this.sidebar = el('aside', { class: 'read-aside', 'aria-label': R.yourSounds });
    this.stateText = el('span', { class: 'panel-state-text' });
    this.hint = el('span', { class: 'panel-hint', role: 'status', 'data-testid': 'reading-message' });
    this.canvas = el('canvas', { class: 'wave', role: 'img', 'aria-label': ru.panel.waveLabel, 'data-testid': 'wave', 'data-level': '0', 'data-source': 'mic' });
    this.micButton = el('button', { type: 'button', class: 'mic-button', 'data-testid': 'reading-mic', 'aria-label': R.record }, [icons.mic()]);
    this.micButton.addEventListener('click', () => this.toggle('phrase'));
    this.keys = el('div', { class: 'keys' });
    const prev = el('button', { type: 'button', class: 'btn btn-small', text: R.prev });
    prev.addEventListener('click', () => this.go(-1));
    const next = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'reading-next', text: R.next });
    next.addEventListener('click', () => this.go(1));
    this.counter = el('span', { class: 'muted small' });
    this.panel = el('footer', { class: 'panel', 'data-testid': 'reading-panel', 'data-state': 'idle' }, [
      el('div', { class: 'panel-inner' }, [
        el('div', { class: 'panel-row' }, [el('div', { class: 'panel-state' }, [this.stateText, this.hint])]),
        el('div', { class: 'wave-wrap' }, [this.canvas]),
        el('div', { class: 'panel-row' }, [
          el('div', { class: 'mic-group' }, [this.micButton, this.keys]),
          el('div', { class: 'helpers' }, [this.counter, prev, next]),
        ]),
      ]),
    ]);
    this.wave = new Wave(this.canvas, () => ({
      level: this.app.audio.micLevel(),
      source: 'mic',
      recording: !!this.recording,
      micOpen: !!this.app.audio.stream,
    }));
  }

  render() {
    clear(this.root);
    const grid = el('div', { class: 'read-grid' }, [el('main', { class: 'read-main' }, [this.phraseBox, this.detailBox]), this.sidebar]);
    this.root.append(el('div', { class: 'main' }, [grid]), this.panel);
    this.load();
    return this.root;
  }

  async load() {
    if (!this.phrases.length) {
      try {
        this.phrases = await get('/api/pron/phrases');
      } catch {
        this.phrases = [];
      }
    }
    this.update();
    this.loadSounds();
  }

  async loadSounds() {
    let sounds = [];
    try {
      sounds = await get('/api/pron/sounds?days=7');
    } catch {
      sounds = [];
    }
    const box = clear(this.sidebar);
    box.append(el('div', { class: 'turn-label', text: R.yourSounds }));
    if (!sounds.length) {
      box.append(el('p', { class: 'muted small', text: R.noSounds }));
      return;
    }
    box.append(
      el(
        'ul',
        { class: 'summary-list' },
        sounds.slice(0, 8).map((s) =>
          el('li', { class: 'summary-li row', 'data-testid': 'sound-row', 'data-phone': s.phone }, [
            el('span', { class: 'row-main' }, [el('span', { class: 'ipa strong', text: `/${s.phone}/` }), el('span', { lang: 'en', class: 'muted', text: s.example })]),
            el('span', { class: 'small muted', text: R.count(s.errors + s.approx, s.attempts) }),
          ]),
        ),
      ),
      el('p', { class: 'muted small', text: R.countHint }),
    );
  }

  get phrase() {
    return this.phrases[this.index] || null;
  }

  go(delta) {
    if (!this.phrases.length || this.recording) return;
    this.index = (this.index + delta + this.phrases.length) % this.phrases.length;
    this.result = null;
    this.selected = null;
    this.lastBlob = null;
    this.message = '';
    this.update();
  }

  // --- rendering -------------------------------------------------------------------------

  update() {
    const state = this.recording ? 'listening' : this.busy ? 'assessing' : 'idle';
    this.panel.dataset.state = state;
    this.stateText.textContent = this.recording ? ru.states.listening : this.busy ? R.assessing : this.micReady ? ru.states.idle : R.micOff;
    this.hint.textContent = this.message;
    this.micButton.classList.toggle('recording', this.recording === 'phrase');
    this.micButton.disabled = this.busy || this.recording === 'word' || !this.phrase;
    this.micButton.setAttribute('aria-label', this.recording === 'phrase' ? R.stop : R.record);
    clear(this.keys).append(el('span', { text: this.recording === 'phrase' ? R.keyStop : R.keyRecord }));
    this.counter.textContent = this.phrases.length ? `${this.index + 1} / ${this.phrases.length}` : '';
    this.renderPhrase();
    this.renderDetail();
  }

  renderPhrase() {
    const box = clear(this.phraseBox);
    const phrase = this.phrase;
    if (!phrase) return;
    box.append(el('div', { class: 'turn-label', text: R.readAloud }));
    const p = el('p', { class: 'read-text', lang: 'en', 'data-testid': 'reading-text', 'data-phrase-id': phrase.id });
    const byIndex = new Map();
    if (this.result) this.result.words.forEach((w, i) => byIndex.set(w.source_index, i));
    phrase.text.split(/\s+/).forEach((token, i) => {
      if (i) p.append(' ');
      const wi = byIndex.get(i);
      if (wi === undefined) {
        p.append(token);
        return;
      }
      const word = this.result.words[wi];
      const btn = el('button', {
        type: 'button',
        class: `read-word ${LEVEL_CLASS[word.level]}`,
        'aria-pressed': String(this.selected === wi),
        'aria-label': R.wordLabel(word.w, ru.levels[word.level]),
        'data-testid': 'read-word',
        'data-level': word.level,
        text: token,
      });
      btn.addEventListener('click', () => {
        this.selected = wi;
        this.update();
      });
      p.append(btn);
    });
    box.append(p);
    if (phrase.targets && phrase.targets.length && !this.result) {
      box.append(el('p', { class: 'muted small', text: R.targets(phrase.targets.map((t) => t.title_ru).join('; ')) }));
    }
    const legend = el('div', { class: 'read-legend' }, [
      el('span', {}, [el('span', { class: 'legend-swatch mark-pron-error', 'aria-hidden': 'true', text: 'a' }), ` ${ru.levels.error}`]),
      el('span', {}, [el('span', { class: 'legend-swatch mark-pron-approx', 'aria-hidden': 'true', text: 'a' }), ` ${ru.levels.approx}`]),
      el('span', { class: 'muted', text: R.legendOk }),
    ]);
    const reference = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'reading-reference', text: R.reference });
    reference.addEventListener('click', () => this.play(`/api/pron/reference?phrase_id=${encodeURIComponent(phrase.id)}`));
    const slow = el('button', { type: 'button', class: 'btn btn-small', text: R.referenceSlow });
    slow.addEventListener('click', () => this.play(`/api/pron/reference?phrase_id=${encodeURIComponent(phrase.id)}&speed=0.8`));
    const mine = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'reading-mine', text: R.mine, disabled: !this.lastBlob });
    mine.addEventListener('click', () => this.lastBlob && this.play(URL.createObjectURL(this.lastBlob), true));
    box.append(el('div', { class: 'read-row' }, [legend, el('div', { class: 'form-actions' }, [reference, slow, mine])]));
    if (this.result) {
      box.append(el('p', { class: 'muted small', 'data-testid': 'reading-score', text: R.score(Math.round(this.result.score * 100)) }));
    }
  }

  renderDetail() {
    const box = clear(this.detailBox);
    if (!this.result || this.selected === null) return;
    const word = this.result.words[this.selected];
    const section = el('section', { class: 'card read-detail', lang: 'ru', 'aria-label': R.detailLabel(word.w), 'data-testid': 'word-detail' });
    section.append(el('div', { class: 'read-detail-head' }, [el('h2', { lang: 'en', text: word.w }), el('span', { class: 'ipa muted', text: `/${word.ipa}/` })]));
    section.append(
      el(
        'div',
        { class: 'phone-row' },
        word.phones.map((ph) =>
          el('span', { class: `phone-chip level-${ph.level}`, 'data-level': ph.level }, [
            el('span', { class: 'ipa phone-symbol', text: ph.expected }),
            el('span', { class: 'small', text: ru.levels[ph.level] }),
          ]),
        ),
      ),
    );
    const tip = word.tip;
    if (tip) {
      const title = tip.heard ? R.heardInstead(tip.heard, tip.expected) : R.notHeard(tip.expected);
      const body = el('p', { class: 'read-tip-text' });
      if (tip.tip_ru) body.append(`${tip.tip_ru} `);
      if (tip.pair) body.append(R.compare, el('span', { lang: 'en', text: `${tip.pair[0]} — ${tip.pair[1]}` }), '.');
      section.append(el('div', { class: 'read-tip' }, [el('p', { class: 'strong', 'data-testid': 'word-tip-title', text: title }), body]));
    } else {
      section.append(el('p', { class: 'muted', text: R.wordOk }));
    }
    const repeat = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'word-repeat', text: this.recording === 'word' ? R.stop : R.repeatWord });
    repeat.disabled = this.busy || this.recording === 'phrase';
    repeat.addEventListener('click', () => this.toggle('word'));
    const listen = el('button', { type: 'button', class: 'btn btn-small', text: R.listenWord });
    listen.addEventListener('click', () => this.play(`/api/pron/reference?text=${encodeURIComponent(word.w)}`));
    const actions = [repeat, listen];
    if (tip && word.level !== 'ok') {
      const correct = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'was-correct', text: R.wasCorrect });
      correct.addEventListener('click', () => this.wasCorrect(tip.phone_index));
      actions.push(correct);
    }
    section.append(el('div', { class: 'form-actions' }, actions));
    if (this.wordResult && this.wordResult.for === this.selected) {
      const w = this.wordResult.word;
      section.append(el('p', { class: 'small', 'data-testid': 'word-repeat-result', text: R.repeatResult(ru.levels[w.level], Math.round(w.score * 100)) }));
    }
    box.append(section);
  }

  // --- actions ---------------------------------------------------------------------------

  async play(url, revoke = false) {
    if (this.player) this.player.pause();
    const audio = new Audio(url);
    this.player = audio;
    const sink = this.app.settings && this.app.settings.output_device_id;
    if (sink && audio.setSinkId) await audio.setSinkId(sink).catch(() => {});
    if (revoke) audio.addEventListener('ended', () => URL.revokeObjectURL(url));
    audio.play().catch(() => {});
  }

  async openMic() {
    const s = this.app.settings || {};
    const raw = s.reading_raw_mic === true;
    try {
      await this.app.audio.init({
        deviceId: s.input_device_id || '',
        noiseSuppression: raw ? false : s.noise_suppression !== false,
        autoGainControl: !raw,
      });
      this.micReady = true;
    } catch (e) {
      console.warn('microphone unavailable', e && e.name);
      this.micReady = false;
      this.message = ru.talk.micDeniedTitle;
    }
    return this.micReady;
  }

  async toggle(kind) {
    if (this.busy || !this.phrase) return;
    if (this.recording && this.recording !== kind) return;
    if (!this.recording) {
      if (!(await this.openMic())) {
        this.update();
        return;
      }
      if (this.player) this.player.pause();
      this.recording = kind;
      this.message = '';
      this.app.audio.startRecording();
      this.update();
      return;
    }
    this.recording = null;
    const rec = this.app.audio.stopRecording();
    if (!rec || rec.seconds < 0.3) {
      this.message = ru.panel.tooShort;
      this.update();
      return;
    }
    const blob = wavBlob(rec.pcm, rec.sampleRate);
    this.busy = true;
    this.update();
    try {
      if (kind === 'phrase') {
        this.lastBlob = blob;
        this.result = await postAudio(`/api/pron/assess?phrase_id=${encodeURIComponent(this.phrase.id)}`, blob, 'audio/wav');
        this.wordResult = null;
        const firstBad = this.result.words.findIndex((w) => w.level === 'error');
        const firstApprox = this.result.words.findIndex((w) => w.level === 'approx');
        this.selected = firstBad >= 0 ? firstBad : firstApprox >= 0 ? firstApprox : null;
        this.message = this.selected === null ? R.allGood : '';
      } else {
        const word = this.result.words[this.selected];
        const form = new FormData();
        form.append('text', word.w);
        form.append('audio', blob, 'word.wav');
        const res = await postAudio('/api/pron/assess-text', form);
        this.wordResult = { for: this.selected, word: res.words[0] };
      }
    } catch (e) {
      this.message = ru.errors[e.code] || e.message || ru.errors.internal;
    }
    this.busy = false;
    this.update();
    this.loadSounds();
  }

  async wasCorrect(phoneIndex) {
    if (!this.result || this.selected === null) return;
    try {
      const res = await post('/api/pron/feedback', {
        attempt_id: this.result.attempt_id,
        word_index: this.selected,
        phone_index: phoneIndex,
        verdict: 'was_correct',
      });
      this.result = res.attempt;
      this.message = R.feedbackSaved(res.phone);
    } catch (e) {
      this.message = ru.errors[e.code] || ru.errors.internal;
    }
    this.update();
    this.loadSounds();
  }
}
