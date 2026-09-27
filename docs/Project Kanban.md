---

kanban-plugin: board

---

## Epics

- [ ] [[0. Clean-up]]
- [ ] [[1. Infrastructure]]
- [ ] [[2. Webhooks]]
- [ ] [[3. TaskIQ Core]]
- [ ] [[4. Domains migration]]
- [ ] [[5. Observability, durability]]
- [ ] [[6. Tests and docs]]


## Backlog

- [ ] [[3.2 Порты телеметрии в воркерах]]
- [ ] [[3.3 InMemoryBroker для тестов]]
- [ ] [[4.1 credits → воркер]]
- [ ] [[4.2 enricher → воркер]]
- [ ] [[4.3 generation → воркер]]
- [ ] [[4.4 Перевод генерации на событие успеха сбоя]]
- [ ] [[5.1 Отмена сквозь очередь]]
- [ ] [[5.2 Retry, DLQ, наблюдаемость]]
- [ ] [[5.3 Порядок деплоя]]
- [ ] [[6.1 Обновление тестовой базы]]
- [ ] [[6.2 Документация]]


## In progress

- [ ] [[3.1 Пакет контрактов shared contracts]]


## Testing

- [ ] [[0.1 Публичные контракты auth вместо приватных импортов]]
- [ ] [[0.2 Разрыв цикла generation ↔ enricher]]
- [ ] [[0.3 Типизированные FSM-данные вместо магических строк]]
- [ ] [[0.4 Реестр и локи в Redis]]
- [ ] [[1.1 RabbitMQ и taskiq в проект]]
- [ ] [[1.2 Конфигурация брокера и сериализация]]
- [ ] [[2.1 Переключение polling → webhook за флагом]]
- [ ] [[2.2 Graceful shutdown и lifecycle]]


## Done



## Deploy

**Complete**
- [x] [[update_infrastructure]]
- [x] [[add_sqlalchemy_models]]
- [x] [[gen_migrations]]
- [x] [[add_keyboards]]
- [x] [[add_group_FSM_for_handling_enriched_prompt]]
- [x] [[add_connection_to_enricher]]
- [x] [[add_handlers_for_enricher]]
- [x] [[add_enriched_prompt_validator]]
- [x] [[combine_registries]]
- [x] [[upgrade_ui]]
- [x] [[upgrade_ux]]
- [x] [[add_feedback_service]]
- [x] [[add_evaluation_handlers]]
- [x] [[add_feedback_handlers]]
- [x] [[add_model_generation]]




%% kanban:settings
```
{"kanban-plugin":"board","list-collapse":[false,false,false,false,true,true],"show-checkboxes":true,"new-note-folder":"Tasks"}
```
%%