# ─── Desacratio VPN API — Dockerfile ─────────────────────────────────────
# Сборка:   docker build -t desacratio-api .
# Деплой:   Render.com (Web Service)
# ──────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

WORKDIR /app

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Копируем API
COPY warp-api.py warp-reg requirements.txt ./
RUN chmod +x warp-reg

# Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Директория для кеша
RUN mkdir -p /app/data/cache

# Порт (Render задаёт свой PORT через переменную окружения)
EXPOSE 8443

# Запуск API
CMD ["python3", "warp-api.py"]
