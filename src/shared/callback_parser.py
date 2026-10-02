"""Typed parser для callback_data в формате fb:action:gen_id."""

from enum import StrEnum


class FeedbackAction(StrEnum):
    """Действия в feedback/evaluation flow."""

    LIKE = "like"
    DISLIKE = "dislike"
    SEND = "send"
    FINISH = "finish"


class CallbackData:
    """Разобранные данные callback-запроса.

    Attributes:
        action: Действие пользователя (like, dislike, send, finish).
        gen_id: ID генерации или None, если не указан или некорректен.
    """

    def __init__(self, action: FeedbackAction, gen_id: int | None = None):
        self.action = action
        self.gen_id = gen_id


def parse_feedback_callback(callback_data: str | None) -> CallbackData | None:
    """Парсит callback_data формата fb:action[:gen_id].

    Args:
        callback_data: Строка данных callback-запроса.

    Returns:
        CallbackData с action и gen_id или None, если формат некорректен.
    """
    if not callback_data or not callback_data.startswith("fb:"):
        return None

    parts = callback_data.split(":")
    if len(parts) < 2:
        return None

    action_str = parts[1]
    try:
        action = FeedbackAction(action_str)
    except ValueError:
        return None

    gen_id = None
    if len(parts) > 2 and parts[2].isdigit():
        gen_id = int(parts[2])

    return CallbackData(action=action, gen_id=gen_id)
