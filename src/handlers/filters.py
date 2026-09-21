"""Кастомные фильтры для роутера бота."""

from aiogram.filters import Filter
from aiogram.types import Message

from fsm.evaluation_fsm import is_pending_auth


class IsPendingAuth(Filter):
    """Фильтр: пользователь ожидает авторизации (ввод ключа доступа).

    Класс вместо лямбды — чтобы корректно обрабатывать служебные
    апдейты без поля from_user (например, some channel posts).
    """

    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and await is_pending_auth(message.from_user.id)


class NotCommand(Filter):
    """Фильтр: текст сообщения не является командой (не начинается с '/')."""

    async def __call__(self, message: Message) -> bool:
        text = message.text or ""
        return not text.startswith("/")
