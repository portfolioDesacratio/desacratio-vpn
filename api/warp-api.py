#!/usr/bin/env python3
"""
Desacratio VPN — Config Generation API (WARP + Relay)
=======================================================
Генерирует WireGuard (WARP) конфиги через Cloudflare WARP.
5 стран, уникальные ключи для каждого пользователя.

Подписка совместима с: Hiddify, v2rayTun, Sing-box, Clash, Happ, Streisand, Nekoray

Запуск:
  python3 warp-api.py

API Endpoints:
  GET   /health                    — проверка
  GET   /api/sub/USER_ID           — Sing-box подписка (JSON)
  GET   /api/sub/USER_ID/clash     — Clash подписка (JSON)
  GET   /api/sub/USER_ID/conf      — WireGuard .conf
  POST  /api/sub/USER_ID/refresh   — перегенерировать ключи
  GET   /api/sub/USER_ID/servers   — список серверов
  GET   /api/sub/USER_ID/status    — статус подписки
"""

import os
import sys
import json
import time
import random
import hashlib
import logging
import urllib.request
from functools import wraps

# ─── DB ───────────────────────────────────────────────────────────────────
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from db import has_active_sub, get_sub_info, ensure_user, init_db
except ImportError:
    def has_active_sub(*a): return True
    def get_sub_info(*a): return {"status": "ok"}
    def ensure_user(*a): pass
    def init_db(): pass

# ─── CryptoBot Payment Webhook ──────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # /app/
try:
    from payments import (
        CryptoBotAPI, get_pending_crypto, remove_pending_crypto,
        activate_subscription, notify_user_telegram,
        CRYPTOBOT_ENABLED, CRYPTOBOT_ASSET,
    )
except ImportError:
    # Если модуль не загрузился — webhook просто вернёт ошибку
    CryptoBotAPI = None
    def get_pending_crypto(*a): return None
    def remove_pending_crypto(*a): pass
    def activate_subscription(*a): return False
    def notify_user_telegram(*a): pass
    CRYPTOBOT_ENABLED = False
    CRYPTOBOT_ASSET = "USDT"

# ─── Flask ───────────────────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, request, Response
except ImportError:
    import subprocess
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "flask", "--break-system-packages"]
    )
    from flask import Flask, jsonify, request, Response

# ─── Конфигурация ────────────────────────────────────────────────────────
HOST          = os.environ.get("API_HOST", "0.0.0.0")
PORT          = int(os.environ.get("PORT", os.environ.get("API_PORT", "8443")))

RENDER_URL    = os.environ.get("RENDER_URL", os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")
RATE_LIMIT    = int(os.environ.get("RATE_LIMIT", "20"))
CACHE_TTL     = int(os.environ.get("CACHE_TTL", "86400"))
SERVERS_CNT   = int(os.environ.get("SERVERS_COUNT", "5"))
DATA_DIR      = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
ADMIN_ID      = int(os.environ.get("ADMIN_ID", "8587090554"))

# Branding
BRAND      = "Desacratio VPN"
BRAND_LOGO = "🛡️"
CHANNEL    = "@ExtractionOfThoughts"
SUPPORT    = "@DesacratioVPNSupportBot"
AUTHOR     = "@desacratio"
PURCHASE   = "@desacratio"

# ─── Логирование ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("desacratio-api")

app = Flask(__name__)
request_log: dict = {}

# ─── WARP Config Generator ───────────────────────────────────────────────

try:
    from api.warp_reg import register_warp, make_wireguard_config
except ImportError:
    from warp_reg import register_warp, make_wireguard_config

# ─── Сервера (5 стран, разные WARP endpoint'ы) ────────────────────────────
SERVERS = [
    {"id": "pl", "name": "Poland",     "flag": "🇵🇱", "emoji": "🌍",
     "endpoint": "162.159.193.3:2408",  "color": "#3B82F6"},
    {"id": "de", "name": "Germany",    "flag": "🇩🇪", "emoji": "🌐",
     "endpoint": "162.159.193.5:2408",  "color": "#F59E0B"},
    {"id": "nl", "name": "Netherlands","flag": "🇳🇱", "emoji": "🌷",
     "endpoint": "162.159.193.7:2408",  "color": "#10B981"},
    {"id": "gb", "name": "UK",         "flag": "🇬🇧", "emoji": "🇬🇧",
     "endpoint": "162.159.193.9:2408",  "color": "#EF4444"},
    {"id": "us", "name": "USA",        "flag": "🇺🇸", "emoji": "🗽",
     "endpoint": "engage.cloudflareclient.com:2408", "color": "#8B5CF6"},
]


# ─── Кеш WARP конфигов ────────────────────────────────────────────────────
# Храним сгенерированные конфиги в памяти (переживают рестарт воркера).
# Ключ: user_id, значение: {config, generated_at}

_warp_cache: dict = {}
_WARP_TTL = 21600  # 6 часов (WARP сессия живёт ~24ч, обновляем с запасом)


def _get_warp_config(user_id: str) -> dict:
    """
    Возвращает WARP конфиг для пользователя (из кеша или новый).
    Один конфиг на пользователя — сервера отличаются только endpoint'ом.
    """
    now = time.time()
    cached = _warp_cache.get(user_id)

    if cached and (now - cached["generated_at"]) < _WARP_TTL:
        logger.debug(f"WARP cache HIT for {user_id}")
        return cached["config"]

    logger.info(f"🆕 Регистрация WARP для {user_id}...")
    config = register_warp()
    _warp_cache[user_id] = {
        "config": config,
        "generated_at": now,
    }
    logger.info(f"  ✅ WARP registered: device={config['device_id'][:8]}...")
    return config


def get_proxy_configs(user_id: str) -> list:
    """
    Возвращает WARP-WireGuard конфиги для пользователя.
    Каждый сервер — тот же WARP ключ, но с разным WARP endpoint'ом.
    """
    warp = _get_warp_config(user_id)

    configs = []
    for srv in SERVERS:
        configs.append({
            "flag":       srv["flag"],
            "name":       srv["name"],
            "type":       "wireguard",
            "country":    srv["id"].upper(),
            "server":     srv["endpoint"].rsplit(":", 1)[0],
            "port":       int(srv["endpoint"].rsplit(":", 1)[1]),
            "local_address": [
                f"{warp['v4']}/32",
                f"{warp['v6']}/128",
            ],
            "private_key":      warp["private_key"],
            "peer_public_key":  warp["peer_public_key"],
            "reserved":         warp["reserved"],
            "mtu": 1280,
        })

    return configs


# ─── Форматтеры подписок (WARP / WireGuard) ────────────────────────────────

def format_singbox(proxy_configs: list, user_id: str) -> dict:
    """Формат Sing-box JSON с WireGuard outbound'ами."""
    outbounds = []
    for cfg in proxy_configs:
        outbounds.append({
            "type": "wireguard",
            "tag": f"{cfg['flag']} {cfg['name']}",
            "server": cfg["server"],
            "server_port": cfg["port"],
            "local_address": cfg["local_address"],
            "private_key": cfg["private_key"],
            "peer_public_key": cfg["peer_public_key"],
            "reserved": cfg["reserved"],
            "mtu": cfg.get("mtu", 1280),
        })

    return {"outbounds": outbounds}


def format_clash(proxy_configs: list, user_id: str) -> str:
    """Формат Clash Meta YAML с WireGuard прокси."""
    lines = [
        "port: 7890",
        "socks-port: 7891",
        "allow-lan: false",
        "mode: Rule",
        "log-level: warning",
        "",
        "proxies:",
    ]
    proxy_names = []
    for cfg in proxy_configs:
        name = f"{cfg['flag']} {cfg['name']}"
        proxy_names.append(name)
        lines.append(f'  - name: "{name}"')
        lines.append("    type: wireguard")
        lines.append(f"    server: {cfg['server']}")
        lines.append(f"    port: {cfg['port']}")
        lines.append(f"    ip: {cfg['local_address'][0].split('/')[0]}")
        lines.append(f"    ipv6: {cfg['local_address'][1].split('/')[0]}")
        lines.append(f"    private-key: {cfg['private_key']}")
        lines.append(f"    public-key: {cfg['peer_public_key']}")
        reserved_str = ", ".join(str(r) for r in cfg["reserved"])
        lines.append(f"    reserved: [{reserved_str}]")
        lines.append("    udp: true")
        lines.append("    mtu: 1280")
        lines.append("")

    lines.append("proxy-groups:")
    lines.append("  - name: Proxy")
    lines.append("    type: select")
    lines.append("    proxies:")
    lines.append('      - "🔀 Авто"')
    for n in proxy_names:
        lines.append(f'      - "{n}"')
    lines.append("")
    lines.append('  - name: "🔀 Авто"')
    lines.append("    type: url-test")
    lines.append("    proxies:")
    for n in proxy_names:
        lines.append(f'      - "{n}"')
    lines.append("    url: http://www.gstatic.com/generate_204")
    lines.append("    interval: 300")
    lines.append("")
    lines.append("rules:")
    lines.append("  - MATCH,Proxy")
    lines.append("")
    return "\n".join(lines)


def format_wg_conf_all(proxy_configs: list, user_id: str) -> str:
    """Генерирует WireGuard .conf со всеми 5 серверами."""
    parts = [
        f"# ============================================",
        f"# {BRAND} — WireGuard Config",
        f"# User: {user_id}",
        f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Servers: {len(proxy_configs)}",
        f"# ============================================",
        f"#",
        f"# Channel: {CHANNEL}",
        f"#",
    ]

    # Берём первый конфиг для Interface (все используют один ключ)
    first = proxy_configs[0]
    parts.append("[Interface]")
    parts.append(f"PrivateKey = {first['private_key']}")
    parts.append(f"Address = {first['local_address'][0]}")
    parts.append(f"Address = {first['local_address'][1]}")
    parts.append("DNS = 1.1.1.1, 1.0.0.1")
    parts.append("MTU = 1280")
    parts.append("")
    parts.append("# ─── Peer'ы (выбери один, раскомментировав) ────")

    for i, cfg in enumerate(proxy_configs):
        parts.append(f"# ========== {cfg['flag']} {cfg['name']} ==========")
        parts.append(f"# [Peer]")
        parts.append(f"# PublicKey = {cfg['peer_public_key']}")
        parts.append(f"# AllowedIPs = 0.0.0.0/0")
        parts.append(f"# AllowedIPs = ::/0")
        parts.append(f"# Endpoint = {cfg['server']}:{cfg['port']}")
        parts.append(f"# PersistentKeepalive = 25")
        parts.append("")

    return "\n".join(parts)


# ─── Subscription Check ──────────────────────────────────────────────────

def require_subscription(f):
    """Декоратор: проверяет активную подписку перед генерацией конфигов."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = kwargs.get("user_id")
        if user_id:
            try:
                original_id = user_id
                user_id = int(user_id)
            except ValueError:
                pass

            # Администратор имеет доступ без подписки
            if user_id == ADMIN_ID:
                logger.info(f"🔑 Админский доступ для {user_id} (ADMIN_ID={ADMIN_ID})")
                return f(*args, **kwargs)
            else:
                logger.debug(f"Пользователь {user_id} != ADMIN_ID {ADMIN_ID}")

            if not has_active_sub(user_id):
                sub_info = get_sub_info(user_id)
                return jsonify({
                    "error": "subscription_required",
                    "message": "Нет активной подписки. Купи подписку у @desacratio",
                    "sub_info": sub_info,
                    "pricing": {
                        "1day": {"label": "1 день", "price": 0.50},
                        "3days": {"label": "3 дня", "price": 1.00},
                        "7days": {"label": "7 дней", "price": 2.50},
                        "14days": {"label": "14 дней", "price": 4.00},
                        "30days": {"label": "30 дней", "price": 6.00},
                        "forever": {"label": "Навсегда", "price": 7.50},
                    },
                    "contact": PURCHASE,
                }), 402
        return f(*args, **kwargs)
    return decorated


# ─── Rate Limiter ────────────────────────────────────────────────────────

def rate_limit(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.remote_addr or "unknown"
        now = time.time()
        if ip not in request_log:
            request_log[ip] = []
        request_log[ip] = [t for t in request_log[ip] if now - t < 60]
        if len(request_log[ip]) >= RATE_LIMIT:
            return jsonify({"error": "Rate limit exceeded"}), 429
        request_log[ip].append(now)
        return f(*args, **kwargs)
    return decorated


# ─── API Routes ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({
        "name":        BRAND,
        "logo":        BRAND_LOGO,
        "version":     "3.0",
        "author":      AUTHOR,
        "channel":     CHANNEL,
        "support":     SUPPORT,
        "purchase":    PURCHASE,
        "description": "Премиум VPN на базе Cloudflare WARP (WireGuard).",
        "endpoints": {
            "health":               "/health",
            "subscription":         "/api/sub/<USER_ID> (Sing-box)",
            "subscription_clash":   "/api/sub/<USER_ID>/clash",
            "wireguard_conf":       "/api/sub/<USER_ID>/conf",
            "refresh":              "/api/sub/<USER_ID>/refresh (POST)",
            "servers_list":         "/api/sub/<USER_ID>/servers",
            "sub_status":           "/api/sub/<USER_ID>/status",
        },
        "servers": len(SERVERS),
        "protocol": "WireGuard (WARP)",
        "sponsor": f"Подпишись: {CHANNEL}",
    })


@app.route("/health")
def health():
    return jsonify({
        "status":   "ok",
        "brand":    BRAND,
        "servers":  SERVERS_CNT,
        "uptime":   time.time() - app_start_time,
        "version":  "3.0",
        "admin_id": ADMIN_ID,
    })


@app.route("/api/sub/<user_id>/status")
@rate_limit
def get_sub_status(user_id: str):
    """Информация о подписке пользователя."""
    try:
        uid = int(user_id) if user_id.isdigit() else user_id
        info = get_sub_info(uid)
        ensure_user(uid)
        return jsonify({
            "user_id": uid,
            "has_active_sub": has_active_sub(uid),
            "subscription": info,
            "pricing": {
                "1day": {"label": "1 день", "price_usd": 0.50, "stars": 25},
                "3days": {"label": "3 дня", "price_usd": 1.00, "stars": 75},
                "7days": {"label": "7 дней", "price_usd": 2.50, "stars": 125},
                "14days": {"label": "14 дней", "price_usd": 4.00, "stars": 175},
                "30days": {"label": "30 дней", "price_usd": 6.00, "stars": 250},
                "forever": {"label": "Навсегда", "price_usd": 7.50, "stars": 350},
            },
            "contact": PURCHASE,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>")
@rate_limit
@require_subscription
def get_subscription(user_id: str):
    """Sing-box подписка (WireGuard WARP, JSON)."""
    try:
        configs = get_proxy_configs(user_id)
        sub = format_singbox(configs, user_id)

        resp = app.response_class(
            response=json.dumps(sub, indent=2, ensure_ascii=False),
            status=200,
            mimetype="text/plain",
        )
        resp.headers["Subscription-Userinfo"] = "upload=0; download=0; total=1099511627776; expire=0"
        resp.headers["Profile-Title"] = BRAND
        resp.headers["Profile-Update-Interval"] = "24"
        resp.headers["Content-Disposition"] = "attachment; filename=\"desacratio-singbox.json\""
        resp.headers["Access-Control-Allow-Origin"] = "*"

        logger.info(f"📤 Sing-box WARP для {user_id}: {len(configs)} серверов")
        return resp
    except Exception as e:
        logger.error(f"Subscription error for {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>/clash")
@rate_limit
@require_subscription
def get_clash_subscription(user_id: str):
    """Clash-подписка YAML с WireGuard WARP."""
    try:
        configs = get_proxy_configs(user_id)
        yaml_str = format_clash(configs, user_id)

        resp = app.response_class(
            response=yaml_str,
            status=200,
            mimetype="text/plain",
        )
        resp.headers["Subscription-Userinfo"] = "upload=0; download=0; total=1099511627776; expire=0"
        resp.headers["Profile-Title"] = BRAND
        resp.headers["Profile-Update-Interval"] = "24"
        resp.headers["Content-Disposition"] = "attachment; filename=\"desacratio-clash.yaml\""
        resp.headers["Access-Control-Allow-Origin"] = "*"

        logger.info(f"📤 Clash WARP для {user_id}: {len(configs)} серверов")
        return resp
    except Exception as e:
        logger.error(f"Clash error for {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>/conf")
@rate_limit
@require_subscription
def get_wg_conf(user_id: str):
    """WireGuard .conf со всеми 5 серверами."""
    try:
        configs = get_proxy_configs(user_id)
        conf = format_wg_conf_all(configs, user_id)

        return Response(
            conf,
            mimetype="text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="desacratio-{user_id}.conf"',
                "Content-Type": "text/plain; charset=utf-8",
            }
        )
    except Exception as e:
        logger.error(f"WG conf error for {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>/servers")
@rate_limit
@require_subscription
def get_servers_list(user_id: str):
    """Возвращает список WARP серверов пользователя."""
    try:
        configs = get_proxy_configs(user_id)
        servers = []
        for i, cfg in enumerate(configs):
            servers.append({
                "id":       cfg.get("country", f"srv{i}"),
                "name":     cfg["name"],
                "flag":     cfg["flag"],
                "type":     "wireguard",
                "endpoint": f"{cfg['server']}:{cfg['port']}",
            })
        return jsonify({
            "user":    user_id,
            "count":   len(servers),
            "servers": servers,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>/refresh", methods=["POST"])
@rate_limit
@require_subscription
def refresh_subscription(user_id: str):
    """Принудительно перерегистрировать WARP (новый ключ)."""
    try:
        # Сбрасываем кеш — при следующем запросе будет новая регистрация
        if user_id in _warp_cache:
            del _warp_cache[user_id]
        configs = get_proxy_configs(user_id)
        return jsonify({
            "success": True,
            "message": f"🆕 WARP перерегистрирован: {len(configs)} серверов",
            "servers": len(configs),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>/reset", methods=["POST"])
@rate_limit
def reset_subscription_keys(user_id: str):
    """
    Сбрасывает WARP ключи пользователя (работает и без подписки).
    Используется ботом при истечении триала/подписки.
    """
    try:
        if user_id in _warp_cache:
            del _warp_cache[user_id]
        logger.info(f"🔄 WARP keys reset for {user_id}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── CryptoBot Webhook ───────────────────────────────────────────────────

@app.route("/cryptobot_webhook", methods=["POST"])
def cryptobot_webhook():
    """
    Webhook для CryptoBot (@send).
    CryptoBot присылает сюда уведомления об оплате счетов.
    Документация: https://help.crypt.bot/crypto-pay-api#webhook
    """
    if not CRYPTOBOT_ENABLED or CryptoBotAPI is None:
        return jsonify({"error": "CryptoBot not configured"}), 503

    # Получаем подпись из заголовка
    signature = request.headers.get("crypto-pay-api-signature", "")
    body = request.get_data()

    # Верифицируем подпись
    api = CryptoBotAPI()
    if not api.verify_webhook(body, signature):
        logger.warning("⚠️ CryptoBot webhook: invalid signature")
        return jsonify({"error": "invalid signature"}), 403

    # Парсим тело
    try:
        data = request.get_json(force=True)
    except Exception as e:
        logger.error(f"⚠️ CryptoBot webhook: invalid JSON: {e}")
        return jsonify({"error": "invalid json"}), 400

    # Проверяем что это уведомление об оплате
    update_type = data.get("update_type", "")
    if update_type != "invoice_paid":
        return jsonify({"ok": True})

    payload_data = data.get("payload", {})
    invoice_id = payload_data.get("invoice_id")

    if not invoice_id:
        logger.warning("⚠️ CryptoBot webhook: no invoice_id")
        return jsonify({"error": "no invoice_id"}), 400

    # Проверяем статус
    status = payload_data.get("status", "")
    if status != "paid":
        logger.info(f"ℹ️ CryptoBot webhook: invoice {invoice_id} status={status}")
        return jsonify({"ok": True})

    # Ищем ожидающий платёж
    pending = get_pending_crypto(invoice_id)
    if not pending:
        logger.warning(f"⚠️ CryptoBot webhook: invoice {invoice_id} not found in pending")
        return jsonify({"ok": True})  # Уже могли обработать

    user_id = pending["user_id"]
    plan_key = pending["plan"]

    # Активируем подписку
    success = activate_subscription(user_id, plan_key)

    if success:
        from db import PLANS
        plan_label = PLANS.get(plan_key, {}).get("label", plan_key)
        notify_user_telegram(user_id, plan_label, f"CryptoBot ({CRYPTOBOT_ASSET})")
        remove_pending_crypto(invoice_id)
        logger.info(
            f"✅ CryptoBot webhook: sub activated user={user_id} "
            f"plan={plan_key} invoice={invoice_id}"
        )
    else:
        logger.error(
            f"❌ CryptoBot webhook: activation failed user={user_id} "
            f"plan={plan_key} invoice={invoice_id}"
        )

    return jsonify({"ok": True})


# ─── Запуск ──────────────────────────────────────────────────────────────
app_start_time = time.time()

if __name__ == "__main__":
    init_db()

    logger.info(f"╔══════════════════════════════════════════╗")
    logger.info(f"║  {BRAND} API Server v3.0 (WARP)")
    logger.info(f"║  Author: {AUTHOR}")
    logger.info(f"║  Channel: {CHANNEL}")
    logger.info(f"║  Support: {SUPPORT}")
    logger.info(f"╚══════════════════════════════════════════╝")
    logger.info(f"  Host: {HOST}:{PORT}")
    logger.info(f"  Render URL: {RENDER_URL or '(не задан)'}")
    logger.info(f"  WARP серверов: {SERVERS_CNT}")
    logger.info(f"  Rate limit: {RATE_LIMIT} req/min")
    logger.info(f"  Data dir: {DATA_DIR}")

    app.run(host=HOST, port=PORT, debug=False)
