#!/usr/bin/env python3
"""
Desacratio VPN — WARP Registration (Pure Python)
==================================================
Регистрация устройства в Cloudflare WARP без warp-reg бинарника.

Использует Curve25519 (X25519) для генерации ключей WireGuard
и HTTP API Cloudflare для получения конфига.

Зависимости: cryptography>=41.0

Использование:
    from warp_reg import register_warp
    config = register_warp()
    print(config["private_key"])
"""

import os
import sys
import json
import time
import uuid
import base64
import logging
import urllib.request
import urllib.error

logger = logging.getLogger("desacratio-warp")

# ─── X25519 (Curve25519) key generation ───────────────────────────────────

try:
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
    )
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False
    logger.warning("cryptography not installed, trying nacl...")
    try:
        import nacl.bindings
        HAS_NACL = True
    except ImportError:
        HAS_NACL = False
        logger.warning("nacl not installed either, will use subprocess fallback")

# ─── WARP API ─────────────────────────────────────────────────────────────

WARP_API_BASE = "https://api.cloudflareclient.com/v0a2158"
WARP_USER_AGENT = "okhttp/3.12.1"
WARP_ENDPOINT = os.environ.get(
    "WARP_ENDPOINT", "engage.cloudflareclient.com:2408"
)
WARP_REFERER = "https://cloudflare.com/"


def _generate_x25519_keypair() -> tuple[bytes, bytes]:
    """
    Генерирует пару ключей Curve25519.
    Возвращает (private_key_bytes, public_key_bytes).
    """
    if HAS_CRYPTOGRAPHY:
        private_key = X25519PrivateKey.generate()
        private_bytes = private_key.private_bytes_raw()

        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
        public_key = private_key.public_key()
        public_bytes = public_key.public_bytes_raw()

        return private_bytes, public_bytes

    elif HAS_NACL:
        private_bytes = nacl.bindings.randombytes(32)
        public_bytes = nacl.bindings.crypto_scalarmult_base(private_bytes)
        return private_bytes, public_bytes

    else:
        return _fallback_generate_keys()


def _fallback_generate_keys() -> tuple[bytes, bytes]:
    """Fallback: запускает warp-reg бинарник как подпроцесс."""
    import subprocess

    warp_reg_paths = [
        os.environ.get("WARP_REG_BIN", ""),
        os.path.join(os.path.dirname(__file__), "warp-reg"),
        "/usr/local/bin/warp-reg",
        "/app/warp-reg",
    ]

    for path in warp_reg_paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            try:
                result = subprocess.run(
                    [path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    config = {}
                    for line in result.stdout.strip().split("\n"):
                        if ":" in line:
                            key, val = line.split(":", 1)
                            config[key.strip()] = val.strip()

                    private_key = base64.b64decode(config.get("private_key", ""))
                    public_key = base64.b64decode(config.get("public_key", ""))
                    return private_key, public_key
            except Exception as e:
                logger.warning(f"warp-reg fallback error: {e}")

    raise RuntimeError(
        "Не удалось сгенерировать ключи: установи cryptography или warp-reg"
    )


def register_warp(device_id: str | None = None) -> dict:
    """
    Регистрирует устройство в Cloudflare WARP.

    Args:
        device_id: UUID для устройства (если None — генерируется случайный).

    Returns:
        dict с ключами:
            - device_id: str
            - private_key: str (base64)
            - peer_public_key: str (base64) — публичный ключ ПИРА (WireGuard peer)
            - client_id: str (hex)
            - reserved: list[int] (3 байта)
            - v4: str
            - v6: str
            - endpoint: str
            - token: str
            - account_id: str
            - account_type: str
    """
    # 1. Генерируем ключи
    private_raw, public_raw = _generate_x25519_keypair()

    private_b64 = base64.b64encode(private_raw).decode()
    public_b64 = base64.b64encode(public_raw).decode()

    # 2. Формируем запрос
    if device_id is None:
        device_id = str(uuid.uuid4())

    headers = {
        "User-Agent": WARP_USER_AGENT,
        "Content-Type": "application/json; charset=UTF-8",
        "Referer": WARP_REFERER,
    }

    body = json.dumps({
        "key": public_b64,
        "install_id": "",
        "warp_enabled": True,
        "tos": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "type": "Android",
        "locale": "ru-RU",
    }).encode()

    # 3. Отправляем запрос
    url = f"{WARP_API_BASE}/reg?install_id=&tuning_version=1"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"WARP API error {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"WARP API connection error: {e.reason}")

    # 4. Парсим ответ
    response_id = data.get("id", device_id)
    token = data.get("token", "")
    account = data.get("account", {})
    config_data = data.get("config", {})

    # Серверный публичный ключ — в config.peers[0].public_key, НЕ в data.key!
    # (data.key — это НАШ собственный ключ, переданный в запросе)
    peers = config_data.get("peers", [])
    if peers:
        server_public_key = peers[0].get("public_key", "")
        peer_endpoint = peers[0].get("endpoint", {})
        # endpoint host (engage.cloudflareclient.com:2408) или v4
        endpoint_host = peer_endpoint.get("host", f"{peer_endpoint.get('v4', 'engage.cloudflareclient.com')}:2408")
        # доступные порты
        peer_ports = peer_endpoint.get("ports", [2408, 500, 1701, 4500])
    else:
        server_public_key = data.get("key", "")
        endpoint_host = WARP_ENDPOINT
        peer_ports = [2408]

    # Извлекаем адреса
    interface_data = config_data.get("interface", {})
    addresses = interface_data.get("addresses", {})
    v4 = addresses.get("v4", "172.16.0.2")
    v6 = addresses.get("v6", "2606:4700:110:8a20::1")

    # Client ID — из config.client_id (base64, 3 байта), НЕ из UUID!
    # Старые API возвращали client_id как hex-префикс UUID,
    # новые — как base64-строку из 3 байт.
    raw_client_id = config_data.get("client_id", "")
    if raw_client_id:
        try:
            reserved_bytes = base64.b64decode(raw_client_id + "==")  # pad
            reserved = list(reserved_bytes[:3])
        except Exception:
            reserved = [0, 0, 0]
    else:
        # Fallback: первые 4 байта UUID (старый метод)
        client_hex = response_id.replace("-", "")[:8]
        reserved_bytes = bytes.fromhex(client_hex)[:3]
        reserved = list(reserved_bytes)

    return {
        "device_id": response_id,
        "token": token,
        "account_id": account.get("id", ""),
        "account_type": account.get("account_type", "free"),
        "license": account.get("license", ""),
        "private_key": private_b64,
        "peer_public_key": server_public_key,
        "client_id": raw_client_id,
        "reserved": reserved,
        "v4": v4,
        "v6": v6,
        "endpoint": endpoint_host,
        "peer_ports": peer_ports,
    }


def make_wireguard_config(warp_config: dict) -> str:
    """Форматирует WARP config в стандартный .conf WireGuard."""
    lines = [
        "[Interface]",
        f"PrivateKey = {warp_config['private_key']}",
        f"Address = {warp_config['v4']}/32",
        f"Address = {warp_config['v6']}/128",
        "DNS = 1.1.1.1, 1.0.0.1",
        "MTU = 1280",
        "",
        "[Peer]",
        f"PublicKey = {warp_config['peer_public_key']}",
        "AllowedIPs = 0.0.0.0/0",
        "AllowedIPs = ::/0",
        f"Endpoint = {warp_config['endpoint']}",
        "PersistentKeepalive = 25",
    ]
    return "\n".join(lines)


# ─── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    config = register_warp()
    print(f"device_id: {config['device_id']}")
    print(f"token: {config['token']}")
    print(f"account_id: {config['account_id']}")
    print(f"account_type: {config['account_type']}")
    print(f"license: {config['license']}")
    print(f"private_key: {config['private_key']}")
    print(f"peer_public_key: {config['peer_public_key']}")
    print(f"client_id: {config['client_id']}")
    print(f"reserved: {json.dumps(config['reserved'])}")
    print(f"v4: {config['v4']}")
    print(f"v6: {config['v6']}")
    print(f"endpoint: {config['endpoint']}")
