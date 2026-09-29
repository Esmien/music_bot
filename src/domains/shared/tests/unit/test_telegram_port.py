"""Юнит-тесты адаптера AiogramTelegramPort."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.utils.error_notify import notify_owner
from shared.ports.fake_telegram import FakeTelegramPort
from shared.ports.telegram import AiogramTelegramPort


@pytest.fixture
def mock_bot():
    """Мокированный экземпляр aiogram Bot."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.session = MagicMock()
    bot.session.close = AsyncMock()
    return bot


async def test_notify_owner_with_owner_id(mock_bot, monkeypatch):
    """При указанном owner_id сообщение владельцу отправляется через send_message."""
    port = AiogramTelegramPort(bot_token="123456:TEST", owner_id=12345)
    monkeypatch.setattr(port, "_bot", mock_bot)

    await port.notify_owner(
        message="Ошибка в сервисе",
        context={"traceback": "Traceback (most recent call last):\nValueError: bad error"},
    )

    mock_bot.send_message.assert_awaited_once()
    _, kwargs = mock_bot.send_message.call_args
    assert kwargs["chat_id"] == 12345
    assert kwargs["parse_mode"] == "HTML"
    assert "Ошибка в сервисе" in kwargs["text"]
    assert "ValueError: bad error" in kwargs["text"]


async def test_notify_owner_without_owner_id(mock_bot, monkeypatch, caplog):
    """При отсутствии owner_id отправка пропускается с предупреждением в лог."""
    port = AiogramTelegramPort(bot_token="123456:TEST", owner_id=None)
    monkeypatch.setattr(port, "_bot", mock_bot)

    await port.notify_owner(message="Ошибка без владельца")

    mock_bot.send_message.assert_not_awaited()
    assert any("Owner notification skipped" in record.message for record in caplog.records)


async def test_notify_owner_via_core_notify_owner(mock_bot, monkeypatch):
    """Проверка интеграции core.utils.error_notify.notify_owner с telegram_port."""
    port = AiogramTelegramPort(bot_token="123456:TEST", owner_id=99999)
    monkeypatch.setattr(port, "_bot", mock_bot)

    err = RuntimeError("Worker failure")
    await notify_owner(context="Сбой воркера", err=err, telegram_port=port)

    mock_bot.send_message.assert_awaited_once()
    _, kwargs = mock_bot.send_message.call_args
    assert kwargs["chat_id"] == 99999
    assert "Сбой воркера" in kwargs["text"]
    assert "RuntimeError: Worker failure" in kwargs["text"]


async def test_fake_port_send_message():
    """Проверяет отправку сообщения через фейковый порт."""
    port = FakeTelegramPort()

    message_id = await port.send_message(chat_id=123, text="Hello", parse_mode="HTML")

    assert message_id == 1
    assert len(port.sent_messages) == 1
    assert port.sent_messages[0]["chat_id"] == 123
    assert port.sent_messages[0]["text"] == "Hello"
    assert port.sent_messages[0]["message_id"] == 1
    assert port.sent_messages[0]["kwargs"]["parse_mode"] == "HTML"


async def test_fake_port_edit_message():
    """Проверяет редактирование сообщения через фейковый порт."""
    port = FakeTelegramPort()

    await port.edit_message(chat_id=123, message_id=42, text="Updated text")

    assert len(port.edited_messages) == 1
    assert port.edited_messages[0]["chat_id"] == 123
    assert port.edited_messages[0]["message_id"] == 42
    assert port.edited_messages[0]["text"] == "Updated text"


async def test_fake_port_send_audio():
    """Проверяет отправку аудио через фейковый порт."""
    port = FakeTelegramPort()
    audio_data = b"fake audio data"

    message_id = await port.send_audio(chat_id=456, audio=audio_data, title="Test Song", performer="Test Artist")

    assert message_id == 1
    assert len(port.sent_audio) == 1
    assert port.sent_audio[0]["chat_id"] == 456
    assert port.sent_audio[0]["audio_type"] == "bytes"
    assert port.sent_audio[0]["title"] == "Test Song"
    assert port.sent_audio[0]["performer"] == "Test Artist"
    assert port.sent_audio[0]["message_id"] == 1


async def test_fake_port_notify_owner():
    """Проверяет уведомление владельца через фейковый порт."""
    port = FakeTelegramPort()
    context = {"error": "Something went wrong", "user_id": 789}

    await port.notify_owner(message="Critical error occurred", context=context)

    assert len(port.owner_notifications) == 1
    assert port.owner_notifications[0]["message"] == "Critical error occurred"
    assert port.owner_notifications[0]["context"] == context


async def test_fake_port_message_id_autoincrement():
    """Проверяет автоинкремент ID сообщений в фейковом порте."""
    port = FakeTelegramPort()

    msg_id_1 = await port.send_message(chat_id=100, text="First")
    msg_id_2 = await port.send_message(chat_id=100, text="Second")
    audio_id = await port.send_audio(chat_id=100, audio=b"audio", title="Track")

    assert msg_id_1 == 1
    assert msg_id_2 == 2
    assert audio_id == 3


async def test_fake_port_reset():
    """Проверяет сброс истории вызовов в фейковом порте."""
    port = FakeTelegramPort()

    await port.send_message(chat_id=100, text="Message")
    await port.edit_message(chat_id=100, message_id=1, text="Edited")
    await port.send_audio(chat_id=100, audio=b"audio", title="Track")
    await port.notify_owner(message="Error")

    port.reset()

    assert len(port.sent_messages) == 0
    assert len(port.edited_messages) == 0
    assert len(port.sent_audio) == 0
    assert len(port.owner_notifications) == 0

    new_msg_id = await port.send_message(chat_id=100, text="After reset")
    assert new_msg_id == 1
