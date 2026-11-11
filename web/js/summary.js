// «Итоги сессии»: key takeaway with a spoken exercise, corrections, recurring mistakes,
// pronunciation (stage 3), tutor phrases to save into the vocabulary.

import { ru } from '../i18n/ru.js';
import { post } from './api.js';
import { el, icons } from './dom.js';

const S = ru.summary;

function correctionItem(c) {
  return el('li', { class: 'summary-li card-item' }, [
    el('span', { class: 'card-quote', lang: 'en', text: c.quote }),
    el('span', { class: 'muted', 'aria-hidden': 'true', text: '→' }),
    el('span', { class: 'card-fix', lang: 'en', text: c.fix }),
    el('span', { class: 'card-note', text: c.note_ru }),
  ]);
}

function patternState(p) {
  if (p.state === 'focus') return { text: S.patternState.focus(p.session_streak), cls: 'state-focus' };
  if (p.state === 'closed') return { text: S.patternState.closed(p.clean_streak), cls: 'state-closed' };
  if (p.state === 'recurring') return { text: S.patternState.recurring, cls: '' };
  return { text: S.patternState.new, cls: '' };
}

export function renderSummary(app, summary, meta, onNew) {
  const root = el('section', { class: 'summary', 'data-testid': 'summary' });
  const head = el('div', { class: 'summary-head' }, [
    el('div', { class: 'page-head' }, [el('h1', { text: S.title }), meta ? el('p', { text: S.meta(meta.title, meta.minutes, meta.turns) }) : null]),
  ]);
  const actions = el('div', { class: 'form-actions' });
  if (summary.session_id) {
    actions.append(el('a', { class: 'btn', href: `/api/sessions/${summary.session_id}/export?format=json`, download: '', text: S.export }));
  }
  if (onNew) {
    const again = el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'new-session', text: S.newSession });
    again.addEventListener('click', onNew);
    actions.append(again);
  }
  head.append(actions);
  root.append(head);

  if (summary.key_takeaway) root.append(takeawayBlock(app, summary));
  else if (summary.corrections && summary.corrections.length) root.append(el('p', { class: 'muted', text: S.noTakeaway }));

  const corr = el('section', { class: 'summary-section', 'aria-label': S.corrections }, [el('h2', { text: S.corrections })]);
  if (summary.corrections && summary.corrections.length) {
    corr.append(el('ul', { class: 'summary-list', lang: 'ru' }, summary.corrections.map(correctionItem)));
  } else corr.append(el('p', { class: 'muted', text: S.none }));
  root.append(corr);

  const grid = el('div', { class: 'summary-grid' });
  const pats = el('section', { class: 'summary-section', 'aria-label': S.patterns }, [el('h2', { text: S.patterns })]);
  if (summary.patterns && summary.patterns.length) {
    pats.append(
      el(
        'ul',
        { class: 'summary-list' },
        summary.patterns.map((p) => {
          const st = patternState(p);
          return el('li', { class: 'summary-li row', 'data-pattern': p.key }, [
            el('span', { class: 'strong', text: p.title_ru }),
            el('span', { class: `small ${st.cls}`, text: st.text }),
          ]);
        }),
      ),
    );
  } else pats.append(el('p', { class: 'muted', text: S.none }));
  grid.append(pats);
  const sounds = el('section', { class: 'summary-section', 'aria-label': S.sounds }, [el('h2', { text: S.sounds })]);
  if (summary.sounds && summary.sounds.length) {
    sounds.append(
      el(
        'ul',
        { class: 'summary-list' },
        summary.sounds.map((s) =>
          el('li', { class: 'summary-li row' }, [
            el('span', { class: 'row-main' }, [el('span', { class: 'ipa strong', text: `/${s.phone}/` }), el('span', { lang: 'en', class: 'muted', text: (s.words || []).join(', ') })]),
            el('a', { href: '#/reading', class: 'small', text: S.train }),
          ]),
        ),
      ),
    );
  } else sounds.append(el('p', { class: 'muted', text: S.noSounds }));
  grid.append(sounds);
  root.append(grid);

  const phr = el('section', { class: 'summary-section', 'aria-label': S.phrases }, [el('h2', { text: S.phrases })]);
  if (summary.phrases && summary.phrases.length) {
    phr.append(
      el(
        'ul',
        { class: 'summary-list' },
        summary.phrases.map((p) => {
          const add = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'add-vocab', text: S.toVocab });
          add.addEventListener('click', async () => {
            add.disabled = true;
            try {
              await post('/api/vocab', { phrase: p.en, translation_ru: p.ru || '', context: p.context || '', session_id: summary.session_id || null });
              add.textContent = S.inVocab;
            } catch {
              add.disabled = false;
            }
          });
          return el('li', { class: 'summary-li row' }, [
            el('span', { class: 'row-main' }, [el('span', { lang: 'en', class: 'strong', text: p.en }), el('span', { class: 'muted', text: p.ru })]),
            add,
          ]);
        }),
      ),
    );
  } else phr.append(el('p', { class: 'muted', text: S.noPhrases }));
  root.append(phr);
  return root;
}

function takeawayBlock(app, summary) {
  const k = summary.key_takeaway;
  const box = el('section', { class: 'takeaway', 'aria-label': S.keyTakeaway, 'data-testid': 'key-takeaway' }, [
    el('div', { class: 'takeaway-label', text: S.keyTakeaway }),
    el('h2', { lang: 'en', text: k.title }),
    el('p', { class: 'takeaway-text', text: k.explanation_ru }),
  ]);
  if (k.examples && k.examples.length) {
    box.append(
      el(
        'ul',
        { class: 'takeaway-examples' },
        k.examples.map((e) => el('li', {}, [el('span', { lang: 'en', class: 'card-quote', text: e.wrong }), el('span', { class: 'muted', 'aria-hidden': 'true', text: '→' }), el('span', { lang: 'en', class: 'card-fix', text: e.right })])),
      ),
    );
  }
  if (k.count_in_session) box.append(el('p', { class: 'muted small', text: S.timesInSession(k.count_in_session) }));
  const ex = summary.exercise;
  if (ex) {
    const result = el('div', { class: 'exercise-result', role: 'status', 'data-testid': 'exercise-result' });
    const say = el('button', { type: 'button', class: 'btn btn-primary btn-small btn-icon', 'data-testid': 'exercise-say' }, [icons.mic(), el('span', { text: S.say })]);
    const show = el('button', { type: 'button', class: 'btn btn-small', text: S.showAnswer });
    show.addEventListener('click', () => {
      result.replaceChildren(el('span', { lang: 'en', text: ex.answer_en }));
    });
    let recording = false;
    say.addEventListener('click', async () => {
      const audio = app.audio;
      if (!audio.ctx) return;
      if (!recording) {
        recording = true;
        say.lastChild.textContent = S.stop;
        say.classList.add('recording');
        audio.startRecording();
        return;
      }
      recording = false;
      say.lastChild.textContent = S.say;
      say.classList.remove('recording');
      const rec = audio.stopRecording();
      if (!rec) return;
      result.replaceChildren(el('span', { class: 'muted', text: S.checking }));
      try {
        const data = await app.askHelper(
          { type: 'exercise.answer', audio: { sample_rate: rec.sampleRate, format: 'pcm_s16le', bytes: rec.pcm.byteLength } },
          rec.pcm,
        );
        result.replaceChildren(
          el('strong', { class: data.correct ? 'ok-text' : 'bad-text', text: data.correct ? S.correct : S.wrong }),
          data.note_ru ? el('span', { text: ` ${data.note_ru}` }) : '',
          data.heard ? el('div', { class: 'muted small' }, [S.heard(''), el('span', { lang: 'en', text: data.heard })]) : '',
          data.correct ? '' : el('div', { class: 'small' }, [el('span', { lang: 'en', text: data.answer_en })]),
        );
      } catch (e) {
        result.replaceChildren(el('span', { class: 'bad-text', text: ru.errors[e.code] || ru.errors.internal }));
      }
    });
    box.append(
      el('div', { class: 'exercise' }, [
        el('div', { class: 'exercise-text' }, [el('span', { class: 'exercise-label', text: S.exercise }), el('span', { text: S.sayIt(ex.prompt_ru) })]),
        el('div', { class: 'form-actions' }, [say, show]),
        result,
      ]),
    );
  }
  return box;
}
