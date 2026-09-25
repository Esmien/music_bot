"""HTTP-клиент обогащения промптов и сохранение результата в БД."""

import json
import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError

from core.config import settings
from core.database.engine import SessionLocal
from core.database.models import GenerationFeedback
from core.utils.exceptions import EnricherNotConfiguredError
from domains.enricher.validator import parse_enricher_json, validate_enriched_prompt

logger = logging.getLogger(__name__)

_parse_enricher_json = parse_enricher_json

REQUEST_TIMEOUT_SECONDS = 120.0

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
    """Обогащает промпт через OpenAI-совместимый API.

    Args:
        prompt: Пользовательский промпт или текст правок.
        history: Предыдущие сообщения диалога в формате OpenAI-чата.

    Returns:
        Нормализованный JSON или очищенный исходный текст. При сбое
        соединения или пустом ответе возвращает None.

    Raises:
        EnricherNotConfiguredError: URL или модель обогатителя не настроены.
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
    """Форматирует JSON-ответ обогатителя для показа пользователю.

    Args:
        raw: JSON-строка или произвольный текст от обогатителя.

    Returns:
        Текст с заголовками секций либо исходный текст, если JSON некорректен.
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
            body = "\n".join(str(item) for item in value) if field == "song_structure" else ", ".join(map(str, value))
        else:
            body = str(value).strip()
        sections.append(f"{title}:\n{body}")

    return "\n\n".join(sections) if sections else raw.strip()


async def save_enriched_prompt(tg_id: int, initial_prompt: str, enriched_prompt: str) -> None:
    """Сохраняет исходный и обогащённый промпты в записи фидбека.

    Args:
        tg_id: Telegram user_id пользователя.
        initial_prompt: Исходный промпт пользователя.
        enriched_prompt: Обогащённый промпт.

    Raises:
        SQLAlchemyError: Если запись не удалось сохранить.
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
    """Извлекает текст из OpenAI-совместимого ответа.

    Args:
        data: Разобранное тело ответа API.

    Returns:
        Текст ответа или None, если структура ответа некорректна.
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
