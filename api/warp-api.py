#!/usr/bin/env python3
"""
Desacratio VPN — Config Generation API
========================================
Генерирует WireGuard конфиги через собственные серверы.
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
import logging
import subprocess
from functools import wraps

# ─── DB ───────────────────────────────────────────────────────────────────
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from db import has_active_sub, get_sub_info, ensure_user, init_db
except ImportError:
    # fallback
    def has_active_sub(*a): return True
    def get_sub_info(*a): return {"status": "ok"}
    def ensure_user(*a): pass
    def init_db(): pass

# ─── Flask ───────────────────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, request, Response
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "flask", "--break-system-packages"]
    )
    from flask import Flask, jsonify, request, Response

# ─── Конфигурация ────────────────────────────────────────────────────────
HOST          = os.environ.get("API_HOST", "0.0.0.0")
PORT          = int(os.environ.get("PORT", os.environ.get("API_PORT", "8443")))

# Render.com URL для self-reference
RENDER_URL    = os.environ.get("RENDER_URL", os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")
WARP_REG_BIN  = os.environ.get("WARP_REG_BIN", os.path.join(os.path.dirname(__file__), "warp-reg"))  # не используется
RATE_LIMIT    = int(os.environ.get("RATE_LIMIT", "20"))
CACHE_TTL     = int(os.environ.get("CACHE_TTL", "86400"))
SERVERS_CNT   = int(os.environ.get("SERVERS_COUNT", "5"))
DATA_DIR      = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

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

# Rate limit storage
request_log: dict = {}

# ─── Сервера (5 стран, разные endpoint'ы) ────────────────────────────────
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

# ─── Proxy Scraper ─────────────────────────────────────────────────────────
try:
    from api.proxy_scraper import get_proxies, init_proxy_scraper
except ImportError:
    try:
        from proxy_scraper import get_proxies, init_proxy_scraper
    except ImportError:
        # fallback — no-op
        def get_proxies(count=5):
            return [{"flag": "🌍", "name": "Error", "type": "http",
                     "server": "127.0.0.1", "port": 8080, "country": "XX"}]
        def init_proxy_scraper(): pass


# ─── Получение прокси-конфигов ─────────────────────────────────────────────
def get_proxy_configs(user_id: str) -> list:
    """
    Возвращает прокси для пользователя (по одному из каждой страны).
    """
    proxies = get_proxies(count=SERVERS_CNT)
    if not proxies:
        raise RuntimeError("💀 Нет доступных прокси!")
    logger.info(f"🌐 Прокси для {user_id}: {len(proxies)} шт")
    return proxies


# ─── Форматтеры подписок (прокси) ──────────────────────────────────────────

def format_singbox(proxy_configs: list, user_id: str) -> dict:
    """
    Формат Sing-box JSON с HTTP/SOCKS5 outbound'ами.
    """
    outbounds = []
    for i, cfg in enumerate(proxy_configs):
        tag = f"{cfg['flag']} {cfg['name']}"
        outbound_type = "http" if cfg["type"] in ("http", "https") else "socks"

        outbounds.append({
            "type": outbound_type,
            "tag": tag,
            "server": cfg["server"],
            "server_port": int(cfg["port"]),
        })

    return {
        "outbounds": outbounds,
    }


def format_clash(proxy_configs: list, user_id: str) -> str:
    """
    Формат Clash Meta YAML с HTTP/SOCKS5 прокси.
    """
    lines = []
    lines.append("port: 7890")
    lines.append("socks-port: 7891")
    lines.append("allow-lan: false")
    lines.append("mode: Rule")
    lines.append("log-level: warning")
    lines.append("")
    lines.append("proxies:")
    proxy_names = []
    for cfg in proxy_configs:
        name = f"{cfg['flag']} {cfg['name']}"
        proxy_names.append(name)
        proxy_type = "http" if cfg["type"] in ("http", "https") else "socks5"
        lines.append(f"  - name: \"{name}\"")
        lines.append(f"    type: {proxy_type}")
        lines.append(f"    server: {cfg['server']}")
        lines.append(f"    port: {cfg['port']}")
        lines.append("")
    lines.append("proxy-groups:")
    lines.append("  - name: Proxy")
    lines.append("    type: select")
    lines.append("    proxies:")
    lines.append("      - \"🔀 Авто\"")
    for n in proxy_names:
        lines.append(f"      - \"{n}\"")
    lines.append("")
    lines.append("  - name: \"🔀 Авто\"")
    lines.append("    type: url-test")
    lines.append("    proxies:")
    for n in proxy_names:
        lines.append(f"      - \"{n}\"")
    lines.append("    url: http://www.gstatic.com/generate_204")
    lines.append("    interval: 300")
    lines.append("")
    lines.append("rules:")
    lines.append("  - MATCH,Proxy")
    lines.append("")
    return "\n".join(lines)


def format_wg_conf_all(proxy_configs: list, user_id: str) -> str:
    """
    Справка: WireGuard .conf больше не генерируется (WARP недоступен).
    """
    return (
        f"# {BRAND}\n"
        f"# ВНИМАНИЕ: WireGuard/WARP больше не используется.\n"
        f"# Используй Sing-box или Clash подписку для Happ.\n"
        f"# User ID: {user_id}\n"
    )


# ─── Subscription Check ──────────────────────────────────────────────────

def require_subscription(f):
    """Декоратор: проверяет активную подписку перед генерацией конфигов."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = kwargs.get("user_id")
        if user_id:
            try:
                user_id = int(user_id)
            except ValueError:
                pass
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
        "version":     "2.0",
        "author":      AUTHOR,
        "channel":     CHANNEL,
        "support":     SUPPORT,
        "purchase":    PURCHASE,
        "description": "Премиум VPN с собственными серверами в 5 странах.",
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
    """Sing-box подписка (прокси, JSON)."""
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

        logger.info(f"📤 Sing-box subscription for {user_id}: {len(configs)} proxies")
        return resp
    except Exception as e:
        logger.error(f"Subscription error for {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>/clash")
@rate_limit
@require_subscription
def get_clash_subscription(user_id: str):
    """Clash-подписка YAML для Happ/v2rayTun."""
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

        logger.info(f"📤 Clash YAML subscription for {user_id}: {len(configs)} proxies")
        return resp
    except Exception as e:
        logger.error(f"Clash error for {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>/conf")
@rate_limit
@require_subscription
def get_wg_conf(user_id: str):
    """Информация: WireGuard .conf больше не поддерживается."""
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
    """Возвращает список прокси пользователя."""
    try:
        configs = get_proxy_configs(user_id)
        servers = []
        for i, cfg in enumerate(configs):
            servers.append({
                "id":       cfg.get("country", f"srv{i}"),
                "name":     cfg["name"],
                "flag":     cfg["flag"],
                "type":     cfg["type"],
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
    """Принудительно обновить список прокси."""
    try:
        # Принудительно обновляем кеш прокси
        from api.proxy_scraper import refresh_cache
        refresh_cache(force=True)
        configs = get_proxy_configs(user_id)
        return jsonify({
            "success": True,
            "message": f"🆕 Обновлено {len(configs)} прокси",
            "servers": len(configs),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Запуск ──────────────────────────────────────────────────────────────
app_start_time = time.time()

if __name__ == "__main__":
    # Инициализируем БД
    init_db()
    # Инициализируем сборщик прокси
    init_proxy_scraper()

    logger.info(f"╔══════════════════════════════════════════╗")
    logger.info(f"║  {BRAND} API Server v3.0")
    logger.info(f"║  Author: {AUTHOR}")
    logger.info(f"║  Channel: {CHANNEL}")
    logger.info(f"║  Support: {SUPPORT}")
    logger.info(f"╚══════════════════════════════════════════╝")
    logger.info(f"  Host: {HOST}:{PORT}")
    logger.info(f"  Render URL: {RENDER_URL or '(не задан)'}")
    logger.info(f"  Прокси на пользователя: {SERVERS_CNT}")
    logger.info(f"  Rate limit: {RATE_LIMIT} req/min")
    logger.info(f"  Data dir: {DATA_DIR}")

    app.run(host=HOST, port=PORT, debug=False)
