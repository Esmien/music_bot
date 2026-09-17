"""Юнит-тесты вспомогательных функций (уведомление владельца)."""

import pytest

import config
from handlers.utils import notify_owner

pytestmark = pytest.mark.unit


class FakeBot:
    def __init__(self, fail=False):
        self.sent = []
        self._fail = fail

    async def send_message(self, chat_id, text, **kwargs):
        if self._fail:
            raise RuntimeError("telegram is down")
        self.sent.append((chat_id, text))


async def test_notify_owner_skips_without_owner(monkeypatch):
    monkeypatch.setattr(config, "BOT_OWNER_ID", 0)
    bot = FakeBot()

    await notify_owner(bot, "контекст", RuntimeError("boom"))

    assert bot.sent == []


async def test_notify_owner_sends_truncated_traceback(monkeypatch):
    monkeypatch.setattr(config, "BOT_OWNER_ID", 123)
    bot = FakeBot()

    def deep_error():
        raise RuntimeError("boom")

    try:
        deep_error()
    except RuntimeError as e:
        err = e

    await notify_owner(bot, "контекст <опасный>", err)

    chat_id, text = bot.sent[0]
    assert chat_id == 123
    assert "контекст &lt;опасный&gt;" in text  # HTML-экранирование
    assert len(text) < 3200  # traceback обрезан до разумного размера


async def test_notify_owner_swallows_send_failure(monkeypatch):
    monkeypatch.setattr(config, "BOT_OWNER_ID", 123)

    # Не должно всплыть исключение, даже если Telegram недоступен
    await notify_owner(FakeBot(fail=True), "контекст", RuntimeError("boom"))
