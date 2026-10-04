"""Управление жизненным циклом экземпляра бота для безопасного rolling restart.

Каждый экземпляр бота получает уникальный instance_id и регистрируется
в Redis с heartbeat. При startup cleanup проверяем наличие других живых
экземпляров — если они есть, пропускаем cleanup, чтобы не удалить их данные.
"""

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

from core.redis import redis_client

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Ключ множества активных экземпляров бота
ACTIVE_INSTANCES_KEY = "bot:instances:active"

# TTL heartbeat для экземпляра (секунды)
INSTANCE_HEARTBEAT_TTL = 30

# Префикс для heartbeat ключей: bot:instance:heartbeat:{instance_id}
INSTANCE_HEARTBEAT_PREFIX = "bot:instance:heartbeat"


class BotInstance:
    """Представляет один экземпляр бота с уникальным ID и heartbeat."""

    def __init__(self) -> None:
        """Создает экземпляр с уникальным ID."""
        self.instance_id = str(uuid.uuid4())
        self._heartbeat_task: asyncio.Task | None = None
        self._shutdown_requested = False

    def heartbeat_key(self) -> str:
        """Возвращает Redis-ключ heartbeat для этого экземпляра.

        Returns:
            Ключ heartbeat в Redis.
        """
        return f"{INSTANCE_HEARTBEAT_PREFIX}:{self.instance_id}"

    async def register(self) -> None:
        """Регистрирует экземпляр в Redis и запускает heartbeat.

        Raises:
            Exception: При ошибке регистрации в Redis.
        """
        try:
            await redis_client.sadd(ACTIVE_INSTANCES_KEY, self.instance_id)
            await redis_client.set(self.heartbeat_key(), "1", ex=INSTANCE_HEARTBEAT_TTL)
            log.info("Bot instance registered: %s", self.instance_id)
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        except Exception as e:
            log.exception("Failed to register bot instance %s: %s", self.instance_id, e)
            raise

    async def unregister(self) -> None:
        """Снимает регистрацию экземпляра и останавливает heartbeat.

        Идемпотентна: повторный вызов безопасен.
        """
        self._shutdown_requested = True

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task

        try:
            await redis_client.srem(ACTIVE_INSTANCES_KEY, self.instance_id)
            await redis_client.delete(self.heartbeat_key())
            log.info("Bot instance unregistered: %s", self.instance_id)
        except Exception as e:
            log.exception("Failed to unregister bot instance %s: %s", self.instance_id, e)

    async def _heartbeat_loop(self) -> None:
        """Периодически обновляет heartbeat в Redis."""
        while not self._shutdown_requested:
            try:
                await asyncio.sleep(INSTANCE_HEARTBEAT_TTL // 2)
                if not self._shutdown_requested:
                    await redis_client.set(self.heartbeat_key(), "1", ex=INSTANCE_HEARTBEAT_TTL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("Heartbeat update failed for instance %s: %s", self.instance_id, e)

    async def get_active_instances(self) -> set[str]:
        """Возвращает множество активных instance_id из Redis.

        Returns:
            Множество строковых instance_id.

        Raises:
            Exception: При ошибке чтения из Redis.
        """
        members = await redis_client.smembers(ACTIVE_INSTANCES_KEY)
        return {m.decode("utf-8") if isinstance(m, bytes) else m for m in members}

    async def has_other_active_instances(self) -> bool:
        """Проверяет, есть ли другие живые экземпляры бота.

        Returns:
            True если есть другие активные экземпляры с живым heartbeat.

        Raises:
            Exception: При ошибке чтения из Redis.
        """
        all_instances = await self.get_active_instances()
        other_instances = all_instances - {self.instance_id}

        if not other_instances:
            return False

        # Проверяем heartbeat каждого экземпляра
        for instance_id in other_instances:
            heartbeat_key = f"{INSTANCE_HEARTBEAT_PREFIX}:{instance_id}"
            exists = await redis_client.exists(heartbeat_key)
            if exists:
                return True

        return False

    async def cleanup_stale_instances(self) -> int:
        """Удаляет записи экземпляров без активного heartbeat.

        Returns:
            Количество удаленных stale записей.

        Raises:
            Exception: При ошибке работы с Redis.
        """
        all_instances = await self.get_active_instances()
        stale_count = 0

        for instance_id in all_instances:
            # Не проверяем свой собственный instance_id
            if instance_id == self.instance_id:
                continue

            heartbeat_key = f"{INSTANCE_HEARTBEAT_PREFIX}:{instance_id}"
            exists = await redis_client.exists(heartbeat_key)
            if not exists:
                await redis_client.srem(ACTIVE_INSTANCES_KEY, instance_id)
                stale_count += 1
                log.info("Removed stale instance record: %s", instance_id)

        return stale_count


# Глобальный экземпляр для использования в приложении
current_instance = BotInstance()
