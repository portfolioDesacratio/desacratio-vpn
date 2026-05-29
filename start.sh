#!/bin/bash
# ─── Desacratio VPN — Startup Script ────────────────────────────────────
# Запускает WARP API (подписки) + опционально Relay Proxy + Telegram бота.
# ──────────────────────────────────────────────────────────────────────────
# Режимы:
#   RELAY_ENABLED=true  — HTTP CONNECT релей-прокси (требуется без Cloudflare)
#   RELAY_ENABLED=false — Только API (для Render.com за Cloudflare) [по умолчанию]
# ──────────────────────────────────────────────────────────────────────────

echo "╔══════════════════════════════════════╗"
echo "║     🛡️ Desacratio VPN — Startup      ║"
echo "╚══════════════════════════════════════╝"

PORT="${PORT:-8443}"

if [ "${RELAY_ENABLED}" = "true" ]; then
    echo "📡 Запуск Relay Proxy (HTTP CONNECT + WARP API) на порту $PORT..."
    python3 api/relay_proxy.py &
    SERVER_PID=$!
    echo "  ⚠️  CONNECT proxy работает ТОЛЬКО если сервер НЕ за Cloudflare!"
else
    echo "📡 Запуск WARP API (WireGuard подписки) на порту $PORT..."
    python3 warp-api.py &
    SERVER_PID=$!
fi

# Даём API время на запуск
sleep 3

echo "🤖 Запуск Telegram бота..."
# Bot на переднем плане — контейнер жив, пока жив бот
exec python3 free-bot.py
