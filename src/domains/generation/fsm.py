"""FSM-состояния и служебные операции сценария генерации."""

import json
import logging

from aiogram.fsm.state import State, StatesGroup
from redis.exceptions import RedisError

from core.redis import redis_client

log = logging.getLogger(__name__)

# Полный текст песни (куплеты + припевы) в среднем занимает 1500–3000 символов
MAX_PROMPT_LEN = 4000
MAX_TITLE_LEN = 100

# Префикс ключей FSM-хранилища aiogram (RedisStorage по умолчанию)
_FSM_KEY_MATCH = "fsm:*"


class GenerationStates(StatesGroup):
    """FSM-состояния процесса генерации песни.

    Атрибуты:
        waiting_for_prompt: Ждём описание/текст песни.
        waiting_for_title: Ждём название трека.
    """

    waiting_for_prompt = State()
    waiting_for_title = State()


async def clear_orphaned_generation_flags() -> int:
    """Чистит осиротевшие флаги generating в FSM после перезапуска бота.

    FSM-состояния живут в Redis и переживают рестарт, а задачи генерации —
    нет: без чистки пользователь навсегда оставался бы с «Дождитесь
    окончания текущей генерации». Вызывается на старте, когда active_tasks
    ещё пуст, поэтому любой выставленный флаг считается осиротевшим.
    RedisStorage aiogram 3 хранит данные FSM как строковый ключ с JSON
    (не как hash), поэтому чтение и запись идут через get/set.

    Инвариант: префикс fsm: зарезервирован за RedisStorage, под ним
    лежат только FSM-записи с JSON в значении ключа. Если складывать туда
    свои ключи в другом формате, scan_iter попытается распарсить их
    как JSON и пропустит с предупреждением в логе.

    Returns:
        Количество очищенных FSM-записей.
    """
    cleaned = 0
    try:
        async for key in redis_client.scan_iter(match=_FSM_KEY_MATCH):
            raw = await redis_client.get(key)
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
            await redis_client.set(key, json.dumps(data))
            cleaned += 1
            log.info("Cleared orphaned generation flag (key=%s)", key)
    except RedisError:
        log.exception("Failed to clean orphaned generation flags in Redis")
    return cleaned
