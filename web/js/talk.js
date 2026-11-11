// «Разговор»: start screen, feed with corrections, bottom panel with mic and waveform.

import { ru } from '../i18n/ru.js';
import { get, patch, post } from './api.js';
import { clear, el, formatClock, icons, isTextField } from './dom.js';
import { AutoTurns, floatToPcm16 } from './vad.js';
import { Wave } from './wave.js';

const RETRYABLE_SWITCH = new Set(['llm_unauthorized', 'llm_unreachable']);
const RETRY_CODES = new Set(['llm_rate_limited', 'llm_unreachable', 'llm_bad_response']);
const MIN_TURN_S = 0.3;

function uuid() {
  return crypto.randomUUID();
}

export class TalkView {
  constructor(app) {
    this.app = app;
    this.root = el('div', { class: 'talk' });
    this.feedColumn = el('div', { class: 'column', 'aria-live': 'polite' });
    this.session = null;
    this.coreState = 'idle';
    this.recording = false;
    this.recordStarted = 0;
    this.turns = new Map(); // turn_id -> {node, textNode, role, text, ...}
    this.sentAt = new Map(); // user turn_id -> performance.now() when turn.audio was sent
    this.replyFor = new Map(); // tutor turn_id -> user turn_id
    this.doneReplies = new Set();
    this.currentTutor = null;
    this.lastUserTurnId = null;
    this.scenarios = [];
    this.selectedScenario = 'free_talk';
    this.micState = 'unknown'; // unknown | ready | denied
    this.summary = null;
    this.openMenu = null;
    this.auto = new AutoTurns(app.audio, {
      onStart: () => this.onAutoStart(),
      onEnd: (audio) => this.onAutoEnd(audio),
      onMisfire: () => this.onAutoMisfire(),
    });
    this.autoTurn = false;
    this.interrupted = new Set(); // tutor turn ids stopped by the learner
    this.buildPanel();
    this.app.audio.onPlaybackDrained = () => this.onPlaybackDrained();
    document.addEventListener('keydown', (e) => this.onKeyDown(e));
    document.addEventListener('keyup', (e) => {
      if (e.code === 'Space' && this.isActive() && !isTextField(e.target)) e.preventDefault();
    });
    document.addEventListener('click', (e) => {
      if (this.openMenu && !this.openMenu.contains(e.target)) this.closeMenu();
    });
    setInterval(() => this.tick(), 250);
  }

  isActive() {
    return this.app.current === 'talk' && document.body.contains(this.root);
  }

  // --- rendering -------------------------------------------------------------------------

  render() {
    clear(this.root);
    const main = el('main', { class: 'main' }, [this.feedColumn]);
    this.root.append(main);
    if (!this.session && !this.summary) this.renderStart();
    else if (this.summary && !this.session) this.renderSummary();
    if (this.session) this.root.append(this.panel);
    return this.root;
  }

  renderHeaderCenter(center) {
    if (!this.session) return;
    this.timerNode = el('span', { class: 'session-timer', text: formatClock((performance.now() - this.session.started) / 1000) });
    center.append(el('span', { class: 'session-title', text: this.session.title }), this.timerNode);
  }

  async renderStart() {
    const col = clear(this.feedColumn);
    const wrap = el('section', { class: 'start' });
    col.append(wrap);
    if (!this.app.activeConnection) {
      wrap.append(
        el('div', { class: 'notice' }, [
          el('h2', { text: ru.talk.noConnectionTitle }),
          el('p', { text: ru.talk.noConnectionText }),
          el('div', { class: 'form-actions' }, [
            el('a', { class: 'btn btn-primary', href: '#/settings/connections', 'data-testid': 'add-connection', text: ru.talk.addConnection }),
          ]),
        ]),
      );
      return;
    }
    if (this.micState === 'denied') wrap.append(this.micDeniedNotice());
    try {
      this.scenarios = await get('/api/scenarios');
    } catch {
      this.scenarios = [];
    }
    const grid = el('div', { class: 'scenarios', role: 'group', 'aria-label': ru.talk.scenarioLabel });
    for (const s of this.scenarios) {
      const btn = el('button', { type: 'button', class: 'scenario', 'aria-pressed': String(s.id === this.selectedScenario), dataset: { scenario: s.id } }, [
        el('span', { class: 'scenario-title', text: s.title_ru }),
        el('span', { class: 'scenario-desc', text: s.description_ru }),
      ]);
      btn.addEventListener('click', () => {
        this.selectedScenario = s.id;
        for (const b of grid.querySelectorAll('.scenario')) b.setAttribute('aria-pressed', String(b === btn));
      });
      grid.append(btn);
    }
    const level = el('select', { class: 'select', id: 'level-select' });
    for (const lv of ['A2', 'B1', 'B2', 'C1']) {
      const opt = el('option', { value: lv, text: lv });
      if (this.app.settings && this.app.settings.level === lv) opt.selected = true;
      level.append(opt);
    }
    const start = el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'start-session', text: ru.talk.start });
    start.addEventListener('click', () => this.startSession(level.value, start));
    wrap.append(
      el('h1', { text: ru.talk.scenarioLabel }),
      grid,
      el('div', { class: 'start-row' }, [
        el('div', { class: 'field' }, [el('label', { for: 'level-select', text: ru.talk.levelLabel }), level]),
        start,
      ]),
      el('p', { class: 'muted', text: ru.talk.startHint }),
    );
  }

  micDeniedNotice() {
    const retry = el('button', { type: 'button', class: 'btn', text: ru.talk.micRetry });
    retry.addEventListener('click', () => this.initAudio().then(() => this.render()));
    return el('div', { class: 'notice', 'data-testid': 'mic-denied' }, [
      el('h2', { text: ru.talk.micDeniedTitle }),
      el('p', { text: ru.talk.micDeniedText }),
      el('div', { class: 'form-actions' }, [retry]),
    ]);
  }

  renderSummary() {
    const col = clear(this.feedColumn);
    const s = this.summary;
    const box = el('section', { class: 'start', 'data-testid': 'summary' }, [el('h1', { text: ru.summary.title })]);
    const items = s.corrections || [];
    if (!items.length) box.append(el('p', { class: 'muted', text: ru.summary.none }));
    else {
      const card = el('div', { class: 'card', lang: 'ru' }, [el('div', { class: 'turn-label', text: ru.summary.corrections })]);
      items.forEach((c, i) => {
        if (i) card.append(el('div', { class: 'card-sep' }));
        card.append(this.cardItem(c));
      });
      box.append(card);
    }
    const again = el('button', { type: 'button', class: 'btn btn-primary', text: ru.summary.newSession });
    again.addEventListener('click', () => {
      this.summary = null;
      this.render();
    });
    box.append(el('div', { class: 'form-actions' }, [again]));
    col.append(box);
  }

  buildPanel() {
    this.stateText = el('span', { class: 'panel-state-text', text: ru.states.idle });
    this.recTimer = el('span', { class: 'panel-timer' });
    this.hintText = el('span', { class: 'panel-hint', role: 'status' });
    this.canvas = el('canvas', { class: 'wave', role: 'img', 'aria-label': ru.panel.waveLabel, 'data-testid': 'wave', 'data-level': '0', 'data-source': 'mic' });
    this.micButton = el('button', { type: 'button', class: 'mic-button', 'data-testid': 'mic-button', 'aria-label': ru.panel.micStart }, [icons.mic()]);
    this.micButton.addEventListener('click', () => this.toggleTurn());
    this.keysNode = el('div', { class: 'keys' });
    this.manualButton = el('button', { type: 'button', 'aria-pressed': 'true', 'data-mode': 'manual', text: ru.panel.manual });
    this.autoButton = el('button', { type: 'button', 'aria-pressed': 'false', 'data-mode': 'auto', text: ru.panel.auto });
    this.manualButton.addEventListener('click', () => this.setTurnMode('manual'));
    this.autoButton.addEventListener('click', () => this.setTurnMode('auto'));
    this.modeToggle = el('div', { class: 'segmented', role: 'group', 'aria-label': ru.panel.turnMode, 'data-testid': 'mode-toggle' }, [this.manualButton, this.autoButton]);
    this.correctionChip = el('button', { type: 'button', class: 'chip-button', 'aria-haspopup': 'menu', 'data-testid': 'correction-mode' });
    this.correctionChip.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleCorrectionMenu();
    });
    this.correctionDropdown = el('div', { class: 'dropdown' }, [this.correctionChip]);
    const finish = el('button', { type: 'button', class: 'btn btn-ghost btn-small', 'data-testid': 'end-session', text: ru.panel.finish });
    finish.addEventListener('click', () => this.endSession());
    this.panel = el('footer', { class: 'panel', 'data-testid': 'panel', 'data-state': 'idle' }, [
      el('div', { class: 'panel-inner' }, [
        el('div', { class: 'panel-row' }, [el('div', { class: 'panel-state' }, [this.stateText, this.recTimer, this.hintText]), this.correctionDropdown]),
        el('div', { class: 'wave-wrap' }, [this.canvas]),
        el('div', { class: 'panel-row' }, [
          this.modeToggle,
          el('div', { class: 'mic-group' }, [this.micButton, this.keysNode]),
          el('div', { class: 'helpers' }, [finish]),
        ]),
      ]),
    ]);
    this.wave = new Wave(this.canvas, () => this.levelInfo());
    this.wave.onSilence = (silent) => {
      if (silent && !this.recording) this.setHint(ru.panel.noSignal);
      else if (!silent && this.hintText.textContent === ru.panel.noSignal) this.setHint('');
    };
    this.updatePanel();
  }

  levelInfo() {
    const audio = this.app.audio;
    if (audio.playing) return { level: audio.outputLevel(), source: 'tutor', recording: false, micOpen: !!audio.stream };
    return { level: audio.micLevel(), source: 'mic', recording: this.recording, micOpen: !!audio.stream };
  }

  updatePanel() {
    const s = this.panelState();
    this.panel.dataset.state = s;
    let text = ru.states[s] || ru.states.idle;
    if (!this.app.socketOpen) text = ru.states.offline;
    else if (!this.app.modelsReady) text = ru.states.loading;
    else if (this.micState === 'denied') text = ru.states.noMic;
    this.stateText.textContent = text;
    const canTalk = this.app.socketOpen && this.app.modelsReady && this.micState === 'ready' && !!this.session;
    this.micButton.disabled = !canTalk;
    this.micButton.classList.toggle('recording', this.recording);
    const label = this.recording ? ru.panel.micSend : s === 'speaking' || s === 'thinking' ? ru.panel.micInterrupt : ru.panel.micStart;
    this.micButton.setAttribute('aria-label', label);
    const keys = this.recording ? ru.panel.keysRecording : s === 'speaking' ? ru.panel.keysSpeaking : ru.panel.keysIdle;
    clear(this.keysNode).append(...keys.map((k) => el('span', { text: k })));
    const mode = (this.app.settings && this.app.settings.correction_mode) || 'on_click';
    clear(this.correctionChip).append(el('span', { text: `${ru.correctionModes.label}: ${ru.correctionModes[mode]}` }), icons.chevron());
    const turnMode = this.turnMode();
    this.manualButton.setAttribute('aria-pressed', String(turnMode === 'manual'));
    this.autoButton.setAttribute('aria-pressed', String(turnMode === 'auto'));
    this.panel.dataset.turnMode = turnMode;
    if (turnMode === 'auto') {
      const autoKeys = s === 'speaking' || s === 'thinking' ? ru.panel.keysAutoSpeaking : ru.panel.keysAuto;
      clear(this.keysNode).append(...autoKeys.map((k) => el('span', { text: k })));
    }
  }

  panelState() {
    if (this.recording) return 'listening';
    return this.coreState;
  }

  setHint(text) {
    this.hintText.textContent = text;
  }

  tick() {
    if (this.recording) this.recTimer.textContent = formatClock((performance.now() - this.recordStarted) / 1000);
    else this.recTimer.textContent = '';
    if (this.session && this.timerNode) this.timerNode.textContent = formatClock((performance.now() - this.session.started) / 1000);
  }

  toggleCorrectionMenu() {
    if (this.openMenu) {
      this.closeMenu();
      return;
    }
    const current = (this.app.settings && this.app.settings.correction_mode) || 'on_click';
    const menu = el('div', { class: 'menu', role: 'menu' });
    for (const mode of ['instant', 'on_click', 'end', 'off']) {
      const item = el('button', { type: 'button', role: 'menuitemradio', 'aria-checked': String(mode === current), text: ru.correctionModes[mode] });
      item.addEventListener('click', async () => {
        this.closeMenu();
        try {
          this.app.settings = await patch('/api/settings', { correction_mode: mode });
        } catch {
          return;
        }
        this.updatePanel();
        this.applyCorrectionMode();
      });
      menu.append(item);
    }
    this.correctionDropdown.append(menu);
    this.openMenu = menu;
  }

  closeMenu() {
    if (this.openMenu) this.openMenu.remove();
    this.openMenu = null;
  }

  // --- session ---------------------------------------------------------------------------

  async initAudio() {
    const s = this.app.settings || {};
    try {
      await this.app.audio.init({ deviceId: s.input_device_id || '', noiseSuppression: s.noise_suppression !== false });
      this.micState = 'ready';
    } catch (e) {
      console.warn('microphone unavailable', e && e.name);
      this.micState = 'denied';
    }
    this.updatePanel();
  }

  async startSession(level, button) {
    button.disabled = true;
    await this.initAudio(); // user gesture: AudioContext + microphone
    if (this.micState !== 'ready') {
      button.disabled = false;
      this.renderStart();
      return;
    }
    this.app.socket.send({ type: 'session.start', scenario_id: this.selectedScenario, level });
  }

  endSession() {
    if (!this.session) return;
    if (this.recording) this.cancelTurn();
    this.app.audio.stopPlayback();
    this.app.socket.send({ type: 'session.end' });
  }

  // --- turns -----------------------------------------------------------------------------

  onKeyDown(e) {
    if (!this.isActive()) return;
    if (e.code === 'Space' && !isTextField(e.target)) {
      e.preventDefault();
      if (!e.repeat) this.toggleTurn();
    } else if (e.key === 'Escape') {
      if (this.recording) this.cancelTurn();
      else this.closeCards();
      this.closeMenu();
    }
  }

  toggleTurn() {
    if (this.micButton.disabled) return;
    if (this.turnMode() === 'auto') {
      // Auto mode: the detector starts and ends turns; Space only stops the tutor's reply.
      if (this.app.audio.playing || this.coreState === 'thinking' || this.coreState === 'speaking') {
        this.stopTutor();
        this.app.socket.send({ type: 'interrupt' });
      }
      return;
    }
    if (this.recording) this.sendTurn();
    else this.startTurn();
  }

  // Stops the tutor's audio now; sentences of that reply still in flight are dropped.
  stopTutor() {
    if (this.currentTutor) this.interrupted.add(this.currentTutor);
    // A reply may already be on its way; stay silent until the learner's next turn is heard.
    this.muted = true;
    this.app.audio.stopPlayback();
  }

  // --- auto mode -------------------------------------------------------------------------

  turnMode() {
    return (this.app.settings && this.app.settings.turn_mode) || 'manual';
  }

  async setTurnMode(mode) {
    if (mode === this.turnMode()) return;
    if (this.recording && !this.autoTurn) this.cancelTurn();
    try {
      this.app.settings = await patch('/api/settings', { turn_mode: mode });
    } catch {
      return;
    }
    this.updatePanel();
    this.syncAuto();
  }

  // Keeps the detector running only when the learner may speak: in a session, mic ready,
  // and the tutor silent (or the learner wears headphones, so voice may interrupt).
  async syncAuto() {
    const s = this.app.settings || {};
    const busy = this.coreState === 'thinking' || this.coreState === 'speaking' || this.app.audio.playing;
    const wanted = this.turnMode() === 'auto' && !!this.session && this.micState === 'ready' && this.app.modelsReady;
    const listen = wanted && (!busy || s.headphones === true) && !(this.autoTurn && this.coreState === 'transcribing');
    try {
      if (listen) {
        if (!this.auto.vad) await this.auto.start(s.pause_ms || 1500);
        else {
          this.auto.setPause(s.pause_ms || 1500);
          await this.auto.resume();
        }
      } else {
        await this.auto.pause();
      }
    } catch (e) {
      console.warn('voice activity detector unavailable', e && e.message);
    }
  }

  onAutoStart() {
    if (!this.session || this.recording) return;
    const s = this.app.settings || {};
    const busy = this.coreState === 'thinking' || this.coreState === 'speaking' || this.app.audio.playing;
    if (busy) {
      if (!s.headphones) return;
      this.stopTutor(); // voice interruption: the core treats turn.begin as interrupt
    }
    const turnId = uuid();
    this.currentTurnId = turnId;
    this.recording = true;
    this.autoTurn = true;
    this.recordStarted = performance.now();
    this.app.socket.send({ type: 'turn.begin', turn_id: turnId, mode: 'auto' });
    this.addUserTurn(turnId);
    this.setHint('');
    this.updatePanel();
  }

  onAutoEnd(samples) {
    if (!this.autoTurn) return;
    const turnId = this.currentTurnId;
    this.recording = false;
    this.autoTurn = false;
    const pcm = floatToPcm16(samples);
    this.app.socket.send(
      { type: 'turn.audio', turn_id: turnId, audio: { sample_rate: 16000, format: 'pcm_s16le', bytes: pcm.byteLength } },
      pcm,
    );
    this.sentAt.set(turnId, performance.now());
    this.lastUserTurnId = turnId;
    const t = this.turns.get(turnId);
    if (t) this.setTurnPlaceholder(t, ru.talk.transcribing);
    this.updatePanel();
  }

  onAutoMisfire() {
    if (!this.autoTurn) return;
    const turnId = this.currentTurnId;
    this.recording = false;
    this.autoTurn = false;
    this.app.socket.send({ type: 'turn.cancel', turn_id: turnId });
    this.removeTurn(turnId);
    this.updatePanel();
  }

  startTurn() {
    if (!this.session) return;
    if (this.app.audio.playing || this.coreState === 'thinking' || this.coreState === 'speaking') {
      this.stopTutor();
    }
    const turnId = uuid();
    this.currentTurnId = turnId;
    this.recording = true;
    this.recordStarted = performance.now();
    this.app.audio.startRecording();
    this.app.socket.send({ type: 'turn.begin', turn_id: turnId, mode: 'manual' });
    this.addUserTurn(turnId);
    this.setHint('');
    this.updatePanel();
  }

  sendTurn() {
    const turnId = this.currentTurnId;
    const rec = this.app.audio.stopRecording();
    this.recording = false;
    if (!rec || rec.seconds < MIN_TURN_S) {
      this.app.socket.send({ type: 'turn.cancel', turn_id: turnId });
      this.removeTurn(turnId);
      this.setHint(ru.panel.tooShort);
      this.updatePanel();
      return;
    }
    this.app.socket.send(
      { type: 'turn.audio', turn_id: turnId, audio: { sample_rate: rec.sampleRate, format: 'pcm_s16le', bytes: rec.pcm.byteLength } },
      rec.pcm,
    );
    this.sentAt.set(turnId, performance.now());
    this.lastUserTurnId = turnId;
    const t = this.turns.get(turnId);
    if (t) this.setTurnPlaceholder(t, ru.talk.transcribing);
    this.updatePanel();
  }

  cancelTurn() {
    const turnId = this.currentTurnId;
    this.app.audio.cancelRecording();
    this.recording = false;
    this.autoTurn = false;
    this.app.socket.send({ type: 'turn.cancel', turn_id: turnId });
    this.removeTurn(turnId);
    this.updatePanel();
  }

  // --- feed ------------------------------------------------------------------------------

  addTurnBlock(role, turnId) {
    const label = el('div', { class: 'turn-label', text: role === 'user' ? ru.talk.you : ru.talk.tutor });
    const text = el('p', { class: 'turn-text', lang: 'en' });
    const node = el('article', { class: `turn ${role}`, 'data-testid': role === 'user' ? 'turn-user' : 'turn-tutor', 'data-turn-id': turnId }, [label, text]);
    this.feedColumn.append(node);
    const t = { node, textNode: text, role, text: '', corrections: null };
    this.turns.set(turnId, t);
    node.scrollIntoView({ block: 'end' });
    return t;
  }

  addUserTurn(turnId) {
    const t = this.addTurnBlock('user', turnId);
    this.setTurnPlaceholder(t, ru.talk.recording, true);
    return t;
  }

  setTurnPlaceholder(t, text, recordingDot = false) {
    t.textNode.classList.add('hidden');
    if (t.placeholder) t.placeholder.remove();
    t.placeholder = el('div', { class: 'recording-line' }, [recordingDot ? el('span', { class: 'dot', 'aria-hidden': 'true' }) : null, el('span', { text })]);
    t.node.append(t.placeholder);
  }

  clearPlaceholder(t) {
    if (t.placeholder) t.placeholder.remove();
    t.placeholder = null;
    t.textNode.classList.remove('hidden');
  }

  removeTurn(turnId) {
    const t = this.turns.get(turnId);
    if (t) t.node.remove();
    this.turns.delete(turnId);
  }

  tutorTurn(turnId) {
    let t = this.turns.get(turnId);
    if (!t) {
      t = this.addTurnBlock('tutor', turnId);
      const actions = el('div', { class: 'turn-actions' });
      t.node.append(actions);
      t.actions = actions;
    }
    return t;
  }

  // --- messages --------------------------------------------------------------------------

  onSocketStatus() {
    this.updatePanel();
  }

  onModelsReady() {
    this.updatePanel();
  }

  onMessage(msg, binary) {
    switch (msg.type) {
      case 'hello':
        this.coreState = msg.state;
        if (!msg.active_connection && !this.session) this.render();
        this.updatePanel();
        break;
      case 'session.started':
        this.session = { id: msg.session_id, title: msg.scenario.title_ru, started: performance.now() };
        this.summary = null;
        this.turns.clear();
        clear(this.feedColumn);
        this.app.activeConnection = msg.connection;
        this.render();
        this.app.renderHeader();
        this.syncAuto();
        break;
      case 'state':
        this.coreState = msg.value;
        this.updatePanel();
        this.syncAuto();
        break;
      case 'user.transcript':
        this.onTranscript(msg);
        break;
      case 'tutor.delta': {
        const t = this.tutorTurn(msg.turn_id);
        t.text += msg.text;
        t.textNode.textContent = t.text;
        this.currentTutor = msg.turn_id;
        if (this.lastUserTurnId && !this.replyFor.has(msg.turn_id)) this.replyFor.set(msg.turn_id, this.lastUserTurnId);
        break;
      }
      case 'tutor.sentence':
        this.onSentence(msg, binary);
        break;
      case 'tutor.done': {
        const t = this.tutorTurn(msg.turn_id);
        t.text = msg.text;
        t.textNode.textContent = msg.text;
        if (msg.interrupted) t.node.append(el('div', { class: 'turn-note', text: ru.talk.interrupted }));
        this.doneReplies.add(msg.turn_id);
        if (!this.app.audio.playing) this.onPlaybackDrained();
        break;
      }
      case 'corrections':
        this.onCorrections(msg);
        break;
      case 'timing': {
        const t = this.turns.get(msg.turn_id);
        if (t) t.node.dataset.coreFirstAudioMs = String(msg.first_audio_ms);
        break;
      }
      case 'error':
        this.onError(msg);
        break;
      case 'summary':
        this.session = null;
        this.summary = msg;
        this.app.audio.stopPlayback();
        this.auto.pause();
        this.render();
        this.app.renderHeader();
        break;
      default:
        break;
    }
  }

  onTranscript(msg) {
    this.muted = false;
    const t = this.turns.get(msg.turn_id);
    if (!t) return;
    if (!msg.text) {
      this.removeTurn(msg.turn_id);
      this.setHint(ru.talk.emptyTranscript);
      return;
    }
    this.clearPlaceholder(t);
    t.text = msg.text;
    t.textNode.textContent = msg.text;
    if (t.pendingCorrections) this.onCorrections(t.pendingCorrections);
  }

  onSentence(msg, binary) {
    const t = this.tutorTurn(msg.turn_id);
    if (!binary || !this.app.audio.ctx) return;
    if ((this.muted || this.interrupted.has(msg.turn_id)) && !msg.replay) {
      this.interrupted.add(msg.turn_id);
      return;
    }
    const startsFresh = this.app.audio.enqueue(binary, msg.audio.sample_rate);
    if (msg.index === 0 && !msg.replay) {
      const userTurn = this.replyFor.get(msg.turn_id);
      const sent = userTurn ? this.sentAt.get(userTurn) : undefined;
      const now = performance.now();
      if (sent !== undefined && startsFresh) {
        const firstAudio = Math.round(now - sent + 20);
        t.node.dataset.firstAudioMs = String(firstAudio);
        this.app.socket.send({ type: 'playback.started', turn_id: msg.turn_id, client_first_audio_ms: firstAudio });
      } else {
        this.app.socket.send({ type: 'playback.started', turn_id: msg.turn_id, client_first_audio_ms: null });
      }
    }
  }

  onPlaybackDrained() {
    this.syncAuto();
    const turnId = this.currentTutor;
    if (turnId && this.doneReplies.has(turnId)) {
      this.doneReplies.delete(turnId);
      this.app.socket.send({ type: 'playback.ended', turn_id: turnId, interrupted: false });
    }
  }

  // --- corrections -----------------------------------------------------------------------

  onCorrections(msg) {
    const t = this.turns.get(msg.turn_id);
    if (!t) return;
    if (!t.text) {
      t.pendingCorrections = msg;
      return;
    }
    t.pendingCorrections = null;
    t.corrections = msg;
    this.renderCorrections(t);
  }

  applyCorrectionMode() {
    for (const t of this.turns.values()) if (t.role === 'user' && t.corrections) this.renderCorrections(t);
  }

  renderCorrections(t) {
    const msg = t.corrections;
    const mode = (this.app.settings && this.app.settings.correction_mode) || 'on_click';
    clear(t.textNode);
    if (t.card) t.card.remove();
    t.card = null;
    if (mode === 'off' || mode === 'end' || msg.status !== 'ok' || !msg.items.length) {
      t.textNode.textContent = t.text;
      return;
    }
    const marked = msg.items.filter((c) => c.start !== null && c.end !== null).sort((a, b) => a.start - b.start);
    let pos = 0;
    for (const c of marked) {
      if (c.start < pos) continue;
      if (c.start > pos) t.textNode.append(t.text.slice(pos, c.start));
      const mark = el('span', { class: 'mark-grammar', role: 'button', tabindex: '0', 'data-testid': 'correction-mark', 'data-correction-id': c.id, 'aria-label': ru.card.markLabel(c.quote), text: t.text.slice(c.start, c.end) });
      const open = (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.showCard(t, c.id);
      };
      mark.addEventListener('click', open);
      mark.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') open(e);
      });
      t.textNode.append(mark);
      pos = c.end;
    }
    if (pos < t.text.length) t.textNode.append(t.text.slice(pos));
    t.card = this.buildCard(t, msg);
    t.node.append(t.card);
    if (mode !== 'instant') t.card.classList.add('hidden');
  }

  buildCard(t, msg) {
    const card = el('section', { class: 'card', lang: 'ru', 'aria-label': ru.card.region, 'data-testid': 'correction-card' });
    msg.items.forEach((c, i) => {
      if (i) card.append(el('div', { class: 'card-sep' }));
      card.append(this.cardItem(c));
    });
    if (msg.better) {
      card.append(
        el('div', { class: 'card-sep' }),
        el('div', { class: 'card-better' }, [el('span', { class: 'card-better-label', text: ru.card.better }), el('span', { lang: 'en', text: msg.better })]),
      );
    }
    const close = el('button', { type: 'button', class: 'link-button card-close', text: ru.card.close });
    close.addEventListener('click', () => card.classList.add('hidden'));
    card.append(close);
    return card;
  }

  cardItem(c) {
    return el('div', { class: 'card-item', 'data-correction-id': c.id }, [
      el('span', { class: 'card-quote', lang: 'en', text: c.quote }),
      el('span', { 'aria-hidden': 'true', class: 'muted', text: '→' }),
      el('span', { class: 'card-fix', lang: 'en', text: c.fix }),
      el('span', { class: 'card-note', text: c.note_ru }),
    ]);
  }

  showCard(t, correctionId) {
    if (!t.card) return;
    t.card.classList.remove('hidden');
    for (const item of t.card.querySelectorAll('.card-item')) item.classList.toggle('focus', item.dataset.correctionId === correctionId);
  }

  closeCards() {
    const mode = (this.app.settings && this.app.settings.correction_mode) || 'on_click';
    if (mode === 'instant') return;
    for (const t of this.turns.values()) if (t.card) t.card.classList.add('hidden');
  }

  // --- errors ----------------------------------------------------------------------------

  async onError(msg) {
    if (msg.code === 'bad_request') return;
    if (msg.code === 'audio_too_short') {
      if (msg.turn_id) this.removeTurn(msg.turn_id);
      this.setHint(ru.panel.tooShort);
      return;
    }
    if (msg.code === 'stt_failed' && msg.turn_id) this.removeTurn(msg.turn_id);
    const text = ru.errors[msg.code] || ru.errors.internal;
    const actions = el('div', { class: 'banner-actions' });
    const banner = el('div', { class: 'banner', role: 'alert', lang: 'ru', 'data-testid': 'error-banner', 'data-code': msg.code }, [el('span', { text }), actions]);
    const turnId = msg.turn_id;
    if (RETRY_CODES.has(msg.code) && turnId) {
      const retry = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'retry', text: ru.errors.retry });
      retry.addEventListener('click', () => {
        banner.remove();
        this.app.socket.send({ type: 'turn.retry', turn_id: turnId });
      });
      actions.append(retry);
    }
    if (msg.code === 'no_active_connection') {
      actions.append(el('a', { class: 'btn btn-small', href: '#/settings/connections', text: ru.errors.openConnections }));
    }
    if (RETRYABLE_SWITCH.has(msg.code) && turnId) {
      const rows = await this.app.refreshActiveConnection();
      for (const row of rows.filter((r) => !r.is_active)) {
        const sw = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'switch-connection', 'data-connection-id': String(row.id), text: ru.errors.switchToName(row.name) });
        sw.addEventListener('click', async () => {
          await post(`/api/connections/${row.id}/activate`);
          await this.app.refreshActiveConnection();
          banner.remove();
          this.app.socket.send({ type: 'turn.retry', turn_id: turnId });
        });
        actions.append(sw);
      }
    }
    this.feedColumn.append(banner);
    banner.scrollIntoView({ block: 'end' });
  }
}
