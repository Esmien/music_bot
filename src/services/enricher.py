"""HTTP-клиент сервиса обогащения промпта (Open WebUI, OpenAI-совместимый API).

Сервис изолирован от Telegram: это чистый HTTP-клиент с парсером JSON-ответа
и сохранением результата в БД. Системный промпт хранится в настройках модели
на стороне Open WebUI, поэтому наружу отправляется только пользовательский
текст (при повторном обогащении с правками — вместе с историей диалога).
"""

import json
import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError

from core.config import settings
from core.database.engine import SessionLocal
from core.database.models import GenerationFeedback
from core.utils.exceptions import EnricherNotConfiguredError
from services.enricher_validator import parse_enricher_json, validate_enriched_prompt

logger = logging.getLogger(__name__)

# DEVIATION: алиас для существующих тестов, обращающихся к enricher._parse_enricher_json;
# реализация переехала в enricher_validator.
_parse_enricher_json = parse_enricher_json

REQUEST_TIMEOUT_SECONDS = 120.0

# Порядок и заголовки секций при форматировании JSON-ответа обогатителя
# для показа пользователю (контракт ответа — см. docs/instructions.md)
_ENRICHED_FIELD_TITLES: tuple[tuple[str, str], ...] = (
    ("genre_and_style", "🎵 Жанр и стиль"),
    ("mood", "🎭 Настроение"),
    ("instrumentation", "🎻 Инструменты"),
    ("tempo_bpm", "⏱ Темп"),
    ("vocal_style", "🎤 Вокал"),
    ("language", "🌐 Язык"),
    ("lyrics", "📝 Текст песни"),
    ("song_structure", "🎼 Структура"),
)


async def enrich_prompt(prompt: str, history: list[dict[str, str]] | None = None) -> str | None:
    """Обогащает промпт через OpenAI-совместимый API обогатителя.

    Отправляет сообщения в чат-комплишн и извлекает текст ответа из поля
    choices[0].message.content. При сетевом сбое или некорректном ответе
    логирует ошибку и возвращает None, чтобы хендлер мог предложить
    пользователю повторить попытку.

    Args:
        prompt: Пользовательский промпт (или текст правок) для обогащения.
        history: Предыдущие ходы диалога обогащения в формате OpenAI-чата;
            добавляются перед текущим сообщением, чтобы модель при правках
            видела исходную идею и свой прошлый ответ.

    Returns:
        Обогащённый промпт: нормализованный JSON по контракту, либо сырой
        текст (без ударений/«ё»), если ответ не соответствует JSON-контракту.
        None возвращается только при сетевом сбое или пустом ответе.

    Raises:
        EnricherNotConfiguredError: Если не сконфигурирован URL или модель обогатителя.
    """
    if not settings.enrich.ENRICH_URL or not settings.enrich.ENRICH_MODEL:
        raise EnricherNotConfiguredError("Enricher URL or model is not configured")

    messages: list[dict[str, str]] = list(history) if history else []
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": settings.enrich.ENRICH_MODEL,
        "messages": messages,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {settings.enrich.ENRICH_TOKEN}"}

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url=settings.enrich.ENRICH_URL, json=payload, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Enricher request failed: %s", exc)
        return None

    enriched = _extract_message_content(response.json())
    if not enriched:
        logger.error("Enricher returned empty or unexpected response body")
        return None
    return validate_enriched_prompt(enriched)


def format_enriched_prompt(raw: str) -> str:
    """Превращает сырой JSON-ответ обогатителя в человекочитаемый текст.

    Разбирает контракт из docs/instructions.md и собирает текст с
    заголовками секций: списки склеиваются запятыми (структура песни —
    построчно), темп дополняется «BPM». Если ответ не парсится как JSON
    (модель ответила вольным текстом), возвращается исходный текст —
    форматирование не должно ломать сценарий.

    Args:
        raw: Сырой ответ обогатителя (JSON-строка или вольный текст).

    Returns:
        Отформатированный текст для показа пользователю.
    """
    try:
        data = parse_enricher_json(raw=raw)
    except (ValueError, json.JSONDecodeError) as error:
        logger.warning("Enricher response is not valid JSON, showing raw text: %s", error)
        return raw.strip()

    sections: list[str] = []
    for field, title in _ENRICHED_FIELD_TITLES:
        value = data.get(field)
        if value is None or value == "" or value == []:
            continue
        if field == "tempo_bpm":
            body = f"{value} BPM"
        elif isinstance(value, list):
            # Структура песни — построчно (теги секций), остальные списки — через запятую
            body = "\n".join(str(item) for item in value) if field == "song_structure" else ", ".join(map(str, value))
        else:
            body = str(value).strip()
        sections.append(f"{title}:\n{body}")

    return "\n\n".join(sections) if sections else raw.strip()


async def save_enriched_prompt(tg_id: int, initial_prompt: str, enriched_prompt: str) -> None:
    """Сохраняет обогащённый промпт (пришедший от хендлера) в БД.

    Запись создаётся в GenerationFeedback на этапе обогащения: оценка
    (is_liked, feedback) дополняется хендлерами оценки после генерации.
    GenerationFeedback.user_id — внешний ключ на users.id, поэтому
    Telegram user_id сначала резолвится во внутренний идентификатор.

    Args:
        tg_id: Telegram user_id пользователя, чей промпт обогащается.
        initial_prompt: Исходный промпт от пользователя.
        enriched_prompt: Обогащённый промпт для сохранения.

    Raises:
        ValueError: Если пользователь с таким tg_id не найден в БД.
        SQLAlchemyError: Если запись не удалось сохранить в БД;
            исключение пробрасывается выше вызывающей стороне.
    """
    try:
        async with SessionLocal() as session:
            session.add(
                GenerationFeedback(
                    user_id=tg_id,
                    initial_prompt=initial_prompt,
                    enriched_prompt=enriched_prompt,
                    is_liked=None,
                )
            )
            await session.commit()
    except SQLAlchemyError as exc:
        logger.error("Failed to save enriched prompt: %s", exc)
        raise


def _extract_message_content(data: dict) -> str | None:
    """Извлекает текст ответа модели из тела OpenAI-совместимого ответа.

    Args:
        data: Разобранный JSON-ответ API.

    Returns:
        Текст сообщения либо None, если контракт ответа нарушен.
    """
    choices = data.get("choices")
    if not choices:
        return None
    message = choices[0].get("message")
    if not message:
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    return content
