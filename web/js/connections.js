// Settings → «API-подключения»: table of connections, one active row, add / edit form.

import { ru } from '../i18n/ru.js';
import { del, get, patch, post } from './api.js';
import { clear, el, icons } from './dom.js';

const R = ru.connections;

export class ConnectionsView {
  constructor(app) {
    this.app = app;
    this.root = el('div', { class: 'settings' });
    this.rows = [];
    this.templates = [];
    this.editing = null; // null | 'new' | connection id
    this.testState = new Map(); // id -> {busy}
  }

  render() {
    clear(this.root);
    const nav = el('nav', { class: 'settings-nav', 'aria-label': ru.settings.title }, [
      el('div', { class: 'settings-nav-title', text: ru.settings.title }),
      el('a', { href: '#/settings/connections', 'aria-current': 'page', text: ru.settings.connections }),
    ]);
    this.main = el('main', { class: 'settings-main' });
    this.root.append(nav, this.main);
    this.load();
    return this.root;
  }

  async load() {
    try {
      [this.rows, this.templates] = await Promise.all([get('/api/connections'), get('/api/connections/templates')]);
    } catch {
      this.rows = [];
    }
    if (!this.rows.length && this.editing === null) this.editing = 'new';
    this.draw();
    await this.app.refreshActiveConnection();
  }

  draw() {
    const m = clear(this.main);
    m.append(el('div', { class: 'page-head' }, [el('h1', { text: R.title }), el('p', { text: R.subtitle })]));
    if (this.rows.length) m.append(this.table());
    else m.append(el('p', { class: 'muted', text: R.empty }));
    if (this.editing !== null) m.append(this.form());
    const add = el('button', { type: 'button', class: 'btn btn-dashed', 'data-testid': 'conn-add' }, [icons.plus(), el('span', { text: R.add })]);
    add.addEventListener('click', () => {
      this.editing = 'new';
      this.draw();
      const name = this.main.querySelector('#conn-name');
      if (name) name.focus();
    });
    m.append(el('div', { class: 'foot-row' }, [add, el('p', { text: R.footnote })]));
  }

  table() {
    const head = el('tr', {}, [
      el('th', { scope: 'col', text: R.colActive }),
      el('th', { scope: 'col', text: R.colName }),
      el('th', { scope: 'col', text: R.colUrl }),
      el('th', { scope: 'col', text: R.colModel }),
      el('th', { scope: 'col', text: R.colKey }),
      el('th', { scope: 'col', text: R.colTest }),
      el('th', { scope: 'col' }, [el('span', { class: 'sr-only', text: R.colActions })]),
    ]);
    const body = el('tbody');
    for (const row of this.rows) body.append(this.row(row));
    return el('div', { class: 'table-wrap' }, [el('table', { class: 'table' }, [el('thead', {}, [head]), body])]);
  }

  row(row) {
    const radio = el('button', {
      type: 'button',
      class: 'radio',
      'aria-pressed': String(row.is_active),
      'aria-label': row.is_active ? R.activeLabel : R.makeActive(row.name),
      'data-testid': 'conn-activate',
    });
    radio.addEventListener('click', async () => {
      if (row.is_active) return;
      await post(`/api/connections/${row.id}/activate`);
      await this.load();
    });
    const name = el('div', { class: 'conn-name' }, [el('span', { text: row.name }), row.is_active ? el('span', { class: 'conn-active-label', text: R.active }) : null]);
    const test = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'conn-test', text: this.testState.get(row.id) ? R.testing : R.test });
    test.disabled = !!this.testState.get(row.id);
    test.addEventListener('click', async () => {
      this.testState.set(row.id, true);
      this.draw();
      try {
        await post(`/api/connections/${row.id}/test`);
      } finally {
        this.testState.delete(row.id);
        await this.load();
      }
    });
    const edit = el('button', { type: 'button', class: 'btn btn-small btn-ghost', 'data-testid': 'conn-edit', text: R.edit });
    edit.addEventListener('click', () => {
      this.editing = row.id;
      this.draw();
    });
    const remove = el('button', { type: 'button', class: 'btn btn-small btn-ghost', 'data-testid': 'conn-delete', text: R.delete, disabled: row.is_active });
    remove.addEventListener('click', async () => {
      if (!window.confirm(R.deleteConfirm(row.name))) return;
      await del(`/api/connections/${row.id}`);
      await this.load();
    });
    return el('tr', { 'data-testid': 'conn-row', 'data-connection-id': String(row.id), 'data-active': String(row.is_active) }, [
      el('td', {}, [radio]),
      el('td', {}, [name]),
      el('td', { class: 'mono', text: row.base_url }),
      el('td', { class: 'mono', text: row.model }),
      el('td', { class: 'mono muted', 'data-testid': 'conn-key-last4', text: `••••${row.key_last4}` }),
      el('td', {}, [this.testCell(row)]),
      el('td', {}, [el('div', { class: 'row-actions' }, [test, edit, remove])]),
    ]);
  }

  testCell(row) {
    const t = row.last_test;
    if (!t) return el('span', { class: 'muted', text: '—' });
    if (t.ok) return el('span', { class: 'test-ok', 'data-testid': 'conn-test-result' }, [icons.check(), el('span', { text: R.latency(t.latency_ms) })]);
    return el('span', { class: 'test-bad', 'data-testid': 'conn-test-result' }, [icons.cross(), el('span', { text: t.message_ru })]);
  }

  form() {
    const existing = this.editing === 'new' ? null : this.rows.find((r) => r.id === this.editing);
    const input = (id, attrs = {}) => el('input', { id, class: 'input', type: 'text', autocomplete: 'off', spellcheck: 'false', ...attrs });
    const name = input('conn-name', { placeholder: R.namePlaceholder, value: existing ? existing.name : '' });
    const template = el('select', { id: 'conn-template', class: 'select' });
    for (const tpl of this.templates) template.append(el('option', { value: tpl.id, text: tpl.name_ru }));
    const url = input('conn-url', { class: 'input mono', type: 'url', placeholder: (this.templates[0] && this.templates[0].base_url) || '', value: existing ? existing.base_url : '' });
    const model = input('conn-model', { class: 'input mono', placeholder: 'deepseek-flash', value: existing ? existing.model : '' });
    const key = input('conn-key', { class: 'input mono', type: 'password', placeholder: existing ? R.keyKeep : R.keyPlaceholder, 'data-testid': 'conn-key-input' });
    const extra = input('conn-extra', { class: 'input mono', placeholder: '{ }', value: existing ? JSON.stringify(existing.extra_params) : '' });
    const applyTemplate = () => {
      const tpl = this.templates.find((x) => x.id === template.value);
      if (!tpl) return;
      url.value = tpl.base_url;
      model.value = tpl.model;
      extra.value = Object.keys(tpl.extra_params).length ? JSON.stringify(tpl.extra_params) : '';
      if (!name.value) name.value = tpl.name_ru;
    };
    template.addEventListener('change', applyTemplate);
    if (!existing) applyTemplate();
    const status = el('span', { class: 'form-error', role: 'status', 'data-testid': 'conn-form-status' });
    const collect = (requireKey) => {
      let extraParams = {};
      if (extra.value.trim()) {
        try {
          extraParams = JSON.parse(extra.value);
        } catch {
          throw new Error(R.extraInvalid);
        }
        if (typeof extraParams !== 'object' || Array.isArray(extraParams) || extraParams === null) throw new Error(R.extraInvalid);
      }
      if (!name.value.trim() || !url.value.trim() || !model.value.trim() || (requireKey && !key.value.trim())) throw new Error(R.required);
      return { name: name.value.trim(), base_url: url.value.trim(), model: model.value.trim(), api_key: key.value.trim(), extra_params: extraParams };
    };
    const save = el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'conn-save', text: R.save });
    save.addEventListener('click', async () => {
      try {
        const body = collect(!existing);
        if (existing) {
          if (!body.api_key) delete body.api_key;
          await patch(`/api/connections/${existing.id}`, body);
        } else {
          await post('/api/connections', body);
        }
        key.value = '';
        this.editing = null;
        await this.load();
      } catch (e) {
        status.textContent = e.message;
      }
    });
    const test = el('button', { type: 'button', class: 'btn', 'data-testid': 'conn-form-test', text: R.test });
    test.addEventListener('click', async () => {
      try {
        const body = collect(!existing);
        status.textContent = R.testing;
        let res;
        if (existing && !body.api_key) res = await post(`/api/connections/${existing.id}/test`);
        else res = await post('/api/connections/test', body);
        status.textContent = res.ok ? `✓ ${R.latency(res.latency_ms)}` : res.message_ru;
      } catch (e) {
        status.textContent = e.message;
      }
    });
    const cancel = el('button', { type: 'button', class: 'btn btn-ghost', text: R.cancel });
    cancel.addEventListener('click', () => {
      this.editing = null;
      this.draw();
    });
    const field = (label, control) => el('div', { class: 'field' }, [el('label', { for: control.id, text: label }), control]);
    const fields = [field(R.name, name)];
    if (!existing) fields.push(field(R.template, template));
    fields.push(field(R.url, url), field(R.model, model), field(R.key, key), field(R.extra, extra));
    return el('section', { class: 'form-card', 'aria-label': existing ? R.editTitle : R.newTitle, 'data-testid': 'conn-form' }, [
      el('h2', { text: existing ? R.editTitle : R.newTitle }),
      el('div', { class: 'form-grid' }, fields),
      el('div', { class: 'form-actions' }, [save, test, cancel, status]),
    ]);
  }
}
