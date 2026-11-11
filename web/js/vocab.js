// «Словарь»: phrases saved from session summaries, simple spaced repetition.

import { ru } from '../i18n/ru.js';
import { del, get, patch } from './api.js';
import { clear, el } from './dom.js';

const V = ru.vocab;

function formatDate(iso) {
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
}

export class VocabView {
  constructor(app) {
    this.app = app;
    this.root = el('main', { class: 'main' });
    this.column = el('div', { class: 'column' });
    this.root.append(this.column);
  }

  render() {
    this.load();
    return this.root;
  }

  async load() {
    const col = clear(this.column);
    col.append(el('div', { class: 'page-head' }, [el('h1', { text: V.title })]));
    let due = [];
    let all = [];
    try {
      [due, all] = await Promise.all([get('/api/vocab/due'), get('/api/vocab')]);
    } catch {
      return;
    }
    col.append(el('h2', { class: 'section-title', text: V.due }));
    if (!due.length) col.append(el('p', { class: 'muted', text: V.noneDue }));
    for (const item of due) col.append(this.reviewCard(item));
    col.append(el('h2', { class: 'section-title', text: V.all }));
    if (!all.length) {
      col.append(el('p', { class: 'muted', text: V.empty }));
      return;
    }
    const list = el('ul', { class: 'summary-list' });
    for (const item of all) {
      const remove = el('button', { type: 'button', class: 'btn btn-small btn-ghost', text: V.delete });
      remove.addEventListener('click', async () => {
        await del(`/api/vocab/${item.id}`);
        this.load();
      });
      list.append(
        el('li', { class: 'summary-li row', 'data-testid': 'vocab-row' }, [
          el('span', { class: 'row-main' }, [
            el('span', { lang: 'en', class: 'strong', text: item.phrase }),
            el('span', { class: 'muted', text: item.translation_ru }),
            el('span', { class: 'muted small', text: V.next(formatDate(item.next_review_at)) }),
          ]),
          remove,
        ]),
      );
    }
    col.append(list);
  }

  reviewCard(item) {
    const translation = el('div', { class: 'hidden' }, [
      el('div', { text: item.translation_ru }),
      item.context ? el('div', { class: 'muted small', lang: 'en', text: item.context }) : null,
    ]);
    const show = el('button', { type: 'button', class: 'btn btn-small', text: V.show });
    show.addEventListener('click', () => {
      translation.classList.remove('hidden');
      show.remove();
    });
    const answer = async (result) => {
      await patch(`/api/vocab/${item.id}`, { result });
      this.load();
    };
    const good = el('button', { type: 'button', class: 'btn btn-small btn-primary', text: V.remember });
    good.addEventListener('click', () => answer('good'));
    const again = el('button', { type: 'button', class: 'btn btn-small', text: V.forgot });
    again.addEventListener('click', () => answer('again'));
    return el('div', { class: 'vocab-card', 'data-testid': 'vocab-due' }, [
      el('div', { class: 'vocab-phrase', lang: 'en', text: item.phrase }),
      translation,
      el('div', { class: 'form-actions' }, [show, good, again]),
    ]);
  }
}
