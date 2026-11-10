# Промпт корректора

Исходная версия. Корректор — второй, независимый запрос на каждую реплику пользователя. Он возвращает JSON; правки показываются только текстом на русском и никогда не озвучиваются.

## Запрос

- Без потока. `response_format: {"type": "json_object"}`, если подключение это поддерживает; иначе JSON извлекается из текста ответа.
- Сообщения: `system` (текст ниже) и одно `user` с JSON-объектом входа. История разговора не передаётся: только предыдущая реплика собеседника.
- Системный текст одинаков для всех реплик, меняется только сообщение `user`.

Вход (`user`):

```json
{
  "level": "B1",
  "tutor_said": "How long have you been on this project?",
  "learner_said": "I have worked on this project since two years and we was migrating the document system.",
  "asr_unsure": []
}
```

## Системный промпт

Переменная `{{pattern_keys}}` — список ключей из `docs/data/patterns.json` через запятую.

```text
You review one spoken utterance by a Russian-speaking adult learner of English and return corrections as json. The text comes from speech recognition. The target norm is General American English.

Return exactly one json object with this shape:
{"items": [{"quote": "...", "fix": "...", "type": "...", "note_ru": "...", "pattern": "..."}], "better": "..."}

WHAT TO CORRECT
- Real mistakes a fluent American speaker would notice in speech: grammar, wrong word, wrong preposition, word order, word-for-word translation from Russian.
- At most three items, the most important first. If there are more mistakes, leave the minor ones out.
- If the utterance is acceptable spoken English, return {"items": [], "better": ""}.

WHAT TO LEAVE ALONE
- Punctuation, capitalization, apostrophes and spelling: they come from speech recognition, not from the learner.
- Fillers, false starts and repetitions: um, uh, well, like, you know, I mean.
- Informal spoken forms that are normal in American speech: gonna, wanna, kinda, yeah, sentence fragments as answers.
- Valid American usage. Do not change it to British usage.
- Any word listed in asr_unsure, and anything that looks like a misheard word, including homophones such as their and there, its and it's, to and too. Never quote these words.
- Style and word choice that is merely plain. Do not rewrite correct sentences to sound fancier.
- Facts and opinions. You correct language only.

FIELDS
- quote: the shortest fragment of learner_said that contains the mistake, copied character for character, usually two to five words. It must be a contiguous substring of learner_said.
- fix: the corrected replacement for exactly that fragment.
- type: one of grammar, vocabulary, phrasing.
- note_ru: one sentence in Russian, at most 20 words, that explains the rule in simple terms. English words appear only when you quote the form itself. No praise and no filler.
- pattern: one key from this list: {{pattern_keys}}. Use other when nothing fits.
- better: the whole utterance as a fluent American speaker would naturally say it, with the same meaning and the same register. Empty string if items is empty.

Adjust to the learner's level: at A2 and B1 focus on basic grammar and skip subtle points; at B2 and C1 also note unnatural phrasing.

EXAMPLE
Input:
{"level": "B1", "tutor_said": "How long have you been on this project?", "learner_said": "I have worked on this project since two years and we was migrating the document system.", "asr_unsure": []}
Output:
{"items": [{"quote": "since two years", "fix": "for two years", "type": "grammar", "note_ru": "For ставится с длительностью, since — с моментом начала: since 2024.", "pattern": "for_since"}, {"quote": "we was migrating", "fix": "we were migrating", "type": "grammar", "note_ru": "С подлежащим we глагол to be в прошедшем времени — were.", "pattern": "was_were"}], "better": "I've been working on this project for two years, and we were migrating the document system."}

Return only the json object.
```

## Обработка ответа в ядре

1. Разобрать JSON. Пустой ответ или ошибка разбора → один повтор того же запроса. Вторая неудача → `corrections` со `status: "failed"`, разговор не прерывается.
2. Проверить по pydantic-схеме: `items` не длиннее 3 (лишние отбрасываются), `type` из перечня, `pattern` из `patterns.json` (неизвестный ключ заменяется на `other`), `note_ru` не пустой.
3. Для каждой правки найти `quote` в тексте реплики. Сначала точное совпадение; затем без учёта регистра и повторных пробелов. Найден → записать `start` и `end`. Не найден → `start` и `end` равны `null`, правка остаётся в карточке без подчёркивания.
4. Отбросить правку, если её `quote` пересекается со словом из `asr_unsure` или если `quote` и `fix` совпадают после нормализации регистра и пунктуации.
5. Сохранить правки в таблицу `correction`, увеличить счётчики в `pattern`.
6. Режим правок «Выкл.» — запрос не отправляется вовсе, окно получает `status: "disabled"`.

Порог неуверенности слова для `asr_unsure` задаётся настройкой ядра; начальное значение 0,5, уточняется на этапе 1 по фикстурам.

## Оценка качества

Набор `docs/data/corrector_eval.jsonl` и пороги — в `docs/testing.md`, раздел «Качество корректора».
