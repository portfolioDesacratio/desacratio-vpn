#!/usr/bin/env python3
"""
Desacratio VPN — Database Module
═══════════════════════════════════════════
SQLite база данных для пользователей и подписок.
Используется и API (warp-api.py), и ботом (free-bot.py).
═══════════════════════════════════════════
"""
import sqlite3
import time
import os
import logging

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DATA_DIR, "desacratio.db")

logger = logging.getLogger("desacratio-db")

TRIAL_DAYS = 7

PLANS = {
    "1day":   {"label": "1 день",     "price_usd": 0.50, "stars": 25,  "days": 1},
    "3days":  {"label": "3 дня",      "price_usd": 1.00, "stars": 75,  "days": 3},
    "7days":  {"label": "7 дней",     "price_usd": 2.50, "stars": 125, "days": 7},
    "14days": {"label": "14 дней",    "price_usd": 4.00, "stars": 175, "days": 14},
    "30days": {"label": "30 дней",    "price_usd": 6.00, "stars": 250, "days": 30},
    "forever":{"label": "Навсегда",   "price_usd": 7.50, "stars": 350, "days": 36500},
}


def get_conn():
    """Создаёт подключение к SQLite."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Создаёт таблицы если их нет."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            trial_start INTEGER DEFAULT NULL,
            trial_used INTEGER DEFAULT 0,
            sub_type TEXT DEFAULT NULL,
            sub_start INTEGER DEFAULT NULL,
            sub_end INTEGER DEFAULT NULL,
            created_at INTEGER DEFAULT (unixepoch()),
            updated_at INTEGER DEFAULT (unixepoch())
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            method TEXT DEFAULT 'manual',
            admin_id INTEGER DEFAULT NULL,
            created_at INTEGER DEFAULT (unixepoch()),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
    """)
    conn.commit()
    conn.close()
    logger.info(f"✅ База данных инициализирована: {DB_PATH}")

    # Пробуем восстановить из бэкапа если БД пуста
    restored = restore_from_backup()
    if restored:
        logger.info("♻️ Данные восстановлены из JSON-бэкапа")


def get_user(user_id: int):
    """Возвращает пользователя или None."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def ensure_user(user_id: int, username: str = "", first_name: str = ""):
    """Создаёт пользователя если его нет."""
    conn = get_conn()
    conn.execute(
        """INSERT OR IGNORE INTO users (user_id, username, first_name, created_at)
           VALUES (?, ?, ?, ?)""",
        (user_id, username[:32], first_name[:64], int(time.time()))
    )
    conn.commit()
    conn.close()


def start_trial(user_id: int) -> bool:
    """
    Активирует пробный период (7 дней).
    Возвращает True если успешно, False если триал уже был использован.
    """
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    # Если уже был платный период — триал не нужен
    now = int(time.time())

    if not user:
        # Новый пользователь — создаём и стартуем триал
        conn.execute(
            "INSERT INTO users (user_id, trial_start, trial_used, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
            (user_id, now, now, now)
        )
        conn.commit()
        conn.close()
        save_backup()  # 💾 бэкап после изменения
        return True

    if user["trial_used"]:
        conn.close()
        return False

    # Стартуем триал
    conn.execute(
        "UPDATE users SET trial_start=?, trial_used=1, updated_at=? WHERE user_id=?",
        (now, now, user_id)
    )
    conn.commit()
    conn.close()
    save_backup()  # 💾 бэкап после изменения
    return True


def has_active_sub(user_id: int) -> bool:
    """
    Проверяет, есть ли у пользователя активная подписка (триал или платная).
    """
    user = get_user(user_id)
    if not user:
        return False

    now = int(time.time())

    # Пробный период
    if user["trial_start"] and user["trial_used"]:
        elapsed = now - user["trial_start"]
        if elapsed < TRIAL_DAYS * 86400:
            return True

    # Платная подписка
    if user["sub_end"] and now < user["sub_end"]:
        return True

    return False


def get_sub_info(user_id: int) -> dict:
    """
    Возвращает информацию о подписке пользователя.
    Приоритет: платная > пробный > истекшие.
    """
    user = get_user(user_id)
    if not user:
        return {"status": "none", "days_left": 0}

    now = int(time.time())

    # ⭐ Платная подписка — высший приоритет
    if user["sub_end"] and now < user["sub_end"]:
        if user["sub_type"] == "forever":
            return {
                "status": "active",
                "days_left": -1,       # -1 = навсегда
                "type": "forever",
                "sub_start": user["sub_start"],
                "sub_end": user["sub_end"],
            }
        days_left = max(0, (user["sub_end"] - now) // 86400)
        return {
            "status": "active",
            "days_left": int(days_left),
            "type": user["sub_type"],
            "sub_start": user["sub_start"],
            "sub_end": user["sub_end"],
        }

    # 🎁 Пробный период (только если нет активной платной)
    if user["trial_start"] and user["trial_used"]:
        elapsed = now - user["trial_start"]
        days_left = max(0, TRIAL_DAYS - elapsed // 86400)
        if days_left > 0:
            return {
                "status": "trial",
                "days_left": int(days_left),
                "total_days": TRIAL_DAYS,
                "trial_used": True,
            }

    # ⏰ Пробный период истёк (и нет платной подписки)
    if user["trial_used"] and not user["sub_end"]:
        return {"status": "expired_trial", "days_left": 0}

    # ⏰ Платная подписка истекла
    if user["sub_end"] and now >= user["sub_end"]:
        return {"status": "expired", "days_left": 0}

    return {"status": "none", "days_left": 0}


def activate_sub(user_id: int, plan: str, admin_id: int = None):
    """
    Активирует платную подписку.
    plan — ключ из PLANS (например "30days").
    """
    if plan not in PLANS:
        raise ValueError(f"Неизвестный план: {plan}")

    plan_info = PLANS[plan]
    now = int(time.time())
    end = now + plan_info["days"] * 86400

    conn = get_conn()

    # Если у пользователя активен триал — он заменяется платной подпиской
    conn.execute(
        """INSERT INTO users (user_id, sub_type, sub_start, sub_end, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               sub_type=excluded.sub_type,
               sub_start=excluded.sub_start,
               sub_end=excluded.sub_end,
               updated_at=excluded.updated_at""",
        (user_id, plan, now, end, now)
    )

    # Запись о покупке
    conn.execute(
        "INSERT INTO purchases (user_id, plan, amount, currency, method, admin_id, created_at) VALUES (?, ?, ?, 'USD', 'manual', ?, ?)",
        (user_id, plan, plan_info["price_usd"], admin_id, now)
    )

    conn.commit()
    conn.close()
    save_backup()  # 💾 бэкап после изменения
    logger.info(f"💳 Активирована подписка {plan} для user_id={user_id}")
    return True


def get_all_users() -> list:
    """Возвращает список всех пользователей."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """Возвращает статистику."""
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM users WHERE sub_end > unixepoch() OR (trial_used AND trial_start > unixepoch() - ? * 86400)", (TRIAL_DAYS,)).fetchone()[0]
    paid = conn.execute("SELECT COUNT(*) FROM users WHERE sub_end IS NOT NULL AND sub_end > unixepoch()").fetchone()[0]
    revenue = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM purchases").fetchone()[0]
    conn.close()
    return {"total": total, "active": active, "paid": paid, "revenue": revenue}


# ─── JSON Backup / Restore ──────────────────────────────────────────────
# Бэкап в JSON-файл в data/cache/. Переживает restart, но НЕ re-deploy.
# Перед деплоем админ делает /export в боте.

BACKUP_FILE = os.path.join(DATA_DIR, "cache", "db-backup.json")


def export_db_to_json() -> dict:
    """Экспортирует всю БД в JSON (для бэкапа)."""
    conn = get_conn()
    users = conn.execute("SELECT * FROM users").fetchall()
    purchases = conn.execute("SELECT * FROM purchases").fetchall()
    conn.close()
    return {
        "version": 1,
        "exported_at": int(time.time()),
        "users": [dict(u) for u in users],
        "purchases": [dict(p) for p in purchases],
    }


def save_backup():
    """Сохраняет бэкап в JSON-файл в cache/."""
    try:
        data = export_db_to_json()
        os.makedirs(os.path.dirname(BACKUP_FILE), exist_ok=True)
        with open(BACKUP_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"💾 Бэкап БД сохранён ({len(data['users'])} users)")
    except Exception as e:
        logger.warning(f"Ошибка сохранения бэкапа: {e}")


def load_backup() -> dict:
    """Загружает бэкап из JSON-файла."""
    if not os.path.isfile(BACKUP_FILE):
        return {}
    try:
        with open(BACKUP_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Ошибка загрузки бэкапа: {e}")
        return {}


def restore_db_from_dict(backup: dict) -> bool:
    """
    Восстанавливает БД из dict (JSON).
    Возвращает True если восстановление выполнено.
    """
    if not backup or "users" not in backup:
        return False

    conn = get_conn()

    try:
        # Очищаем текущие данные
        conn.execute("DELETE FROM purchases")
        conn.execute("DELETE FROM users")

        # Восстанавливаем пользователей
        for u in backup["users"]:
            conn.execute(
                """INSERT INTO users 
                   (user_id, username, first_name, trial_start, trial_used, sub_type, sub_start, sub_end, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (u["user_id"], u.get("username", ""), u.get("first_name", ""),
                 u.get("trial_start"), u.get("trial_used", 0),
                 u.get("sub_type"), u.get("sub_start"), u.get("sub_end"),
                 u.get("created_at", int(time.time())), int(time.time()))
            )

        # Восстанавливаем покупки
        for p in backup.get("purchases", []):
            conn.execute(
                """INSERT INTO purchases (id, user_id, plan, amount, currency, method, admin_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (p["id"], p["user_id"], p["plan"], p["amount"],
                 p.get("currency", "USD"), p.get("method", "manual"),
                 p.get("admin_id"), p.get("created_at", int(time.time())))
            )

        conn.commit()
        conn.close()
        save_backup()  # Обновляем JSON-бэкап
        logger.info(f"✅ БД восстановлена ({len(backup['users'])} users)")
        return True
    except Exception as e:
        conn.close()
        logger.error(f"Ошибка восстановления БД: {e}")
        return False


def restore_from_backup() -> bool:
    """
    Восстанавливает БД из JSON-бэкапа в cache/, если SQLite БД пуста.
    """
    backup = load_backup()
    if not backup or "users" not in backup:
        return False

    conn = get_conn()
    existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    if existing > 0:
        return False  # БД не пуста — не восстанавливаем

    return restore_db_from_dict(backup)
