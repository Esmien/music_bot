"""Метрики Prometheus для жизненного цикла генерации, доставки и инфраструктуры."""

from typing import Any

try:
    from prometheus_client import Counter, Histogram
except ImportError:  # pragma: no cover - fallback при отсутствии библиотеки

    class _MetricStub:
        """Заглушка метрики при отсутствии установленного prometheus_client."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.values: dict[tuple[Any, ...], float] = {}

        def labels(self, *args: Any, **kwargs: Any) -> "_MetricStub":
            return self

        def inc(self, amount: float = 1.0, **kwargs: Any) -> None:
            key = tuple(sorted(kwargs.items()))
            self.values[key] = self.values.get(key, 0.0) + amount

        def observe(self, amount: float) -> None:
            self.values[("observed",)] = amount

    Counter = _MetricStub  # type: ignore[misc,assignment]
    Histogram = _MetricStub  # type: ignore[misc,assignment]


# 1. Счетчики статусов генерации
GENERATION_TOTAL = Counter(
    "lyria_generation_total",
    "Количество генераций по финальному статусу",
    ["status"],
)

# 2. Счетчики статусов доставки пользователю в Telegram
DELIVERY_TOTAL = Counter(
    "lyria_delivery_total",
    "Количество попыток доставки аудиофайла пользователю",
    ["status"],
)

# 3. Задержки ключевых этапов
GENERATION_LATENCY_SECONDS = Histogram(
    "lyria_generation_latency_seconds",
    "Длительность этапа генерации через OpenRouter (в секундах)",
    buckets=(1.0, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0),
)

STORAGE_SAVE_LATENCY_SECONDS = Histogram(
    "lyria_storage_save_latency_seconds",
    "Длительность сохранения аудиофайла на диск (в секундах)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

DELIVERY_LATENCY_SECONDS = Histogram(
    "lyria_delivery_latency_seconds",
    "Длительность отправки аудиофайла через Telegram API (в секундах)",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0),
)

# 4. Счетчики отклоненных устаревших попыток и сбоев Redis
STALE_ATTEMPTS_TOTAL = Counter(
    "lyria_stale_attempts_total",
    "Количество отклоненных устаревших попыток генерации и доставки",
)

REDIS_ERRORS_TOTAL = Counter(
    "lyria_redis_errors_total",
    "Количество ошибок взаимодействия с Redis",
    ["operation"],
)
