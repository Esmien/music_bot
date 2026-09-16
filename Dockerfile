FROM python:3.12-slim

WORKDIR /app

# Зависимости отдельным слоем для кэша
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создаем непривилегированного пользователя и папку для данных
RUN useradd -m botuser \
    && mkdir /data \
    && chown botuser:botuser /data

# Копируем entrypoint для корректной смены прав на смонтированный том
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
