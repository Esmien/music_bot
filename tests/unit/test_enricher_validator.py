"""Тесты валидации обогащённых промптов и фоллбэка при сломанном JSON.

Проверяются:
- нормализация текста (удаление комбинирующих ударений, замена «Ё/ё»);
- разбор JSON с markdown-ограждением;
- фоллбэк валидатора на очищенный исходный текст при невалидном JSON;
- поведение сервиса обогащения: None для повтора при сетевой ошибке и
  очищенный raw-текст для прямой отправки/генерации при сломанном JSON.
"""

import json

import httpx
import pytest

from services.enricher import enrich_prompt
from services.enricher_validator import (
    EnrichedSongPrompt,
    _strip_forbidden_diacritics,
    parse_enricher_json,
    validate_enriched_prompt,
)


def test_strip_forbidden_diacritics_removes_accents_and_replaces_yo() -> None:
    """Комбинирующие ударения удаляются, «ё/Ё» заменяются на «е/Е»."""
    raw = "Ро́к À́ Ёлка ёжик"

    assert _strip_forbidden_diacritics(raw) == "Рок A Елка ежик"


def test_enriched_song_prompt_normalizes_all_text_fields() -> None:
    """Все строковые поля контракта нормализуются перед валидацией."""
    prompt = EnrichedSongPrompt.model_validate(
        {
            "genre_and_style": "Ро́к",
            "mood": "Весёлый",
            "instrumentation": ["Гита́ра", "Бараба́н"],
            "tempo_bpm": 120,
            "vocal_style": "Мужско́й",
            "language": "Ру́сский",
            "lyrics": "Ёлка\nёжик",
            "song_structure": ["Купле́т", "Припе́в"],
        }
    )

    assert prompt.genre_and_style == "Рок"
    assert prompt.mood == "Веселый"
    assert prompt.instrumentation == ["Гитара", "Барабан"]
    assert prompt.vocal_style == "Мужской"
    assert prompt.language == "Русский"
    assert prompt.lyrics == "Елка\nежик"
    assert prompt.song_structure == ["Куплет", "Припев"]


def test_parse_enricher_json_strips_markdown_fence() -> None:
    """JSON в markdown-ограждении разбирается корректно."""
    raw = '```json\n{"mood": "тест"}\n```'

    assert parse_enricher_json(raw=raw) == {"mood": "тест"}


def test_parse_enricher_json_rejects_non_object() -> None:
    """Массив вместо объекта считается некорректным контрактом."""
    with pytest.raises(ValueError, match="not an object"):
        parse_enricher_json(raw='["a"]')


def test_parse_enricher_json_raises_on_invalid_json() -> None:
    """Невалидный JSON поднимает JSONDecodeError."""
    with pytest.raises(json.JSONDecodeError):
        parse_enricher_json(raw="not json")


def test_validate_enriched_prompt_returns_normalized_json_for_valid_payload() -> None:
    """Валидный ответ возвращается как нормализованный JSON-контракт."""
    raw = json.dumps(
        {
            "genre_and_style": "Ро́к",
            "mood": "Весёлый",
            "tempo_bpm": 120,
            "lyrics": "Ёлка",
        },
        ensure_ascii=False,
    )

    result = validate_enriched_prompt(raw=raw)
    parsed = json.loads(result)

    assert parsed["genre_and_style"] == "Рок"
    assert parsed["mood"] == "Веселый"
    assert parsed["lyrics"] == "Елка"
    assert parsed["tempo_bpm"] == 120


def test_validate_enriched_prompt_strips_markdown_fence_and_normalizes() -> None:
    """Ответ в markdown-ограждении валидируется и нормализуется."""
    raw = '```json\n{"mood": "Весёлый"}\n```'

    result = validate_enriched_prompt(raw=raw)
    parsed = json.loads(result)

    assert parsed["mood"] == "Веселый"


def test_validate_enriched_prompt_falls_back_to_cleaned_raw_on_invalid_json() -> None:
    """Сломанный JSON не роняет валидатор: возвращается очищенный исходный текст."""
    raw = "Сломанный Ё и ударени́ем"

    assert validate_enriched_prompt(raw=raw) == "Сломанный Е и ударением"


def test_validate_enriched_prompt_falls_back_to_cleaned_raw_on_incomplete_json() -> None:
    """Неполный JSON тоже уходит в фоллбэк с очисткой текста."""
    raw = '{"genre_and_style":"Ро́к"'

    assert validate_enriched_prompt(raw=raw) == '{"genre_and_style":"Рок"'


def test_validate_enriched_prompt_falls_back_on_non_object_json() -> None:
    """JSON-массив нарушает контракт и возвращается как очищенный raw-текст."""
    raw = '["Ро́к"]'

    assert validate_enriched_prompt(raw=raw) == '["Рок"]'


def test_validate_enriched_prompt_falls_back_on_contract_violation() -> None:
    """Нарушение типов контракта возвращает очищенный raw-текст."""
    raw = json.dumps({"tempo_bpm": "не\u0301 число"}, ensure_ascii=False)
    expected = json.dumps({"tempo_bpm": "не число"}, ensure_ascii=False)

    assert validate_enriched_prompt(raw=raw) == expected


@pytest.fixture
def patch_enricher_client(monkeypatch) -> dict[str, object]:
    """Подменяет httpx.AsyncClient в сервисе обогащения на управляемую заглушку.

    Args:
        monkeypatch: Стандартная pytest-фикстура для патчинга атрибутов.

    Returns:
        Словарь-конфигурация ответа/ошибки для тестов.
    """
    from services import enricher as enricher_service

    config = {"payload": None, "raise_error": False}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return config["payload"]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool | None:
            return None

        async def post(self, url, json=None, headers=None) -> FakeResponse:
            if config["raise_error"]:
                raise httpx.HTTPError("network error")
            return FakeResponse()

    monkeypatch.setattr(enricher_service.httpx, "AsyncClient", FakeAsyncClient)
    return config


async def test_enrich_prompt_returns_normalized_json_for_valid_response(
    patch_enricher_client,
) -> None:
    """Сервис возвращает нормализованный JSON при корректном ответе API."""
    content = json.dumps({"mood": "Весёлый"}, ensure_ascii=False)
    patch_enricher_client["payload"] = {"choices": [{"message": {"content": content}}]}

    result = await enrich_prompt(prompt="тест")

    assert result is not None
    assert json.loads(result)["mood"] == "Веселый"


async def test_enrich_prompt_falls_back_to_cleaned_raw_on_invalid_json(
    patch_enricher_client,
) -> None:
    """При невалидном JSON сервис возвращает очищенный raw-текст для прямой отправки."""
    content = "Сломанный Ё и ударени́ем"
    patch_enricher_client["payload"] = {"choices": [{"message": {"content": content}}]}

    result = await enrich_prompt(prompt="тест")

    assert result == "Сломанный Е и ударением"


async def test_enrich_prompt_returns_none_on_network_error(patch_enricher_client) -> None:
    """Сетевая ошибка возвращает None — хендлер должен предложить повтор."""
    patch_enricher_client["raise_error"] = True

    assert await enrich_prompt(prompt="тест") is None


async def test_enrich_prompt_returns_none_on_empty_content(patch_enricher_client) -> None:
    """Пустой ответ модели возвращает None — хендлер должен предложить повтор."""
    patch_enricher_client["payload"] = {"choices": [{"message": {"content": "   "}}]}

    assert await enrich_prompt(prompt="тест") is None
