"""Юнит-тесты текстового индикатора прогресса из сервисного конвейера."""

import pytest

from domains.generation.service import _progress_bar

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fraction, width, expected",
    [
        pytest.param(0.0, 10, "░" * 10, id="empty-at-start"),
        pytest.param(1.0, 10, "█" * 10, id="full-at-end"),
        pytest.param(0.5, 10, "█" * 5 + "░" * 5, id="half"),
        pytest.param(0.3, 10, "█" * 3 + "░" * 7, id="rounds-to-nearest"),
        pytest.param(0.99, 10, "█" * 10, id="rounds-up-to-full"),
        pytest.param(0.04, 10, "░" * 10, id="rounds-down-to-empty"),
        pytest.param(0.25, 4, "█" + "░" * 3, id="custom-width"),
    ],
)
def test_progress_bar(fraction, width, expected):
    """Полоса строится по round(fraction * width): заполненные и пустые блоки.

    Отдельно покрываем округление вверх (0.99 → полная) и вниз
    (0.04 → пустая), а также нестандартную ширину.
    """
    assert _progress_bar(fraction, width) == expected


def test_progress_bar_default_width():
    """Без указания ширины используется значение по умолчанию (10)."""
    assert _progress_bar(0.0) == "░" * 10
