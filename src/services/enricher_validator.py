"""Валидатор обогащённых промптов, приходящих от энричера.

Ответ LLM должен соответствовать JSON-контракту из docs/instructions.md.
Дополнительно модель нормализует текст: убирает знаки ударения и букву «ё»,
которые категорически запрещены контрактом.
"""

import json
import logging
import unicodedata
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from core.utils.exceptions import EnricherResponseInvalidError

logger = logging.getLogger(__name__)

_YO_TRANSLATION = str.maketrans("ёЁ", "еЕ")

# DEVIATION: «й» защищаем placeholder-ом из PUA на время NFD-разложения,
# иначе он раскладывается в «и»+breve и буква теряется; остальную диакритику
# (включая прекомпозированные «À», «é») снимаем через NFD + фильтр знаков.
_PROTECT_SHORT_I = str.maketrans("йЙ", "\ue000\ue001")
_RESTORE_SHORT_I = str.maketrans("\ue000\ue001", "йЙ")


def _strip_forbidden_diacritics(text: str) -> str:
    """Убирает знаки ударения (в т.ч. с прекомпозированных букв) и заменяет «ё» на «е».

    Args:
        text: Исходная строка.

    Returns:
        Строка без ударений и буквы «ё», с сохранением «й».
    """
    protected = text.translate(_PROTECT_SHORT_I)
    decomposed = unicodedata.normalize("NFD", protected)
    without_marks = "".join(ch for ch in decomposed if unicodedata.combining(ch) == 0)
    return unicodedata.normalize("NFC", without_marks).translate(_RESTORE_SHORT_I).translate(_YO_TRANSLATION)


class EnrichedSongPrompt(BaseModel):
    """Контракт обогащённого промпта песни.

    Поля соответствуют JSON-структуре из docs/instructions.md.
    Все строковые значения проходят нормализацию (без ударений и «ё»).
    """

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
        """Нормализует строковое поле, пропуская не-строки дальше по валидации Pydantic.

        Args:
            value: Сырое значение поля.

        Returns:
            Нормализованная строка либо исходное значение.
        """
        if isinstance(value, str):
            return _strip_forbidden_diacritics(value)
        return value

    @field_validator("instrumentation", "song_structure", mode="before")
    @classmethod
    def _normalize_text_list(cls, value: Any) -> Any:
        """Нормализует каждый строковый элемент списка.

        Args:
            value: Сырое значение поля.

        Returns:
            Список с нормализованными строками либо исходное значение.
        """
        if isinstance(value, list):
            return [_strip_forbidden_diacritics(item) if isinstance(item, str) else item for item in value]
        return value


def parse_enricher_json(raw: str) -> dict:
    """Разбирает сырой ответ обогатителя в словарь.

    Снимает markdown-ограждение кода (```json ... ```), которым модель
    может обернуть JSON, и проверяет, что результат — объект.

    Args:
        raw: Сырой ответ обогатителя.

    Returns:
        Разобранный JSON-объект с полями промпта.

    Raises:
        json.JSONDecodeError: Если текст не является корректным JSON.
        EnricherResponseInvalidError: Если JSON не является объектом.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Срезаем открывающую строку ограждения (``` или ```json)
        newline_index = text.find("\n")
        text = text[newline_index + 1 :] if newline_index != -1 else text.lstrip("`")
        closing_index = text.rfind("```")
        if closing_index != -1:
            text = text[:closing_index]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise EnricherResponseInvalidError("Enricher JSON payload is not an object")
    return data


def validate_enriched_prompt(raw: str) -> str | None:
    """Проверяет ответ обогатителя по контракту и возвращает нормализованный JSON.

    Если ответ не парсится как JSON или не проходит контракт, возвращаем
    исходный текст (всё равно очищенный от ударений/«ё»), чтобы не ломать
    сценарий — format_enriched_prompt тоже умеет работать с вольным текстом.

    Args:
        raw: Сырой ответ обогатителя.

    Returns:
        JSON-строка по контракту (очищенная от ударений/«ё») либо сырой
        текст, очищенный от ударений/«ё», если контракт не выполнен.
    """
    try:
        data = parse_enricher_json(raw=raw)
        prompt = EnrichedSongPrompt.model_validate(data)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Enricher response does not match contract, keeping raw text: %s", exc)
        return _strip_forbidden_diacritics(raw)
    return prompt.model_dump_json()
