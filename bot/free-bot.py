#!/usr/bin/env python3
"""
╔══════════════════════════════════════╗
║     🛡️ Desacratio VPN Bot            ║
║     Автор: @desacratio               ║
║     Канал: @ExtractionOfThoughts     ║
╚══════════════════════════════════════╝
Бесплатный VPN на базе Cloudflare WARP.
5 серверов, подписка, .conf — всё для свободы в интернете.

Запуск:
  export BOT_TOKEN="8977861654:AAEkkQe19YioVjznqLRPue84-gW8OACY_6s"
  python3 free-bot.py
"""

import os
import sys
import json
import time
import logging
import io
import subprocess
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "--break-system-packages"])
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ─── Конфигурация ────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
API_PORT    = os.environ.get("PORT", "8443")
API_BASE    = os.environ.get("API_BASE", f"http://localhost:{API_PORT}")
ADMIN_ID    = int(os.environ.get("ADMIN_ID", "8587090554"))

# Branding
BRAND       = "Desacratio VPN"
LOGO        = "🛡️"
AUTHOR      = "@desacratio"
CHANNEL     = "@ExtractionOfThoughts"
SUPPORT     = "@DesacratioVPNSupportBot"
BOT_UNAME   = "@DesacratioVPNBot"
CHANNEL_LINK = "https://t.me/ExtractionOfThoughts"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("desacratio-bot")

# ─── Сервера ─────────────────────────────────────────────────────────────
SERVERS = [
    {"id": "pl", "name": "Poland",     "flag": "🇵🇱", "ping": "~12ms", "color": "#3B82F6"},
    {"id": "de", "name": "Germany",    "flag": "🇩🇪", "ping": "~28ms", "color": "#F59E0B"},
    {"id": "nl", "name": "Netherlands","flag": "🇳🇱", "ping": "~35ms", "color": "#10B981"},
    {"id": "gb", "name": "UK",         "flag": "🇬🇧", "ping": "~52ms", "color": "#EF4444"},
    {"id": "us", "name": "USA",        "flag": "🇺🇸", "ping": "~98ms", "color": "#8B5CF6"},
]

# ─── Утилиты ─────────────────────────────────────────────────────────────

def api_get(path: str, timeout: int = 10) -> dict:
    """GET запрос к API, возвращает dict."""
    url = f"{API_BASE.rstrip('/')}/{path.lstrip('/')}"
    try:
        req = Request(url, headers={"User-Agent": "DesacratioBot/1.0"})
        resp = urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except URLError as e:
        logger.warning(f"API error ({url}): {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.warning(f"API error ({url}): {e}")
        return {"error": str(e)}


def api_get_text(path: str, timeout: int = 10) -> str:
    """GET запрос, возвращает текст."""
    url = f"{API_BASE.rstrip('/')}/{path.lstrip('/')}"
    try:
        req = Request(url, headers={"User-Agent": "DesacratioBot/1.0"})
        resp = urlopen(req, timeout=timeout)
        return resp.read().decode()
    except Exception as e:
        logger.warning(f"API text error ({url}): {e}")
        return ""


def get_api_url():
    """Возвращает публичный URL API."""
    return API_BASE


async def delete_and_send(update, context, text, keyboard, parse_mode="HTML"):
    """Удаляет старое сообщение и отправляет новое."""
    query = update.callback_query
    if query:
        await query.answer()
        try:
            await query.message.delete()
        except:
            pass
        chat_id = query.message.chat_id
    else:
        chat_id = update.message.chat_id

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=parse_mode,
        disable_web_page_preview=True,
    )


def make_servers_text() -> str:
    """Красивый список серверов."""
    lines = ["🌍 <b>Наши серверы:</b>\n"]
    for s in SERVERS:
        lines.append(f"  {s['flag']} <b>{s['name']}</b> — {s['ping']}")
    return "\n".join(lines)


# ─── Команда /start (главное меню) ──────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        try:
            await query.message.delete()
        except:
            pass
        chat_id = query.message.chat_id
    else:
        chat_id = update.message.chat_id

    text = (
        f"{LOGO} <b>{BRAND}</b>\n\n"
        f"🌐 <b>Free безлимитный VPN</b>\n"
        f"Обходит любые блокировки. 5 серверов по всему миру.\n"
        f"Работает там, где другие бессильны.\n\n"
        f"▫️ Без регистрации и смс\n"
        f"▫️ Без лимитов скорости и трафика\n"
        f"▫️ 5 стран на выбор\n"
        f"▫️ 24/7 — серверы всегда в сети\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📢 <b>Канал:</b> {CHANNEL}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👇 <b>Выбери платформу:</b>"
    )

    keyboard = [
        [InlineKeyboardButton("📱 Android", callback_data="android"),
         InlineKeyboardButton("🍎 iOS", callback_data="ios")],
        [InlineKeyboardButton("💻 Windows", callback_data="windows"),
         InlineKeyboardButton("🍏 macOS", callback_data="macos")],
        [InlineKeyboardButton("🐧 Linux", callback_data="linux")],
        [],
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("🌍 Серверы", callback_data="servers"),
         InlineKeyboardButton("📖 Помощь", callback_data="help")],
        [],
        [InlineKeyboardButton(f"📢 {CHANNEL}", url=CHANNEL_LINK)],
    ]

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ─── Моя подписка ────────────────────────────────────────────────────────

async def my_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает подписку пользователя."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id
    username = user.username or "—"
    first_name = user.first_name or ""

    api_base = get_api_url()
    sub_url = f"{api_base}/api/sub/{user_id}"
    clash_url = f"{api_base}/api/sub/{user_id}/clash"
    conf_url = f"{api_base}/api/sub/{user_id}/conf"

    # Пробуем получить список серверов
    servers_info = ""
    try:
        resp = api_get(f"/api/sub/{user_id}/servers", timeout=30)
        if "servers" in resp:
            srvs = resp["servers"]
            servers_info = "\n".join([
                f"  {s['flag']} <b>{s['name']}</b> — {s['endpoint']}"
                for s in srvs
            ])
            servers_info = f"\n🌍 <b>Твои серверы:</b>\n{servers_info}\n"
    except:
        servers_info = ""

    text = (
        f"📋 <b>Моя подписка — {BRAND}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>Пользователь:</b>\n"
        f"   ID: <code>{user_id}</code>\n"
        f"   Username: @{username}\n"
        f"   Имя: {first_name}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{servers_info}"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 <b>Ссылка подписки (Sing-box):</b>\n"
        f"<code>{sub_url}</code>\n\n"
        f"🔗 <b>Ссылка подписки (Clash):</b>\n"
        f"<code>{clash_url}</code>\n\n"
        f"📄 <b>WireGuard .conf:</b>\n"
        f"<code>{conf_url}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🧩 <b>Куда вставить:</b>\n\n"
        f"▫️ <b>Happ (HiddifyNext)</b> — + → Импорт из буфера\n"
        f"▫️ <b>Sing-box</b> — Remote URL → вставь ссылку\n"
        f"▫️ <b>Clash Meta</b> — Подписки → Добавить\n"
        f"▫️ <b>V2rayTun</b> — + → Импорт из буфера\n"
        f"▫️ <b>NekoBox / Streisand</b> — + → Импорт подписки\n"
        f"▫️ <b>WireGuard</b> — Открыть .conf файл\n\n"
        f"💡 <i>Скопируй ссылку и вставь в приложение —\n"
        f"всё настроится само.</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📢 {CHANNEL} — подпишись!"
    )

    keyboard = [
        [InlineKeyboardButton("📥 Скачать .conf", callback_data="dl_conf")],
        [InlineKeyboardButton("🔄 Обновить ключи", callback_data="refresh_keys")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]

    await delete_and_send(update, context, text, keyboard)


async def refresh_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перегенерация ключей подписки."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    await query.edit_message_text("🔄 Генерирую новые ключи... Подожди немного...")

    try:
        resp = api_get(f"/api/sub/{user_id}/refresh", timeout=60)
        if resp.get("success"):
            await query.edit_message_text(
                f"✅ <b>Ключи обновлены!</b>\n\n"
                f"Сгенерировано {resp.get('servers', 5)} новых серверов.\n"
                f"Обнови подписку в приложении.\n\n"
                f"📢 {CHANNEL}",
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text(
                f"❌ Ошибка: {resp.get('error', 'неизвестная')}\n"
                f"Попробуй позже.",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Refresh error for {user_id}: {e}")
        await query.edit_message_text(
            f"❌ Ошибка при генерации: {e}\n"
            f"Попробуй позже или напиши в {SUPPORT}",
            parse_mode="HTML"
        )

    # Возвращаем меню подписки
    keyboard = [[InlineKeyboardButton("◀️ Назад к подписке", callback_data="my_sub")]]
    await query.message.reply_text(
        "👆 Нажми чтобы вернуться",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def dl_conf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерирует и отправляет .conf пользователю."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    try:
        conf_url = f"{API_BASE}/api/sub/{user_id}/conf"
        req = Request(conf_url, headers={"User-Agent": "DesacratioBot/1.0"})
        resp = urlopen(req, timeout=15)
        conf_data = resp.read().decode()

        file = io.BytesIO(conf_data.encode())
        await query.message.reply_document(
            document=InputFile(file, filename=f"desacratio-{user_id}.conf"),
            caption=(
                f"📄 <b>WireGuard .conf — {BRAND}</b>\n\n"
                f"5 серверов в одном файле.\n"
                f"Выбери один [Interface]+[Peer] блок и импортируй.\n\n"
                f"Или просто открой файл в WireGuard приложении.\n\n"
                f"📢 {CHANNEL}"
            ),
            parse_mode="HTML"
        )
        logger.info(f"Sent .conf to {user_id}")
    except Exception as e:
        logger.error(f"Conf error for {user_id}: {e}")
        await query.message.reply_text(
            f"❌ Ошибка генерации .conf: {e}\n"
            f"Попробуй позже.",
            parse_mode="HTML"
        )


# ─── Серверы ─────────────────────────────────────────────────────────────

async def servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список серверов."""
    text = (
        f"🌍 <b>Серверы {BRAND}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"5 серверов в разных странах.\n"
        f"У каждого пользователя — свои уникальные ключи\n"
        f"и свой набор серверов.\n\n"
    )
    text += make_servers_text()
    text += (
        f"\n\n━━━━━━━━━━━━━━━━━━\n"
        f"💡 Выбери ближайший к тебе сервер\n"
        f"для минимального пинга.\n\n"
        f"📢 {CHANNEL} — новости и обновления"
    )

    keyboard = [
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]

    await delete_and_send(update, context, text, keyboard)


# ─── Платформы ───────────────────────────────────────────────────────────

def platform_text(name: str, icon: str, instructions: str, extra: str = "") -> str:
    """Собирает текст для страницы платформы."""
    text = (
        f"{icon} <b>{name} — {BRAND}</b>\n\n"
        f"{instructions}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🔌 <b>Ручное подключение:</b>\n\n"
        f"Подписка подходит для:\n"
        f"▫️ <b>Happ (HiddifyNext)</b> — iOS/Android\n"
        f"▫️ <b>Sing-box</b> — все платформы\n"
        f"▫️ <b>Clash Meta</b> — Windows/Android\n"
        f"▫️ <b>V2rayTun</b> — Android/Windows\n"
        f"▫️ <b>NekoBox / Streisand</b> — Android\n"
        f"▫️ <b>WireGuard</b> — все платформы\n\n"
        f"Нажми «📋 Моя подписка» в меню —\n"
        f"получи ссылку и .conf файл.\n\n"
        f"📢 {CHANNEL}"
    )
    if extra:
        text += f"\n\n{extra}"
    return text


async def android(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = platform_text("Android", "📱",
        "▫️ Скачай приложение <b>Happ</b> или <b>Sing-box</b>\n"
        "▫️ Нажми «📋 Моя подписка» ниже\n"
        "▫️ Скопируй ссылку и вставь в приложение\n"
        "▫️ Или скачай .conf файл и открой в WireGuard\n\n"
        "✅ Готово! Трафик пойдёт через наши серверы.",
        extra="📱 <b>Рекомендуемые приложения:</b>\n"
              "▫️ <b>Happ</b> — play.google.com\n"
              "▫️ <b>Sing-box</b> — github.com/SagerNet/sing-box\n"
              "▫️ <b>NekoBox</b> — github.com/MatsuriDayo/NekoBox\n"
              "▫️ <b>WireGuard</b> — play.google.com"
    )

    keyboard = [
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]
    await delete_and_send(update, context, text, keyboard)


async def ios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = platform_text("iOS", "🍎",
        "▫️ Скачай <b>Happ (HiddifyNext)</b> из AppStore\n"
        "▫️ Нажми «📋 Моя подписка» ниже\n"
        "▫️ Скопируй ссылку\n"
        "▫️ Happ → + → Импорт из буфера\n\n"
        "Или используй <b>Sing-box</b> из AppStore.\n\n"
        "✅ Готово!",
        extra="🍎 <b>Рекомендуемые приложения:</b>\n"
              "▫️ <b>Happ</b> — AppStore\n"
              "▫️ <b>Sing-box</b> — AppStore"
    )

    keyboard = [
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]
    await delete_and_send(update, context, text, keyboard)


async def windows(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = platform_text("Windows", "💻",
        "▫️ Скачай <b>Clash Meta</b> или <b>Happ</b>\n"
        "▫️ Нажми «📋 Моя подписка» ниже\n"
        "▫️ Скопируй ссылку\n"
        "▫️ Clash → Подписки → Добавить\n\n"
        "Или используй <b>WireGuard</b>:\n"
        "▫️ Скачай .conf файл\n"
        "▫️ WireGuard → Импорт → выбери файл\n\n"
        "✅ Готово!",
        extra="💻 <b>Рекомендуемые приложения:</b>\n"
              "▫️ <b>Clash Meta</b> — github.com/MetaCubeX/Clash.Meta\n"
              "▫️ <b>Happ</b> — GitHub\n"
              "▫️ <b>WireGuard</b> — wireguard.com/install"
    )

    keyboard = [
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]
    await delete_and_send(update, context, text, keyboard)


async def macos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = platform_text("macOS", "🍏",
        "▫️ Скачай <b>Sing-box</b> или <b>Happ</b>\n"
        "▫️ Нажми «📋 Моя подписка» ниже\n"
        "▫️ Скопируй ссылку\n"
        "▫️ Вставь в приложение\n\n"
        "Или используй <b>WireGuard</b>:\n"
        "▫️ Скачай .conf файл\n"
        "▫️ WireGuard → Импорт\n\n"
        "✅ Готово!",
        extra="🍏 <b>Рекомендуемые приложения:</b>\n"
              "▫️ <b>Sing-box</b> — github.com/SagerNet/sing-box\n"
              "▫️ <b>WireGuard</b> — AppStore"
    )

    keyboard = [
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]
    await delete_and_send(update, context, text, keyboard)


async def linux(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = platform_text("Linux", "🐧",
        "▫️ Установи <b>Sing-box</b> или <b>Clash Meta</b>\n"
        "▫️ Нажми «📋 Моя подписка» ниже\n"
        "▫️ Скопируй ссылку\n"
        "▫️ Вставь в приложение\n\n"
        "Или используй <b>WireGuard</b>:\n"
        "▫️ <code>sudo apt install wireguard</code>\n"
        "▫️ Скачай .conf\n"
        "▫️ <code>sudo wg-quick up desacratio-*.conf</code>\n\n"
        "✅ Готово!",
        extra="🐧 <b>Установка:</b>\n"
              "▫️ <b>Sing-box:</b> sing-box.sagernet.org\n"
              "▫️ <b>WireGuard:</b> <code>sudo apt install wireguard</code>"
    )

    keyboard = [
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]
    await delete_and_send(update, context, text, keyboard)


# ─── Помощь ─────────────────────────────────────────────────────────────

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Страница помощи."""
    text = (
        f"❓ <b>{BRAND} — Помощь</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🛡️ <b>Как это работает?</b>\n"
        f"Трафик шифруется через Cloudflare WARP\n"
        f"и выходит через наши серверы в 5 странах.\n"
        f"Обходит любые блокировки.\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <b>Android:</b> Happ / NekoBox + подписка\n"
        f"🍎 <b>iOS:</b> Happ из AppStore + подписка\n"
        f"💻 <b>Windows:</b> Clash Meta / WireGuard + подписка\n"
        f"🍏 <b>macOS:</b> Sing-box / WireGuard + подписка\n"
        f"🐧 <b>Linux:</b> Sing-box / WireGuard + подписка\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🔐 <b>Безопасность:</b>\n"
        f"▫️ Шифрование WARP (протокол MASQUE)\n"
        f"▫️ Мы не храним логи\n"
        f"▫️ Абсолютно бесплатно\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🌍 <b>Серверы:</b>\n"
    )
    text += make_servers_text()
    text += (
        f"\n\n━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 <b>Поддержка:</b> {SUPPORT}\n"
        f"📢 <b>Канал:</b> {CHANNEL}\n"
        f"👤 <b>Автор:</b> {AUTHOR}\n\n"
        f"💰 <b>Стоимость:</b> Абсолютно бесплатно. Без лимитов."
    )

    keyboard = [
        [InlineKeyboardButton(f"📢 {CHANNEL}", url=CHANNEL_LINK)],
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Главное меню", callback_data="back")],
    ]
    await delete_and_send(update, context, text, keyboard)


# ─── Обработчики ─────────────────────────────────────────────────────────

async def back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан!")
        print("❌ export BOT_TOKEN='твой_токен'")
        sys.exit(1)

    bot = Application.builder().token(BOT_TOKEN).build()

    # Команды
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("help", help_cmd))

    # Навигация
    bot.add_handler(CallbackQueryHandler(back, pattern="^back$"))
    bot.add_handler(CallbackQueryHandler(android, pattern="^android$"))
    bot.add_handler(CallbackQueryHandler(ios, pattern="^ios$"))
    bot.add_handler(CallbackQueryHandler(windows, pattern="^windows$"))
    bot.add_handler(CallbackQueryHandler(macos, pattern="^macos$"))
    bot.add_handler(CallbackQueryHandler(linux, pattern="^linux$"))
    bot.add_handler(CallbackQueryHandler(help_cmd, pattern="^help$"))
    bot.add_handler(CallbackQueryHandler(servers, pattern="^servers$"))
    bot.add_handler(CallbackQueryHandler(my_sub, pattern="^my_sub$"))
    bot.add_handler(CallbackQueryHandler(refresh_keys, pattern="^refresh_keys$"))
    bot.add_handler(CallbackQueryHandler(dl_conf, pattern="^dl_conf$"))

    logger.info(f"🚀 {BRAND} Bot запущен!")
    print(f"╔══════════════════════════════════════╗")
    print(f"║     {LOGO} {BRAND} Bot")
    print(f"║     Автор: {AUTHOR}")
    print(f"║     Канал: {CHANNEL}")
    print(f"╚══════════════════════════════════════╝")
    print(f"Бот: {BOT_UNAME}")
    print(f"Поддержка: {SUPPORT}")
    print(f"API: {API_BASE}")

    bot.run_polling()


if __name__ == "__main__":
    main()
