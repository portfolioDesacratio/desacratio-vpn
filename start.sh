#!/bin/bash
# ─── Desacratio VPN — Startup Script ────────────────────────────────────
# Запускает API и Telegram бота в одном контейнере.
# API — HTTP сервер (подписки, .conf)
# Bot — Telegram поллинг (меню, команды)
#
# Render.com: Web Service → Start Command: bash start.sh
# ──────────────────────────────────────────────────────────────────────────

set -e

echo "╔══════════════════════════════════════╗"
echo "║     🛡️ Desacratio VPN — Startup      ║"
echo "╚══════════════════════════════════════╝"

# Запускаем API в фоне
echo "📡 Запуск API на порту ${PORT:-8443}..."
python3 warp-api.py &
API_PID=$!

# Даём API секунду на старт
sleep 1

# Запускаем бота (остаётся на переднем плане — контейнер жив, пока жив бот)
echo "🤖 Запуск Telegram бота..."
python3 free-bot.py
