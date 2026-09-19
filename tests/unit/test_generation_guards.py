"""Юнит-тесты вспомогательных механизмов хендлера генерации."""

import pytest

from handlers.generation_pipeline import _generation_lock

pytestmark = pytest.mark.unit


def test_generation_lock_is_per_user():
    """Один и тот же пользователь получает один лок, разные — разные."""
    first = _generation_lock(1)
    assert _generation_lock(1) is first
    assert _generation_lock(2) is not first
