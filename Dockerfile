# ─── Desacratio VPN — Main Dockerfile ───────────────────────────────────
# Деплой:   Render.com (Web Service)
# Содержит API (warp-api.py) + Telegram бота (free-bot.py).
# Запускаются через start.sh.
# ──────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

WORKDIR /app

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ─── Shared DB ──────────────────────────────────────────────────────────
COPY db.py ./

# ─── API ────────────────────────────────────────────────────────────────
COPY api/requirements.txt ./requirements-api.txt
COPY api/warp-api.py ./
COPY api/warp-reg ./
RUN chmod +x warp-reg

# ─── Bot ────────────────────────────────────────────────────────────────
COPY bot/requirements.txt ./requirements-bot.txt
COPY bot/free-bot.py ./

# ─── Startup ────────────────────────────────────────────────────────────
COPY start.sh ./
RUN chmod +x start.sh

# ─── Python зависимости ─────────────────────────────────────────────────
RUN pip install --no-cache-dir -r requirements-api.txt
RUN pip install --no-cache-dir -r requirements-bot.txt

# Директория для кеша
RUN mkdir -p /app/data/cache

# Порт (Render задаёт PORT, но EXPOSE для документации)
EXPOSE 8443

# Запуск: API (фон) + Bot (передний план)
CMD ["bash", "start.sh"]
