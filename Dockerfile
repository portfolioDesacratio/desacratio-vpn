# ─── Desacratio VPN — Main Dockerfile ───────────────────────────────────
# Сборка:   docker build -t desacratio-vpn .
# Запуск:   docker run -p 8443:8443 -e BOT_TOKEN="..." desacratio-vpn
# Деплой:   Render.com (Web Service)
#
# Содержит и API (warp-api.py), и Telegram бота (free-bot.py).
# Запускаются через start.sh.
# ──────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

WORKDIR /app

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Копируем API
COPY api/warp-api.py api/warp-reg api/requirements.txt ./
RUN chmod +x warp-reg

# Копируем бота
COPY bot/free-bot.py bot/requirements.txt ./
RUN mv requirements.txt requirements-bot.txt

# Копируем стартовый скрипт
COPY start.sh ./
RUN chmod +x start.sh

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r requirements-bot.txt

# Директория для кеша
RUN mkdir -p /app/data/cache

# Порт
EXPOSE 8443

# Запуск через start.sh
CMD ["bash", "start.sh"]
