"""Валидатор JSON-ответов обогатителя и нормализатор текста."""

import json
import logging
import unicodedata
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from core.utils.exceptions import EnricherResponseInvalidError

logger = logging.getLogger(__name__)

_YO_TRANSLATION = str.maketrans("ёЁ", "еЕ")
_PROTECT_SHORT_I = str.maketrans("йЙ", "\ue000\ue001")
_RESTORE_SHORT_I = str.maketrans("\ue000\ue001", "йЙ")


def _strip_forbidden_diacritics(text: str) -> str:
    """Удаляет диакритические знаки, сохраняет «й» и заменяет «ё» на «е».

    Args:
        text: Исходный текст.

    Returns:
        Нормализованный текст.
    """
    protected = text.translate(_PROTECT_SHORT_I)
    decomposed = unicodedata.normalize("NFD", protected)
    without_marks = "".join(ch for ch in decomposed if unicodedata.combining(ch) == 0)
    return unicodedata.normalize("NFC", without_marks).translate(_RESTORE_SHORT_I).translate(_YO_TRANSLATION)


class EnrichedSongPrompt(BaseModel):
    """Модель JSON-контракта обогащённого промпта."""

    genre_and_style: str | None = None
    mood: str | None = None
    instrumentation: list[str] = Field(default_factory=list)
    tempo_bpm: int | None = None
    vocal_style: str | None = None
    language: str | None = None
    lyrics: str | None = None
    song_structure: list[str] = Field(default_factory=list)

    @field_validator("genre_and_style", "mood", "vocal_style", "language", "lyrics", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> Any:
        """Нормализует строковое значение поля.

        Args:
            value: Исходное значение.

        Returns:
            Нормализованная строка или исходное значение.
        """
        if isinstance(value, str):
            return _strip_forbidden_diacritics(value)
        return value

    @field_validator("instrumentation", "song_structure", mode="before")
    @classmethod
    def _normalize_text_list(cls, value: Any) -> Any:
        """Нормализует строковые элементы списка.

        Args:
            value: Исходное значение.

        Returns:
            Список с нормализованными строками или исходное значение.
        """
        if isinstance(value, list):
            return [_strip_forbidden_diacritics(item) if isinstance(item, str) else item for item in value]
        return value


def parse_enricher_json(raw: str) -> dict:
    """Разбирает JSON-ответ обогатителя, снимая markdown-ограждение.

    Args:
        raw: Сырой ответ обогатителя.

    Returns:
        Разобранный JSON-объект.

    Raises:
        json.JSONDecodeError: Если строка не является корректным JSON.
        EnricherResponseInvalidError: Если JSON не является объектом.
    """
    text = raw.strip()
    if text.startswith("```"):
        newline_index = text.find("\n")
        text = text[newline_index + 1 :] if newline_index != -1 else text.lstrip("`")
        closing_index = text.rfind("```")
        if closing_index != -1:
            text = text[:closing_index]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise EnricherResponseInvalidError("Enricher JSON payload is not an object")
    return data


def validate_enriched_prompt(raw: str) -> str:
    """Проверяет и нормализует JSON-ответ или возвращает очищенный raw-текст.

    Args:
        raw: Сырой ответ обогатителя.

    Returns:
        Нормализованный JSON или очищенный от диакритики исходный текст.
    """
    try:
        data = parse_enricher_json(raw=raw)
        prompt = EnrichedSongPrompt.model_validate(data)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Enricher response does not match contract, keeping raw text: %s", exc)
        return _strip_forbidden_diacritics(raw)
    return prompt.model_dump_json()
