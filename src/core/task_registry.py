"""Реестр живых задач генерации по user_id.

Позволяет честно погасить генерацию из cmd_cancel_generation и
cmd_logout. Сам asyncio.Task в FSM-данные не положишь (не сериализуется),
поэтому реестр живёт в памяти процесса: при перезапуске задачи теряются,
а их след в FSM чистит clear_orphaned_generation_flags на старте бота.
"""

import asyncio

active_tasks: dict[int, asyncio.Task] = {}
