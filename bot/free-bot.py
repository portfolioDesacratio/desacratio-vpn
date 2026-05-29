#!/usr/bin/env python3
"""
╔══════════════════════════════════════╗
║     🛡️ Desacratio VPN Bot            ║
║     Автор: @desacratio               ║
║     Канал: @ExtractionOfThoughts     ║
╚══════════════════════════════════════╝
Премиум VPN с собственными серверами.
5 стран, уникальные ключи, подписка.
"""

import os
import sys
import json
import time
import io
import logging
import subprocess
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, LabeledPrice
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "--break-system-packages"])
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, LabeledPrice
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters

# Подключаем БД
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from db import (
        init_db, ensure_user, start_trial, has_active_sub, get_sub_info,
        get_user, activate_sub, get_all_users, get_stats, PLANS, TRIAL_DAYS,
        export_db_to_json, restore_db_from_dict
    )
except ImportError:
    # Если db.py рядом
    try:
        from db import *
    except ImportError:
        PLANS = {}
        TRIAL_DAYS = 7
        def init_db(): pass
        def ensure_user(*a): pass
        def start_trial(*a): return True
        def has_active_sub(*a): return True
        def get_sub_info(*a): return {"status": "unknown"}
        def get_user(*a): return None
        def activate_sub(*a): return True
        def get_all_users(): return []
        def get_stats(): return {}


# ─── Payments Module ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from payments import (
    CryptoBotAPI, CRYPTOBOT_ENABLED, CRYPTOBOT_ASSET,
    parse_star_payload, make_star_payload,
    save_pending_crypto, get_pending_crypto,
    activate_subscription, notify_user_telegram,
    STAR_PRICES, format_pricing_text, format_asset_icon,
)

# ─── Конфигурация ────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")

# Внутренний URL для API (localhost — оба в одном контейнере)
API_PORT    = os.environ.get("PORT", "8443")
API_BASE    = os.environ.get("API_BASE", f"http://localhost:{API_PORT}")

# Внешний URL для подписок (Render устанавливает RENDER_EXTERNAL_URL)
RENDER_URL  = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
PUBLIC_URL  = os.environ.get("PUBLIC_URL", RENDER_URL or API_BASE)

ADMIN_ID    = int(os.environ.get("ADMIN_ID", "8587090554"))

# Branding
BRAND       = "Desacratio VPN"
LOGO        = "🛡️"
AUTHOR      = "@desacratio"
CHANNEL     = "@ExtractionOfThoughts"
SUPPORT     = "@DesacratioVPNSupportBot"
BOT_UNAME   = "@DesacratioVPNBot"
CHANNEL_LINK = "https://t.me/ExtractionOfThoughts"
PURCHASE_CONTACT = "@desacratio"

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
    """GET запрос к API."""
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


def api_post(path: str, timeout: int = 10) -> dict:
    """POST запрос к API."""
    url = f"{API_BASE.rstrip('/')}/{path.lstrip('/')}"
    try:
        data = json.dumps({}).encode()
        req = Request(url, data=data, headers={
            "User-Agent": "DesacratioBot/1.0",
            "Content-Type": "application/json",
        })
        req.method = "POST"
        resp = urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except URLError as e:
        logger.warning(f"API POST error ({url}): {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.warning(f"API POST error ({url}): {e}")
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


def get_api_url() -> str:
    """Возвращает публичный URL для подписок (показывается пользователю)."""
    return PUBLIC_URL


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


def make_pricing_text() -> str:
    """Таблица цен."""
    lines = [
        "━━━━━━━━━━━━━━━━━━",
        "💎 <b>Прайс-лист:</b>\n",
    ]
    for key, plan in PLANS.items():
        lines.append(f"▫️ <b>{plan['label']}</b>")
        lines.append(f"   💵 ${plan['price_usd']:.2f}  |  ⭐️ {plan['stars']} звёзд")
        lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"💳 <b>Как купить:</b>")
    lines.append(f"Напиши {PURCHASE_CONTACT} или")
    lines.append(f"нажми кнопку «💳 Купить подписку»")
    return "\n".join(lines)


# ─── Keep-Alive (бот не даёт Render заснуть) ─────────────────────────────

async def keep_alive_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Каждые 5 минут пингует /health через публичный URL.
    Render считает это входящим трафиком → не выключает сервис.
    """
    if not PUBLIC_URL or "localhost" in PUBLIC_URL:
        return  # Нет публичного URL — нечего пинговать

    try:
        req = Request(
            f"{PUBLIC_URL}/health",
            headers={"User-Agent": "DesacratioKeepAlive/1.0"},
        )
        resp = urlopen(req, timeout=15)
        status = resp.status
        logger.debug(f"💓 Keep-alive ping -> {PUBLIC_URL}/health = {status}")
    except Exception as e:
        logger.debug(f"Keep-alive ping failed: {e}")


# ─── Главное меню /start ────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню."""
    query = update.callback_query
    if query:
        await query.answer()
        try:
            await query.message.delete()
        except:
            pass
        chat_id = query.message.chat_id
        user_id = query.from_user.id
    else:
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

    # Регистрируем пользователя
    user = update.effective_user
    ensure_user(user_id, user.username or "", user.first_name or "")

    text = (
        f"{LOGO} <b>{BRAND}</b>\n\n"
        f"🌐 <b>Премиум VPN без компромиссов</b>\n"
        f"Собственные серверы в 5 странах мира.\n"
        f"Высокая скорость, полная анонимность.\n\n"
        f"▫️ 5 стран: Польша, Германия, Нидерланды, UK, USA\n"
        f"▫️ Уникальные ключи для каждого пользователя\n"
        f"▫️ Без лимитов скорости и трафика\n"
        f"▫️ Работает везде — обходит любые блокировки\n"
        f"▫️ 7 дней пробного периода 🎁\n\n"
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
        [InlineKeyboardButton("💳 Купить подписку", callback_data="pricing"),
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
    """Показывает статус подписки и ссылки."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = query.from_user

    # Проверяем подписку
    sub_info = get_sub_info(user_id)
    has_sub = has_active_sub(user_id)

    # Если нет подписки — автостарт триала при первом входе
    if not has_sub:
        if sub_info["status"] == "none":
            # Первый вход — стартуем триал
            start_trial(user_id)
            sub_info = get_sub_info(user_id)
            has_sub = True
            text = (
                f"🎁 <b>Пробный период активирован!</b>\n\n"
                f"У тебя {TRIAL_DAYS} дней бесплатного доступа.\n"
                f"Пользуйся всеми серверами без ограничений.\n\n"
            )
        else:
            # Триал истёк или подписка истекла
            await show_no_sub(update, context, sub_info)
            return
    else:
        text = ""

    # Собираем информацию о подписке
    if sub_info["status"] == "trial":
        text += (
            f"📋 <b>Моя подписка — {BRAND}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Пользователь:</b> @{user.username or '—'}\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            f"🎁 <b>Статус:</b> Пробный период\n"
            f"⏱ <b>Осталось:</b> {sub_info['days_left']} из {TRIAL_DAYS} дней\n\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
        )
    elif sub_info["status"] == "active":
        text += (
            f"📋 <b>Моя подписка — {BRAND}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Пользователь:</b> @{user.username or '—'}\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            f"💎 <b>Статус:</b> Активна\n"
            f"📦 <b>Тариф:</b> {PLANS.get(sub_info.get('type', ''), {}).get('label', sub_info.get('type', '—'))}\n"
            f"⏱ <b>Осталось:</b> {sub_info['days_left']} дней\n\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
        )

    # Получаем серверы
    api_base = get_api_url()
    servers_info = ""
    try:
        resp = api_get(f"/api/sub/{user_id}/servers", timeout=30)
        if "servers" in resp:
            srvs = resp["servers"]
            servers_info = "\n".join([
                f"  {s['flag']} <b>{s['name']}</b> — {s['endpoint']}"
                for s in srvs
            ])
            servers_info = f"🌍 <b>Твои серверы:</b>\n{servers_info}\n\n"
    except:
        servers_info = ""

    sub_url = f"{api_base}/api/sub/{user_id}"
    clash_url = f"{api_base}/api/sub/{user_id}/clash"
    conf_url = f"{api_base}/api/sub/{user_id}/conf"

    text += (
        f"{servers_info}"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 <b>Ссылка подписки:</b>\n"
        f"<code>{sub_url}</code>\n\n"
        f"🔗 <b>Clash подписка:</b>\n"
        f"<code>{clash_url}</code>\n\n"
        f"📄 <b>WireGuard .conf:</b>\n"
        f"<code>{conf_url}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"💡 Вставь ссылку в Happ, Sing-box, Clash Meta\n"
        f"или скачай .conf для WireGuard.\n\n"
        f"📢 {CHANNEL}"
    )

    keyboard = [
        [InlineKeyboardButton("📥 Скачать .conf", callback_data="dl_conf")],
        [InlineKeyboardButton("🔄 Обновить ключи", callback_data="refresh_keys")],
        [InlineKeyboardButton("💳 Удлинить подписку", callback_data="pricing")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]

    await delete_and_send(update, context, text, keyboard)


async def show_no_sub(update, context, sub_info):
    """Показывает что подписки нет и предлагает купить."""
    query = update.callback_query
    user_id = query.from_user.id

    if sub_info["status"] == "expired_trial":
        title = "⏰ Пробный период закончился"
        msg = "Твои 7 дней бесплатного доступа истекли.\nКупи подписку чтобы продолжить пользоваться."
    elif sub_info["status"] == "expired":
        title = "⏰ Подписка истекла"
        msg = "Срок действия подписки закончился.\nПродли её чтобы продолжить."
    else:
        title = "📋 У тебя нет активной подписки"
        msg = "Начни с пробного периода на 7 дней или сразу купи подписку."

    text = (
        f"{title}\n\n{msg}\n\n"
        f"{make_pricing_text()}"
    )

    keyboard = [
        [InlineKeyboardButton("🎁 Пробный период (7 дней)", callback_data="start_trial")],
        [InlineKeyboardButton("💳 Купить подписку", url=f"https://t.me/{PURCHASE_CONTACT[1:]}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]

    try:
        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except:
        keyboard_full = keyboard + [[InlineKeyboardButton(f"📢 {CHANNEL}", url=CHANNEL_LINK)]]
        await delete_and_send(update, context, text, keyboard_full)


async def start_trial_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Активирует пробный период."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if start_trial(user_id):
        await query.edit_message_text(
            f"🎁 <b>Пробный период активирован!</b>\n\n"
            f"Теперь у тебя {TRIAL_DAYS} дней бесплатного доступа.\n"
            f"Нажми «📋 Моя подписка» чтобы получить ссылки.",
            parse_mode="HTML"
        )
        keyboard = [[InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")]]
        await query.message.reply_text(
            "👇 Продолжить",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await query.edit_message_text(
            "❌ Ты уже использовал пробный период.\n"
            "Купи подписку чтобы продолжить.",
            parse_mode="HTML"
        )
        keyboard = [
            [InlineKeyboardButton("💳 Купить подписку", url=f"https://t.me/{PURCHASE_CONTACT[1:]}")],
        ]
        await query.message.reply_text(
            "👇",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )


# ─── Цены и покупка ──────────────────────────────────────────────────────

async def pricing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает цены и способы оплаты."""
    query = update.callback_query
    await query.answer()

    text = (
        f"💎 <b>Тарифы {BRAND}</b>\n\n"
        f"{format_pricing_text()}"
    )

    buttons = [
        [InlineKeyboardButton("⭐️ Telegram Stars", callback_data="pay_stars")],
    ]

    # CryptoBot — только если настроен токен
    if CRYPTOBOT_ENABLED:
        asset_icon = format_asset_icon(CRYPTOBOT_ASSET)
        buttons.append(
            [InlineKeyboardButton(f"{asset_icon} Crypto ({CRYPTOBOT_ASSET})", callback_data="pay_crypto")]
        )

    buttons += [
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]

    await delete_and_send(update, context, text, buttons)


# ─── Выбор тарифа (Stars) ────────────────────────────────────────────────

async def pay_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает тарифы для оплаты звёздами."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    text = (
        f"⭐️ <b>Оплата Telegram Stars</b>\n\n"
        f"Выбери тариф:\n\n"
    )
    for key, stars in STAR_PRICES.items():
        from db import PLANS
        plan = PLANS.get(key, {})
        text += f"▫️ <b>{plan.get('label', key)}</b> — {stars} ⭐\n"

    text += f"\n💳 Спишется со звёзд твоего аккаунта Telegram."
    text += f"\nПополнить: Настройки → Telegram Stars"

    keyboard = build_plan_keyboard("stars", user_id)
    await delete_and_send(update, context, text, keyboard)


async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает тарифы для оплаты криптой."""
    if not CRYPTOBOT_ENABLED:
        await pricing(update, context)
        return

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    asset_icon = format_asset_icon(CRYPTOBOT_ASSET)
    text = (
        f"{asset_icon} <b>Оплата {CRYPTOBOT_ASSET}</b>\n\n"
        f"Выбери тариф:\n\n"
    )
    from db import PLANS
    for key, plan in PLANS.items():
        text += f"▫️ <b>{plan['label']}</b> — {asset_icon} {plan['price_usd']:.2f} {CRYPTOBOT_ASSET}\n"

    text += f"\n🔒 Оплата через @CryptoBot (анонимно)"
    text += f"\nПосле оплаты подписка активируется автоматически."

    keyboard = build_plan_keyboard("crypto", user_id)
    await delete_and_send(update, context, text, keyboard)


def build_plan_keyboard(method: str, user_id: int) -> list:
    """Строит клавиатуру выбора тарифа."""
    from db import PLANS
    buttons = []
    row = []
    for i, (key, plan) in enumerate(PLANS.items()):
        data = f"{method}_plan_{key}"
        if method == "stars":
            stars = STAR_PRICES.get(key, 0)
            label = f"{plan['label']} — {stars}⭐"
        else:
            label = f"{plan['label']} — ${plan['price_usd']:.2f}"
        row.append(InlineKeyboardButton(label, callback_data=data))
        if len(row) == 2 or i == len(PLANS) - 1:
            buttons.append(row)
            row = []
    buttons.append([InlineKeyboardButton("◀️ Назад к способам", callback_data="pricing")])
    return buttons


async def pay_stars_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор конкретного тарифа для Stars — отправляет инвойс."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Парсим callback: "stars_plan_30days"
    data = query.data
    plan_key = data.replace("stars_plan_", "")

    stars = STAR_PRICES.get(plan_key)
    if not stars:
        await query.edit_message_text("❌ Неизвестный тариф.", parse_mode="HTML")
        return

    from db import PLANS
    plan = PLANS.get(plan_key)
    if not plan:
        await query.edit_message_text("❌ Неизвестный тариф.", parse_mode="HTML")
        return

    payload = make_star_payload(plan_key, user_id)

    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"{BRAND} — {plan['label']}",
            description=(
                f"Премиум VPN подписка на {plan['label'].lower()}.\n"
                f"🌍 5 стран | 🚀 Без лимитов | 🔐 WireGuard WARP"
            ),
            payload=payload,
            provider_token="",       # Пусто для Telegram Stars (XTR)
            currency="XTR",
            prices=[LabeledPrice(plan['label'], stars)],
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False,
        )
        logger.info(f"⭐️ Star invoice sent: user={user_id}, plan={plan_key}, stars={stars}")
    except Exception as e:
        logger.error(f"❌ Star invoice error: {e}")
        text = (
            f"❌ <b>Ошибка отправки счёта</b>\n\n"
            f"{e}\n\n"
            f"Попробуй позже или напиши {SUPPORT}"
        )
        await query.edit_message_text(text, parse_mode="HTML")


async def pay_crypto_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор тарифа для CryptoBot — создаёт инвойс."""
    if not CRYPTOBOT_ENABLED:
        await pricing(update, context)
        return

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Парсим callback: "crypto_plan_30days"
    data = query.data
    plan_key = data.replace("crypto_plan_", "")

    from db import PLANS
    plan = PLANS.get(plan_key)
    if not plan:
        await query.edit_message_text("❌ Неизвестный тариф.", parse_mode="HTML")
        return

    amount = f"{plan['price_usd']:.2f}"
    payload = make_star_payload(plan_key, user_id)  # используем тот же формат
    description = f"{BRAND} — {plan['label']}"

    # Создаём инвойс через CryptoBot API
    api = CryptoBotAPI()
    result = api.create_invoice(
        amount=amount,
        payload=payload,
        description=description,
        expires_in=7200,  # 2 часа
    )

    if not result.get("ok"):
        error_msg = result.get("error", "Неизвестная ошибка")
        logger.error(f"❌ CryptoBot invoice error: {error_msg}")
        text = (
            f"❌ <b>Ошибка создания счёта</b>\n\n"
            f"{error_msg}\n\n"
            f"Попробуй позже или напиши {SUPPORT}"
        )
        # Пробуем отредактировать сообщение, иначе шлём новое
        try:
            await query.edit_message_text(text, parse_mode="HTML")
        except:
            await query.message.reply_text(text, parse_mode="HTML")
        return

    invoice = result["result"]
    invoice_id = invoice["invoice_id"]
    pay_url = invoice["pay_url"]
    asset = invoice.get("asset", CRYPTOBOT_ASSET)
    amount_str = invoice.get("amount", amount)

    # Сохраняем ожидающий платёж
    save_pending_crypto(invoice_id, user_id, plan_key)

    asset_icon = format_asset_icon(asset)
    expires_at = invoice.get("expires_at", "через 2 часа")

    text = (
        f"{asset_icon} <b>Счёт на оплату</b>\n\n"
        f"📦 <b>Тариф:</b> {plan['label']}\n"
        f"💵 <b>Сумма:</b> {amount_str} {asset}\n"
        f"⏱ <b>Действует:</b> {expires_at}\n\n"
        f"👇 Нажми кнопку чтобы оплатить через @CryptoBot:\n\n"
        f"⚠️ <b>Важно:</b> после оплаты подписка активируется "
        f"автоматически в течение 1-2 минут."
    )

    keyboard = [
        [InlineKeyboardButton(f"💳 Оплатить {amount_str} {asset}", url=pay_url)],
        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_crypto_{invoice_id}")],
        [InlineKeyboardButton("◀️ Назад к тарифам", callback_data="pay_crypto")],
    ]

    try:
        await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True)
        await query.message.reply_text(
            "💳 <b>Ссылка для оплаты:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except:
        await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def check_crypto_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет статус крипто-платежа."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Парсим callback: "check_crypto_123456"
    invoice_id = int(query.data.replace("check_crypto_", ""))

    api = CryptoBotAPI()
    result = api.get_invoices(invoice_ids=[invoice_id])

    if not result.get("ok"):
        await query.message.reply_text(
            "❌ Не удалось проверить статус. Попробуй позже.",
            parse_mode="HTML"
        )
        return

    invoices = result.get("result", {}).get("items", [])
    if not invoices:
        await query.message.reply_text(
            "❌ Счёт не найден. Возможно, истёк срок действия.",
            parse_mode="HTML"
        )
        return

    invoice = invoices[0]
    status = invoice.get("status", "unknown")

    if status == "paid":
        # Активируем подписку
        pending = get_pending_crypto(invoice_id)
        if pending:
            plan_key = pending["plan"]
            activate_subscription(user_id, plan_key)

            from db import PLANS
            plan_label = PLANS.get(plan_key, {}).get("label", plan_key)
            notify_user_telegram(user_id, plan_label, f"CryptoBot ({CRYPTOBOT_ASSET})")

            remove_pending_crypto(invoice_id)

        await query.message.reply_text(
            f"✅ <b>Оплата подтверждена!</b>\n\n"
            f"Подписка активирована 🎉\n"
            f"Нажми «📋 Моя подписка» чтобы начать пользоваться.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")
            ]])
        )
    elif status == "expired":
        await query.message.reply_text(
            "⏰ <b>Срок счёта истёк</b>\n\n"
            "Создай новый счёт.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Создать новый счёт", callback_data="pay_crypto")
            ]])
        )
    else:
        await query.message.reply_text(
            f"⏳ <b>Статус:</b> ожидание оплаты\n\n"
            f"Счёт ещё не оплачен. Нажми кнопку «Оплатить» "
            f"или попробуй проверить позже.",
            parse_mode="HTML"
        )


# ─── PreCheckout (Stars) ─────────────────────────────────────────────────

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает PreCheckoutQuery от Telegram Stars.
    Подтверждает или отклоняет платёж.
    """
    query = update.pre_checkout_query
    payload = query.invoice_payload

    # Проверяем payload
    parsed = parse_star_payload(payload)
    if not parsed:
        logger.warning(f"❌ Invalid star payload: {payload}")
        await query.answer(ok=False, error_message="Ошибка: неверные данные платежа")
        return

    plan_key, user_id = parsed

    # Проверяем что план существует
    stars = STAR_PRICES.get(plan_key)
    if not stars:
        logger.warning(f"❌ Unknown plan in star payment: {plan_key}")
        await query.answer(ok=False, error_message="Ошибка: неизвестный тариф")
        return

    # Проверяем что сумма совпадает
    if query.total_amount != stars:
        logger.warning(
            f"❌ Star amount mismatch: expected={stars}, got={query.total_amount}"
        )
        await query.answer(ok=False, error_message="Ошибка: неверная сумма")
        return

    # Проверяем что валюта XTR
    if query.invoice_payload != "XTR" and query.currency != "XTR":
        # В PTB v22+ валюта в query.currency, проверим
        pass

    logger.info(f"✅ PreCheckout OK: user={user_id}, plan={plan_key}, stars={stars}")
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает успешный платёж Telegram Stars.
    Активирует подписку и отправляет уведомление.
    """
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload

    parsed = parse_star_payload(payload)
    if not parsed:
        logger.error(f"❌ Cannot parse star payment payload: {payload}")
        return

    plan_key, _ = parsed
    from db import PLANS
    plan_label = PLANS.get(plan_key, {}).get("label", plan_key)

    # Активируем подписку
    success = activate_subscription(user_id, plan_key)

    if success:
        text = (
            f"⭐️ <b>Оплата получена!</b>\n\n"
            f"📦 <b>Тариф:</b> {plan_label}\n"
            f"💳 <b>Способ:</b> Telegram Stars\n"
            f"💰 <b>Списано:</b> {payment.total_amount} ⭐\n\n"
            f"👇 Нажми чтобы начать пользоваться:"
        )
    else:
        text = (
            f"⚠️ <b>Оплата прошла, но ошибка активации</b>\n\n"
            f"Напиши {SUPPORT} — мы поможем!\n"
            f"Не переживай, звёзды уже списаны."
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")
        ]])
    )

    logger.info(f"⭐️ Star payment successful: user={user_id}, plan={plan_key}")


# ─── Обновление ключей ───────────────────────────────────────────────────

async def refresh_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перегенерация ключей."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not has_active_sub(user_id):
        await query.edit_message_text(
            "❌ Нет активной подписки.\n"
            "Купи подписку чтобы продолжить.",
            parse_mode="HTML"
        )
        keyboard = [[InlineKeyboardButton("💳 Купить", callback_data="pricing")]]
        await query.message.reply_text("👇", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    await query.edit_message_text("🔄 Генерирую новые ключи... Это займёт до 30 секунд.")

    try:
        resp = api_post(f"/api/sub/{user_id}/refresh", timeout=60)
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
            f"❌ Ошибка: {e}\n"
            f"Напиши в {SUPPORT}",
            parse_mode="HTML"
        )

    keyboard = [[InlineKeyboardButton("◀️ Назад к подписке", callback_data="my_sub")]]
    await query.message.reply_text(
        "👆",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# ─── Скачать .conf ───────────────────────────────────────────────────────

async def dl_conf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет .conf пользователю."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not has_active_sub(user_id):
        await query.message.reply_text(
            "❌ Нет активной подписки.\n"
            "Купи подписку чтобы скачать .conf.",
            parse_mode="HTML"
        )
        return

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
                f"Выбери один блок [Interface]+[Peer] и импортируй.\n\n"
                f"Совместимо с: WireGuard, Happ, Sing-box, Clash\n\n"
                f"📢 {CHANNEL}"
            ),
            parse_mode="HTML"
        )
        logger.info(f"Sent .conf to {user_id}")
    except Exception as e:
        logger.error(f"Conf error for {user_id}: {e}")
        await query.message.reply_text(
            f"❌ Ошибка: {e}\nПопробуй позже.",
            parse_mode="HTML"
        )


# ─── Серверы ─────────────────────────────────────────────────────────────

async def servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список серверов."""
    text = (
        f"🌍 <b>Серверы {BRAND}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"У нас собственные серверы в 5 странах мира.\n"
        f"Каждый пользователь получает уникальные ключи\n"
        f"и персональный набор серверов.\n\n"
    )
    text += make_servers_text()
    text += (
        f"\n\n━━━━━━━━━━━━━━━━━━\n"
        f"💡 Выбери ближайший сервер для минимального пинга.\n\n"
        f"📢 {CHANNEL}"
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
        f"🔌 <b>Подключение:</b>\n\n"
        f"Нажми «📋 Моя подписка» в меню —\n"
        f"получи ссылку подписки и .conf файл.\n\n"
        f"Подходит для приложений:\n"
        f"▫️ <b>Happ (HiddifyNext)</b> — iOS/Android\n"
        f"▫️ <b>Sing-box</b> — все платформы\n"
        f"▫️ <b>Clash Meta</b> — Windows/Android\n"
        f"▫️ <b>V2rayTun</b> — Android/Windows\n"
        f"▫️ <b>NekoBox / Streisand</b> — Android\n"
        f"▫️ <b>WireGuard</b> — все платформы\n\n"
        f"📢 {CHANNEL}"
    )
    if extra:
        text += f"\n\n{extra}"
    return text


async def android(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = platform_text("Android", "📱",
        "▫️ Скачай <b>Happ</b> или <b>Sing-box</b> из магазина\n"
        "▫️ Нажми «📋 Моя подписка» ниже\n"
        "▫️ Скопируй ссылку и вставь в приложение\n"
        "▫️ Или скачай .conf и открой в WireGuard\n\n"
        "✅ Готово!",
        extra="📱 <b>Приложения:</b>\n"
              "▫️ <b>Happ</b> — Google Play\n"
              "▫️ <b>Sing-box</b> — GitHub\n"
              "▫️ <b>NekoBox</b> — GitHub\n"
              "▫️ <b>WireGuard</b> — Google Play"
    )
    keyboard = [
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ]
    await delete_and_send(update, context, text, keyboard)


async def ios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = platform_text("iOS", "🍎",
        "▫️ Скачай <b>Happ</b> из AppStore\n"
        "▫️ Нажми «📋 Моя подписка» ниже\n"
        "▫️ Скопируй ссылку\n"
        "▫️ Happ → + → Импорт из буфера\n\n"
        "Или используй <b>Sing-box</b> из AppStore.\n\n"
        "✅ Готово!",
        extra="🍎 <b>Приложения:</b>\n"
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
        extra="💻 <b>Приложения:</b>\n"
              "▫️ <b>Clash Meta</b> — GitHub\n"
              "▫️ <b>Happ</b> — GitHub\n"
              "▫️ <b>WireGuard</b> — wireguard.com"
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
        extra="🍏 <b>Приложения:</b>\n"
              "▫️ <b>Sing-box</b> — GitHub\n"
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
        f"Мы используем собственные серверы в 5 странах.\n"
        f"Твой трафик шифруется и проходит через наши\n"
        f"серверы, обеспечивая полную анонимность\n"
        f"и обход любых блокировок.\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <b>Android:</b> Happ / NekoBox + подписка\n"
        f"🍎 <b>iOS:</b> Happ из AppStore + подписка\n"
        f"💻 <b>Windows:</b> Clash Meta / WireGuard\n"
        f"🍏 <b>macOS:</b> Sing-box / WireGuard\n"
        f"🐧 <b>Linux:</b> Sing-box / WireGuard\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🔐 <b>Безопасность:</b>\n"
        f"▫️ Современное шифрование трафика\n"
        f"▫️ Мы не храним логи\n"
        f"▫️ Уникальные ключи для каждого пользователя\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🌍 <b>Серверы:</b>\n"
    )
    text += make_servers_text()
    text += (
        f"\n\n━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 <b>Поддержка:</b> {SUPPORT}\n"
        f"📢 <b>Канал:</b> {CHANNEL}\n"
        f"👤 <b>Автор:</b> {AUTHOR}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>Тарифы:</b>\n"
    )
    for key, plan in PLANS.items():
        text += f"▫️ <b>{plan['label']}</b> — ${plan['price_usd']:.2f} / ⭐️ {plan['stars']}\n"
    text += (
        f"\n💳 Для покупки напиши {PURCHASE_CONTACT}"
    )

    keyboard = [
        [InlineKeyboardButton(f"💳 Купить", url=f"https://t.me/{PURCHASE_CONTACT[1:]}")],
        [InlineKeyboardButton(f"📢 {CHANNEL}", url=CHANNEL_LINK)],
        [InlineKeyboardButton("📋 Моя подписка", callback_data="my_sub")],
        [InlineKeyboardButton("◀️ Главное меню", callback_data="back")],
    ]
    await delete_and_send(update, context, text, keyboard)


# ─── ADMIN COMMANDS ──────────────────────────────────────────────────────

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика (только для админа)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    stats = get_stats()
    text = (
        f"📊 <b>Статистика {BRAND}</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total']}</b>\n"
        f"✅ Активных: <b>{stats['active']}</b>\n"
        f"💎 Платных: <b>{stats['paid']}</b>\n"
        f"💰 Доход: <b>${stats['revenue']:.2f}</b>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить подписку пользователю. /add <user_id> <plan>"""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    args = context.args
    if len(args) < 2:
        plans = "\n".join([f"  <code>{k}</code> — {v['label']}" for k, v in PLANS.items()])
        await update.message.reply_text(
            f"❌ Использование: /add &lt;user_id&gt; &lt;plan&gt;\n\n"
            f"Планы:\n{plans}",
            parse_mode="HTML"
        )
        return

    target_id = int(args[0])
    plan = args[1]

    if plan not in PLANS:
        await update.message.reply_text(f"❌ Неизвестный план: {plan}")
        return

    try:
        activate_sub(target_id, plan, user_id)
        plan_label = PLANS[plan]["label"]
        await update.message.reply_text(
            f"✅ Подписка активирована!\n\n"
            f"👤 Пользователь: <code>{target_id}</code>\n"
            f"📦 Тариф: <b>{plan_label}</b>\n"
            f"👑 Админ: <code>{user_id}</code>",
            parse_mode="HTML"
        )

        # Уведомляем пользователя
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    f"💎 <b>Подписка активирована!</b>\n\n"
                    f"Тариф: {plan_label}\n"
                    f"Спасибо за покупку! 🎉\n\n"
                    f"Нажми /start чтобы начать пользоваться."
                ),
                parse_mode="HTML"
            )
        except:
            logger.warning(f"Не удалось уведомить {target_id}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список пользователей (только для админа)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    users = get_all_users()
    now = int(time.time())
    lines = [f"📋 <b>Все пользователи ({len(users)}):</b>\n"]
    for u in users[:20]:  # показываем первых 20
        status = "❌"
        if u["sub_end"] and now < u["sub_end"]:
            status = "💎"
        elif u["trial_used"] and u["trial_start"] and now - u["trial_start"] < TRIAL_DAYS * 86400:
            status = "🎁"

        sub_info = ""
        if u["sub_type"]:
            sub_info = f" [{PLANS.get(u['sub_type'], {}).get('label', u['sub_type'])}]"

        username = u["username"] or f"ID:{u['user_id']}"
        lines.append(f"{status} <code>{u['user_id']}</code> @{username}{sub_info}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def admin_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Экспорт БД в JSON (перед деплоем)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    data = export_db_to_json()
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)

    # Отправляем как файл
    file = io.BytesIO(text.encode())
    await update.message.reply_document(
        document=InputFile(file, filename=f"desacratio-backup-{int(time.time())}.json"),
        caption=f"📦 <b>Бэкап БД</b>\n"
                f"Пользователей: {len(data['users'])}\n"
                f"Покупок: {len(data['purchases'])}",
        parse_mode="HTML"
    )


async def admin_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Импорт БД из JSON (после деплоя). /import (reply на JSON файл)"""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    # Проверяем reply на документ
    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text(
            "❌ Отправь JSON файл бэкапа и reply на него командой <code>/import</code>\n\n"
            "Или отправь JSON как текст: <code>/import {\"users\": [...]}</code>",
            parse_mode="HTML"
        )
        return

    try:
        # Скачиваем файл
        doc = update.message.reply_to_message.document
        file = await doc.get_file()
        content = await file.download_as_bytearray()
        data = json.loads(content.decode())

        if restore_db_from_dict(data):
            await update.message.reply_text(
                f"✅ <b>БД восстановлена!</b>\n"
                f"Пользователей: {len(data.get('users', []))}\n"
                f"Покупок: {len(data.get('purchases', []))}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ Ошибка восстановления БД")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Помощь по админ-командам."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    plans = "\n".join([f"  <code>{k}</code> — {v['label']} (${v['price_usd']:.2f})" for k, v in PLANS.items()])
    text = (
        f"👑 <b>Админ-команды {BRAND}</b>\n\n"
        f"<code>/stats</code> — статистика\n"
        f"<code>/list</code> — список пользователей\n"
        f"<code>/add &lt;user_id&gt; &lt;plan&gt;</code> — активировать подписку\n"
        f"<code>/export</code> — выгрузить БД (перед деплоем)\n"
        f"<code>/import</code> — загрузить БД (reply на JSON файл)\n"
        f"<code>/admin</code> — эта справка\n\n"
        f"📦 <b>Планы:</b>\n{plans}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ─── Обработчики ─────────────────────────────────────────────────────────

async def back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан!")
        print("❌ export BOT_TOKEN='твой_токен'")
        sys.exit(1)

    # Инициализируем БД
    init_db()

    bot = Application.builder().token(BOT_TOKEN).build()

    # Команды
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("help", help_cmd))

    # Admin команды
    bot.add_handler(CommandHandler("stats", admin_stats))
    bot.add_handler(CommandHandler("list", admin_list))
    bot.add_handler(CommandHandler("add", admin_add))
    bot.add_handler(CommandHandler("export", admin_export))
    bot.add_handler(CommandHandler("import", admin_import))
    bot.add_handler(CommandHandler("admin", admin_help))

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
    bot.add_handler(CallbackQueryHandler(pricing, pattern="^pricing$"))
    bot.add_handler(CallbackQueryHandler(refresh_keys, pattern="^refresh_keys$"))
    bot.add_handler(CallbackQueryHandler(dl_conf, pattern="^dl_conf$"))
    bot.add_handler(CallbackQueryHandler(start_trial_cmd, pattern="^start_trial$"))

    # 💳 Оплата
    bot.add_handler(CallbackQueryHandler(pay_stars, pattern="^pay_stars$"))
    bot.add_handler(CallbackQueryHandler(pay_crypto, pattern="^pay_crypto$"))
    bot.add_handler(CallbackQueryHandler(pay_stars_plan, pattern=r"^stars_plan_"))
    bot.add_handler(CallbackQueryHandler(pay_crypto_plan, pattern=r"^crypto_plan_"))
    bot.add_handler(CallbackQueryHandler(check_crypto_payment, pattern=r"^check_crypto_"))

    # PreCheckout (Stars) — должен быть ДО MessageHandler
    bot.add_handler(PreCheckoutQueryHandler(pre_checkout))

    # Successful Payment (Stars)
    bot.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # ⏰ Keep-alive: каждые 5 минут пингуем свой health endpoint
    # чтобы Render не выключал сервис за простой
    if "localhost" not in PUBLIC_URL and PUBLIC_URL and bot.job_queue is not None:
        bot.job_queue.run_repeating(keep_alive_job, interval=300, first=60)
        logger.info(f"💓 Keep-alive запущен: каждые 5 мин -> {PUBLIC_URL}/health")
    elif "localhost" in PUBLIC_URL or not PUBLIC_URL:
        logger.info("💤 Keep-alive отключён (нет публичного URL)")
    else:
        logger.warning("💤 Keep-alive отключён (job_queue не поддерживается)")

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
