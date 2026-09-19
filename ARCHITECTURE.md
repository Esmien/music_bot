# System Design:
Монолит с распределением по доменам

# Структура проекта:
* /.github - GitHub Actions
* /src - исходный код бота
  * /handlers - все обработчики команд телеграма
  * /services - бизнес-логика, движок
  * /database - модули, связанные с работой БД (модели, подключения, фабрики)
  * /utils
  * bot.py - точка входа
  * config.py - модуль с конфигурацией (константы, флаги)
  
* /tests - тесты
* /infra - инфраструктурные файлы (Docker, entrypoint.sh)
* poetry.lock - зависимости проекта
* pyproject.toml - конфигурация проекта
* .dockerignore - исключения из Docker-образа
* .gitignore - исключения git
* .env.example - шаблон переменных окружения