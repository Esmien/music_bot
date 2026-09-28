"""TaskIQ-консьюмер событий для сценария оценки генерации."""

import logging

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage
from taskiq import Context, TaskiqDepends

from core.broker import evaluation_broker
from core.config import settings
from core.database.engine import get_session
from core.utils.fsm_helpers import get_fsm_data, update_fsm_data
from domains.evaluation.evaluation_messages import EVALUATION_PROMPT_TEXT
from domains.evaluation.keyboards import get_evaluation_keyboard
from domains.feedback.fsm import FeedbackStates
from domains.generation.models import Generation, GenerationStatus
from domains.generation.state_models import GenerationFlowState
from shared.contracts.events import GenerationSucceeded
from shared.ports.telegram import TelegramPort

log = logging.getLogger(__name__)


@evaluation_broker.task(task_name="request_evaluation_handler", queue_name=settings.rabbitmq.queue_name("evaluation"))
async def request_evaluation_handler(
    event: GenerationSucceeded,
    context: Context = TaskiqDepends(),
) -> None:
    """Отправляет результат генерации и запрашивает оценку.

    Args:
        event: Событие успешной генерации.
        context: Контекст TaskIQ с TelegramPort, Bot и Storage.
    """
    telegram: TelegramPort = context.state["telegram_port"]
    bot: Bot = context.state["bot"]
    storage: RedisStorage = context.state["storage"]

    async with get_session() as session:
        generation = await session.get(Generation, event.gen_id)
        if generation is None or generation.status is not GenerationStatus.PENDING:
            return
        generation.status = GenerationStatus.SUCCESS
        await session.commit()

    if event.status_message_id is not None:
        await telegram.edit_message(
            chat_id=event.chat_id,
            message_id=event.status_message_id,
            text="🎵 Готово!",
        )

    fsm_context = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot.id, chat_id=event.chat_id, user_id=event.user_id),
    )

    flow_state = await get_fsm_data(state=fsm_context, model_class=GenerationFlowState)
    if flow_state.gen_id != event.gen_id:
        log.info("Evaluation event ignored for outdated generation (gen_id=%s)", event.gen_id)
        return

    flow_state.generating = False
    await update_fsm_data(state=fsm_context, model=flow_state)
    await fsm_context.set_state(FeedbackStates.waiting_evaluation)

    await telegram.send_message(
        chat_id=event.chat_id,
        text=EVALUATION_PROMPT_TEXT,
        reply_markup=get_evaluation_keyboard(),
    )

    log.info("Evaluation started (user=%s, gen_id=%s)", event.user_id, event.gen_id)
