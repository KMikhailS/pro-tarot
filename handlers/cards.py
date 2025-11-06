"""Обработчики команд для работы с картами Таро"""

import logging
import aiofiles
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile

from database.db import get_user_deck, add_user
from scheduler.daily_sender import (
    get_random_card_number,
    get_card_path,
    get_card_description,
)

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("get_card"))
async def cmd_get_card(message: Message, bot: Bot):
    """
    Обработчик команды /get_card - отправить карту дня по запросу.

    В отличие от автоматической отправки, эта команда не обновляет
    last_daily_card_date, чтобы не мешать запланированной отправке.
    """
    # Убеждаемся, что пользователь есть в БД
    await add_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    # Получаем тип колоды пользователя
    deck_type = await get_user_deck(message.from_user.id)

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
                f"Failed to find any valid card for user {message.from_user.id} "
                f"with deck {deck_type} after {max_attempts} attempts"
            )
            await message.answer(
                "😔 Извините, не удалось найти карту. "
                "Попробуйте выбрать другую колоду в /settings"
            )
            return

        # Получаем описание карты
        card_name, card_desc = await get_card_description(card_number)

        # Асинхронно читаем файл изображения
        async with aiofiles.open(card_path, 'rb') as f:
            photo_bytes = await f.read()

        # Создаём BufferedInputFile из байтов
        photo = BufferedInputFile(photo_bytes, filename=card_path.name)

        # Формируем подпись с названием и описанием
        caption = f"🔮 Ваша карта: {card_name}\n\n"
        if card_desc:
            caption += f"{card_desc}"

        await bot.send_photo(
            chat_id=message.from_user.id,
            photo=photo,
            caption=caption,
            parse_mode='HTML'
        )

        logger.info(
            f"Card sent to user {message.from_user.id} via /get_card: "
            f"card={card_number}, deck={deck_type}"
        )

    except Exception as e:
        logger.error(
            f"Failed to send card to user {message.from_user.id}: {e}",
            exc_info=True
        )
        await message.answer(
            "😔 Произошла ошибка при отправке карты. Попробуйте позже."
        )
