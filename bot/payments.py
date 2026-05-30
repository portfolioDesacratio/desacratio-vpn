#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║     💰 Desacratio VPN — Payment Module                     ║
║     Telegram Stars ⭐ + CryptoBot (@send) 💎                ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import time
import hmac
import hashlib
import logging
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logger = logging.getLogger("desacratio-payments")

# ─── Путь к DB ──────────────────────────────────────────────────────────
# Пробуем все возможные варианты (dev-окружение vs Docker)
_PAYMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _try_dir in (_PAYMENTS_DIR, os.path.dirname(_PAYMENTS_DIR), "/app"):
    if _try_dir not in sys.path and os.path.isdir(_try_dir):
        sys.path.insert(0, _try_dir)

# ─── CryptoBot Configuration ─────────────────────────────────────────────
CRYPTOBOT_TOKEN   = os.environ.get("CRYPTOBOT_TOKEN", "")
CRYPTOBOT_ENABLED = bool(CRYPTOBOT_TOKEN)
CRYPTOBOT_API     = "https://pay.crypt.bot/api"
CRYPTOBOT_ASSET   = os.environ.get("CRYPTOBOT_ASSET", "USDT")

# ─── BOT_TOKEN (для отправки уведомлений из webhook) ────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ═══════════════════════════════════════════════════════════════════════════
# 🔷 1. Telegram Stars (XTR)
# ═══════════════════════════════════════════════════════════════════════════

# Цены в звёздах — уже есть в db.PLANS, продублируем для скорости
STAR_PRICES = {
    "1day":   25,
    "3days":  75,
    "7days":  125,
    "14days": 175,
    "30days": 250,
    "forever": 350,
}


def parse_star_payload(payload: str) -> tuple | None:
    """
    Парсит payload из Star-инвойса.
    Формат: "sub_{plan_key}_{user_id}"
    Возвращает: (plan_key, user_id) или None
    """
    try:
        parts = payload.split("_")
        if len(parts) == 3 and parts[0] == "sub":
            plan_key = parts[1]
            user_id  = int(parts[2])
            return plan_key, user_id
    except (ValueError, IndexError):
        pass
    return None


def make_star_payload(plan_key: str, user_id: int) -> str:
    """Создаёт payload для Star-инвойса."""
    return f"sub_{plan_key}_{user_id}"


def get_stars_price(plan_key: str) -> int | None:
    """Возвращает цену в звёздах для плана."""
    return STAR_PRICES.get(plan_key)


# ═══════════════════════════════════════════════════════════════════════════
# 🔶 2. CryptoBot (@send)
# ═══════════════════════════════════════════════════════════════════════════

class CryptoBotAPI:
    """
    Интеграция с CryptoBot (@send) Pay API.
    Документация: https://help.crypt.bot/crypto-pay-api

    Использование:
        api = CryptoBotAPI()
        invoice = api.create_invoice(amount="6.00", payload="sub_30days_12345")
        print(invoice["result"]["pay_url"])
    """

    def __init__(self, token: str = None):
        self.token    = token or CRYPTOBOT_TOKEN
        self.base_url = CRYPTOBOT_API

    # ── API Methods ──────────────────────────────────────────────────────

    def _request(self, method: str, data: dict = None) -> dict:
        """Базовый запрос к CryptoBot API (поддерживает GET и POST)."""
        if not self.token:
            return {"ok": False, "error": "CryptoBot token not configured"}

        url     = f"{self.base_url}/{method}"
        headers = {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }
        body = json.dumps(data).encode() if data else None
        # GET если нет тела, иначе POST
        http_method = "GET" if not data else "POST"

        try:
            req = Request(url, data=body, headers=headers, method=http_method)
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            # Пробуем прочитать тело ответа — там может быть детальная ошибка
            error_body = ""
            try:
                error_body = e.read().decode()[:500]
            except:
                pass
            msg = f"HTTP {e.code}: {e.reason}"
            if error_body:
                msg += f" | {error_body}"
            logger.error(f"🌐 CryptoBot API error ({method}): {msg}")
            return {"ok": False, "error": msg, "code": e.code}
        except URLError as e:
            logger.error(f"🌐 CryptoBot API error ({method}): {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.error(f"🌐 CryptoBot API error ({method}): {e}")
            return {"ok": False, "error": str(e)}

    def get_me(self) -> dict:
        """Проверка токена / получение инфо о приложении."""
        return self._request("getMe")

    def get_balance(self) -> dict:
        """Баланс кошелька."""
        return self._request("getBalance")

    def create_invoice(
        self,
        amount: str,
        payload: str = "",
        description: str = "",
        asset: str = None,
        expires_in: int = 7200,
    ) -> dict:
        """
        Создаёт счёт на оплату.

        Параметры:
            amount:      сумма (строка, напр. "6.00")
            payload:     ваш идентификатор (вернётся в webhook)
            description: описание платежа
            asset:       актив (USDT, TON, BTC, ETH и т.д.)
            expires_in:  время жизни счёта в секундах

        Возвращает:
            {"ok": True, "result": {"invoice_id": ..., "pay_url": ..., ...}}
        """
        data = {
            "asset":       asset or CRYPTOBOT_ASSET,
            "amount":      amount,
            "description": description,
            "payload":     payload,
            "expires_in":  expires_in,
        }
        result = self._request("createInvoice", data)
        if result.get("ok"):
            logger.info(
                f"💰 CryptoBot invoice created: {result['result']['invoice_id']} "
                f"({amount} {asset or CRYPTOBOT_ASSET})"
            )
        return result

    def get_invoices(
        self,
        invoice_ids: list[int] = None,
        status: str = "active",
        offset: int = 0,
        count: int = 100,
    ) -> dict:
        """Получение списка счетов."""
        data = {
            "status": status,
            "offset": offset,
            "count":  count,
        }
        if invoice_ids:
            data["invoice_ids"] = ",".join(str(i) for i in invoice_ids)
        return self._request("getInvoices", data)

    def transfer(
        self,
        user_id: int,
        asset: str,
        amount: str,
        spend_id: str = None,
    ) -> dict:
        """Перевод средств пользователю CryptoBot."""
        data = {
            "user_id": user_id,
            "asset":   asset,
            "amount":  amount,
            "spend_id": spend_id or f"transfer_{int(time.time())}_{user_id}",
        }
        return self._request("transfer", data)

    # ── Webhook ──────────────────────────────────────────────────────────

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        """
        Верифицирует подпись webhook'а от CryptoBot.

        CryptoBot подписывает тело запроса HMAC-SHA256,
        используя SHA256(API-токен) как ключ.
        Подпись в заголовке 'crypto-pay-api-signature'.

        Документация:
        https://help.send.tg/en/articles/10279948-crypto-pay-api#verifying-webhook-updates
        """
        if not self.token or not signature:
            return False
        # ⚠️ Важно: секретный ключ = SHA256(API-токен), а не сам токен!
        secret = hashlib.sha256(self.token.encode("utf-8")).digest()
        expected = hmac.new(
            secret,
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ── Currency info ────────────────────────────────────────────────────

    @staticmethod
    def get_supported_assets() -> list:
        """Список поддерживаемых активов для отображения."""
        return [
            {"id": "USDT", "name": "Tether",   "icon": "💵", "stable": True},
            {"id": "TON",  "name": "Toncoin",  "icon": "💎", "stable": False},
            {"id": "BTC",  "name": "Bitcoin",  "icon": "₿",  "stable": False},
            {"id": "ETH",  "name": "Ethereum", "icon": "⟠",  "stable": False},
        ]


# ═══════════════════════════════════════════════════════════════════════════
# 🗃️ Pending Payments (крипто-платежи, ожидающие подтверждения)
# ═══════════════════════════════════════════════════════════════════════════

_PENDING_FILE = None


def _get_pending_path() -> str:
    """Путь к файлу с ожидающими крипто-платежами."""
    global _PENDING_FILE
    if _PENDING_FILE is None:
        data_dir = os.environ.get("DATA_DIR", "/app/data")
        _PENDING_FILE = os.path.join(data_dir, "cache", "pending-crypto.json")
    return _PENDING_FILE


def save_pending_crypto(invoice_id: int, user_id: int, plan_key: str) -> None:
    """
    Сохраняет информацию об ожидающем крипто-платеже.
    После оплаты webhook найдёт这笔 запись и активирует подписку.
    """
    path = _get_pending_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        pending = {}
        if os.path.exists(path):
            with open(path, "r") as f:
                pending = json.load(f)

        pending[str(invoice_id)] = {
            "user_id":    user_id,
            "plan":       plan_key,
            "created_at": int(time.time()),
            "status":     "pending",
        }
        with open(path, "w") as f:
            json.dump(pending, f, indent=2)
        logger.info(f"📝 Pending crypto payment saved: invoice={invoice_id}, user={user_id}, plan={plan_key}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to save pending crypto: {e}")


def get_pending_crypto(invoice_id: int) -> dict | None:
    """Получает информацию об ожидающем платеже по ID инвойса."""
    path = _get_pending_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            pending = json.load(f)
        return pending.get(str(invoice_id))
    except Exception as e:
        logger.warning(f"⚠️ Failed to read pending crypto: {e}")
        return None


def remove_pending_crypto(invoice_id: int) -> None:
    """Удаляет запись об ожидающем платеже (после успешной активации)."""
    path = _get_pending_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, "r") as f:
            pending = json.load(f)
        pending.pop(str(invoice_id), None)
        with open(path, "w") as f:
            json.dump(pending, f, indent=2)
    except Exception as e:
        logger.warning(f"⚠️ Failed to remove pending crypto: {e}")


def get_all_pending_crypto() -> dict:
    """Возвращает все ожидающие крипто-платежи."""
    path = _get_pending_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"⚠️ Failed to load pending crypto: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# 🔄 Активация подписки (через БД)
# ═══════════════════════════════════════════════════════════════════════════

def activate_subscription(user_id: int, plan_key: str) -> bool:
    """
    Активирует подписку пользователю.
    Вызывается после успешной оплаты (Stars или Crypto).
    """
    try:
        from db import activate_sub, ensure_user, PLANS

        ensure_user(user_id)
        activate_sub(user_id, plan_key, admin_id=0)  # 0 = авто-оплата

        plan_label = PLANS.get(plan_key, {}).get("label", plan_key)
        logger.info(f"✅ Подписка активирована: user={user_id}, plan={plan_key} ({plan_label})")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка активации подписки: {e}")
        return False


def notify_user_telegram(user_id: int, plan_label: str, payment_method: str):
    """
    Отправляет пользователю уведомление об успешной оплате
    через Telegram Bot API (используется из webhook, где нет объекта bot).
    """
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN not set, cannot notify user")
        return

    emoji = "⭐️" if "Star" in payment_method else "💎"
    text = (
        f"✅ <b>Оплата получена!</b>\n\n"
        f"{emoji} <b>Тариф:</b> {plan_label}\n"
        f"💳 <b>Способ:</b> {payment_method}\n\n"
        f"👇 Нажми кнопку чтобы начать пользоваться:"
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": user_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "📋 Моя подписка", "callback_data": "my_sub"}
            ]]
        },
        "disable_web_page_preview": True,
    }).encode()

    try:
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                logger.info(f"📬 Уведомление отправлено user={user_id}")
            else:
                logger.warning(f"⚠️ Ошибка отправки уведомления: {result}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось уведомить пользователя {user_id}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 📋 Вспомогательные функции для UI
# ═══════════════════════════════════════════════════════════════════════════

def format_pricing_text() -> str:
    """Красивый прайс-лист со звёздами и криптой."""
    from db import PLANS

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "💎 <b>Тарифы Desacratio VPN</b>\n",
    ]

    for key, plan in PLANS.items():
        stars = STAR_PRICES.get(key, 0)
        lines.append(f"▫️ <b>{plan['label']}</b>")
        lines.append(f"   ⭐️ {stars} звёзд  |  💵 ${plan['price_usd']:.2f}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("👇 <b>Выбери способ оплаты:</b>")
    return "\n".join(lines)


def format_asset_icon(asset: str) -> str:
    """Иконка для крипто-актива."""
    icons = {"USDT": "💵", "TON": "💎", "BTC": "₿", "ETH": "⟠"}
    return icons.get(asset, "💰")


# ═══════════════════════════════════════════════════════════════════════════
# 🧪 Self-test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("╔══════════════════════════════════════╗")
    print("║  💰 Desacratio Payments — Self-Test  ║")
    print("╚══════════════════════════════════════╝")
    print()

    # Проверка Stars
    print("🔷 Telegram Stars:")
    test_payload = make_star_payload("30days", 12345)
    print(f"  Payload: {test_payload}")
    parsed = parse_star_payload(test_payload)
    print(f"  Parsed:  {parsed}")
    assert parsed == ("30days", 12345), "Star payload parse failed!"
    print("  ✅ OK")
    print()

    # Проверка CryptoBot
    print("🔶 CryptoBot:")
    if CRYPTOBOT_ENABLED:
        api = CryptoBotAPI()
        me = api.get_me()
        if me.get("ok"):
            app_name = me["result"].get("name", "?")
            print(f"  ✅ Подключено: {app_name}")
            balance = api.get_balance()
            if balance.get("ok"):
                for item in balance.get("result", []):
                    print(f"     {item['asset']}: {item['available']}")
        else:
            print(f"  ❌ Ошибка: {me.get('error', 'неизвестная')}")
    else:
        print("  ⏸️  Отключено (нет CRYPTOBOT_TOKEN)")
    print()

    # Проверка pending
    print("🗃️ Pending payments:")
    save_pending_crypto(999999, 12345, "30days")
    saved = get_pending_crypto(999999)
    print(f"  Saved: {saved}")
    assert saved and saved["plan"] == "30days", "Pending save/get failed!"
    remove_pending_crypto(999999)
    assert get_pending_crypto(999999) is None, "Pending remove failed!"
    print("  ✅ OK")
    print()

    print("✅ Все тесты пройдены!")
