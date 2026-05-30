#!/bin/bash
# ─── Desacratio VPN — Startup Script ────────────────────────────────────
# Запускает WARP API (подписки) + Relay Proxy + Telegram бота.
# ──────────────────────────────────────────────────────────────────────────
# Relay Proxy включает HTTP CONNECT туннель на том же порту, что и API.
# Это позволяет обходить блокировки WARP портов (2408/500/1701/4500 в РФ)
# через Render (США, порт 443 всегда открыт).
# ──────────────────────────────────────────────────────────────────────────

echo "╔══════════════════════════════════════╗"
echo "║     🛡️ Desacratio VPN — Startup      ║"
echo "╚══════════════════════════════════════╝"

PORT="${PORT:-8443}"

# Всегда запускаем Relay Proxy — он сам обрабатывает HTTP CONNECT + API
echo "📡 Запуск Relay Proxy (HTTP CONNECT + WARP API) на порту $PORT..."
python3 api/relay_proxy.py &
SERVER_PID=$!

# Даём API время на запуск
sleep 3

echo "🤖 Запуск Telegram бота..."
# Bot на переднем плане — контейнер жив, пока жив бот
exec python3 free-bot.py
