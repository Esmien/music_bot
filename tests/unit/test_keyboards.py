"""Юнит-тесты клавиатур бота."""

import pytest

from domains.base.keyboards import get_cancel_keyboard, get_main_keyboard

pytestmark = pytest.mark.unit


def test_main_keyboard_layout():
    markup = get_main_keyboard()
    rows = markup.keyboard
    assert [btn.text for btn in rows[0]] == ["🎵 Сгенерировать", "💳 Кредиты"]
    assert [btn.text for btn in rows[1]] == ["🚪 Выйти"]
    assert markup.resize_keyboard is True


def test_cancel_keyboard_has_single_button():
    markup = get_cancel_keyboard()
    assert [btn.text for btn in markup.keyboard[0]] == ["❌ Отмена"]
    assert markup.resize_keyboard is True
