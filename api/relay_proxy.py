#!/usr/bin/env python3
"""
Desacratio VPN — Relay Proxy Server
=====================================
HTTP CONNECT proxy + API server в одном процессе.

Render exposes PORT 8443 externally via HTTPS.
Этот сервер слушает PORT и обрабатывает:
  - HTTP CONNECT → создаёт TCP туннель (для Happ/Sing-box/Clash)
  - GET/POST    → перенаправляет в Flask API (подписки, статус, etc.)

Запуск:
  python3 relay_proxy.py
"""

import os
import sys
import json
import time
import socket
import asyncio
import logging
import traceback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("relay-proxy")

# ─── Flask API ─────────────────────────────────────────────────────────────
# Импортируем Flask app из warp-api.py (без запуска run())

# Добавляем пути для импорта warp-api.py и db.py
_api_dir = os.path.dirname(__file__)
_project_root = os.path.join(_api_dir, "..")
for p in [_api_dir, _project_root]:
    if p not in sys.path:
        sys.path.insert(0, p)

from warp_api_wrapper import app as flask_app

# Импортируем БД для инициализации
from db import init_db

# Конфигурация
HOST = os.environ.get("RELAY_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("RELAY_PORT", "8443")))

# ─── HTTP CONNECT Proxy Handler ─────────────────────────────────────────────

async def handle_proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Обрабатывает одно входящее соединение."""
    try:
        # Читаем первую строку запроса
        request_line = await asyncio.wait_for(
            reader.readline(), timeout=10
        )
        if not request_line:
            writer.close()
            return

        request_str = request_line.decode("utf-8", errors="replace").strip()
        logger.debug(f"→ {request_str[:100]}")

        parts = request_str.split()
        method = parts[0] if parts else ""

        # CONNECT запрос — создаём туннель
        if method.upper() == "CONNECT":
            await handle_connect(request_str, reader, writer)
            return

        # HTTP GET/POST с полным URL (forward proxy режим)
        if method.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            full_url = parts[1] if len(parts) >= 2 else ""
            if full_url.startswith(("http://", "https://")):
                await handle_forward_proxy(request_str, request_line, reader, writer)
                return

        # Обычный HTTP запрос — перенаправляем в Flask
        await handle_http(request_str, request_line, reader, writer)

    except asyncio.TimeoutError:
        logger.debug("Timeout reading request")
    except Exception as e:
        logger.error(f"Handler error: {e}")
        traceback.print_exc()
    finally:
        try:
            writer.close()
        except:
            pass


async def handle_connect(request_str: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Обрабатывает HTTP CONNECT — создаёт TCP туннель к целевому хосту."""
    # Парсим CONNECT host:port HTTP/1.1
    parts = request_str.split()
    if len(parts) < 2:
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        return

    target = parts[1]
    target_host, _, target_port_str = target.partition(":")
    try:
        target_port = int(target_port_str) if target_port_str else 443
    except ValueError:
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        return

    logger.info(f"🔌 CONNECT {target_host}:{target_port}")

    # Читаем оставшиеся заголовки (нам они не нужны, но нужно пропустить)
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        line_str = line.decode("utf-8", errors="replace").strip()
        if not line_str:  # пустая строка = конец заголовков
            break

    # Подключаемся к целевому хосту
    try:
        target_reader, target_writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port),
            timeout=10
        )
    except Exception as e:
        logger.warning(f"  ❌ Невозможно подключиться к {target_host}:{target_port} — {e}")
        writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\n".encode())
        await writer.drain()
        return

    # Успех! Отправляем 200 Connection Established
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()

    logger.info(f"  ✅ Туннель {target_host}:{target_port} установлен")

    # Бидирекциональная передача данных
    await asyncio.gather(
        pipe_stream(reader, target_writer, f"{target_host}:{target_port} → клиент"),
        pipe_stream(target_reader, writer, f"{target_host}:{target_port} ← клиент"),
    )


async def pipe_stream(src: asyncio.StreamReader, dst: asyncio.StreamWriter, label: str):
    """Копирует данные из src в dst."""
    try:
        while True:
            data = await asyncio.wait_for(src.read(65536), timeout=300)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except asyncio.TimeoutError:
        logger.debug(f"Таймаут: {label}")
    except ConnectionResetError:
        pass
    except Exception as e:
        logger.debug(f"{label}: {e}")
    finally:
        try:
            dst.close()
        except:
            pass


async def handle_forward_proxy(request_str: str, first_line: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Обрабатывает HTTP forward proxy запрос (GET http://host/path HTTP/1.1).

    Запрашивает ресурс через HTTP и возвращает клиенту.
    """
    parts = request_str.split()
    if len(parts) < 2:
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        return

    method = parts[0]
    full_url = parts[1]
    version = parts[2] if len(parts) >= 3 else "HTTP/1.1"

    # Парсим URL
    from urllib.parse import urlparse
    parsed = urlparse(full_url)
    target_host = parsed.hostname
    target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    target_path = parsed.path or "/"
    if parsed.query:
        target_path += "?" + parsed.query

    logger.info(f"🔀 Forward proxy {method} {full_url}")

    try:
        # Подключаемся к целевому хосту
        target_reader, target_writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port),
            timeout=10
        )

        # Пересылаем запрос (меняем путь, убираем полный URL)
        target_request = f"{method} {target_path} {version}\r\n"
        target_writer.write(target_request.encode())

        # Пропускаем заголовки от клиента, заменяем Host
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            line_str = line.decode("utf-8", errors="replace")
            if line_str.lower().startswith("host:"):
                target_writer.write(f"Host: {target_host}:{target_port}\r\n".encode())
            elif line_str.strip() == "":
                target_writer.write(b"\r\n")
                break
            else:
                target_writer.write(line)

        await target_writer.drain()

        # Пересылаем ответ обратно клиенту
        while True:
            data = await asyncio.wait_for(target_reader.read(65536), timeout=30)
            if not data:
                break
            writer.write(data)
            await writer.drain()

        target_writer.close()
    except Exception as e:
        logger.warning(f"  ❌ Forward proxy error: {e}")
        writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\nBad Gateway: {e}".encode())
        await writer.drain()


async def handle_http(request_str: str, first_line: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Обрабатывает обычный HTTP запрос — передаёт в Flask WSGI."""
    # Читаем все заголовки
    headers_raw = first_line
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        headers_raw += line
        if line == b"\r\n" or line == b"\n":
            break

    # Определяем Content-Length для чтения тела
    content_length = 0
    headers_str = headers_raw.decode("utf-8", errors="replace")
    for h_line in headers_str.split("\r\n"):
        if h_line.lower().startswith("content-length:"):
            try:
                content_length = int(h_line.split(":")[1].strip())
            except:
                pass

    # Читаем тело запроса
    body = b""
    if content_length > 0:
        body = await asyncio.wait_for(reader.readexactly(content_length), timeout=10)

    # Собираем WSGI environ
    first_line_str = request_str
    method, path, version = first_line_str.split(" ", 2)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": HOST,
        "SERVER_PORT": str(PORT),
        "SERVER_PROTOCOL": version,
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input": None,  # не нужно, мы уже прочитали тело
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "HTTP_HOST": f"{HOST}:{PORT}",
    }

    # Парсим заголовки
    for h_line in headers_str.split("\r\n")[1:]:
        if ":" in h_line:
            key, value = h_line.split(":", 1)
            wsgi_key = f"HTTP_{key.strip().upper().replace('-', '_')}"
            environ[wsgi_key] = value.strip()

    # Вызываем Flask
    response_data = []
    status_code = 200
    response_headers = []

    def start_response(status, headers, exc_info=None):
        nonlocal status_code, response_headers
        status_code = int(status.split()[0])
        response_headers = headers
        return response_data.append

    # Запускаем Flask WSGI
    try:
        response_iter = flask_app(environ, start_response)
        for chunk in response_iter:
            response_data.append(chunk)
    except Exception as e:
        logger.error(f"Flask error: {e}")
        status_code = 500
        response_data = [b'{"error":"Internal Server Error"}']

    # Отправляем ответ
    status_text = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error",
                   402: "Payment Required", 429: "Too Many Requests"}
    status_msg = status_text.get(status_code, "Unknown")

    resp_line = f"HTTP/1.1 {status_code} {status_msg}\r\n"
    writer.write(resp_line.encode())

    for key, value in response_headers:
        writer.write(f"{key}: {value}\r\n".encode())
    writer.write(b"\r\n")

    for chunk in response_data:
        if isinstance(chunk, bytes):
            writer.write(chunk)
        else:
            writer.write(chunk.encode("utf-8"))

    await writer.drain()


# ─── Запуск ────────────────────────────────────────────────────────────────

async def main():
    # Инициализируем БД
    init_db()
    logger.info("🗄️  База данных инициализирована")

    server = await asyncio.start_server(handle_proxy, HOST, PORT)
    addr = server.sockets[0].getsockname()
    logger.info(f"🛡️  Relay Proxy Server v1.0")
    logger.info(f"   Listening on {addr[0]}:{addr[1]}")
    logger.info(f"   API:     GET/POST /api/sub/<user_id>")
    logger.info(f"   Proxy:   CONNECT host:port (HTTP CONNECT)")
    logger.info("")
    logger.info(f"   📱 Для Happ: используй как HTTP прокси")
    logger.info(f"      http://desacratio-vpn.onrender.com")
    logger.info(f"      или https://desacratio-vpn.onrender.com")
    logger.info("")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
