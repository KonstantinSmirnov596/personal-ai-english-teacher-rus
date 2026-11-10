# DeepSeek API: параметры для шаблона подключения

Пункт 0.4 плана. Сверено с официальной документацией 11 ноября 2026 года. Живой запрос не выполнялся: ключ владельцем ещё не сохранён (см. `docs/HUMAN_TASKS.md`, этап 1).

## Итог для шаблона «DeepSeek»

| Поле | Значение | Основание |
| --- | --- | --- |
| Адрес | `https://api.deepseek.com` | [Первый запрос](https://api-docs.deepseek.com/): OpenAI-формат на корневом адресе; вариант `/v1` в текущей документации не упоминается |
| Модель | `deepseek-flash` (быстрая), `deepseek-v4-pro` (сильнее и дороже) | [Модели и цены](https://api-docs.deepseek.com/quick_start/pricing) |
| Доп. параметры | `{"thinking": {"type": "disabled"}}` | Рассуждения включены по умолчанию у обеих моделей; для разговора их нужно выключить явно |
| JSON-режим | `response_format: {"type": "json_object"}` для корректора, итогов и помощников | [JSON Output](https://api-docs.deepseek.com/guides/json_mode) |

## Модели

- В документации перечислены только `deepseek-flash` (DeepSeek-V4.1-Flash) и `deepseek-v4-pro`. Контекст у обеих 1M токенов. Источник: [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing), [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion).
- Старые имена `deepseek-v4-flash` ещё принимаются и направляются на V4.1 Flash.
- `deepseek-chat` и `deepseek-reasoner` выведены из работы после 24 июля 2026 года. Источник: [новость от 24.04.2026](https://api-docs.deepseek.com/news/news260424). Шаблон их не предлагает.

## Режим рассуждений: параметр `thinking`

- Форма: `"thinking": {"type": "enabled" | "disabled"}` в теле запроса. Источник: [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion).
- Значение по умолчанию: если `thinking` не передан или равен `null`, режим определяется `reasoning_effort`; если не передан и он, рассуждения **включены**. На странице цен обе модели помечены как поддерживающие режимы «non-thinking and thinking (default)».
- `reasoning_effort`: `none` выключает рассуждения; `low`, `high`, `max` включают; по умолчанию `high`. Объект `thinking`, если передан, имеет приоритет.
- Как выключить: `{"thinking": {"type": "disabled"}}`. Через OpenAI SDK параметр передаётся в `extra_body` ([Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)); наш клиент на httpx кладёт его прямо в тело запроса через «Доп. параметры».
- В режиме рассуждений `temperature`, `presence_penalty`, `frequency_penalty` игнорируются без ошибки.
- `max_tokens` по умолчанию: 8K без рассуждений, 64K с рассуждениями.

Вывод для приложения: шаблон DeepSeek заполняет «Доп. параметры» значением `{"thinking": {"type": "disabled"}}`. Без этого первый токен ответа задерживается на время рассуждений, и цель «до 1,5 с до первого звука» недостижима.

## JSON-режим

- Включается `response_format: {"type": "json_object"}`; допустимые типы — `text` и `json_object`. Источник: [JSON Output](https://api-docs.deepseek.com/guides/json_mode).
- Условия: слово «json» в системном или пользовательском промпте; пример нужного формата в промпте; `max_tokens` с запасом, чтобы JSON не обрезался.
- Документация предупреждает, что ответ иногда приходит пустым (только пробелы) при `finish_reason: stop`. Ядро делает один повтор (пункт 1.7).
- Обе модели поддерживают JSON Output (таблица на странице цен).
- Совместимость JSON-режима с включёнными рассуждениями в документации явно не описана. Пример в документации использует `deepseek-flash` без `thinking`, то есть с рассуждениями по умолчанию. Мы отправляем все запросы с выключенными рассуждениями, поэтому вопрос нас не касается; живой запрос проверит это на этапе 1.

## Поток

- Формат: SSE только с данными (`data: {...}`), конец потока — `data: [DONE]`. Источник: [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion).
- Текст ответа в `choices[0].delta.content`; рассуждения, если включены, в `choices[0].delta.reasoning_content`. Клиент игнорирует `reasoning_content`.
- `stream_options: {"include_usage": true}` допустим только при `stream: true`; статистика приходит в последнем куске: `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens`, `completion_tokens_details.reasoning_tokens`.
- Пока запрос ждёт обработки, сервер шлёт комментарии `: keep-alive`; запрос без начала генерации закрывается через 10 минут. Источник: [Rate Limit](https://api-docs.deepseek.com/quick_start/rate_limit). Парсер SSE пропускает строки-комментарии.
- Кэш контекста включён по умолчанию для всех пользователей ([Context Caching](https://api-docs.deepseek.com/guides/kv_cache)): совпадающее начало `messages` между запросами дешевле и быстрее. Поэтому системный промпт и начало истории держатся побайтно стабильными (пункт 1.6).

## Коды ошибок

Источник: [Error Codes](https://api-docs.deepseek.com/quick_start/error_codes).

| HTTP | Название | Смысл | Код в протоколе приложения |
| --- | --- | --- | --- |
| 400 | Invalid Format | Неверный формат тела | `llm_bad_response` |
| 401 | Authentication Fails | Неверный ключ | `llm_unauthorized` |
| 402 | Insufficient Balance | Кончился баланс | `llm_unauthorized` (нужно действие владельца, повтор не поможет) |
| 422 | Invalid Parameters | Неверные параметры | `llm_bad_response` |
| 429 | Rate Limit Reached | Слишком часто | `llm_rate_limited` |
| 500 | Server Error | Ошибка сервера, повторить позже | `llm_bad_response` |
| 503 | Server Overloaded | Перегрузка, повторить позже | `llm_bad_response` |

## Что осталось проверить живым запросом

Выполняется после того, как владелец сохранит ключ в окне «API-подключения» (этап 1, набор `live`):

- поток с `{"thinking": {"type": "disabled"}}` не содержит `reasoning_content`, первый кусок приходит быстрее 0,8 с;
- JSON-режим корректора с выключенными рассуждениями возвращает валидный JSON;
- `prompt_cache_hit_tokens` растёт на второй реплике сессии.
