#!/bin/bash
# ─── Desacratio VPN — Startup Script ────────────────────────────────────
# Запускает Relay Proxy (встроенный HTTP CONNECT + API) и Telegram бота.
# ──────────────────────────────────────────────────────────────────────────

echo "╔══════════════════════════════════════╗"
echo "║     🛡️ Desacratio VPN — Startup      ║"
echo "╚══════════════════════════════════════╝"

PORT="${PORT:-8443}"
echo "📡 Запуск Relay Proxy (HTTP CONNECT + API) на порту $PORT..."
python3 api/relay_proxy.py &
RELAY_PID=$!

# Даём API время на запуск
sleep 3

echo "🤖 Запуск Telegram бота..."
# Bot на переднем плане — контейнер жив, пока жив бот
exec python3 free-bot.py
