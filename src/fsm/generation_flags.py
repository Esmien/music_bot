"""Чистка осиротевших FSM-флагов генерации после перезапуска бота.

Вынесено из evaluation_fsm.py: функция не относится к состояниям
обогащения промпта, а обслуживает сценарий генерации.
"""

import json
import logging

from redis.exceptions import RedisError

from core.redis import redis_client

log = logging.getLogger(__name__)

# Префикс ключей FSM-хранилища aiogram (RedisStorage по умолчанию)
_FSM_KEY_MATCH = "fsm:*"


async def clear_orphaned_generation_flags() -> int:
    """Чистит осиротевшие флаги generating в FSM после перезапуска бота.

    FSM-состояния живут в Redis и переживают рестарт, а задачи генерации —
    нет: без чистки пользователь навсегда оставался бы с «Дождитесь
    окончания текущей генерации». Вызывается на старте, когда active_tasks
    ещё пуст, поэтому любой выставленный флаг считается осиротевшим.
    Данные FSM RedisStorage хранит как JSON в hash-поле data.

    Инвариант: префикс fsm: зарезервирован за RedisStorage, под ним
    лежат только FSM-записи с JSON в поле data. Если складывать туда
    свои ключи в другом формате, scan_iter попытается распарсить их
    как JSON и пропустит с предупреждением в логе.

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
