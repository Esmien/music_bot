"""TaskIQ-консьюмер событий для сценария оценки генерации."""

import logging

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage
from taskiq import Context, TaskiqDepends

from core.broker import generation_broker
from core.config import settings
from core.redis import redis_client
from core.utils.fsm_helpers import get_fsm_data, update_fsm_data
from domains.evaluation.evaluation_messages import EVALUATION_PROMPT_TEXT
from domains.evaluation.keyboards import get_evaluation_keyboard
from domains.feedback.fsm import FeedbackStates
from domains.generation.state_models import GenerationFlowState
from shared.contracts.events import GenerationSucceeded
from shared.ports.telegram import TelegramPort

log = logging.getLogger(__name__)


@generation_broker.task(task_name="handle_generation_evaluation")
async def handle_generation_evaluation(
    event: GenerationSucceeded,
    context: Context = TaskiqDepends(),
) -> None:
    """Переводит актуальную генерацию в сценарий оценки.

    Args:
        event: Событие успешной генерации.
        context: Контекст TaskIQ с портом Telegram.
    """
    telegram: TelegramPort = context.state["telegram_port"]
    bot = Bot(token=settings.bot.BOT_TOKEN)
    storage = RedisStorage(redis=redis_client)

    try:
        fsm_context = FSMContext(
            bot=bot,
            storage=storage,
            key=StorageKey(
                bot_id=bot.id,
                chat_id=event.chat_id,
                user_id=event.user_id,
            ),
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
    finally:
        await bot.session.close()
