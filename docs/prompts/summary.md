# Итоги сессии

Исходная версия. Итоги собираются из двух частей: большую часть ядро берёт из базы без LLM, и один запрос к LLM пишет главный вывод, упражнение и полезные фразы.

## Что собирает ядро без LLM

| Поле сообщения `summary` | Источник |
| --- | --- |
| `corrections` | Все правки сессии из таблицы `correction`, в порядке реплик |
| `patterns` | Ключи, встретившиеся в сессии: число в сессии, серия сессий подряд, состояние (`new`, `recurring`, `closed`) |
| `sounds` | Пометки произношения за сессию по звукам, со словами-примерами (этап 3; до него пустой список) |
| Выбор ключа для главного вывода | Ключ с наибольшим числом правок в сессии; при равенстве — тот, у которого длиннее серия сессий; ключ `other` не выбирается |

Если правок в сессии нет, `key_takeaway` и `exercise` равны `null`, запрос к LLM просит только фразы.

## Запрос к LLM

Без потока, `response_format: {"type": "json_object"}`.

Вход (`user`):

```json
{
  "level": "B1",
  "focus_pattern": "for_since",
  "focus_examples": [
    {"quote": "since two years", "fix": "for two years"},
    {"quote": "since three months", "fix": "for three months"}
  ],
  "tutor_lines": ["Thanks for joining. To start, could you walk me through a project you're proud of?", "Two years is a solid stretch. What was the hardest part of the migration?"]
}
```

Системный промпт:

```text
You write the end-of-session notes for a Russian-speaking adult learner of American English. Return exactly one json object:
{"key_takeaway": {"title": "...", "explanation_ru": "...", "examples": [{"wrong": "...", "right": "..."}]}, "exercise": {"prompt_ru": "...", "answer_en": "...", "accept": ["..."]}, "phrases": [{"en": "...", "ru": "...", "context": "..."}]}

key_takeaway
- It is about the mistake type given in focus_pattern, illustrated by focus_examples.
- title: the forms in question, a few words, for example "for / since".
- explanation_ru: two or three short sentences in Russian that state the rule and how to choose the right form. Simple wording, no grammar jargon beyond school level.
- examples: one or two pairs taken from focus_examples.

exercise
- One short task on the same rule. prompt_ru is a Russian sentence to say in English, eight words or fewer, about work or everyday life.
- answer_en is the natural American English answer. accept lists up to three other correct variants, including contracted and uncontracted forms.

phrases
- Up to four useful expressions that appear word for word in tutor_lines: idioms, phrasal verbs or collocations worth learning at the learner's level. Skip plain vocabulary.
- en is the expression in its dictionary form, ru is a short Russian equivalent, context is the tutor's sentence where it appeared.
- If nothing is worth saving, return an empty list.

If focus_pattern is null, set key_takeaway and exercise to null and fill only phrases.
Russian text goes only into explanation_ru, prompt_ru and ru. Return only the json object.
```

## Проверка упражнения

Пользователь отвечает голосом. Реплика распознаётся и уходит сообщением `exercise.answer`. Сначала ядро сравнивает распознанный текст с `answer_en` и `accept` после нормализации (регистр, пунктуация, сокращения). Совпало → ответ верный без запроса к LLM. Не совпало → запрос `exercise_check` из `docs/prompts/helpers.md`.

## Проверки

- Ответ проходит pydantic-схему; при неудаче один повтор, затем итоги показываются без главного вывода.
- Каждая фраза из `phrases` проверяется как подстрока одной из `tutor_lines` без учёта регистра и словоформы первого слова; не найденные отбрасываются.
- Поля `title`, `answer_en`, `accept`, `en`, `context` не содержат кириллицы.
