"""Сервис проверки оставшихся кредитов OpenRouter."""

from dataclasses import dataclass

import httpx


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


async def get_credits_summary(*, api_key: str, song_price: float) -> CreditsSummary:
    """Запрашивает баланс OpenRouter и пересчитывает суммы в число генераций.

    Args:
        api_key: Ключ API OpenRouter.
        song_price: Стоимость одной генерации в долларах.

    Returns:
        Сводка с кодом ответа и количеством доступных генераций.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url="https://openrouter.ai/api/v1/key", headers=headers)
        if response.status_code != 200:
            return CreditsSummary(status_code=response.status_code)

        key_info = response.json().get("data", {})
        total = key_info.get("limit")
        remaining = key_info.get("limit_remaining")
        used = key_info.get("usage")

        return CreditsSummary(
            status_code=response.status_code,
            total_songs=_songs_counter(value=total, song_price=song_price, placeholder="Без лимита"),
            used_songs=_songs_counter(value=used, song_price=song_price, placeholder="0"),
            remaining_songs=_songs_counter(
                value=remaining,
                song_price=song_price,
                placeholder="Невозможно посчитать",
            ),
        )
