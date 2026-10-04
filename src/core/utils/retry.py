"""Единая retry-политика для внешних API в соответствии с CONVENTIONS.md.

Предоставляет стратегию ожидания с учётом заголовка Retry-After при HTTP 429,
экспоненциальный backoff и валидацию повторяемых транзиентных ошибок.
"""

import email.utils
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import (
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from core.utils.exceptions import (
    CreditsAPIError,
    GenerationAPIError,
    GenerationStreamError,
)

log = logging.getLogger(__name__)

# Стандартные параметры tenacity согласно CONVENTIONS.md
DEFAULT_RETRY_STOP = stop_after_attempt(3)
DEFAULT_BACKOFF_MIN = 2.0
DEFAULT_BACKOFF_MAX = 10.0
DEFAULT_MAX_RETRY_AFTER = 30.0


def parse_retry_after(value: str | None) -> float | None:
    """Парсит значение HTTP-заголовка Retry-After в секунды.

    Поддерживает как количество секунд (целое или вещественное число),
    так и дату в формате RFC 7231 / RFC 2822.

    Args:
        value: Строковое значение заголовка Retry-After.

    Returns:
        Количество секунд ожидания или None, если заголовок отсутствует или некорректен.
    """
    if not value:
        return None

    stripped = value.strip()
    # Попытка парсинга как число секунд
    try:
        seconds = float(stripped)
        return max(0.0, seconds)
    except (ValueError, TypeError):
        pass

    # Попытка парсинга как HTTP-даты
    try:
        target_dt = email.utils.parsedate_to_datetime(stripped)
        if target_dt is not None:
            now = datetime.now(UTC)
            delta = (target_dt - now).total_seconds()
            return max(0.0, delta)
    except Exception:
        pass

    return None


def extract_retry_after(exc: BaseException | None) -> float | None:
    """Извлекает задержку Retry-After из исключения, если оно содержит ответ или заголовки.

    Args:
        exc: Исключение для инспекции.

    Returns:
        Число секунд для ожидания или None.
    """
    if exc is None:
        return None

    headers: Any = None
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        headers = exc.response.headers
    elif hasattr(exc, "headers") and exc.headers is not None:
        headers = exc.headers
    elif hasattr(exc, "response") and exc.response is not None:
        headers = getattr(exc.response, "headers", None)

    if headers and hasattr(headers, "get"):
        return parse_retry_after(headers.get("Retry-After"))

    return None


def is_retryable_error(exception: BaseException) -> bool:
    """Определяет, является ли ошибка повторяемой согласно CONVENTIONS.md.

    Повторяются только транзиентные сетевые ошибки подключения до ответа
    (TimeoutException, ConnectError) и HTTP-статусы 429 и 503.
    Ошибки авторизации (401, 403), клиентские ошибки (4xx кроме 429),
    серверные ошибки (5xx кроме 503) и сбои чтения потока (post-request)
    строго не повторяются.

    Args:
        exception: Проверяемое исключение.

    Returns:
        True, если запрос можно повторить, иначе False.
    """
    # DEVIATION: AUD-040 включает GenerationStreamError в повторяемые ошибки с UX-уведомлением
    if isinstance(exception, GenerationStreamError):
        return True

    # Транзиентные сетевые ошибки httpx
    if isinstance(exception, (httpx.TimeoutException, httpx.ConnectError)):
        return True

    # httpx.HTTPStatusError
    if isinstance(exception, httpx.HTTPStatusError):
        code = exception.response.status_code if exception.response is not None else None
        return code in (429, 503)

    # Кастомные ошибки сервисов с кодом статуса
    if isinstance(exception, (GenerationAPIError, CreditsAPIError)):
        if exception.status_code is not None:
            return exception.status_code in (429, 503)
        msg = str(exception)
        return "429" in msg or "503" in msg

    return False


class wait_retry_after_or_exponential(wait_base):
    """Стратегия ожидания Tenacity с приоритетом заголовка Retry-After при HTTP 429."""

    def __init__(
        self,
        multiplier: float = 1.0,
        min: float = DEFAULT_BACKOFF_MIN,
        max: float = DEFAULT_BACKOFF_MAX,
        max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
    ) -> None:
        """Инициализация стратегии.

        Args:
            multiplier: Множитель экспоненты.
            min: Минимальная пауза в секундах.
            max: Максимальная пауза в секундах для экспоненты.
            max_retry_after: Предельное значение ожидания по Retry-After.
        """
        self.fallback = wait_exponential(multiplier=multiplier, min=min, max=max)
        self.max_retry_after = max_retry_after

    def __call__(self, retry_state: Any) -> float:
        """Вычисляет паузу перед следующей попыткой.

        Args:
            retry_state: Текущее состояние retry tenacity.

        Returns:
            Пауза в секундах.
        """
        if retry_state.outcome and retry_state.outcome.failed:
            exc = retry_state.outcome.exception()
            retry_after = extract_retry_after(exc)
            if retry_after is not None:
                return min(max(0.0, retry_after), self.max_retry_after)

        return float(self.fallback(retry_state))


async def make_retry_logger(service_name: str) -> Callable[[Any], Any]:
    """Создаёт колбэк before_sleep для логирования попыток повтора с gen_id и фазой.

    Args:
        service_name: Название сервиса/API для идентификации в логе.

    Returns:
        Функция-обработчик для tenacity before_sleep.
    """

    async def _log_attempt(retry_state: Any) -> None:
        attempt = retry_state.attempt_number
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        gen_id = retry_state.kwargs.get("gen_id", "none") if retry_state.kwargs else "none"
        phase = "post-request" if isinstance(exception, GenerationStreamError) else "pre-request"
        log.warning(
            "Retry attempt %d for %s (gen_id=%s, phase=%s) due to %s: %s",
            attempt,
            service_name,
            gen_id,
            phase,
            type(exception).__name__ if exception else "unknown",
            str(exception)[:200] if exception else "",
        )

    return _log_attempt


default_retry_predicate = retry_if_exception(is_retryable_error)
default_retry_wait = wait_retry_after_or_exponential()
