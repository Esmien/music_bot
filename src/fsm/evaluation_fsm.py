"""Общее состояние между обработчиками, без циклических импортов.

Вынесено в отдельный модуль, чтобы auth и filters могли ссылаться
на одни и те же данные, не импортируя друг друга.

pending_auth хранится в Redis: как и FSM-состояния, реестр ожидающих
ключ переживает рестарт контейнера. Реестр живых задач генерации
active_tasks переехал в core/task_registry.py; осиротевшие флаги
generating вычищаются на старте бота.
"""

import json
import logging

from aiogram.fsm.state import State, StatesGroup
from redis.exceptions import RedisError

from core.redis import redis_client

log = logging.getLogger(__name__)

# Ключ множества пользователей, ожидающих ввода ключа доступа
PENDING_AUTH_KEY = "bot:pending_auth"

# Префикс ключей FSM-хранилища aiogram (RedisStorage по умолчанию)
_FSM_KEY_MATCH = "fsm:*"


async def add_pending_auth(uid: int) -> None:
    """Отмечает пользователя как ожидающего ввод ключа доступа.

    Args:
        uid: Telegram user_id.
    """
    await redis_client.sadd(PENDING_AUTH_KEY, uid)


async def discard_pending_auth(uid: int) -> None:
    """Убирает пользователя из ожидающих ввод ключа (идемпотентно).

    Args:
        uid: Telegram user_id.
    """
    await redis_client.srem(PENDING_AUTH_KEY, uid)


async def is_pending_auth(uid: int) -> bool:
    """Проверяет, ожидает ли пользователь ввод ключа доступа.

    Args:
        uid: Telegram user_id.

    Returns:
        True, если пользователь в реестре ожидающих.
    """
    return bool(await redis_client.sismember(PENDING_AUTH_KEY, uid))


async def clear_orphaned_generation_flags() -> int:
    """Чистит осиротевшие флаги generating в FSM после перезапуска бота.

    FSM-состояния живут в Redis и переживают рестарт, а задачи генерации —
    нет: без чистки пользователь навсегда оставался бы с «Дождитесь
    окончания текущей генерации». Вызывается на старте, когда active_tasks
    ещё пуст, поэтому любой выставленный флаг считается осиротевшим.
    Данные FSM RedisStorage хранит как JSON в hash-поле data.

    Returns:
        Количество очищенных FSM-записей.
    """
    cleaned = 0
    try:
        async for key in redis_client.scan_iter(match=_FSM_KEY_MATCH):
            raw = await redis_client.hget(key, "data")
            if raw is None:
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                log.warning("Skipping non-JSON FSM data under key %s", key)
                continue
            if not isinstance(data, dict) or not data.get("generating"):
                continue
            data.pop("generating", None)
            data.pop("gen_id", None)
            await redis_client.hset(key, "data", json.dumps(data))
            cleaned += 1
            log.info("Cleared orphaned generation flag (key=%s)", key)
    except RedisError:
        log.exception("Failed to clean orphaned generation flags in Redis")
    return cleaned


class PromptEnricherStates(StatesGroup):
    """FSM-состояния сценария обогащения промпта.

    Attributes:
        waiting_for_idea: Ожидание промпта пользователя.
        waiting_for_approval: Ожидание подтверждения сгенерированного промпта.
        waiting_for_edits: Ожидание правок сгенерированного промпта.
    """

    waiting_for_idea = State()
    waiting_for_approval = State()
    waiting_for_edits = State()


class FeedbackStates(StatesGroup):
    """FSM-состояния сценария сбора фидбека по генерации.

    Attributes:
        waiting_evaluation: Ожидание оценки (понравилось/не понравилось).
        waiting_feedback: Ожидание фидека (сообщение пользователя, что ок, что нет).
    """

    waiting_evaluation = State()
    waiting_feedback = State()
