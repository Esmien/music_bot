# Сообщения пользователю
CREDITS_API_ERROR = "❌ Ошибка запроса: {status_code}"
CREDITS_API_KEY_NOT_CONFIGURED = "⚠️ Бот не настроен, владелец уже уведомлен."
CREDITS_CHECK_FAILED = "❌ Не получилось проверить остатки. Владелец уведомлен."
CREDITS_SUMMARY = (
    "💳 Баланс песен:\n"
    "Всего доступно генераций: {total_songs}\n"
    "Сгенерировано композиций: {used_songs}\n"
    "Доступное количество генераций: {remaining_songs}"
)

# Диагностические сообщения
API_KEY_NOT_CONFIGURED_CONTEXT = "Не настроен ключ API"
API_KEY_NOT_SET_ERROR = "Provider key not set."
CREDITS_CHECK_FAILED_LOG = "Failed to check OpenRouter credits (user=%s)"
CREDITS_CHECK_FAILED_OWNER_CONTEXT = "Проверка кредитов упала (user={user_id})"
