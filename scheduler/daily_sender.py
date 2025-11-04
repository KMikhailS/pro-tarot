"""Планировщик отправки ежедневной карты дня"""

import asyncio
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiogram import Bot
from aiogram.types import FSInputFile

from database.db import get_users_for_daily_send, mark_daily_card_sent

logger = logging.getLogger(__name__)

# Константы для работы с картами
CARDS_BASE_DIR = Path("cards/images")
CARDS_DESC_DIR = Path("cards/description")
# TOTAL_CARDS = 78  # Карты нумеруются от 0 до 77 (всего 78 карт Таро)
TOTAL_CARDS = 21  # Карты нумеруются от 0 до 77 (всего 78 карт Таро)

# Маппинг типов колод на папки
DECK_FOLDERS = {
    'alfons_mucha': 'alfons',
    'rider_waite': 'raider',
}

# Названия колод для отображения
DECK_NAMES = {
    'alfons_mucha': 'Колода Альфонса Мухи',
    'rider_waite': 'Колода Райдера-Уэйта',
}


def get_card_path(deck_type: str, card_number: int) -> Optional[Path]:
    """
    Получить путь к файлу карты.

    Args:
        deck_type: Тип колоды ('alfons_mucha' или 'rider_waite')
        card_number: Номер карты (0-77)

    Returns:
        Path к файлу карты или None если карта не найдена
    """
    folder = DECK_FOLDERS.get(deck_type, 'alfons')
    card_path = CARDS_BASE_DIR / folder / f"{card_number}.jpg"

    if card_path.exists():
        return card_path

    logger.warning(f"Card file not found: {card_path}")
    return None


def get_random_card_number() -> int:
    """
    Получить случайный номер карты от 0 до 77.

    Returns:
        Случайное число в диапазоне [0, 77]
    """
    return random.randint(0, TOTAL_CARDS - 1)


def markdown_to_html(text: str) -> str:
    """
    Конвертирует Markdown разметку в HTML для Telegram.

    Args:
        text: Текст с Markdown разметкой

    Returns:
        Текст с HTML разметкой
    """
    import re
    # Заменяем **текст** на <b>текст</b>
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    return text


def get_card_description(card_number: int) -> tuple[str, str]:
    """
    Получить название и описание карты из файла.

    Args:
        card_number: Номер карты (0-77)

    Returns:
        Кортеж (название, полное описание с HTML разметкой)
    """
    desc_path = CARDS_DESC_DIR / f"{card_number}.txt"

    try:
        with open(desc_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return "", ""

            # Разделяем на строки
            lines = content.split('\n', 1)
            card_name = lines[0].strip()

            # Всё после первой строки — это полное описание
            card_desc = lines[1].strip() if len(lines) > 1 else ""

            # Конвертируем Markdown в HTML
            card_desc = markdown_to_html(card_desc)

            return card_name, card_desc
    except FileNotFoundError:
        logger.warning(f"Description file not found: {desc_path}")
        return "", ""
    except Exception as e:
        logger.error(f"Error reading card description {card_number}: {e}")
        return "", ""


async def send_daily_card(bot: Bot, user_id: int, deck_type: str):
    """
    Отправить карту дня одному пользователю.

    Args:
        bot: Экземпляр бота
        user_id: ID пользователя Telegram
        deck_type: Тип колоды ('alfons_mucha' или 'rider_waite')
    """
    try:
        # Выбираем случайную карту
        card_number = get_random_card_number()
        card_path = get_card_path(deck_type, card_number)

        # Если карта не найдена, пробуем найти другую
        max_attempts = 10
        attempt = 0
        while card_path is None and attempt < max_attempts:
            card_number = get_random_card_number()
            card_path = get_card_path(deck_type, card_number)
            attempt += 1

        if card_path is None:
            logger.error(
                f"Failed to find any valid card for user {user_id} "
                f"with deck {deck_type} after {max_attempts} attempts"
            )
            return

        # Получаем описание карты
        card_name, card_desc = get_card_description(card_number)

        # Отправляем карту
        photo = FSInputFile(card_path)

        # Формируем подпись с названием и описанием
        caption = f"🌅 Ваша карта дня: {card_name}\n\n"
        if card_desc:
            caption += f"{card_desc}"

        await bot.send_photo(
            chat_id=user_id,
            photo=photo,
            caption=caption,
            parse_mode='HTML'
        )

        # Отмечаем отправку в БД
        await mark_daily_card_sent(user_id)

        logger.info(
            f"Daily card sent to user {user_id}: "
            f"card={card_number}, deck={deck_type}"
        )

    except Exception as e:
        logger.error(
            f"Failed to send daily card to user {user_id}: {e}",
            exc_info=True
        )


async def process_daily_cards(bot: Bot, utc_hour: int):
    """
    Обработать отправку карт всем пользователям для указанного UTC часа.

    Args:
        bot: Экземпляр бота
        utc_hour: Час UTC (0-23)
    """
    logger.info(f"Processing daily cards for UTC hour {utc_hour}")

    users = await get_users_for_daily_send(utc_hour)

    if not users:
        logger.debug(f"No users to send for UTC hour {utc_hour}")
        return

    logger.info(
        f"Found {len(users)} users to send daily card at UTC hour {utc_hour}"
    )

    # Отправляем карты параллельно
    tasks = [
        send_daily_card(bot, user['user_id'], user['deck_type'])
        for user in users
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Логируем ошибки, если они были
    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        logger.warning(f"{len(errors)} errors occurred while sending daily cards")

    logger.info(
        f"Finished processing daily cards for UTC hour {utc_hour}: "
        f"{len(users) - len(errors)} sent successfully, {len(errors)} failed"
    )


async def daily_sender_loop(bot: Bot):
    """
    Основной цикл планировщика.
    Проверяет каждую минуту, не начался ли новый час для отправки карт.
    """
    logger.info("Daily sender loop started")

    last_processed_hour = None

    while True:
        try:
            now = datetime.utcnow()
            current_hour = now.hour
            current_minute = now.minute

            # Отправляем карты только в начале часа (первые 2 минуты)
            # и только если этот час еще не обрабатывали
            if current_minute < 2 and current_hour != last_processed_hour:
                logger.info(
                    f"Triggering daily card processing: "
                    f"UTC time is {now.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                await process_daily_cards(bot, current_hour)
                last_processed_hour = current_hour

            # Проверяем каждую минуту
            await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"Error in daily sender loop: {e}", exc_info=True)
            await asyncio.sleep(60)


def start_daily_sender(bot: Bot):
    """
    Запустить планировщик отправки в фоновой задаче.

    Usage:
        asyncio.create_task(start_daily_sender(bot))

    Returns:
        asyncio.Task объект фоновой задачи
    """
    logger.info("Starting daily sender task")
    return asyncio.create_task(daily_sender_loop(bot))
