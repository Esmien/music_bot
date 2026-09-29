"""Сервис проверки оставшихся кредитов OpenRouter."""

import logging
from dataclasses import dataclass

import httpx
from tenacity import retry

from core.utils.exceptions import CreditsAPIError
from core.utils.retry import (
    DEFAULT_RETRY_STOP,
    default_retry_predicate,
    default_retry_wait,
    make_retry_logger,
)

logger = logging.getLogger(__name__)

_log_credits_retry = make_retry_logger("OpenRouter Credits API")


@dataclass(frozen=True)
class CreditsSummary:
    """Сводка по доступным кредитам и примерному числу генераций."""

    status_code: int
    total_songs: int | str | None = None
    used_songs: int | str | None = None
    remaining_songs: int | str | None = None


def _songs_counter(value: int | float | None, song_price: float, placeholder: str) -> int | str:
    """Конвертирует сумму в долларах в примерное число песен.

    Args:
        value: Сумма или None, если API не вернул значение.
        song_price: Стоимость одной генерации в долларах.
        placeholder: Заглушка, если посчитать нельзя.

    Returns:
        Число песен либо placeholder.
    """
    if isinstance(value, (int, float)) and song_price > 0:
        return int(value / song_price)
    return placeholder


@retry(
    stop=DEFAULT_RETRY_STOP,
    wait=default_retry_wait,
    retry=default_retry_predicate,
    before_sleep=_log_credits_retry,
    reraise=True,
)
async def _fetch_credits_data(api_key: str) -> tuple[int, dict]:
    """Выполняет защищённый retry-политикой GET-запрос к OpenRouter API для проверки ключа.

    Args:
        api_key: Ключ API OpenRouter.

    Returns:
        Кортеж (status_code, data_dict).

    Raises:
        CreditsAPIError: При повторяемых статусах 429 или 503.
        httpx.HTTPError: При транзиентных сетевых сбоях.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url="https://openrouter.ai/api/v1/key", headers=headers)
        # 429 и 503 вызывают CreditsAPIError для запуска retry с экспоненциальным backoff и Retry-After
        if response.status_code in (429, 503):
            raise CreditsAPIError(
                f"OpenRouter credits API returned {response.status_code}",
                status_code=response.status_code,
                headers=getattr(response, "headers", None),
            )
        if response.status_code != 200:
            return response.status_code, {}

        return response.status_code, response.json().get("data", {})


async def get_credits_summary(*, api_key: str, song_price: float) -> CreditsSummary:
    """Запрашивает баланс OpenRouter и пересчитывает суммы в число генераций.

    Args:
        api_key: Ключ API OpenRouter.
        song_price: Стоимость одной генерации в долларах.

    Returns:
        Сводка с кодом ответа и количеством доступных генераций.
    """
    try:
        status_code, key_info = await _fetch_credits_data(api_key=api_key)
    except CreditsAPIError as exc:
        logger.warning("Credits API request failed after retries: %s", exc)
        return CreditsSummary(status_code=exc.status_code or 503)

    if status_code != 200:
        return CreditsSummary(status_code=status_code)

    total = key_info.get("limit")
    remaining = key_info.get("limit_remaining")
    used = key_info.get("usage")

    return CreditsSummary(
        status_code=status_code,
        total_songs=_songs_counter(value=total, song_price=song_price, placeholder="Без лимита"),
        used_songs=_songs_counter(value=used, song_price=song_price, placeholder="0"),
        remaining_songs=_songs_counter(
            value=remaining,
            song_price=song_price,
            placeholder="Невозможно посчитать",
        ),
    )
