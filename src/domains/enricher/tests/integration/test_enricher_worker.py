"""Интеграционные тесты воркера обогащения промптов."""

import pytest

from shared.contracts.commands import StartEnrichment
from shared.contracts.events import EnrichmentCompleted, GenerationFailed
from shared.ports.fake_telegram import FakeTelegramPort


@pytest.mark.asyncio
async def test_enrich_prompt_task_success(monkeypatch):
    """Проверяет успешное выполнение задачи обогащения.

    Args:
        monkeypatch: Pytest monkeypatch для подмены зависимостей.
    """

    # Мокаем enrich_prompt
    async def mock_enrich(prompt: str, history: list[dict[str, str]] | None = None) -> str:
        return '{"enriched_prompt": "Test enriched prompt"}'

    monkeypatch.setattr("domains.enricher.worker.enrich_prompt", mock_enrich)

    # Мокаем validate_enriched_prompt
    def mock_validate(raw: str) -> str:
        return "Test enriched prompt"

    monkeypatch.setattr("domains.enricher.worker.validate_enriched_prompt", mock_validate)

    # Список опубликованных событий
    published_events = []

    class FakeBroker:
        def kicker(self, task_name: str):
            class FakeKicker:
                async def kiq(_, event):
                    published_events.append((event, task_name))

            return FakeKicker()

    # Импортируем воркер после применения патчей
    import domains.enricher.worker
    from domains.enricher.worker import enrich_prompt_task

    monkeypatch.setattr(domains.enricher.worker, "broker", FakeBroker())

    # Создаём команду
    command = StartEnrichment(
        user_id=123,
        chat_id=456,
        prompt="Test prompt",
        history=None,
    )

    # Создаём контекст с зависимостями
    class FakeContext:
        dependencies = {"telegram_port": FakeTelegramPort()}

    # Выполняем задачу
    await enrich_prompt_task(command=command, context=FakeContext())

    # Проверяем, что опубликовано событие успеха
    assert len(published_events) == 1
    event, task_name = published_events[0]
    assert isinstance(event, EnrichmentCompleted)
    assert event.user_id == 123
    assert event.chat_id == 456
    assert event.enriched_prompt == "Test enriched prompt"
    assert task_name == "handle_enrichment_completed"


@pytest.mark.asyncio
async def test_enrich_prompt_task_failure(monkeypatch):
    """Проверяет обработку ошибки при обогащении.

    Args:
        monkeypatch: Pytest monkeypatch для подмены зависимостей.
    """

    # Мокаем enrich_prompt с ошибкой
    async def mock_enrich_error(prompt: str, history: list[dict[str, str]] | None = None) -> str:
        raise ValueError("API error")

    monkeypatch.setattr("domains.enricher.worker.enrich_prompt", mock_enrich_error)

    # Список опубликованных событий
    published_events = []

    class FakeBroker:
        def kicker(self, task_name: str):
            class FakeKicker:
                async def kiq(_, event):
                    published_events.append((event, task_name))

            return FakeKicker()

    # Мокаем notify_owner
    notified = []

    async def mock_notify(telegram_port=None, error=None, err=None, context=""):
        notified.append((err or error, context))

    monkeypatch.setattr("domains.enricher.worker.notify_owner", mock_notify)

    # Импортируем воркер после применения патчей
    import domains.enricher.worker
    from domains.enricher.worker import enrich_prompt_task

    monkeypatch.setattr(domains.enricher.worker, "broker", FakeBroker())

    # Создаём команду
    command = StartEnrichment(
        user_id=123,
        chat_id=456,
        prompt="Test prompt",
        history=None,
    )

    # Создаём контекст с зависимостями
    class FakeContext:
        dependencies = {"telegram_port": FakeTelegramPort()}

    # Выполняем задачу
    await enrich_prompt_task(command=command, context=FakeContext())

    # Проверяем, что владелец уведомлён
    assert len(notified) == 1
    error, context = notified[0]
    assert isinstance(error, ValueError)
    assert "user_id=123" in context

    # Проверяем, что опубликовано событие сбоя
    assert len(published_events) == 1
    event, task_name = published_events[0]
    assert isinstance(event, GenerationFailed)
    assert event.user_id == 123
    assert event.chat_id == 456
    assert event.stage == "enrichment"
    assert task_name == "handle_generation_failed"


@pytest.mark.asyncio
async def test_enrich_prompt_task_invalid_response(monkeypatch):
    """Проверяет обработку невалидного ответа от обогатителя.

    Args:
        monkeypatch: Pytest monkeypatch для подмены зависимостей.
    """

    # Мокаем enrich_prompt с невалидным ответом
    async def mock_enrich_invalid(prompt: str, history: list[dict[str, str]] | None = None) -> str:
        return "not json"

    monkeypatch.setattr("domains.enricher.worker.enrich_prompt", mock_enrich_invalid)

    # Мокаем validate_enriched_prompt с ошибкой
    def mock_validate_error(raw: str) -> str:
        raise ValueError("Invalid JSON")

    monkeypatch.setattr("domains.enricher.worker.validate_enriched_prompt", mock_validate_error)

    # Список опубликованных событий
    published_events = []

    class FakeBroker:
        def kicker(self, task_name: str):
            class FakeKicker:
                async def kiq(_, event):
                    published_events.append((event, task_name))

            return FakeKicker()

    # Мокаем notify_owner
    notified = []

    async def mock_notify(telegram_port=None, error=None, err=None, context=""):
        notified.append((err or error, context))

    monkeypatch.setattr("domains.enricher.worker.notify_owner", mock_notify)

    # Импортируем воркер после применения патчей
    import domains.enricher.worker
    from domains.enricher.worker import enrich_prompt_task

    monkeypatch.setattr(domains.enricher.worker, "broker", FakeBroker())

    # Создаём команду
    command = StartEnrichment(
        user_id=123,
        chat_id=456,
        prompt="Test prompt",
        history=None,
    )

    # Создаём контекст с зависимостями
    class FakeContext:
        dependencies = {"telegram_port": FakeTelegramPort()}

    # Выполняем задачу
    await enrich_prompt_task(command=command, context=FakeContext())

    # Проверяем, что владелец уведомлён
    assert len(notified) == 1

    # Проверяем, что опубликовано событие сбоя
    assert len(published_events) == 1
    event, task_name = published_events[0]
    assert isinstance(event, GenerationFailed)
    assert event.stage == "enrichment"
