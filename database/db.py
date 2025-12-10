"""Модуль работы с SQLite базой данных"""

import aiosqlite
from typing import Optional, List
from datetime import datetime, date

DB_PATH = "tarot_bot.db"


async def init_db():
    """Инициализация базы данных с таблицей users"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TEXT NOT NULL,
                daily_card_enabled INTEGER DEFAULT 1,
                send_hour INTEGER,
                timezone_offset INTEGER DEFAULT 0,
                deck_type TEXT DEFAULT 'rider_waite',
                last_daily_card_date TEXT,
                role VARCHAR(32) DEFAULT 'USER'
            )
        """)

        # Индекс для быстрого поиска пользователей для отправки
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_daily_send
            ON users(daily_card_enabled, send_hour, last_daily_card_date)
            WHERE daily_card_enabled = 1
        """)

        # Миграция: добавить поле role если его нет
        async with db.execute("PRAGMA table_info(users)") as cursor:
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            if 'role' not in column_names:
                await db.execute("ALTER TABLE users ADD COLUMN role VARCHAR(32) DEFAULT 'USER'")

        await db.commit()


async def add_user(user_id: int, username: Optional[str], first_name: Optional[str]):
    """
    Добавить нового пользователя или обновить существующего.

    Настройки по умолчанию для новых пользователей:
    - daily_card_enabled = 1 (включено)
    - timezone_offset = 180 (GMT+3)
    - deck_type = 'alfons_mucha' (Колода Альфонса Мухи)
    - send_hour = 8 (08:00)
    - role = 'USER'

    При повторном входе (пользователь уже существует):
    - Обновляется username и first_name
    - daily_card_enabled автоматически включается обратно (полезно если бот был заблокирован)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (user_id, username, first_name, created_at, daily_card_enabled, timezone_offset, deck_type, send_hour, role)
               VALUES (?, ?, ?, ?, 1, 180, 'alfons_mucha', 8, 'USER')
               ON CONFLICT(user_id) DO UPDATE SET
                   username = excluded.username,
                   first_name = excluded.first_name,
                   daily_card_enabled = 1""",
            (user_id, username, first_name, datetime.now().isoformat())
        )
        await db.commit()


async def get_user_settings(user_id: int) -> Optional[dict]:
    """Получить настройки пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT user_id, daily_card_enabled, send_hour,
                      timezone_offset, deck_type, last_daily_card_date
               FROM users WHERE user_id = ?""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None


async def toggle_daily_card(user_id: int) -> bool:
    """Переключить статус ежедневной карты. Возвращает новый статус."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT daily_card_enabled FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            current_status = row[0] if row else 0

        new_status = 0 if current_status else 1

        await db.execute(
            "UPDATE users SET daily_card_enabled = ? WHERE user_id = ?",
            (new_status, user_id)
        )
        await db.commit()
        return bool(new_status)


async def get_user_send_hour(user_id: int) -> Optional[int]:
    """Получить час отправки пользователя (0-23)"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT send_hour FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def update_user_send_hour(user_id: int, hour: int):
    """Обновить час отправки (0-23)"""
    if not 0 <= hour <= 23:
        raise ValueError("Hour must be between 0 and 23")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET send_hour = ? WHERE user_id = ?",
            (hour, user_id)
        )
        await db.commit()


async def get_user_deck(user_id: int) -> str:
    """Получить тип колоды пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT deck_type FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "rider_waite"


async def update_user_deck(user_id: int, deck_type: str):
    """Обновить тип колоды"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET deck_type = ? WHERE user_id = ?",
            (deck_type, user_id)
        )
        await db.commit()


async def update_user_timezone(user_id: int, timezone_offset: int):
    """
    Обновить часовой пояс пользователя

    Args:
        user_id: ID пользователя
        timezone_offset: Смещение относительно UTC в минутах (от -720 до +840)
    """
    # Валидация: от -12 часов (-720 минут) до +14 часов (+840 минут)
    if not -720 <= timezone_offset <= 840:
        raise ValueError("Timezone offset must be between -720 and +840 minutes (-12 to +14 hours)")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET timezone_offset = ? WHERE user_id = ?",
            (timezone_offset, user_id)
        )
        await db.commit()


async def get_users_for_daily_send(utc_hour: int) -> List[dict]:
    """
    Получить список пользователей для отправки карты дня в указанный UTC час.

    Логика расчета:
    - Пользователь хранит send_hour в своем локальном времени
    - timezone_offset - смещение от UTC в минутах (например, UTC+3 = 180)
    - Чтобы найти пользователей для текущего UTC часа, мы ищем тех,
      у кого (send_hour - timezone_offset/60) = utc_hour

    Пример:
    - Пользователь в UTC+3 хочет получать карту в 09:00 локально
    - send_hour = 9, timezone_offset = 180
    - Когда UTC время 06:00, мы ищем: 9 - 180/60 = 9 - 3 = 6 ✓

    Args:
        utc_hour: Текущий час UTC (0-23)

    Returns:
        Список словарей с информацией о пользователях
    """
    today = date.today().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT user_id, deck_type, timezone_offset, send_hour
               FROM users
               WHERE daily_card_enabled = 1
               AND send_hour IS NOT NULL
               AND (last_daily_card_date IS NULL OR last_daily_card_date != ?)
               AND (send_hour - CAST(timezone_offset AS FLOAT) / 60) = ?""",
            (today, utc_hour)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def mark_daily_card_sent(user_id: int):
    """Отметить, что карта дня отправлена сегодня"""
    today = date.today().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_daily_card_date = ? WHERE user_id = ?",
            (today, user_id)
        )
        await db.commit()


async def can_receive_daily_card(user_id: int) -> bool:
    """
    Проверить, может ли пользователь получить карту дня сегодня.

    Args:
        user_id: ID пользователя

    Returns:
        True если пользователь еще не получал карту сегодня
    """
    today = date.today().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT last_daily_card_date FROM users
               WHERE user_id = ?""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return True

            last_date = row[0]
            # Если last_daily_card_date NULL или не сегодня - можно получить
            return last_date is None or last_date != today


async def get_user_role(user_id: int) -> Optional[str]:
    """
    Получить роль пользователя.

    Args:
        user_id: ID пользователя

    Returns:
        Роль пользователя или None если пользователь не найден
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT role FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def disable_daily_card(user_id: int):
    """
    Отключить ежедневную отправку карт для пользователя.
    Используется когда бот заблокирован пользователем.

    Args:
        user_id: ID пользователя
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET daily_card_enabled = 0 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()
