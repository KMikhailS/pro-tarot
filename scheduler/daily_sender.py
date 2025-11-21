"""Планировщик отправки ежедневной карты дня"""

import asyncio
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database.db import get_users_for_daily_send, mark_daily_card_sent

logger = logging.getLogger(__name__)

# Константы для работы с картами
CARDS_BASE_DIR = Path("cards/images")
CARDS_DESC_DIR = Path("cards/description")
TOTAL_CARDS = 78  # Карты нумеруются от 0 до 77 (всего 78 карт Таро)

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

# Кэш для описаний карт: {card_number: (название, описание)}
_card_descriptions_cache: dict[int, tuple[str, str]] = {}


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


def generate_unique_cards(deck_type: str, count: int = 3) -> list[int]:
    """
    Генерирует список уникальных номеров карт для указанной колоды.

    Оптимизированная версия: сначала собирает список всех доступных карт,
    затем выбирает случайные из них. Это намного быстрее, чем проверка
    существования файла в цикле.

    Args:
        deck_type: Тип колоды ('alfons_mucha' или 'rider_waite')
        count: Количество карт для генерации (по умолчанию 3)

    Returns:
        Список уникальных номеров карт

    Raises:
        ValueError: Если недостаточно доступных карт в колоде
    """
    # Собираем список всех доступных карт для данной колоды
    available_cards = [
        i for i in range(TOTAL_CARDS)
        if get_card_path(deck_type, i) is not None
    ]

    if len(available_cards) < count:
        raise ValueError(
            f"Not enough cards available for deck {deck_type}: "
            f"required {count}, found {len(available_cards)}"
        )

    # Используем random.sample для эффективного выбора уникальных карт
    return random.sample(available_cards, count)


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


async def preload_card_descriptions():
    """
    Предзагрузить все описания карт в кэш при старте бота.

    Это значительно ускоряет отправку карт, так как не нужно
    читать файлы с диска каждый раз.
    """
    logger.info("Preloading card descriptions into cache...")

    loaded_count = 0
    for card_number in range(TOTAL_CARDS):
        desc_path = CARDS_DESC_DIR / f"{card_number}.txt"

        if desc_path.exists():
            try:
                import aiofiles
                async with aiofiles.open(desc_path, 'r', encoding='utf-8') as f:
                    content = await f.read()

                lines = content.strip().split('\n')
                if len(lines) >= 2:
                    card_name = lines[0].strip()
                    card_description = '\n'.join(lines[1:]).strip()
                    card_description_html = markdown_to_html(card_description)

                    _card_descriptions_cache[card_number] = (card_name, card_description_html)
                    loaded_count += 1
            except Exception as e:
                logger.warning(f"Failed to load description for card {card_number}: {e}")
        else:
            logger.debug(f"Description file not found for card {card_number}")

    logger.info(f"Preloaded {loaded_count} card descriptions into cache")


async def get_card_description(card_number: int) -> tuple[str, str]:
    """
    Получить название и описание карты.

    Сначала проверяет кэш, затем читает файл если описание не найдено.

    Args:
        card_number: Номер карты (0-77)

    Returns:
        Кортеж (название, полное описание с HTML разметкой)
    """
    # Проверяем кэш
    if card_number in _card_descriptions_cache:
        return _card_descriptions_cache[card_number]

    # Если не в кэше, читаем из файла (fallback)
    logger.debug(f"Cache miss for card {card_number}, reading from file")

    import aiofiles
    desc_path = CARDS_DESC_DIR / f"{card_number}.txt"

    try:
        async with aiofiles.open(desc_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            content = content.strip()

            if not content:
                return "", ""

            # Разделяем на строки
            lines = content.split('\n', 1)
            card_name = lines[0].strip()

            # Всё после первой строки — это полное описание
            card_desc = lines[1].strip() if len(lines) > 1 else ""

            # Конвертируем Markdown в HTML
            card_desc = markdown_to_html(card_desc)

            # Сохраняем в кэш для будущего использования
            _card_descriptions_cache[card_number] = (card_name, card_desc)

            return card_name, card_desc
    except FileNotFoundError:
        logger.warning(f"Description file not found: {desc_path}")
        return "", ""
    except Exception as e:
        logger.error(f"Error reading card description {card_number}: {e}")
        return "", ""


async def send_daily_card(bot: Bot, user_id: int, deck_type: str):
    """
    Отправить предложение выбора карты дня одному пользователю.

    Новая логика:
    1. Генерируем 3 случайные карты
    2. Показываем кнопки выбора
    3. Пользователь нажимает - получает карту

    Args:
        bot: Экземпляр бота
        user_id: ID пользователя Telegram
        deck_type: Тип колоды ('alfons_mucha' или 'rider_waite')
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from messages import CARD_CHOICE_TEXT

    try:
        # Генерируем 3 случайные уникальные карты
        try:
            cards = generate_unique_cards(deck_type, count=3)
        except ValueError as e:
            logger.error(
                f"Failed to generate cards for user {user_id}: {e}"
            )
            return

        # Формируем callback_data с номерами карт
        # Формат: card:daily:<user_id>:<card0>:<card1>:<card2>:<selected>
        keyboard = InlineKeyboardBuilder()

        for i in range(3):
            callback_data = f"card:daily:{user_id}:{cards[0]}:{cards[1]}:{cards[2]}:{i}"
            keyboard.button(
                text=f"✨ Карта {i + 1}",
                callback_data=callback_data
            )

        keyboard.adjust(1)  # По 1 кнопке в строку (вертикально)

        await bot.send_message(
            chat_id=user_id,
            text=CARD_CHOICE_TEXT,
            reply_markup=keyboard.as_markup()
        )

        logger.info(
            f"Card choice sent to user {user_id}: "
            f"cards={cards}, deck={deck_type}"
        )

    except Exception as e:
        logger.error(
            f"Failed to send card choice to user {user_id}: {e}",
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

    # Отправляем карты с rate limiting (максимум 20 параллельных запросов)
    # Это предотвращает превышение лимитов Telegram API (30 сообщений/сек)
    semaphore = asyncio.Semaphore(20)

    async def send_with_limit(user):
        async with semaphore:
            return await send_daily_card(bot, user['user_id'], user['deck_type'])

    tasks = [send_with_limit(user) for user in users]
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
            now = datetime.now(timezone.utc)
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


async def scheduled_daily_cards_job(bot: Bot):
    """
    APScheduler job для отправки ежедневных карт.
    Вызывается каждый час по расписанию.

    Args:
        bot: Экземпляр бота
    """
    try:
        utc_hour = datetime.now(timezone.utc).hour
        logger.info(
            f"APScheduler triggered daily cards sending for UTC hour {utc_hour}"
        )
        await process_daily_cards(bot, utc_hour)
    except Exception as e:
        logger.error(
            f"Error in scheduled daily cards job: {e}",
            exc_info=True
        )


def start_daily_sender_apscheduler(bot: Bot) -> AsyncIOScheduler:
    """
    Запустить планировщик отправки карт с использованием APScheduler.

    Планировщик выполняет отправку каждый час в 0 минут (UTC).

    Args:
        bot: Экземпляр бота для отправки сообщений

    Returns:
        AsyncIOScheduler instance для возможности graceful shutdown

    Example:
        >>> scheduler = start_daily_sender_apscheduler(bot)
        >>> # Бот работает...
        >>> scheduler.shutdown(wait=True)  # При остановке
    """
    scheduler = AsyncIOScheduler(timezone='UTC')

    # Добавляем задачу: каждый час в 0 минут
    scheduler.add_job(
        scheduled_daily_cards_job,
        trigger=CronTrigger(minute=0, timezone='UTC'),
        args=[bot],
        id='daily_cards_sender',
        name='Send daily tarot cards to subscribed users',
        max_instances=1,  # Предотвращаем параллельное выполнение
        replace_existing=True,
        misfire_grace_time=120,  # Если пропустили, выполнить в течение 2 минут
        coalesce=True  # Объединить пропущенные запуски в один
    )

    scheduler.start()

    logger.info(
        "APScheduler started for daily cards sending "
        "(schedule: every hour at minute 0, UTC)"
    )

    return scheduler
