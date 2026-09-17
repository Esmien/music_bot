"""Юнит-тесты текстового индикатора прогресса из хендлера генерации."""

import pytest

from handlers.generation import _progress_bar

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
    assert _progress_bar(fraction, width) == expected


def test_progress_bar_default_width():
    assert _progress_bar(0.0) == "░" * 10
