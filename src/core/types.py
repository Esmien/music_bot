"""Общие type aliases для проекта.

Централизованное место для часто используемых типов, которые применяются
в разных доменах: JSON-структуры, callback'и, прогресс-репортеры и т.д.
"""

from collections.abc import Awaitable, Callable
from typing import Any

# Рекурсивный тип для произвольных JSON-структур
JSONValue = dict[str, Any] | list[Any] | str | int | float | bool | None

# Callback для отправки прогресса генерации/обогащения.
# Принимает текстовое сообщение, отправляет пользователю асинхронно.
ProgressReporter = Callable[[str], Awaitable[None]]

# Callback для отправки прогресса с дробной частью (0.0-1.0).
# Используется в генерации музыки для прогресс-бара.
ProgressCallback = Callable[[str, float], Awaitable[None]]
