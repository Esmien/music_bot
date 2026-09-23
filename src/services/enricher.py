"""HTTP-клиент сервиса обогащения промпта (Open WebUI, OpenAI-совместимый API).

Сервис изолирован от Telegram: это чистый HTTP-клиент с парсером JSON-ответа
и сохранением результата в БД. Системный промпт хранится в настройках модели
на стороне Open WebUI, поэтому наружу отправляется только пользовательский
текст (при повторном обогащении с правками — вместе с историей диалога).
"""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database.engine import SessionLocal
from core.database.models import GenerationFeedback, User

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 120.0


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
        Обогащённый промпт либо None, если запрос не удался или ответ пуст.

    Raises:
        ValueError: Если не сконфигурирован URL или модель обогатителя.
    """
    if not settings.enrich.ENRICH_URL or not settings.enrich.ENRICH_MODEL:
        raise ValueError("Enricher URL or model is not configured")

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
    return enriched


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
            internal_user_id = await _resolve_internal_user_id(session=session, tg_id=tg_id)
            if internal_user_id is None:
                logger.error("Cannot save enriched prompt: user tg_id=%s is not found", tg_id)
                raise ValueError(f"User with tg_id={tg_id} is not found in database")
            # is_liked=False — заглушка обязательного поля, обновится хендлерами оценки
            session.add(
                GenerationFeedback(
                    user_id=internal_user_id,
                    initial_prompt=initial_prompt,
                    enriched_prompt=enriched_prompt,
                    is_liked=False,
                )
            )
            await session.commit()
    except SQLAlchemyError as exc:
        logger.error("Failed to save enriched prompt: %s", exc)
        raise


async def _resolve_internal_user_id(session: AsyncSession, tg_id: int) -> int | None:
    """Резолвит Telegram user_id в первичный ключ пользователя users.id.

    Args:
        session: Активная сессия БД.
        tg_id: Telegram user_id.

    Returns:
        Внутренний id пользователя либо None, если не найден.
    """
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    db_user = result.scalar_one_or_none()
    return db_user.id if db_user else None


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
