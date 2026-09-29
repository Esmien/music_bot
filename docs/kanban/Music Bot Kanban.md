---

kanban-plugin: board

---

## Quick wins

- [x] `TASK-001` — `context.state` в event handlers
- [x] `TASK-002` — исправление `AiogramTelegramPort.notify_owner`
- [x] `TASK-003` — очистка неиспользуемых воркеров в Compose и брокеров
- [x] `TASK-004` — закрытие публичных infrastructure-портов
- [x] `TASK-005` — корректный shutdown бота при polling
- [ ] `TASK-006` — удаление сброса FSM `state.clear()` в `/credits`


## 🔴 P0 — BLOCKER
- [ ] [[🔴 `TASK-006` [🔴 P0] Убрать `state.clear()` из ` credits` (AUD-013)]] ^lr73hm
- [ ] [[🔴 `TASK-007` [🔴 P0] Атомарный claim платной генерации (AUD-002)]]
- [ ] [[🔴 `TASK-008` [🔴 P0] Безопасная политика повторов OpenRouter (AUD-003)]]


## 🟠 P1 — HIGH

- [ ] [[🟠 `TASK-009` [P1] Привести event flow к единой модели (AUD-017)]]
- [ ] [[🟠 `TASK-010` [P1] Durable audio и отдельная доставка (AUD-004)]]
- [ ] [[🟠 `TASK-011` [P1] Завершение и recovery generation state (AUD-005)]]
- [ ] [[🟠 `TASK-012` [P1] Привязать feedback к конкретному `gen_id` (AUD-007)]]
- [ ] [[🟠 `TASK-013` [P1] Исправить SSE audio assembly (AUD-010)]]
- [ ] [[🟠 `TASK-014` [P1] Устранить TOCTOU в Redis lock (AUD-009)]]
- [ ] [[🟠 `TASK-015` [P1] Единообразная retry policy (AUD-014)]]


## 🟡 P2 — MEDIUM

- [ ] [[🟡 `TASK-016` [P2] Достоверность тестов и broker integration (AUD-012, AUD-015)]]
- [ ] [[🟡 `TASK-017` [P2] Контрактные тесты Telegram-адаптера и API policies (AUD-011, AUD-014)]]


## 🟢 P3 — LOW

- [ ] [[🟢 `TASK-018` [P3] Синхронизировать docs, backlog и quality gates (AUD-016)]]


## In Progress



## Done

- [x] [[🔴 `TASK-005` [🔴 P0] Исправить shutdown бота (AUD-006)]]
- [x] [[🔴 `TASK-001` [🔴 P0] Исправить доступ к TaskIQ dependencies в event handlers (AUD-018)]]
- [x] [[🔴 `TASK-002` [🔴 P0] Исправить `AiogramTelegramPort.notify_owner` (AUD-011)]]
- [x] [[🔴 `TASK-003` [🔴 P0] Удалить неиспользуемые worker units и broker connections (AUD-001)]]
- [x] [[🔴 `TASK-004` [🔴 P0] Закрыть production infrastructure ports (AUD-008)]]




%% kanban:settings
```
{"kanban-plugin":"board","list-collapse":[null,null,null,null,null,false,false],"show-checkboxes":false,"move-tags":true,"tag-action":"obsidian"}
```
%%
