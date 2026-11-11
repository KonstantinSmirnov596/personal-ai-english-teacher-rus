"""Russian texts produced by the core (protocol field ``message_ru``).

The window shows its own texts from ``web/i18n/ru.js`` by error ``code``; these strings are the
core-side fallback and detail, kept in one place.
"""

from __future__ import annotations

ERRORS: dict[str, str] = {
    "no_active_connection": "Нет активного API-подключения.",
    "llm_unauthorized": "Провайдер отклонил ключ или запрос не оплачен.",
    "llm_rate_limited": "Провайдер просит подождать: слишком много запросов.",
    "llm_unreachable": "Провайдер недоступен: нет сети или истекло время ожидания.",
    "llm_bad_response": "Провайдер вернул ошибку или оборванный ответ.",
    "stt_failed": "Не удалось распознать речь.",
    "tts_failed": "Не удалось озвучить ответ.",
    "audio_too_short": "Реплика слишком короткая.",
    "audio_too_long": "Реплика длиннее 60 секунд.",
    "bad_request": "Некорректное сообщение.",
    "internal": "Внутренняя ошибка приложения.",
    "unauthorized": "Доступ запрещён.",
    "forbidden_origin": "Доступ запрещён.",
    "origin_required": "Доступ запрещён.",
    "misdirected_request": "Доступ запрещён.",
    "not_found": "Не найдено.",
    "conflict": "Действие недоступно.",
    "validation": "Проверьте поля формы.",
    "models_loading": "Модели ещё загружаются.",
    "no_gpu": "Не найдена видеокарта NVIDIA с поддержкой CUDA: распознавание и синтез речи "
    "недоступны. Обновите драйвер NVIDIA и перезапустите приложение.",
    "models_missing": "Модели не найдены. Запустите scripts\\setup.ps1 (нужен интернет) "
    "и перезапустите приложение.",
    "model_load_failed": "Не удалось загрузить модели. Подробности — в журнале "
    "%LOCALAPPDATA%\\EnglishTrainer\\logs\\trainer.log.",
    "pron_no_speech": "Не расслышал фразу. Прочитайте её ещё раз, чуть громче.",
    "pron_no_text": "Нет текста для оценки.",
    "pron_bad_audio": "Не удалось прочитать запись.",
    "pron_unavailable": "Оценка произношения недоступна: не удалось загрузить модель.",
}

TEST_OK = "Подключение работает"
INVALID_URL = "Адрес должен начинаться с https:// (http:// — только для 127.0.0.1 и localhost)."
INVALID_EXTRA = "Доп. параметры должны быть JSON-объектом без ключей model, messages, stream."
ACTIVE_DELETE = "Активное подключение нельзя удалить, пока не выбрано другое."
BUILTIN_SCENARIO = "Встроенный сценарий нельзя удалить."


def error_text(code: str) -> str:
    return ERRORS.get(code, ERRORS["internal"])
