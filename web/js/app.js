// Window shell: header, hash router, shared state, WebSocket to the core.

import { ru } from '../i18n/ru.js';
import { get } from './api.js';
import { AudioEngine } from './audio.js';
import { ConnectionsView } from './connections.js';
import { clear, el, icons } from './dom.js';
import { HistoryView } from './history.js';
import { TalkView } from './talk.js';
import { CoreSocket } from './ws.js';

class App {
  constructor(root) {
    this.root = root;
    this.audio = new AudioEngine();
    this.hello = null;
    this.settings = null;
    this.activeConnection = null;
    this.modelsReady = false;
    this.socketOpen = false;
    this.listeners = new Set();
    this.socket = new CoreSocket(
      (msg, bin) => this.onMessage(msg, bin),
      (status, code) => this.onSocketStatus(status, code),
    );
    this.talk = new TalkView(this);
    this.connections = new ConnectionsView(this);
    this.history = new HistoryView(this);
    this.header = el('header', { class: 'header' });
    this.body = el('div', { class: 'app-body' });
    root.append(this.header, this.body);
    window.addEventListener('hashchange', () => this.route());
  }

  async start() {
    document.title = ru.appTitle;
    try {
      this.settings = await get('/api/settings');
    } catch {
      this.settings = null;
    }
    await this.refreshActiveConnection();
    this.socket.connect();
    this.route();
  }

  async refreshActiveConnection() {
    try {
      const rows = await get('/api/connections');
      const active = rows.find((r) => r.is_active);
      this.activeConnection = active ? { id: active.id, name: active.name, model: active.model } : null;
      this.connectionRows = rows;
    } catch {
      this.activeConnection = null;
      this.connectionRows = [];
    }
    this.renderHeader();
    return this.connectionRows;
  }

  onSocketStatus(status, code) {
    this.socketOpen = status === 'open';
    this.talk.onSocketStatus(status, code);
  }

  onMessage(msg, binary) {
    if (msg.type === 'hello') {
      this.hello = msg;
      this.settings = msg.settings;
      this.activeConnection = msg.active_connection;
      this.modelsReady = msg.models_ready;
      this.renderHeader();
      if (!this.modelsReady) this.pollModels();
    }
    this.talk.onMessage(msg, binary);
  }

  async pollModels() {
    while (!this.modelsReady) {
      await new Promise((r) => setTimeout(r, 1000));
      try {
        const h = await get('/api/health');
        if (h.status === 'ok') {
          this.modelsReady = true;
          this.talk.onModelsReady();
        }
      } catch {
        // core restarting; keep polling
      }
    }
  }

  route() {
    const hash = location.hash || '#/talk';
    const [, section, sub] = hash.split('/');
    clear(this.body);
    let current = 'talk';
    if (section === 'settings') {
      current = 'settings';
      this.body.append(this.connections.render(sub || 'connections'));
    } else if (section === 'history') {
      current = 'history';
      this.body.append(this.history.render(sub));
    } else {
      this.body.append(this.talk.render());
    }
    this.current = current;
    this.renderHeader();
  }

  renderHeader() {
    const h = clear(this.header);
    const navLink = (key, href) =>
      el('a', { href, 'aria-current': this.current === key ? 'page' : null, text: ru.nav[key] });
    h.append(el('nav', { class: 'nav', 'aria-label': ru.nav.sections }, [navLink('talk', '#/talk'), navLink('history', '#/history')]));
    const center = el('div', { class: 'header-center' });
    if (this.current === 'talk') this.talk.renderHeaderCenter(center);
    h.append(center);
    const conn = this.activeConnection;
    h.append(
      el('a', { class: conn ? 'conn-chip' : 'conn-chip none', href: '#/settings/connections', 'data-testid': 'active-connection' }, [
        el('span', { class: 'dot', 'aria-hidden': 'true' }),
        el('span', { text: conn ? `${conn.name} · ${conn.model}` : ru.talk.noConnectionTitle }),
      ]),
    );
    h.append(
      el('a', { class: 'icon-link', href: '#/settings/connections', 'aria-label': ru.nav.settings, 'aria-current': this.current === 'settings' ? 'page' : null }, [icons.settings()]),
    );
  }
}

const app = new App(document.getElementById('app'));
app.start();
