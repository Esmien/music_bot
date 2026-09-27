"""TaskIQ-воркер для запроса обратной связи после оценки."""

import logging

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from taskiq import TaskiqDepends, TaskiqState

from core.broker import feedback_broker
from core.config import settings
from core.utils.error_notify import notify_owner
from domains.evaluation.evaluation_messages import FEEDBACK_CHOICE_TEXT
from domains.feedback.fsm import FeedbackStates
from domains.feedback.keyboards import get_feedback_keyboard
from domains.feedback.state_models import FeedbackFlowState
from shared.contracts.events import EvaluationCompleted
from shared.ports.telegram import TelegramPort

log = logging.getLogger(__name__)


@feedback_broker.task(task_name="request_feedback_handler", queue_name=settings.rabbitmq.queue_name("feedback"))
async def request_feedback_handler(
    event: EvaluationCompleted,
    state: TaskiqState = TaskiqDepends(),
) -> None:
    """Обрабатывает событие успешной оценки и запрашивает текстовый отзыв."""

    # 1. Выносим безопасное извлечение зависимостей за пределы try
    telegram_port: TelegramPort = state["telegram_port"]
    storage = state["storage"]
    bot = state["bot"]

    try:
        # 2. Внутри try остается только бизнес-логика
        fsm_context = FSMContext(
            storage=storage,
            key=StorageKey(bot_id=bot.id, chat_id=event.chat_id, user_id=event.user_id),
        )

        await fsm_context.set_state(FeedbackStates.waiting_for_feedback_choice)
        await fsm_context.update_data(FeedbackFlowState().model_dump())

        await telegram_port.send_message(
            chat_id=event.chat_id,
            text=FEEDBACK_CHOICE_TEXT,
            reply_markup=get_feedback_keyboard(),
        )

        log.info("Feedback request sent (user=%s, gen_id=%s)", event.user_id, event.gen_id)

    except Exception as err:  # Ловим саму ошибку
        log.exception("Failed to request feedback (user=%s, gen_id=%s)", event.user_id, event.gen_id)
        await notify_owner(
            telegram_port=telegram_port,
            context=f"request_feedback user={event.user_id} gen_id={event.gen_id}",
            err=err,  # Передаем реальный трейсбек, а не новый Exception
        )
