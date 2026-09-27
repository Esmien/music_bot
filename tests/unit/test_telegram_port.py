"""Тесты для TelegramPort и его реализаций."""

from src.shared.ports.fake_telegram import FakeTelegramPort


async def test_fake_port_send_message():
    """Проверяет отправку сообщения через фейковый порт.

    Фейковый порт должен сохранять информацию о вызове send_message
    и возвращать автоинкрементный ID сообщения.
    """
    port = FakeTelegramPort()

    message_id = await port.send_message(chat_id=123, text="Hello", parse_mode="HTML")

    assert message_id == 1
    assert len(port.sent_messages) == 1
    assert port.sent_messages[0]["chat_id"] == 123
    assert port.sent_messages[0]["text"] == "Hello"
    assert port.sent_messages[0]["message_id"] == 1
    assert port.sent_messages[0]["kwargs"]["parse_mode"] == "HTML"


async def test_fake_port_edit_message():
    """Проверяет редактирование сообщения через фейковый порт.

    Фейковый порт должен сохранять информацию о вызове edit_message.
    """
    port = FakeTelegramPort()

    await port.edit_message(chat_id=123, message_id=42, text="Updated text")

    assert len(port.edited_messages) == 1
    assert port.edited_messages[0]["chat_id"] == 123
    assert port.edited_messages[0]["message_id"] == 42
    assert port.edited_messages[0]["text"] == "Updated text"


async def test_fake_port_send_audio():
    """Проверяет отправку аудио через фейковый порт.

    Фейковый порт должен сохранять информацию о вызове send_audio
    и возвращать автоинкрементный ID сообщения.
    """
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
    """Проверяет уведомление владельца через фейковый порт.

    Фейковый порт должен сохранять информацию о вызове notify_owner.
    """
    port = FakeTelegramPort()
    context = {"error": "Something went wrong", "user_id": 789}

    await port.notify_owner(message="Critical error occurred", context=context)

    assert len(port.owner_notifications) == 1
    assert port.owner_notifications[0]["message"] == "Critical error occurred"
    assert port.owner_notifications[0]["context"] == context


async def test_fake_port_message_id_autoincrement():
    """Проверяет автоинкремент ID сообщений.

    При каждом вызове send_message или send_audio ID должен увеличиваться.
    """
    port = FakeTelegramPort()

    msg_id_1 = await port.send_message(chat_id=100, text="First")
    msg_id_2 = await port.send_message(chat_id=100, text="Second")
    audio_id = await port.send_audio(chat_id=100, audio=b"audio", title="Track")

    assert msg_id_1 == 1
    assert msg_id_2 == 2
    assert audio_id == 3


async def test_fake_port_reset():
    """Проверяет сброс истории вызовов.

    После reset() все списки должны быть пустыми, а счётчик ID сброшен.
    """
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

    # Проверяем, что счётчик ID также сброшен
    new_msg_id = await port.send_message(chat_id=100, text="After reset")
    assert new_msg_id == 1
