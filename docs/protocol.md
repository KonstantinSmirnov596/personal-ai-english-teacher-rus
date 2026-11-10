# Контракт между окном и ядром

Окно (фронтенд в Edge) и ядро (Python) общаются по HTTP и одному WebSocket на `127.0.0.1`. Этот документ уточняет сводную таблицу сообщений из `docs/design.md`; при расхождении имён и полей действует он. Сообщения описываются pydantic-моделями в `trainer/protocol.py`; изменение контракта вносится сюда тем же коммитом.

## Запуск и допуск

1. Лаунчер выбирает свободный порт, создаёт случайный токен (не короче 32 байт, `secrets.token_urlsafe`) и запускает ядро.
2. Лаунчер открывает окно на `http://127.0.0.1:<порт>/?t=<токен>`.
3. Ядро сверяет токен, ставит cookie `trainer_session` (`HttpOnly; SameSite=Strict; Path=/`) и отвечает перенаправлением `303` на `/`. Токен одноразовый: второй запрос с ним получает `403`.
4. Все остальные маршруты, статические файлы и WebSocket требуют эту cookie.

Проверки на каждом запросе, включая рукопожатие WebSocket:

| Проверка | Условие | Ответ при нарушении |
| --- | --- | --- |
| `Host` | Ровно `127.0.0.1:<порт>` или `localhost:<порт>` | `421` |
| Cookie | Совпадает с выданной при запуске | `401` |
| `Origin` | Если заголовок есть, он равен `http://127.0.0.1:<порт>` или `http://localhost:<порт>`. Для WebSocket и запросов, меняющих состояние, заголовок обязателен | `403` |

Заголовков CORS ядро не отдаёт. Каждый ответ с HTML несёт заголовок CSP: `default-src 'self'; connect-src 'self' ws://127.0.0.1:<порт>; media-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`. Если для работы VAD или аудио-ворклета понадобится расширить политику, расширение записывается в «Решения».

## WebSocket

Адрес: `ws://127.0.0.1:<порт>/ws`. Одно соединение на окно; второе соединение закрывает первое с кодом `4001`.

Кадры двух видов:

- Текстовый кадр — JSON-объект с полем `type`.
- Двоичный кадр — звук. Он всегда идёт сразу после текстового сообщения, в котором есть объект `audio` с полем `bytes`, и содержит ровно столько байт. Других двоичных кадров нет.

Формат звука в обе стороны: PCM, 16 бит со знаком, little-endian, моно. Частота указана в сообщении.

Ограничения: реплика от 0,3 до 60 с; текстовый кадр до 256 КБ; двоичный кадр до 8 МБ. Параметр максимального размера сообщения у сервера должен это допускать.

### Окно → ядро

| `type` | Поля | Смысл |
| --- | --- | --- |
| `session.start` | `scenario_id`, `level?` | Начать сессию. Ядро отвечает `session.started` и первой репликой собеседника |
| `session.end` | — | Закончить сессию. Ядро отвечает `summary` |
| `turn.begin` | `turn_id`, `mode: "manual" \| "auto"` | Окно начало запись. Если ядро в состоянии `thinking` или `speaking`, это равносильно `interrupt` |
| `turn.audio` | `turn_id`, `audio: {sample_rate, format: "pcm_s16le", bytes}` + двоичный кадр | Реплика целиком. Частота от 16000 до 48000; ядро само приводит её к 16 кГц |
| `turn.cancel` | `turn_id` | Запись отменена (Esc). Ядро возвращается в `idle` |
| `turn.retry` | `turn_id` | Повторить ответ собеседника на реплику после ошибки LLM (кнопки «Повторить» и «Переключить на…»). Реплика уже в истории, заново она не распознаётся |
| `interrupt` | — | Оборвать текущий ответ: закрыть поток LLM, очистить очередь синтеза. Запрос корректора не отменяется |
| `playback.started` | `turn_id`, `client_first_audio_ms` | Окно начало воспроизводить ответ. Число — время от отправки `turn.audio` до первого звука по часам окна |
| `playback.ended` | `turn_id`, `interrupted: bool` | Очередь воспроизведения пуста. Ядро переходит в `idle` |
| `helper.hint` | `request_id` | Два-три варианта ответа на последнюю реплику собеседника |
| `helper.translate` | `request_id`, `turn_id` | Перевод реплики собеседника на русский |
| `helper.how_to_say` | `request_id`, `text_ru` | Как сказать эту фразу по-английски |
| `tutor.replay` | `turn_id`, `speed: 1.0 \| 0.8` | Повторить реплику собеседника. Ядро заново присылает `tutor.sentence` с флагом `replay: true` |
| `exercise.answer` | `request_id`, `audio: {...}` + двоичный кадр | Ответ на упражнение из итогов голосом |

Идентификаторы `turn_id` и `request_id` создаёт окно (UUID). Реплики собеседника получают `turn_id` от ядра.

### Ядро → окно

| `type` | Поля | Смысл |
| --- | --- | --- |
| `hello` | `protocol: 1`, `app_version`, `state`, `active_connection: {id, name, model} \| null`, `settings`, `models_ready: bool` | Первое сообщение после подключения. `models_ready` ложно, пока модели распознавания и синтеза загружаются |
| `session.started` | `session_id`, `scenario: {id, title_ru}`, `connection: {id, name, model}` | Сессия открыта |
| `state` | `value: "idle" \| "listening" \| "transcribing" \| "thinking" \| "speaking"`, `turn_id?` | Каждая смена состояния ядра |
| `user.transcript` | `turn_id`, `text`, `words: [{w, start, end, p}]`, `low_conf: [индексы слов]` | Текст реплики. `p` — уверенность слова от 0 до 1; `low_conf` — слова ниже порога |
| `tutor.delta` | `turn_id`, `text` | Очередной фрагмент текста ответа для ленты |
| `tutor.sentence` | `turn_id`, `index`, `text`, `replay?: bool`, `audio: {sample_rate: 24000, format: "pcm_s16le", bytes}` + двоичный кадр | Предложение ответа со звуком |
| `tutor.done` | `turn_id`, `text`, `interrupted: bool` | Ответ закончен; `text` — то, что успело прозвучать или было отправлено |
| `corrections` | `turn_id`, `status: "ok" \| "empty" \| "failed" \| "disabled"`, `items: [{id, quote, start, end, fix, type, note_ru, pattern}]`, `better` | Правки. `start` и `end` — позиции `quote` в `text` в символах; `null`, если фрагмент не найден |
| `pron.flags` | `turn_id`, `items: [{word_index, expected, heard, tip_ru}]` | Пометки произношения в разговоре (этап 3) |
| `helper.result` | `request_id`, `kind: "hint" \| "translate" \| "how_to_say" \| "exercise"`, `data` | Ответ помощника; схема `data` — в `docs/prompts/helpers.md` |
| `summary` | `session_id`, `key_takeaway`, `exercise`, `corrections`, `patterns`, `sounds`, `phrases` | Итоги сессии; схема — в `docs/prompts/summary.md` |
| `timing` | `turn_id`, `stt_ms`, `llm_first_sentence_ms`, `tts_first_ms`, `first_audio_ms` | Замеры ядра по реплике. `first_audio_ms` — от приёма `turn.audio` до отправки первого `tutor.sentence` |
| `error` | `code`, `message_ru`, `retryable: bool`, `turn_id?`, `request_id?` | Ошибка. После неё ядро в состоянии `idle` |

Значения `type` у правки: `grammar`, `vocabulary`, `phrasing`. Значения `pattern` — ключи из `docs/data/patterns.json`.

### Коды ошибок

| Код | Когда | Что показывает окно |
| --- | --- | --- |
| `no_active_connection` | Нет активного подключения | Ссылка на «API-подключения» |
| `llm_unauthorized` | Провайдер ответил 401 или 403 | Сообщение и кнопка «Переключить на…» |
| `llm_rate_limited` | 429 | Сообщение и кнопка «Повторить» |
| `llm_unreachable` | Нет сети, DNS, TLS, тайм-аут | Сообщение, кнопки «Повторить» и «Переключить на…» |
| `llm_bad_response` | 5xx, оборванный поток, пустой ответ | Сообщение и кнопка «Повторить» |
| `stt_failed` | Сбой распознавания | Сообщение; реплику можно повторить |
| `tts_failed` | Сбой синтеза | Текст ответа остаётся в ленте без звука |
| `audio_too_short` | Реплика короче 0,3 с | Тихая подсказка в панели, без записи в ленту |
| `audio_too_long` | Реплика длиннее 60 с | Сообщение |
| `bad_request` | Сообщение не прошло проверку схемы | Запись в журнал; в ленте ничего |
| `internal` | Всё остальное | Сообщение и запись в журнал |

Текст ошибки провайдера в `message_ru` не подставляется дословно: он обрезается до 200 символов и проходит фильтр ключей.

### Порядок сообщений одной реплики

```text
окно  → turn.begin
ядро  → state listening
окно  → turn.audio + звук
ядро  → state transcribing
ядро  → user.transcript
ядро  → state thinking              (с этого момента параллельно идёт запрос корректора)
ядро  → tutor.delta ...
ядро  → tutor.sentence + звук       (первое предложение)
ядро  → state speaking
ядро  → timing
окно  → playback.started
ядро  → tutor.sentence + звук ...
ядро  → tutor.done
ядро  → corrections                 (в любой момент после user.transcript)
окно  → playback.ended
ядро  → state idle
```

Пустое распознавание (тишина, шум) даёт `user.transcript` с пустым `text` и возврат в `idle` без запроса к LLM.

## HTTP

Все маршруты под `/api`, тела и ответы в JSON, кроме отмеченных. Ошибка: статус 4xx или 5xx и тело `{"error": {"code", "message_ru"}}`.

### Подключения

Объект подключения в ответах: `{id, name, base_url, model, extra_params, key_last4, is_active, last_test: {ok, latency_ms, message_ru, at} | null}`. Поля с полным ключом в ответах не существует.

| Метод и путь | Тело | Действие |
| --- | --- | --- |
| `GET /api/connections` | — | Список строк |
| `POST /api/connections` | `{name, base_url, model, api_key, extra_params?}` | Создать строку. Первая строка становится активной |
| `PATCH /api/connections/{id}` | Любые поля; `api_key` только если ключ меняется | Изменить строку |
| `DELETE /api/connections/{id}` | — | Удалить строку и её ключ. Активную удалить нельзя: `409` |
| `POST /api/connections/{id}/activate` | — | Сделать строку активной |
| `POST /api/connections/{id}/test` | — | Короткий запрос к провайдеру; ответ `{ok, latency_ms, message_ru}` |
| `POST /api/connections/test` | `{base_url, model, api_key, extra_params?}` | Проверить несохранённую строку. Ключ нигде не сохраняется |
| `GET /api/connections/templates` | — | Шаблоны: «DeepSeek», «Другой OpenAI-совместимый» |

Проверка `base_url`: схема `https`; схема `http` допускается только для `127.0.0.1` и `localhost` (локальные серверы моделей и мок в тестах). `extra_params` — JSON-объект; ключи `model`, `messages`, `stream` в нём запрещены.

### Настройки, сценарии, история

| Метод и путь | Действие |
| --- | --- |
| `GET /api/settings`, `PATCH /api/settings` | Настройки одним объектом |
| `GET /api/voices` | Американские голоса Kokoro для выбора |
| `POST /api/voices/preview` | `{voice, speed}` → короткая фраза выбранным голосом, ответ `audio/wav` |
| `GET /api/scenarios`, `POST /api/scenarios`, `PATCH /api/scenarios/{id}`, `DELETE /api/scenarios/{id}` | Сценарии; встроенные удалить нельзя |
| `GET /api/sessions` | Список сессий, новые сверху |
| `GET /api/sessions/{id}` | Лента сессии с правками и итогами |
| `GET /api/sessions/{id}/export?format=json\|csv` | Экспорт сессии |
| `DELETE /api/sessions/{id}` | Удалить сессию и её аудио |
| `GET /api/turns/{id}/audio` | Запись реплики, `audio/wav`; `404`, если хранение отключено или срок истёк |
| `GET /api/patterns` | Повторяющиеся ошибки со счётчиками и состоянием |
| `GET /api/vocab`, `POST /api/vocab`, `PATCH /api/vocab/{id}`, `DELETE /api/vocab/{id}` | Словарь |
| `GET /api/vocab/due` | Фразы к повторению на сегодня |
| `DELETE /api/data` | `{confirm: "DELETE", include_keys: bool}` — удалить все данные |
| `GET /api/health` | `{status, gpu: {name, vram_used_mb, vram_total_mb}, models: {stt, tts, phoneme}, active_connection}` |

### Произношение (этап 3)

| Метод и путь | Тело | Ответ |
| --- | --- | --- |
| `GET /api/pron/phrases` | — | Фразы из `reading_phrases.json` с целевыми звуками |
| `POST /api/pron/assess?phrase_id=<id>` | `audio/wav`, моно | Разбор попытки, см. ниже |
| `POST /api/pron/assess-text` | multipart: `text`, `audio` | То же для произвольного текста |
| `GET /api/pron/reference?phrase_id=<id>&speed=1.0` | — | Эталон голосом Kokoro, `audio/wav` |
| `POST /api/pron/feedback` | `{attempt_id, word_index, phone_index, verdict: "was_correct"}` | Сдвинуть поправку чувствительности по звуку |
| `GET /api/pron/sounds?days=7` | — | Статистика по звукам: попытки, ошибки |

Разбор попытки:

```json
{
  "attempt_id": "…",
  "score": 0.82,
  "words": [
    {
      "w": "think",
      "ipa": "θɪŋk",
      "score": 0.61,
      "level": "error",
      "phones": [
        {"expected": "θ", "heard": "s", "start": 0.42, "end": 0.51, "gop": -3.1, "level": "error"},
        {"expected": "ɪ", "heard": "ɪ", "start": 0.51, "end": 0.58, "gop": -0.2, "level": "ok"}
      ],
      "tip": {"catalog_id": "th_voiceless", "title_ru": "…", "tip_ru": "…", "pair": ["think", "sink"]}
    }
  ]
}
```

Значения `level`: `ok`, `approx`, `error` — в интерфейсе «верно», «неточно», «ошибка».

## Что ядро отправляет провайдеру LLM

Единственный внешний обмен. Запрос: `POST {base_url}/chat/completions`, заголовок `Authorization: Bearer <ключ>`, тело `{model, messages, stream, ...extra_params}`. Корректор, итоги и помощники добавляют `response_format: {"type": "json_object"}`, если шаблон подключения это поддерживает. В `messages` попадают системные промпты, тексты реплик и ответы собеседника. Звук, пути к файлам, имя пользователя Windows и содержимое настроек туда не попадают.
