# Cancellation Flow — Отмена генерации

## State Machine генерации

Генерация проходит через следующие статусы в БД (таблица `generations`, поле `status`):

```
PENDING → PROCESSING → SUCCESS
                    ↘ FAILED
                    ↘ CANCELLED
```

**Допустимые переходы:**

| Из статуса | В статус | Условие | Инициатор |
|------------|----------|---------|-----------|
| PENDING | PROCESSING | Воркер успешно захватил задачу через `attempt_id` | Worker (`_claim_generation`) |
| PENDING | CANCELLED | Пользователь отменил до начала обработки | User (через `/cancel` или `/logout`) |
| PROCESSING | SUCCESS | Генерация завершена, MP3 сохранён, метаданные записаны | Worker (`_save_generation_success`) |
| PROCESSING | FAILED | Ошибка генерации (API, сеть, валидация) | Worker (`_handle_generation_failure`) |
| PROCESSING | CANCELLED | Пользователь отменил во время генерации | User + Worker (`_handle_generation_cancel`) |
| SUCCESS | SUCCESS | Идемпотентность: повторная доставка не меняет статус | Worker (при повторной попытке) |
| FAILED | FAILED | Идемпотентность: повторная обработка сбоя | Worker/Handler |
| CANCELLED | CANCELLED | Идемпотентность: повторная отмена | User/Worker |

**Недопустимые переходы:**
- SUCCESS → любой другой статус (финальное состояние)
- FAILED → любой другой статус (финальное состояние)
- CANCELLED → любой другой статус (финальное состояние)

## Source of Truth

**Два независимых Redis cancel-токена** являются **source of truth** для запросов отмены:

1. **Generation cancel-токен** (`bot:cancel:gen:{gen_id}`) — отменяет генерацию в статусах PENDING/PROCESSING
2. **Delivery cancel-токен** (`bot:cancel:delivery:{gen_id}`) — пропускает доставку после SUCCESS

**Роли компонентов:**

| Компонент | Роль | Область ответственности |
|-----------|------|-------------------------|
| Redis generation cancel-токен (`bot:cancel:gen:{gen_id}`) | Source of truth для отмены генерации | Устанавливается `/cancel` для PENDING/PROCESSING, проверяется воркером перед API-запросом |
| Redis delivery cancel-токен (`bot:cancel:delivery:{gen_id}`) | Source of truth для пропуска доставки | Устанавливается `/cancel` для SUCCESS, проверяется воркером перед delivery |
| In-memory реестр `_active_tasks` | Локальная отмена asyncio.Task | Хранит Task для отмены в процессе бота; не переживает рестарт |
| FSM флаг `generating` | UI-блокировка повторных запусков | Хранится в Redis FSM-storage, переживает рестарт, проверяется перед запуском |
| БД статус `Generation.status` | Историческая запись результата | Фиксирует финальное состояние генерации для аналитики и отображения истории |
| БД поле `Generation.attempt_id` | Защита от race condition воркеров | Гарантирует, что только один воркер обновит статус при конкурентной обработке |

**Приоритет при проверке отмены:**
1. Redis cancel-токены — проверяются **перед каждым необратимым действием** (generation перед API, delivery перед отправкой)
2. asyncio.Task — отменяется **только если генерация ещё в процессе бота** (до отправки в TaskIQ)

## TTL и очистка cancel-токенов

**TTL:** 600 секунд (10 минут) для обоих типов токенов

**Очистка:**
- **Автоматическая:** Redis удалит токены через 10 минут (защита от утечки ключей)
- **Явная:**
  - Generation cancel-токен: `clear_generation_cancel(gen_id)` после обработки отмены в `_handle_generation_cancel()`
  - Delivery cancel-токен: `clear_delivery_cancel(gen_id)` после пропуска доставки в `deliver_generation_audio()`

**Обоснование TTL:**
- Максимальное время генерации ~3 минуты + запас на сетевые задержки и retry
- 10 минут покрывает даже аномально долгую генерацию и позволяет воркеру корректно обработать отмену
- Избыточный TTL безопасен: после завершения генерации/delivery токены явно удаляются

## Четыре этапа отмены

### 1. Отмена до claim (PENDING → CANCELLED)

**Сценарий:** Пользователь нажал `/cancel` до того, как воркер захватил задачу.

**Обработка:**
- `/cancel` проверяет статус генерации в БД: PENDING → устанавливает generation cancel-токен
- Воркер в `run_generation_task` вызывает `_claim_generation`, который атомарно переводит статус `PENDING → PROCESSING`
- Если claim успешен, воркер проверяет `is_generation_cancelled()` перед POST-запросом к API
- Если токен найден, воркер переходит в `_handle_generation_cancel`, устанавливает статус `CANCELLED` и завершает задачу
- Воркер вызывает `clear_generation_cancel(gen_id)` после обработки

**Результат:** Статус `CANCELLED`, MP3 не создан, деньги не списаны.

### 2. Отмена во время API call (PROCESSING → CANCELLED)

**Сценарий:** Пользователь нажал `/cancel` во время генерации через OpenRouter.

**Обработка:**
- `/cancel` проверяет статус генерации в БД: PROCESSING → устанавливает generation cancel-токен
- Воркер в `run_generation_task` вызывает `run_generation()`
- `run_generation()` проверяет `is_generation_cancelled()` **до POST-запроса**
- `generate_song_real()` проверяет `is_generation_cancelled()` **перед POST-запросом** (критично для предотвращения списания денег)
- Callback `on_progress` в воркере проверяет `is_generation_cancelled()` во время чтения SSE-потока
- При обнаружении отмены поднимается `asyncio.CancelledError`
- Обработчик `except asyncio.CancelledError` вызывает `_handle_generation_cancel`
- Воркер вызывает `clear_generation_cancel(gen_id)` после обработки

**Результат:** Статус `CANCELLED`, частичный MP3 отброшен, деньги списаны (POST уже выполнен).

**ВАЖНО:** Проверка **перед POST** гарантирует, что при быстрой отмене деньги не будут списаны.

### 3. Отмена после сохранения MP3 (SUCCESS остаётся SUCCESS)

**Сценарий:** Пользователь нажал `/cancel` после того, как MP3 сохранён на диск, но до доставки в Telegram.

**Обработка:**
- `_save_generation_success()` атомарно обновляет статус на `SUCCESS` с проверкой `attempt_id` и статуса `PROCESSING`
- Если обновление успешно, статус зафиксирован как `SUCCESS`
- `/cancel` проверяет статус генерации в БД: SUCCESS → устанавливает **delivery** cancel-токен
- Перед вызовом `deliver_generation_audio()` воркер проверяет `is_delivery_cancelled()`
- Если отмена обнаружена, доставка пропускается, статус остаётся `SUCCESS`, delivery_status = FAILED
- Воркер вызывает `clear_delivery_cancel(gen_id)` после пропуска доставки

**Результат:** Статус `SUCCESS`, MP3 сохранён, но не доставлен пользователю. Артефакт можно доставить позже.

**Обоснование:** MP3 — ценный артефакт, за который списаны деньги. Статус `SUCCESS` отражает, что генерация завершена успешно, даже если доставка не состоялась.

### 4. Отмена во время Telegram delivery (SUCCESS остаётся SUCCESS)

**Сценарий:** Пользователь нажал `/cancel` во время отправки MP3 в Telegram.

**Обработка:**
- Статус уже `SUCCESS` в БД
- `/cancel` проверяет статус генерации в БД: SUCCESS → устанавливает **delivery** cancel-токен
- `deliver_generation_audio()` проверяет `is_delivery_cancelled()` перед отправкой аудио
- Если отмена обнаружена, доставка прерывается, статус остаётся `SUCCESS`, delivery_status = FAILED
- Воркер вызывает `clear_delivery_cancel(gen_id)` после пропуска доставки
- При ошибке отправки (сетевой или Telegram API) публикуется событие `GenerationFailed(stage="delivery")`, но статус `SUCCESS` не меняется

**Результат:** Статус `SUCCESS`, MP3 сохранён, доставка прервана. Событие `GenerationFailed` сообщит пользователю об ошибке.

**Обоснование:** Ошибка доставки — не ошибка генерации. Артефакт создан и сохранён, проблема в инфраструктуре Telegram или сети.

## Защита от stale attempt_id

Все обновления БД проверяют `attempt_id` и текущий статус:

```python
# _save_generation_success
update(Generation).where(
    Generation.id == gen_id,
    Generation.attempt_id == attempt_id,  # Только текущая попытка
    Generation.status == GenerationStatus.PROCESSING,  # Только из PROCESSING
)
```

Если другой воркер или конкурентная задача уже изменила статус, обновление не произойдёт (вернёт `None`).

## Политика ошибок сохранения промптов

**Blocking-политика:** Если сохранение промпта в БД не удалось, генерация НЕ запускается.

**Обоснование:**
- Без записи Generation воркер не сможет обновить статус и сохранить результат
- Пользователь получит ошибку генерации без объяснения причины
- Теряются данные для аналитики и истории

**Обработка:**
1. При ошибке `SQLAlchemyError` или `ValueError`:
   - Логируется с контекстом `user_id` и текстом ошибки
   - Отправляется уведомление владельцу через `notify_owner`
   - Показывается пользователю сообщение "Не удалось сохранить описание песни"
   - FSM очищается, пользователь возвращается в главное меню
   - Генерация НЕ запускается

2. Тесты покрывают:
   - `OperationalError` (потеря соединения с БД)
   - `IntegrityError` (нарушение constraints)
   - Проверку, что Generation не создан в БД
   - Проверку, что FSM очищен

**Реализация:** `domains/enricher/handlers.py:_save_prompt_blocking()`

## Проверочный список для разработчика

При добавлении новой точки необратимого действия (API-запрос, списание денег, отправка данных):

- [ ] Добавлена проверка `is_generation_cancelled(gen_id)` **до** необратимого действия
- [ ] При обнаружении отмены поднимается `asyncio.CancelledError`
- [ ] `except asyncio.CancelledError` вызывает `_handle_generation_cancel` или аналогичную очистку
- [ ] Логируется контекст отмены (gen_id, user_id, этап)
- [ ] Добавлен тест для нового этапа отмены
