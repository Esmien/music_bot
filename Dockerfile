FROM python:3.12-slim

WORKDIR /app

# Зависимости отдельным слоем для кэша
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Непривилегированный пользователь
RUN useradd -m botuser
USER botuser

CMD ["python", "-u", "bot.py"]