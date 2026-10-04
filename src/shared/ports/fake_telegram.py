"""Фейковая реализация TelegramPort для тестирования воркеров."""

import logging
from typing import BinaryIO

from shared.ports.telegram import TelegramPort

logger = logging.getLogger(__name__)


class FakeTelegramPort(TelegramPort):
    """Фейковая реализация порта для тестов.

    Сохраняет все вызовы методов в истории для последующей проверки.
    """

    def __init__(self):
        """Инициализация фейкового порта."""
        self.sent_messages: list[dict] = []
        self.edited_messages: list[dict] = []
        self.sent_audio: list[dict] = []
        self.owner_notifications: list[dict] = []
        self._next_message_id = 1

    async def send_message(self, chat_id: int, text: str, **kwargs) -> int:
        """Сохранить информацию об отправленном сообщении.

        Args:
            chat_id: ID чата получателя.
            text: Текст сообщения.
            **kwargs: Дополнительные параметры.

        Returns:
            ID сообщения (автоинкремент).
        """
        message_id = self._next_message_id
        self._next_message_id += 1

        self.sent_messages.append({"chat_id": chat_id, "text": text, "message_id": message_id, "kwargs": kwargs})
        return message_id

    async def edit_message(self, chat_id: int, message_id: int, text: str, **kwargs) -> None:
        """Сохранить информацию о редактировании сообщения.

        Args:
            chat_id: ID чата.
            message_id: ID сообщения для редактирования.
            text: Новый текст сообщения.
            **kwargs: Дополнительные параметры.
        """
        self.edited_messages.append({"chat_id": chat_id, "message_id": message_id, "text": text, "kwargs": kwargs})

    async def send_audio(
        self,
        chat_id: int,
        audio: bytes | BinaryIO | str,
        title: str | None = None,
        performer: str | None = None,
        **kwargs,
    ) -> int:
        """Сохранить информацию об отправленном аудио.

        Args:
            chat_id: ID чата получателя.
            audio: Аудио (bytes, file-like объект или путь к файлу).
            title: Название трека.
            performer: Исполнитель.
            **kwargs: Дополнительные параметры.

        Returns:
            ID сообщения (автоинкремент).
        """
        message_id = self._next_message_id
        self._next_message_id += 1

        audio_type = "bytes" if isinstance(audio, bytes) else "file" if isinstance(audio, str) else "binary_io"

        self.sent_audio.append(
            {
                "chat_id": chat_id,
                "audio": audio,
                "audio_type": audio_type,
                "title": title,
                "performer": performer,
                "message_id": message_id,
                "kwargs": kwargs,
            }
        )
        return message_id

    async def notify_owner(self, message: str, context: dict | None = None) -> None:
        """Сохранить информацию об уведомлении владельца.

        Args:
            message: Текст уведомления.
            context: Дополнительный контекст.
        """
        self.owner_notifications.append({"message": message, "context": context})

    def reset(self) -> None:
        """Очистить историю вызовов."""
        self.sent_messages.clear()
        self.edited_messages.clear()
        self.sent_audio.clear()
        self.owner_notifications.clear()
        self._next_message_id = 1
