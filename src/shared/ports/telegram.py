"""Порт для отправки сообщений в Telegram из воркеров.

Абстракция позволяет воркерам отправлять сообщения, обновлять статусы,
отправлять аудио и уведомлять владельца без прямой зависимости от aiogram.
"""

import html
import logging
from abc import ABC, abstractmethod
from typing import BinaryIO

from aiogram import Bot
from aiogram.types import BufferedInputFile, FSInputFile

logger = logging.getLogger(__name__)


class TelegramPort(ABC):
    """Абстрактный порт для взаимодействия с Telegram.

    Воркеры используют этот интерфейс для отправки сообщений,
    обновления статусов, отправки аудио и уведомления владельца.
    """

    @abstractmethod
    async def send_message(self, chat_id: int, text: str, **kwargs) -> int:
        """Отправить текстовое сообщение.

        Args:
            chat_id: ID чата получателя.
            text: Текст сообщения.
            **kwargs: Дополнительные параметры (reply_markup, parse_mode и т.д.).

        Returns:
            ID отправленного сообщения.
        """

    @abstractmethod
    async def edit_message(self, chat_id: int, message_id: int, text: str, **kwargs) -> None:
        """Редактировать существующее сообщение.

        Args:
            chat_id: ID чата.
            message_id: ID сообщения для редактирования.
            text: Новый текст сообщения.
            **kwargs: Дополнительные параметры (reply_markup, parse_mode и т.д.).
        """

    @abstractmethod
    async def send_audio(
        self,
        chat_id: int,
        audio: bytes | BinaryIO | str,
        title: str | None = None,
        performer: str | None = None,
        **kwargs,
    ) -> int:
        """Отправить аудиофайл.

        Args:
            chat_id: ID чата получателя.
            audio: Аудио (bytes, file-like объект или путь к файлу).
            title: Название трека.
            performer: Исполнитель.
            **kwargs: Дополнительные параметры (reply_markup и т.д.).

        Returns:
            ID отправленного сообщения.
        """

    @abstractmethod
    async def notify_owner(self, message: str, context: dict | None = None) -> None:
        """Уведомить владельца бота об ошибке или важном событии.

        Args:
            message: Текст уведомления.
            context: Дополнительный контекст (например, traceback).
        """


class AiogramTelegramPort(TelegramPort):
    """Реализация порта на основе aiogram Bot.

    Использует standalone инстанс Bot для отправки сообщений из воркеров.
    """

    def __init__(self, bot_token: str, owner_id: int | None = None):
        """Инициализация порта.

        Args:
            bot_token: Токен Telegram-бота.
            owner_id: ID владельца бота для уведомлений (опционально).
        """
        self._bot = Bot(token=bot_token)
        self._owner_id = owner_id

    async def send_message(self, chat_id: int, text: str, **kwargs) -> int:
        """Отправить текстовое сообщение.

        Args:
            chat_id: ID чата получателя.
            text: Текст сообщения.
            **kwargs: Дополнительные параметры.

        Returns:
            ID отправленного сообщения.
        """
        message = await self._bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return message.message_id

    async def edit_message(self, chat_id: int, message_id: int, text: str, **kwargs) -> None:
        """Редактировать существующее сообщение.

        Args:
            chat_id: ID чата.
            message_id: ID сообщения для редактирования.
            text: Новый текст сообщения.
            **kwargs: Дополнительные параметры.
        """
        await self._bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, **kwargs)

    async def send_audio(
        self,
        chat_id: int,
        audio: bytes | BinaryIO | str,
        title: str | None = None,
        performer: str | None = None,
        **kwargs,
    ) -> int:
        """Отправить аудиофайл.

        Args:
            chat_id: ID чата получателя.
            audio: Аудио (bytes, file-like объект или путь к файлу).
            title: Название трека.
            performer: Исполнитель.
            **kwargs: Дополнительные параметры.

        Returns:
            ID отправленного сообщения.
        """
        # Определяем тип входных данных и подготавливаем InputFile
        if isinstance(audio, bytes):
            input_file = BufferedInputFile(file=audio, filename="audio.mp3")
        elif isinstance(audio, str):
            input_file = FSInputFile(path=audio)
        else:
            # BinaryIO
            input_file = BufferedInputFile(file=audio.read(), filename="audio.mp3")

        message = await self._bot.send_audio(
            chat_id=chat_id, audio=input_file, title=title, performer=performer, **kwargs
        )
        return message.message_id

    async def notify_owner(self, message: str, context: dict | None = None) -> None:
        """Уведомить владельца бота об ошибке или важном событии.

        Args:
            message: Текст уведомления.
            context: Дополнительный контекст (например, traceback).
        """
        if not self._owner_id:
            logger.warning(f"Owner notification skipped (no owner_id): {message}")
            return

        text = f"🐞 <b>{html.escape(message)}</b>"
        if context and "traceback" in context:
            traceback_text = str(context["traceback"])
            if len(traceback_text) > 3000:
                traceback_text = "…\n" + traceback_text[-2997:]
            text += f"\n<code>{html.escape(traceback_text)}</code>"
        elif context:
            text += f"\n<code>{html.escape(str(context))}</code>"

        try:
            await self._bot.send_message(chat_id=self._owner_id, text=text, parse_mode="HTML")
        except Exception:
            logger.exception("Failed to notify owner via AiogramTelegramPort")

    async def close(self) -> None:
        """Закрыть сессию бота."""
        await self._bot.session.close()
