// «Настройки»: sound and voice, conversation, corrections, data. API connections live in
// connections.js and share the same section navigation.

import { ru } from '../i18n/ru.js';
import { api, get, patch } from './api.js';
import { clear, el } from './dom.js';

const SECTIONS = ['connections', 'sound', 'conversation', 'corrections', 'data'];

export function settingsNav(current) {
  return el('nav', { class: 'settings-nav', 'aria-label': ru.settings.title }, [
    el('div', { class: 'settings-nav-title', text: ru.settings.title }),
    ...SECTIONS.map((s) => el('a', { href: `#/settings/${s}`, 'aria-current': s === current ? 'page' : null, 'data-section': s, text: ru.settingsSections[s] })),
  ]);
}

export class SettingsView {
  constructor(app) {
    this.app = app;
    this.root = el('div', { class: 'settings' });
  }

  render(section) {
    clear(this.root);
    this.main = el('main', { class: 'settings-main' });
    this.root.append(settingsNav(section), this.main);
    this.section = section;
    this.load();
    return this.root;
  }

  async load() {
    try {
      this.settings = await get('/api/settings');
      this.app.settings = this.settings;
    } catch {
      return;
    }
    const m = clear(this.main);
    m.append(el('div', { class: 'page-head' }, [el('h1', { text: ru.settingsSections[this.section] })]));
    this.status = el('span', { class: 'muted small', role: 'status', 'data-testid': 'settings-status' });
    if (this.section === 'sound') m.append(await this.soundSection());
    else if (this.section === 'conversation') m.append(this.conversationSection());
    else if (this.section === 'corrections') m.append(this.correctionsSection());
    else if (this.section === 'data') m.append(this.dataSection());
    m.append(this.status);
  }

  async save(change) {
    try {
      this.settings = await patch('/api/settings', change);
      this.app.settings = this.settings;
      this.status.textContent = ru.saved;
      this.app.talk.updatePanel();
      this.app.talk.syncAuto();
    } catch (e) {
      this.status.textContent = e.message;
    }
  }

  field(label, control) {
    return el('div', { class: 'field' }, [el('label', { for: control.id, text: label }), control]);
  }

  checkbox(id, label, checked, onChange) {
    const box = el('input', { type: 'checkbox', id });
    box.checked = !!checked;
    box.addEventListener('change', () => onChange(box.checked));
    return el('label', { class: 'check', for: id }, [box, el('span', { text: label })]);
  }

  async soundSection() {
    const S = ru.sound;
    const s = this.settings;
    let devices = [];
    try {
      devices = await navigator.mediaDevices.enumerateDevices();
    } catch {
      devices = [];
    }
    const deviceSelect = (id, kind, value) => {
      const sel = el('select', { class: 'select', id });
      sel.append(el('option', { value: '', text: S.systemDefault }));
      for (const d of devices.filter((x) => x.kind === kind && x.deviceId && x.deviceId !== 'default')) {
        const opt = el('option', { value: d.deviceId, text: d.label || d.deviceId.slice(0, 8) });
        if (d.deviceId === value) opt.selected = true;
        sel.append(opt);
      }
      return sel;
    };
    const input = deviceSelect('input-device', 'audioinput', s.input_device_id);
    input.addEventListener('change', () => this.save({ input_device_id: input.value }));
    const output = deviceSelect('output-device', 'audiooutput', s.output_device_id);
    output.addEventListener('change', async () => {
      await this.save({ output_device_id: output.value });
      if (this.app.audio.ctx && this.app.audio.ctx.setSinkId) {
        try {
          await this.app.audio.ctx.setSinkId(output.value);
        } catch {
          // device unavailable; the system default stays in use
        }
      }
    });
    const voices = await get('/api/voices');
    const voice = el('select', { class: 'select', id: 'voice-select', 'data-testid': 'voice-select' });
    for (const v of voices) {
      const opt = el('option', { value: v.id, text: `${v.id} — ${v.gender === 'female' ? S.female : S.male}` });
      if (v.id === s.voice) opt.selected = true;
      voice.append(opt);
    }
    voice.addEventListener('change', () => this.save({ voice: voice.value }));
    const speed = el('input', { type: 'range', id: 'speed', min: '0.7', max: '1.3', step: '0.05', value: String(s.speed) });
    const speedValue = el('span', { class: 'muted', text: `×${Number(s.speed).toFixed(2)}` });
    speed.addEventListener('input', () => (speedValue.textContent = `×${Number(speed.value).toFixed(2)}`));
    speed.addEventListener('change', () => this.save({ speed: Number(speed.value) }));
    const preview = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'voice-preview', text: S.preview });
    preview.addEventListener('click', async () => {
      preview.disabled = true;
      try {
        const resp = await fetch('/api/voices/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ voice: voice.value, speed: Number(speed.value) }),
        });
        if (resp.ok) {
          const url = URL.createObjectURL(await resp.blob());
          const audio = new Audio(url);
          if (s.output_device_id && audio.setSinkId) await audio.setSinkId(s.output_device_id).catch(() => {});
          audio.addEventListener('ended', () => URL.revokeObjectURL(url));
          await audio.play();
        }
      } finally {
        preview.disabled = false;
      }
    });
    const ns = this.checkbox('noise', S.noiseSuppression, s.noise_suppression, (v) => this.save({ noise_suppression: v }));
    return el('div', { class: 'settings-group' }, [
      this.field(S.input, input),
      this.field(S.output, output),
      devices.some((d) => d.label) ? null : el('p', { class: 'muted small', text: S.devicesHint }),
      this.field(S.voice, voice),
      this.field(S.speed, el('div', { class: 'range-row' }, [speed, speedValue, preview])),
      ns,
    ]);
  }

  conversationSection() {
    const C = ru.conversationSettings;
    const s = this.settings;
    const level = el('select', { class: 'select', id: 'set-level' });
    for (const lv of ['A2', 'B1', 'B2', 'C1']) {
      const o = el('option', { value: lv, text: lv });
      if (lv === s.level) o.selected = true;
      level.append(o);
    }
    level.addEventListener('change', () => this.save({ level: level.value }));
    const mode = el('select', { class: 'select', id: 'set-mode' });
    for (const m of ['manual', 'auto']) {
      const o = el('option', { value: m, text: ru.panel[m] });
      if (m === s.turn_mode) o.selected = true;
      mode.append(o);
    }
    mode.addEventListener('change', () => this.save({ turn_mode: mode.value }));
    const pause = el('input', { type: 'range', id: 'set-pause', min: '1000', max: '3000', step: '100', value: String(s.pause_ms), 'data-testid': 'pause-slider' });
    const pauseValue = el('span', { class: 'muted', text: C.pauseValue((s.pause_ms / 1000).toFixed(1)) });
    pause.addEventListener('input', () => (pauseValue.textContent = C.pauseValue((Number(pause.value) / 1000).toFixed(1))));
    pause.addEventListener('change', () => this.save({ pause_ms: Number(pause.value) }));
    return el('div', { class: 'settings-group' }, [
      this.field(C.level, level),
      this.field(C.mode, mode),
      this.field(C.pause, el('div', { class: 'range-row' }, [pause, pauseValue])),
      this.checkbox('set-recast', C.recast, s.recast, (v) => this.save({ recast: v })),
      this.checkbox('set-headphones', C.headphones, s.headphones, (v) => this.save({ headphones: v })),
    ]);
  }

  correctionsSection() {
    const C = ru.correctionSettings;
    const list = el('div', { class: 'radio-list', role: 'radiogroup', 'aria-label': C.mode });
    for (const m of ['instant', 'on_click', 'end', 'off']) {
      const r = el('input', { type: 'radio', name: 'corr-mode', id: `corr-${m}`, value: m });
      r.checked = this.settings.correction_mode === m;
      r.addEventListener('change', () => this.save({ correction_mode: m }));
      list.append(el('label', { class: 'check', for: `corr-${m}` }, [r, el('span', { text: C[m] })]));
    }
    return el('div', { class: 'settings-group' }, [list]);
  }

  dataSection() {
    const D = ru.dataSettings;
    const retention = el('input', { type: 'number', class: 'input', id: 'set-retention', min: '0', max: '3650', value: String(this.settings.audio_retention_days) });
    retention.addEventListener('change', () => this.save({ audio_retention_days: Math.max(0, Number(retention.value) || 0) }));
    const includeKeys = el('input', { type: 'checkbox', id: 'include-keys' });
    const del = el('button', { type: 'button', class: 'btn danger', 'data-testid': 'delete-all', text: D.deleteAll });
    del.addEventListener('click', async () => {
      if (!window.confirm(D.deleteConfirm)) return;
      try {
        await api('DELETE', '/api/data', { confirm: 'DELETE', include_keys: includeKeys.checked });
        this.status.textContent = D.deleted;
        await this.app.refreshActiveConnection();
      } catch (e) {
        this.status.textContent = e.message;
      }
    });
    return el('div', { class: 'settings-group' }, [
      this.field(D.retention, retention),
      el('p', { class: 'muted small', text: D.retentionHint }),
      el('div', { class: 'form-actions' }, [
        el('span', { class: 'strong', text: D.export }),
        el('a', { class: 'btn btn-small', href: '/api/export?format=json', download: '', text: 'JSON' }),
        el('a', { class: 'btn btn-small', href: '/api/export?format=csv', download: '', text: 'CSV' }),
      ]),
      el('p', { class: 'muted small', text: D.deleteHint }),
      el('label', { class: 'check', for: 'include-keys' }, [includeKeys, el('span', { text: D.includeKeys })]),
      el('div', { class: 'form-actions' }, [del]),
    ]);
  }
}
