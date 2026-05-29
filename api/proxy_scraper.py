#!/usr/bin/env python3
"""
Desacratio VPN — Free Proxy Scraper
=====================================
Собирает бесплатные HTTP/SOCKS5 прокси из публичных источников,
фильтрует по странам (PL, DE, NL, GB, US), валидирует и кеширует.

Используется вместо WARP для обхода блокировок UDP.
"""

import json
import os
import time
import random
import logging
import socket
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger("desacratio-proxy")

# ─── Конфигурация ──────────────────────────────────────────────────────────
TARGET_COUNTRIES = ["PL", "DE", "NL", "GB", "US"]
COUNTRY_NAMES = {
    "PL": "Poland", "DE": "Germany", "NL": "Netherlands",
    "GB": "UK", "US": "USA",
}
COUNTRY_FLAGS = {
    "PL": "🇵🇱", "DE": "🇩🇪", "NL": "🇳🇱", "GB": "🇬🇧", "US": "🇺🇸",
}

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(__file__), "data"),
)
CACHE_FILE = os.path.join(DATA_DIR, "cache", "proxies.json")
CACHE_TTL = 300  # 5 минут (частая перепроверка — прокси умирают быстро)
FETCH_TIMEOUT = 3  # таймаут загрузки URL (сек) — быстро отваливаемся от мёртвых источников
VALIDATION_TIMEOUT = 3   # таймаут проверки одного прокси (сек)
VALIDATION_WORKERS = 40  # параллельных проверок
VALIDATION_TARGET = "httpbin.org"  # цель для тестового запроса
MAX_PER_COUNTRY = 50      # сколько прокси проверяем на страну

# ─── Источники прокси ──────────────────────────────────────────────────────
SOURCES = [
    # JSON с полями: host, port, protocol (http/socks4/socks5), country
    {
        "url": "https://raw.githubusercontent.com/monosans/proxy-scraper/main/proxies.json",
        "format": "monosans",
    },
    # Списки IP:PORT — большое количество
    {
        "url": "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000&country=all",
        "format": "plain",
        "default_type": "http",
    },
    {
        "url": "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=5000&country=all",
        "format": "plain",
        "default_type": "socks5",
    },
    {
        "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "format": "plain",
        "default_type": "http",
    },
    {
        "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
        "format": "plain",
        "default_type": "socks5",
    },
    {
        "url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
        "format": "plain",
        "default_type": "socks5",
    },
    {
        "url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
        "format": "plain",
        "default_type": "socks5",
    },
    {
        "url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
        "format": "plain",
        "default_type": "socks5",
    },
    {
        "url": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt",
        "format": "plain",
        "default_type": "socks5",
    },
]

# Per-country списки (только подтверждённо работающие источники)
COUNTRY_PROXY_SOURCES = []
for cc in TARGET_COUNTRIES:
    for proto in ["http", "socks5"]:
        COUNTRY_PROXY_SOURCES.append(
            f"https://api.proxyscrape.com/v2/?request=getproxies&protocol={proto}&timeout=10000&country={cc}"
        )

# Локальный кеш (thread-safe)
_cache_lock = threading.Lock()
_cache: dict = {"proxies": [], "timestamp": 0}
_last_refresh_time: float = 0


# ─── Вспомогательные функции ───────────────────────────────────────────────
def fetch_url(url: str, timeout: int = 10) -> str | None:
    """Загружает URL с таймаутом."""
    try:
        req = Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug(f"fetch_url failed: {url[:60]} — {e}")
        return None


def parse_monosans(content: str) -> list:
    """Парсит JSON от monosans/proxy-scraper."""
    proxies = []
    try:
        data = json.loads(content)
        for p in data:
            country = (p.get("country") or "").upper()
            if country in TARGET_COUNTRIES:
                proxies.append({
                    "host": p["host"],
                    "port": int(p["port"]),
                    "type": p.get("protocol", "http").lower(),
                    "country": country,
                })
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"monosans parse error: {e}")
    return proxies


def parse_plain(content: str, default_type: str = "http") -> list:
    """Парсит список IP:PORT (по одному на строку)."""
    proxies = []
    for line in content.strip().split("\n"):
        line = line.strip()
        if ":" in line:
            parts = line.rsplit(":", 1)
            host, port = parts[0], parts[1]
            if port.isdigit():
                port_num = int(port)
                # Авто-определение типа по порту
                detected_type = default_type
                if port_num in (1080, 1081, 1082, 9050, 9150, 10800):
                    detected_type = "socks5"
                elif port_num in (80, 443, 8080, 3128, 8888, 8889, 9090):
                    detected_type = "http"
                proxies.append({
                    "host": host,
                    "port": port_num,
                    "type": detected_type,
                    "country": None,
                })
    return proxies


def resolve_country(ip: str) -> str | None:
    """GeoIP через ip-api.com (бесплатно, 45 запросов/мин)."""
    try:
        url = f"http://ip-api.com/json/{ip}?fields=countryCode"
        with urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read())
            return data.get("countryCode")
    except Exception:
        return None


def test_socks5(host: str, port: int, timeout: int = 5) -> bool:
    """Реальная проверка SOCKS5: CONNECT + HTTP GET через прокси.

    Использует setdefaulttimeout для гарантированного таймаута.
    """
    socket.setdefaulttimeout(timeout)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))

        # SOCKS5 handshake
        s.send(b'\x05\x01\x00')
        resp = s.recv(2)
        if resp != b'\x05\x00':
            s.close()
            return False

        # SOCKS5 CONNECT к реальному хосту
        target_host = VALIDATION_TARGET.encode()
        target_port = 80
        msg = b'\x05\x01\x00\x03' + bytes([len(target_host)]) + target_host + struct.pack('>H', target_port)
        s.send(msg)

        # Ответ: байт 1 = 0 означает успех
        resp = s.recv(4)
        if len(resp) < 2 or resp[1] != 0:
            s.close()
            return False

        # Туннель установлен — отправляем HTTP-запрос через него
        http_req = (
            b'GET /ip HTTP/1.1\r\n'
            b'Host: ' + target_host + b'\r\n'
            b'User-Agent: curl/8.0\r\n'
            b'Connection: close\r\n\r\n'
        )
        s.send(http_req)

        # Читаем первые 512 байт (достаточно для заголовков)
        data = s.recv(512)
        s.close()

        return b'HTTP/' in data

    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(None)


def test_http_connect(host: str, port: int, timeout: int = 5) -> bool:
    """Реальная проверка HTTP CONNECT прокси."""
    socket.setdefaulttimeout(timeout)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))

        # CONNECT к целевому хосту
        target_host = VALIDATION_TARGET.encode()
        s.send(b'CONNECT ' + target_host + b':80 HTTP/1.1\r\nHost: ' + target_host + b'\r\n\r\n')

        resp = s.recv(4096)
        if b'200' not in resp and b'OK' not in resp:
            s.close()
            return False

        # CONNECT успешен — теперь отправляем HTTP-запрос через туннель
        http_req = (
            b'GET /ip HTTP/1.1\r\n'
            b'Host: ' + target_host + b'\r\n'
            b'Connection: close\r\n\r\n'
        )
        s.send(http_req)

        data = s.recv(512)
        s.close()

        return b'HTTP/' in data

    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(None)


SOCK5_PORTS = {1080, 1081, 1082, 9050, 9150, 10800, 4145, 10800}
HTTP_PORTS  = {80, 443, 8080, 3128, 8888, 8889, 9090, 8081, 8443}


def validate_proxy_real(host: str, port: int, timeout: int = 3) -> tuple:
    """Проверяет прокси: возвращает (тип, True/False).

    Сначала пробует SOCKS5 (+ реальный CONNECT к httpbin), затем HTTP CONNECT.
    На неизвестных портах пробует только SOCKS5 (экономит время).
    """
    # Известные SOCKS5 порты
    if port in SOCK5_PORTS:
        ok = test_socks5(host, port, timeout)
        if ok:
            return ("socks5", True)
        return (None, False)

    # Известные HTTP порты
    if port in HTTP_PORTS:
        ok = test_http_connect(host, port, timeout)
        if ok:
            return ("http", True)
        return (None, False)

    # Неизвестный порт — пробуем SOCKS5
    ok = test_socks5(host, port, timeout)
    if ok:
        return ("socks5", True)

    return (None, False)


# ─── Основные функции ──────────────────────────────────────────────────────
def collect_all() -> list:
    """Собирает прокси из всех источников (параллельно)."""
    all_proxies = []
    all_urls = []

    for src in SOURCES:
        all_urls.append((src["url"], src["format"], src.get("default_type")))

    for url in COUNTRY_PROXY_SOURCES:
        all_urls.append((url, "plain", "http" if "type=http" in url else "socks5"))

    def fetch_one(url: str, fmt: str, default_type: str | None) -> list:
        content = fetch_url(url, FETCH_TIMEOUT)
        if not content:
            return []
        if fmt == "monosans":
            parsed = parse_monosans(content)
        else:
            parsed = parse_plain(content, default_type or "http")
            # Определяем страну из URL per-country
            for cc in TARGET_COUNTRIES:
                if f"country={cc}" in url:
                    for p in parsed:
                        p["country"] = cc
                    break
        return parsed

    with ThreadPoolExecutor(max_workers=10) as pool:
        fut_to_url = {}
        for url, fmt, dt in all_urls:
            fut = pool.submit(fetch_one, url, fmt, dt)
            fut_to_url[fut] = url

        for fut in as_completed(fut_to_url, timeout=30):
            url = fut_to_url[fut]
            try:
                parsed = fut.result()
                all_proxies.extend(parsed)
                if parsed:
                    logger.info(f"  {url[:60]}: {len(parsed)} прокси")
                else:
                    logger.debug(f"  {url[:60]}: пусто")
            except Exception as e:
                logger.debug(f"  {url[:60]}: ошибка — {e}")

    return all_proxies


def deduplicate(proxies: list) -> list:
    """Удаляет дубликаты."""
    seen = set()
    unique = []
    for p in proxies:
        key = (p["host"], p["port"], p["type"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def assign_countries(proxies: list, max_lookups: int = 20) -> list:
    """Определяет страну для прокси без country через GeoIP (лимит ip-api)."""
    unresolved = [p for p in proxies if not p.get("country")]
    logger.info(f"  GeoIP: {len(unresolved)} без страны, проверим {min(max_lookups, len(unresolved))}...")

    done = 0
    for p in unresolved:
        if done >= max_lookups:
            break
        cc = resolve_country(p["host"])
        if cc in TARGET_COUNTRIES:
            p["country"] = cc
        done += 1
        if done % 10 == 0:
            time.sleep(1)  # не превышаем rate limit ip-api

    return proxies


SOCKS_VALIDATION_TIMEOUT = 3  # сколько ждать SOCKS5 CONNECT + HTTP GET


def validate_global(proxies: list) -> list:
    """Глобальная параллельная проверка прокси реальным трафиком.

    Берём большую выборку (500), TCP префильтр (1s), затем реальный SOCKS5 тест.
    """
    if not proxies:
        return []

    # Берём 500 случайных прокси (достаточно для нахождения 10+ живых)
    sample_size = min(500, len(proxies))
    candidates = random.sample(proxies, sample_size)

    # Stage 1: TCP prefilter (1s timeout, 50 workers)
    tcp_alive = []
    with ThreadPoolExecutor(max_workers=50) as pool:
        def tcp_check(p):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)
                r = s.connect_ex((p["host"], p["port"]))
                s.close()
                return r == 0
            except:
                return False

        fut_map = {pool.submit(tcp_check, p): p for p in candidates}
        for fut in as_completed(fut_map, timeout=20):
            p = fut_map[fut]
            try:
                if fut.result():
                    tcp_alive.append(p)
            except:
                pass

    logger.info(f"  TCP prefilter: {len(tcp_alive)}/{sample_size} живы (по TCP)")
    if not tcp_alive:
        logger.warning("  Нет живых по TCP! Возможно, сетевые проблемы.")
        return []

    # Stage 2: Real SOCKS5/HTTP test
    logger.info(f"  SOCKS5 тест: {len(tcp_alive)} кандидатов...")
    valid = []
    with ThreadPoolExecutor(max_workers=VALIDATION_WORKERS) as pool:
        fut_to_proxy = {}
        for p in tcp_alive:
            fut = pool.submit(
                validate_proxy_real,
                p["host"], p["port"],
                SOCKS_VALIDATION_TIMEOUT,
            )
            fut_to_proxy[fut] = p

        for fut in as_completed(fut_to_proxy, timeout=SOCKS_VALIDATION_TIMEOUT + 15):
            p = fut_to_proxy[fut]
            try:
                detected_type, ok = fut.result()
                if ok:
                    p["type"] = detected_type
                    valid.append(p)
            except Exception:
                pass

    valid.sort(key=lambda x: (0 if x["type"] == "socks5" else 1, random.random()))
    logger.info(f"  Живых (реальный трафик): {len(valid)}/{len(tcp_alive)}")
    return valid


def refresh_cache(force: bool = False) -> list:
    """Обновляет кеш прокси: сбор → дедуп → валидация → GeoIP → страны."""
    global _cache, _last_refresh_time
    now = time.time()

    with _cache_lock:
        if not force and _cache["proxies"] and (now - _last_refresh_time) < CACHE_TTL:
            return _cache["proxies"]

    logger.info("🌐 Обновление списка прокси...")
    deadline = time.time() + 120  # общий таймаут 120с
    valid = []

    try:
        # 1. Сбор из всех источников
        raw = collect_all()
        logger.info(f"  Собрано: {len(raw)} прокси")
        if time.time() > deadline:
            raise TimeoutError("Превышено время сбора")

        # 2. Дедупликация
        raw = deduplicate(raw)
        logger.info(f"  После дедупликации: {len(raw)}")
        if time.time() > deadline:
            raise TimeoutError("Превышено время дедупликации")

        # 3. Валидация реальным трафиком (без GeoIP — сначала проверяем)
        valid = validate_global(raw)
        if not valid:
            logger.warning("  Ни одного живого прокси не найдено!")
        else:
            # 4. GeoIP только для живых прокси
            valid = assign_countries(valid, max_lookups=45)
            logger.info(f"  После GeoIP: {len([p for p in valid if p.get('country')])} со страной")

        if time.time() > deadline:
            raise TimeoutError("Превышено время валидации")

        # Если всё равно нет прокси со странами — пытаемся ещё через GeoIP
        has_country = [p for p in valid if p.get("country") in TARGET_COUNTRIES]
        if not has_country and valid:
            logger.warning("  Нет прокси с целевыми странами! Повторное GeoIP...")
            valid = assign_countries(valid, max_lookups=45)

        # Финал: оставляем только с целевыми странами
        final = [p for p in valid if p.get("country") in TARGET_COUNTRIES]
        if not final and valid:
            # Если совсем нет — возвращаем все живые с любыми странами
            logger.warning("  Используем живые прокси без фильтра по стране")
            final = valid[:5]

        valid = final

        with _cache_lock:
            _cache = {"proxies": valid, "timestamp": now}
            _last_refresh_time = now

        # Сохраняем в файл
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump({"timestamp": now, "proxies": valid}, f)

        logger.info(f"✅ Кеш прокси обновлён: {len(valid)} рабочих")
    except Exception as e:
        logger.error(f"Ошибка обновления прокси: {e}")
        # Возвращаем старые из кеша/файла
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE) as f:
                    cached = json.load(f)
                    valid = cached.get("proxies", [])
            except (json.JSONDecodeError, IOError):
                pass

    return valid


def get_proxies(count: int = 5) -> list:
    """
    Возвращает список прокси для пользователя.
    По одному прокси из каждой целевой страны, всего `count` штук.
    Приоритет: SOCKS5 > HTTP.
    """
    global _cache
    proxies = refresh_cache()

    if not proxies:
        logger.warning("Нет прокси в кеше!")
        return []

    # Группируем по странам, SOCKS5 в приоритете
    by_country: dict = {}
    for p in proxies:
        cc = p.get("country", "")
        by_country.setdefault(cc, {"socks5": [], "http": []})
        if p["type"] == "socks5":
            by_country[cc]["socks5"].append(p)
        else:
            by_country[cc]["http"].append(p)

    result = []
    # Сначала берём SOCKS5 из каждой целевой страны
    for cc in TARGET_COUNTRIES:
        pool = by_country.get(cc, {"socks5": [], "http": []})
        socks = pool.get("socks5", [])
        random.shuffle(socks)
        if socks:
            p = socks[0]
            result.append({
                "flag": COUNTRY_FLAGS.get(cc, "🌍"),
                "name": COUNTRY_NAMES.get(cc, cc),
                "type": p["type"],
                "server": p["host"],
                "port": int(p["port"]),
                "country": cc,
            })
        else:
            http_pool = pool.get("http", [])
            random.shuffle(http_pool)
            if http_pool:
                p = http_pool[0]
                result.append({
                    "flag": COUNTRY_FLAGS.get(cc, "🌍"),
                    "name": COUNTRY_NAMES.get(cc, cc),
                    "type": p["type"],
                    "server": p["host"],
                    "port": int(p["port"]),
                    "country": cc,
                })

    # Если не хватает — добираем из любых стран (SOCKS5 в приоритете)
    if len(result) < count:
        extras = [p for p in proxies if p.get("country") in TARGET_COUNTRIES]
        extras.sort(key=lambda x: (0 if x["type"] == "socks5" else 1, random.random()))
        for p in extras:
            if len(result) >= count:
                break
            cc = p.get("country", "XX")
            # Не дублируем уже выбранные IP
            if not any(r["server"] == p["host"] for r in result):
                result.append({
                    "flag": COUNTRY_FLAGS.get(cc, "🌍"),
                    "name": COUNTRY_NAMES.get(cc, cc),
                    "type": p["type"],
                    "server": p["host"],
                    "port": int(p["port"]),
                    "country": cc,
                })

    return result[:count]


# ─── INIT ──────────────────────────────────────────────────────────────────
def init_proxy_scraper():
    """Инициализация при старте: загружаем кеш из файла, если есть."""
    global _cache, _last_refresh_time
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cached = json.load(f)
            _cache = cached
            _last_refresh_time = cached.get("timestamp", 0)
            logger.info(f"📦 Загружен кеш прокси: {len(cached.get('proxies', []))} шт")
        except (json.JSONDecodeError, IOError):
            pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_proxy_scraper()
    proxies = get_proxies(5)
    print(f"\n=== Прокси ({len(proxies)} шт) ===")
    for p in proxies:
        print(f"  {p['flag']} {p['name']}: {p['type']}://{p['server']}:{p['port']}")
