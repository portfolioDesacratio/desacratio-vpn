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
import string
import logging
import subprocess
import tempfile
from pathlib import Path
from functools import wraps
from urllib.parse import urlparse

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
WARP_REG_BIN  = os.environ.get("WARP_REG_BIN", os.path.join(os.path.dirname(__file__), "warp-reg"))
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

# Дополнительные endpoint'ы для ротации, если какой-то не работает
FALLBACK_ENDPOINTS = [
    "162.159.193.4:2408",
    "162.159.193.6:2408",
    "162.159.193.8:2408",
    "162.159.193.10:2408",
]

# ─── Генерация WARP конфигов ─────────────────────────────────────────────

def run_warp_reg(endpoint: str) -> dict:
    """
    Запускает warp-reg, парсит вывод, возвращает dict с ключами.
    """
    try:
        result = subprocess.run(
            [WARP_REG_BIN],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError(f"❌ warp-reg не найден: {WARP_REG_BIN}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("⏰ warp-reg превысил таймаут (30с)")

    if result.returncode != 0:
        raise RuntimeError(f"warp-reg error ({result.returncode}): {result.stderr}")

    config = {}
    for line in result.stdout.strip().split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            config[key.strip()] = val.strip()

    config["endpoint"] = endpoint
    return config


def make_wireguard_config(config: dict) -> str:
    """
    Форматирует конфиг warp-reg в стандартный .conf WireGuard.
    """
    private_key = config.get("private_key", "")
    peer_pub    = config.get("public_key", "")
    endpoint    = config.get("endpoint", "engage.cloudflareclient.com:2408")
    v4          = config.get("v4", "172.16.0.2")
    v6          = config.get("v6", "2606:4700:110:8a20::1")
    reserved_raw = config.get("reserved", "[0, 0, 0]")

    try:
        reserved = json.loads(reserved_raw.replace("'", '"'))
    except (json.JSONDecodeError, TypeError):
        reserved = [0, 0, 0]

    reserved_str = ", ".join(str(r) for r in reserved)

    return "\n".join([
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {v4}/32",
        f"Address = {v6}/128",
        "DNS = 1.1.1.1, 1.0.0.1",
        "MTU = 1280",
        "",
        "[Peer]",
        f"PublicKey = {peer_pub}",
        "AllowedIPs = 0.0.0.0/0",
        "AllowedIPs = ::/0",
        f"Endpoint = {endpoint}",
        "PersistentKeepalive = 25",
    ])


def generate_user_configs(user_id: str) -> list:
    """
    Генерирует SERVERS_CNT конфигов для пользователя.
    Каждый конфиг использует endpoint из SERVERS.
    Возвращает список dict'ов с полным конфигом.
    """
    # Перемешиваем сервера для распределения нагрузки
    servers = SERVERS.copy()
    random.shuffle(servers)

    configs = []
    errors = 0

    for i, srv in enumerate(servers):
        endpoint = srv["endpoint"]
        try:
            raw = run_warp_reg(endpoint)
            wg = make_wireguard_config(raw)

            configs.append({
                "id":        srv["id"],
                "name":      srv["name"],
                "flag":      srv["flag"],
                "emoji":     srv["emoji"],
                "color":     srv["color"],
                "endpoint":  endpoint,
                "private_key": raw.get("private_key", ""),
                "public_key":  raw.get("public_key", ""),
                "v4":          raw.get("v4", ""),
                "v6":          raw.get("v6", ""),
                "reserved":    raw.get("reserved", "[0, 0, 0]"),
                "device_id":   raw.get("device_id", ""),
                "wg_config":   wg,
            })
            logger.info(f"✅ [{i+1}/{len(servers)}] {srv['flag']} {srv['name']} @ {endpoint}")
        except Exception as e:
            logger.warning(f"❌ [{i+1}/{len(servers)}] {srv['flag']} {srv['name']}: {e}")
            errors += 1
            # Пробуем fallback endpoint для этого сервера
            for fb in FALLBACK_ENDPOINTS:
                try:
                    raw = run_warp_reg(fb)
                    wg = make_wireguard_config(raw)
                    configs.append({
                        "id":   srv["id"],
                        "name": srv["name"],
                        "flag": srv["flag"],
                        "emoji": srv["emoji"],
                        "color": srv["color"],
                        "endpoint": fb,
                        "private_key": raw.get("private_key", ""),
                        "public_key":  raw.get("public_key", ""),
                        "v4":          raw.get("v4", ""),
                        "v6":          raw.get("v6", ""),
                        "reserved":    raw.get("reserved", "[0, 0, 0]"),
                        "device_id":   raw.get("device_id", ""),
                        "wg_config":   wg,
                    })
                    logger.info(f"  ✅ Fallback {fb} works for {srv['name']}")
                    break
                except:
                    continue

        time.sleep(random.uniform(0.3, 0.8))

    if not configs:
        raise RuntimeError("💀 Не удалось сгенерировать ни одного конфига!")

    logger.info(f"🎯 Сгенерировано {len(configs)} конфигов для {user_id}")
    return configs


# ─── Кеширование ─────────────────────────────────────────────────────────

def get_cache_dir() -> Path:
    """Создаёт и возвращает директорию для кеша."""
    d = Path(DATA_DIR) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cached_configs(user_id: str) -> list:
    """
    Возвращает кешированные конфиги для user_id.
    Если кеш протух или отсутствует — генерирует новые.
    """
    cache_dir = get_cache_dir()
    cache_file = cache_dir / f"{user_id}.json"

    # Пробуем загрузить
    if cache_file.exists():
        try:
            with open(cache_file, "r") as f:
                data = json.load(f)
            if time.time() - data.get("cached_at", 0) < CACHE_TTL:
                configs = data.get("configs", [])
                if len(configs) >= 3:
                    logger.info(f"📦 Cache HIT for {user_id}: {len(configs)} configs")
                    return configs
        except Exception as e:
            logger.warning(f"Cache read failed for {user_id}: {e}")

    # Генерируем новые
    logger.info(f"🔄 Генерация новых конфигов для {user_id}...")
    configs = generate_user_configs(user_id)

    # Сохраняем
    try:
        cache_data = {
            "cached_at": time.time(),
            "user_id":   user_id,
            "configs":   configs,
        }
        with open(cache_file, "w") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 Кеш сохранён для {user_id} ({len(configs)} configs)")
    except Exception as e:
        logger.warning(f"Cache save failed: {e}")

    return configs


def clear_user_cache(user_id: str) -> bool:
    """Удаляет кеш пользователя. Возвращает True если файл был."""
    cache_file = get_cache_dir() / f"{user_id}.json"
    if cache_file.exists():
        cache_file.unlink()
        logger.info(f"🗑️  Кеш удалён для {user_id}")
        return True
    return False


# ─── Форматтеры подписок ─────────────────────────────────────────────────

def format_singbox(configs: list, user_id: str) -> dict:
    """
    Формат Sing-box JSON (Hiddify, Streisand, Nekoray).
    """
    outbounds = []
    for cfg in configs:
        server_host = cfg["endpoint"].rsplit(":", 1)[0]
        server_port = int(cfg["endpoint"].rsplit(":", 1)[1])
        try:
            reserved = json.loads(cfg.get("reserved", "[0,0,0]").replace("'", '"'))
        except:
            reserved = [0, 0, 0]

        outbounds.append({
            "type": "wireguard",
            "tag": f"{BRAND} {cfg['flag']} {cfg['name']}",
            "server": server_host,
            "server_port": server_port,
            "local_address": [f"{cfg['v4']}/32", f"{cfg['v6']}/128"],
            "private_key": cfg["private_key"],
            "peer_public_key": cfg["public_key"],
            "reserved": reserved,
            "mtu": 1280,
        })

    return {
        "version": 2,
        "outbounds": outbounds,
    }


def format_clash(configs: list, user_id: str) -> dict:
    """
    Формат Clash Meta (Happ, v2rayTun, Clash Meta).
    """
    proxies = []
    for cfg in configs:
        server_host = cfg["endpoint"].rsplit(":", 1)[0]
        server_port = int(cfg["endpoint"].rsplit(":", 1)[1])
        try:
            reserved = json.loads(cfg.get("reserved", "[0,0,0]").replace("'", '"'))
        except:
            reserved = [0, 0, 0]

        proxies.append({
            "name": f"{cfg['flag']} {cfg['name']}",
            "type": "wireguard",
            "server": server_host,
            "port": server_port,
            "ip": cfg["v4"],
            "ipv6": cfg["v6"],
            "private-key": cfg["private_key"],
            "public-key": cfg["public_key"],
            "reserved": reserved,
            "udp": True,
            "mtu": 1280,
        })

    return {"proxies": proxies}


def format_wg_conf_all(configs: list, user_id: str) -> str:
    """
    Один .conf файл со всеми серверами.
    Каждый сервер — отдельный блок [Interface]+[Peer].
    Совместимо с: WireGuard, Happ, Sing-box, Clash, OpenVPN (импорт .conf)
    """
    api_url = RENDER_URL or f"http://localhost:{PORT}"
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

    parts = [
        f"# ==============================================",
        f"# {BRAND} — WireGuard Configuration",
        f"# User ID: {user_id}",
        f"# Generated: {timestamp}",
        f"# Servers: {len(configs)}",
        f"# ==============================================",
        f"#",
        f"# 📋 ИНСТРУКЦИЯ:",
        f"# Выбери ОДИН блок [Interface] + [Peer] ниже",
        f"# и импортируй его в приложение:",
        f"#",
        f"#   📱 Happ / HiddifyNext — + → Импорт из буфера",
        f"#   🔗 Sing-box — Remote URL: {api_url}/api/sub/{user_id}",
        f"#   ⚡ Clash Meta — Подписки → Добавить",
        f"#   📄 WireGuard — Открыть .conf файл",
        f"#",
        f"# 📢 Канал: {CHANNEL}",
        f"# 📞 Поддержка: {SUPPORT}",
        f"# 💳 Купить: {PURCHASE}",
        f"#",
        f"# ==============================================",
        "",
    ]

    for i, cfg in enumerate(configs):
        parts.append(f"# ===== {cfg['flag']} {cfg['name']} ({cfg['endpoint']}) =====")
        parts.append(cfg["wg_config"])
        parts.append("")

    # В конце добавляем информацию о всех серверах
    parts.append("# ==============================================")
    parts.append(f"# {BRAND_LOGO} {BRAND} — 5 серверов по всему миру")
    parts.append(f"# Подпишись: {CHANNEL}")
    parts.append("# ==============================================")

    return "\n".join(parts)


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
    warp_reg_exists = os.path.isfile(WARP_REG_BIN)
    cache_dir_ok = get_cache_dir().exists()
    return jsonify({
        "status":        "ok",
        "brand":         BRAND,
        "warp_reg":      "found" if warp_reg_exists else "missing",
        "cache_dir":     "ok" if cache_dir_ok else "error",
        "servers":       len(SERVERS),
        "cached_users":  len(list(get_cache_dir().glob("*.json"))),
        "uptime":        time.time() - app_start_time,
        "version":       "2.0",
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
    """Sing-box подписка (JSON)."""
    try:
        configs = get_cached_configs(user_id)
        sub = format_singbox(configs, user_id)

        resp = app.response_class(
            response=json.dumps(sub, indent=2, ensure_ascii=False),
            status=200,
            mimetype="application/json",
        )
        resp.headers["Subscription-Userinfo"] = "upload=0; download=0; total=1099511627776; expire=0"
        resp.headers["Profile-Title"] = BRAND
        resp.headers["Profile-Update-Interval"] = "24"
        resp.headers["Access-Control-Allow-Origin"] = "*"

        logger.info(f"📤 Sing-box subscription for {user_id}: {len(configs)} servers")
        return resp
    except Exception as e:
        logger.error(f"Subscription error for {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>/clash")
@rate_limit
@require_subscription
def get_clash_subscription(user_id: str):
    """Clash-подписка для Happ/v2rayTun."""
    try:
        configs = get_cached_configs(user_id)
        sub = format_clash(configs, user_id)

        resp = app.response_class(
            response=json.dumps(sub, indent=2, ensure_ascii=False),
            status=200,
            mimetype="application/json",
        )
        resp.headers["Subscription-Userinfo"] = "upload=0; download=0; total=1099511627776; expire=0"
        resp.headers["Profile-Title"] = BRAND
        resp.headers["Profile-Update-Interval"] = "24"
        resp.headers["Access-Control-Allow-Origin"] = "*"

        logger.info(f"📤 Clash subscription for {user_id}: {len(configs)} servers")
        return resp
    except Exception as e:
        logger.error(f"Clash error for {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sub/<user_id>/conf")
@rate_limit
@require_subscription
def get_wg_conf(user_id: str):
    """WireGuard .conf со всеми серверами."""
    try:
        configs = get_cached_configs(user_id)
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
    """Возвращает список серверов пользователя."""
    try:
        configs = get_cached_configs(user_id)
        servers = []
        for cfg in configs:
            servers.append({
                "id":        cfg["id"],
                "name":      cfg["name"],
                "flag":      cfg["flag"],
                "emoji":     cfg["emoji"],
                "color":     cfg["color"],
                "endpoint":  cfg["endpoint"],
                "ip":        cfg["v4"],
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
    """Принудительно перегенерировать ключи."""
    try:
        clear_user_cache(user_id)
        configs = get_cached_configs(user_id)
        return jsonify({
            "success": True,
            "message": f"🆕 Сгенерировано {len(configs)} новых серверов",
            "servers": len(configs),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Запуск ──────────────────────────────────────────────────────────────
app_start_time = time.time()

if __name__ == "__main__":
    # Инициализируем БД
    init_db()

    logger.info(f"╔══════════════════════════════════════════╗")
    logger.info(f"║  {BRAND} API Server v2.0")
    logger.info(f"║  Author: {AUTHOR}")
    logger.info(f"║  Channel: {CHANNEL}")
    logger.info(f"║  Support: {SUPPORT}")
    logger.info(f"╚══════════════════════════════════════════╝")
    logger.info(f"  Host: {HOST}:{PORT}")
    logger.info(f"  Render URL: {RENDER_URL or '(не задан)'}")
    logger.info(f"  warp-reg: {WARP_REG_BIN}")
    logger.info(f"  Servers per user: {SERVERS_CNT}")
    logger.info(f"  Cache TTL: {CACHE_TTL // 3600}ч")
    logger.info(f"  Rate limit: {RATE_LIMIT} req/min")
    logger.info(f"  Data dir: {DATA_DIR}")

    if not os.path.isfile(WARP_REG_BIN):
        logger.warning(f"  ⚠️  warp-reg не найден: {WARP_REG_BIN}")

    app.run(host=HOST, port=PORT, debug=False)
