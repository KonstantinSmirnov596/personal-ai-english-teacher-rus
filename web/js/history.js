// «История»: past sessions and a read-only feed of one session.

import { ru } from '../i18n/ru.js';
import { del, get } from './api.js';
import { clear, el } from './dom.js';
import { renderSummary } from './summary.js';

const H = ru.history;

function formatDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
}

export class HistoryView {
  constructor(app) {
    this.app = app;
    this.root = el('main', { class: 'main' });
    this.column = el('div', { class: 'column' });
    this.root.append(this.column);
  }

  render(sessionId) {
    clear(this.column);
    if (sessionId) this.showSession(sessionId);
    else this.showList();
    return this.root;
  }

  async showList() {
    const col = this.column;
    col.append(el('div', { class: 'page-head' }, [el('h1', { text: H.title })]));
    let sessions = [];
    try {
      sessions = await get('/api/sessions');
    } catch {
      sessions = [];
    }
    if (!sessions.length) {
      col.append(el('p', { class: 'muted', text: H.empty }));
      return;
    }
    const list = el('div', { class: 'list' });
    for (const s of sessions) {
      const meta = [formatDate(s.started_at), H.turns(s.user_turns || 0), H.corrections(s.corrections || 0)];
      if (!s.ended_at) meta.push(H.unfinished);
      const remove = el('button', { type: 'button', class: 'btn btn-small btn-ghost', text: H.delete });
      remove.addEventListener('click', async () => {
        if (!window.confirm(H.deleteConfirm)) return;
        await del(`/api/sessions/${s.id}`);
        this.render();
      });
      list.append(
        el('div', { class: 'list-item', 'data-testid': 'session-row', 'data-session-id': s.id }, [
          el('div', { class: 'list-item-main' }, [
            el('a', { href: `#/history/${s.id}`, text: s.scenario_title_ru }),
            el('span', { class: 'list-item-meta', text: meta.join(' · ') }),
          ]),
          el('div', { class: 'row-actions' }, [remove]),
        ]),
      );
    }
    col.append(list);
  }

  async showSession(id) {
    const col = this.column;
    let s;
    try {
      s = await get(`/api/sessions/${id}`);
    } catch {
      location.hash = '#/history';
      return;
    }
    col.append(
      el('a', { href: '#/history', text: H.back }),
      el('div', { class: 'page-head' }, [el('h1', { text: s.scenario_title_ru }), el('p', { text: formatDate(s.started_at) })]),
      el('div', { class: 'form-actions' }, [
        el('a', { class: 'btn btn-small', href: `/api/sessions/${id}/export?format=json`, download: '', text: `${H.export} JSON` }),
        el('a', { class: 'btn btn-small', href: `/api/sessions/${id}/export?format=csv`, download: '', text: `${H.export} CSV` }),
      ]),
    );
    if (s.summary) col.append(renderSummary(this.app, { ...s.summary, session_id: id }, null, null));
    for (const t of s.turns) {
      const node = el('article', { class: `turn ${t.role}`, 'data-turn-id': t.id }, [
        el('div', { class: 'turn-label', text: t.role === 'user' ? ru.talk.you : ru.talk.tutor }),
        el('p', { class: 'turn-text', lang: 'en', text: t.text }),
      ]);
      if (t.corrections.length) {
        const card = el('section', { class: 'card', lang: 'ru', 'aria-label': ru.card.region });
        t.corrections.forEach((c, i) => {
          if (i) card.append(el('div', { class: 'card-sep' }));
          card.append(
            el('div', { class: 'card-item' }, [
              el('span', { class: 'card-quote', lang: 'en', text: c.quote }),
              el('span', { class: 'muted', 'aria-hidden': 'true', text: '→' }),
              el('span', { class: 'card-fix', lang: 'en', text: c.fix }),
              el('span', { class: 'card-note', text: c.note_ru }),
            ]),
          );
        });
        if (t.better) {
          card.append(
            el('div', { class: 'card-sep' }),
            el('div', { class: 'card-better' }, [el('span', { class: 'card-better-label', text: ru.card.better }), el('span', { lang: 'en', text: t.better })]),
          );
        }
        node.append(card);
      }
      col.append(node);
    }
  }
}
