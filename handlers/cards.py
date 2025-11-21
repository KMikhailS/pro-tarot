"""Обработчики команд для работы с картами Таро"""

import logging
import aiofiles
from datetime import date
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.db import get_user_deck, add_user, mark_daily_card_sent, can_receive_daily_card, get_user_role
from scheduler.daily_sender import (
    get_random_card_number,
    get_card_path,
    get_card_description,
    generate_unique_cards,
)
from messages import CARD_CHOICE_TEXT_ONDEMAND

logger = logging.getLogger(__name__)

router = Router()


async def send_card_to_user(
    bot: Bot,
    user_id: int,
    card_number: int,
    deck_type: str,
    is_daily: bool = False
) -> bool:
    """
    Отправить карту пользователю.

    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        card_number: Номер карты (0-77)
        deck_type: Тип колоды
        is_daily: Является ли это ежедневной картой

    Returns:
        True если карта успешно отправлена
    """
    try:
        card_path = get_card_path(deck_type, card_number)

        if card_path is None:
            logger.error(f"Card path not found: {card_number}, deck={deck_type}")
            return False

        # Получаем описание карты
        card_name, card_desc = await get_card_description(card_number)

        # Асинхронно читаем файл изображения
        async with aiofiles.open(card_path, 'rb') as f:
            photo_bytes = await f.read()

        # Создаём BufferedInputFile из байтов
        photo = BufferedInputFile(photo_bytes, filename=card_path.name)

        # Формируем подпись с названием и описанием
        today = date.today()
        formatted_date = today.strftime("%d.%m.%Y")

        if is_daily:
            # Для карты дня
            caption = f"✨ Твоя карта дня на сегодня ({formatted_date}) — <b>{card_name}</b>\n\n"
        else:
            # Для карты по запросу /get_card - тоже с датой
            caption = f"✨ Твоя карта дня на сегодня ({formatted_date}) — <b>{card_name}</b>\n\n"

        if card_desc:
            caption += f"{card_desc}"

        await bot.send_photo(
            chat_id=user_id,
            photo=photo,
            caption=caption,
            parse_mode='HTML'
        )

        # Если это ежедневная карта - отмечаем в БД
        if is_daily:
            await mark_daily_card_sent(user_id)

        logger.info(
            f"Card sent to user {user_id}: "
            f"card={card_number}, deck={deck_type}, daily={is_daily}"
        )

        return True

    except Exception as e:
        logger.error(
            f"Failed to send card to user {user_id}: {e}",
            exc_info=True
        )
        return False


@router.callback_query(F.data.startswith("card:daily:"))
async def callback_card_daily_choice(callback: CallbackQuery, bot: Bot):
    """
    Обработчик выбора карты дня из 3 предложенных.

    Формат callback_data: card:daily:<user_id>:<card0>:<card1>:<card2>:<selected>
    """
    try:
        # Парсим callback_data
        parts = callback.data.split(":")
        if len(parts) != 7:
            await callback.answer("Ошибка формата данных", show_alert=True)
            return

        _, action, str_user_id, card0, card1, card2, selected = parts

        # Проверяем, что пользователь тот же самый
        if int(str_user_id) != callback.from_user.id:
            await callback.answer(
                "Это не твоя карта! Используй /get_card для своей карты.",
                show_alert=True
            )
            return

        # Проверяем, можно ли еще получить карту сегодня
        if not await can_receive_daily_card(callback.from_user.id):
            await callback.answer(
                "Вы уже получили карту дня сегодня! Приходите завтра.",
                show_alert=True
            )
            return

        # Получаем выбранную карту
        cards = [int(card0), int(card1), int(card2)]
        selected_index = int(selected)
        selected_card = cards[selected_index]

        # Получаем тип колоды пользователя
        deck_type = await get_user_deck(callback.from_user.id)

        # Удаляем сообщение с кнопками
        await callback.message.delete()

        # Отправляем карту
        success = await send_card_to_user(
            bot,
            callback.from_user.id,
            selected_card,
            deck_type,
            is_daily=True
        )

        if success:
            await callback.answer("Карта отправлена!")
        else:
            await callback.answer(
                "Произошла ошибка при отправке карты",
                show_alert=True
            )

    except Exception as e:
        logger.error(f"Error in callback_card_daily_choice: {e}", exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("card:ondemand:"))
async def callback_card_ondemand_choice(callback: CallbackQuery, bot: Bot):
    """
    Обработчик выбора карты по команде /get_card.

    Формат callback_data: card:ondemand:<user_id>:<card0>:<card1>:<card2>:<selected>
    """
    try:
        # Парсим callback_data
        parts = callback.data.split(":")
        if len(parts) != 7:
            await callback.answer("Ошибка формата данных", show_alert=True)
            return

        _, action, str_user_id, card0, card1, card2, selected = parts

        # Проверяем, что пользователь тот же самый
        if int(str_user_id) != callback.from_user.id:
            await callback.answer(
                "Это не твоя карта! Используй /get_card для своей карты.",
                show_alert=True
            )
            return

        # Получаем выбранную карту
        cards = [int(card0), int(card1), int(card2)]
        selected_index = int(selected)
        selected_card = cards[selected_index]

        # Получаем тип колоды пользователя
        deck_type = await get_user_deck(callback.from_user.id)

        # Удаляем сообщение с кнопками
        await callback.message.delete()

        # Отправляем карту (НЕ ежедневную)
        success = await send_card_to_user(
            bot,
            callback.from_user.id,
            selected_card,
            deck_type,
            is_daily=False
        )

        if success:
            await callback.answer("Карта отправлена!")
        else:
            await callback.answer(
                "Произошла ошибка при отправке карты",
                show_alert=True
            )

    except Exception as e:
        logger.error(f"Error in callback_card_ondemand_choice: {e}", exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)


@router.message(Command("get_card"))
async def cmd_get_card(message: Message, bot: Bot):
    """
    Обработчик команды /get_card - показать выбор из 3 карт.

    В отличие от автоматической отправки, эта команда не обновляет
    last_daily_card_date, чтобы не мешать запланированной отправке.

    Команда доступна только пользователям с ролью ADMIN.
    """
    # Убеждаемся, что пользователь есть в БД
    await add_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    # Проверяем роль пользователя
    user_role = await get_user_role(message.from_user.id)
    if user_role != 'ADMIN':
        # Пропускаем выполнение команды без уведомления пользователя
        return

    # Получаем тип колоды пользователя
    deck_type = await get_user_deck(message.from_user.id)

    try:
        # Генерируем 3 случайные уникальные карты
        try:
            cards = generate_unique_cards(deck_type, count=3)
        except ValueError as e:
            logger.error(
                f"Failed to generate cards for user {message.from_user.id}: {e}"
            )
            await message.answer(
                "😔 Извините, не удалось сгенерировать карты. "
                "Попробуйте выбрать другую колоду в /settings"
            )
            return

        # Формируем callback_data с номерами карт
        # Формат: card:ondemand:<user_id>:<card0>:<card1>:<card2>:<selected>
        keyboard = InlineKeyboardBuilder()

        for i in range(3):
            callback_data = f"card:ondemand:{message.from_user.id}:{cards[0]}:{cards[1]}:{cards[2]}:{i}"
            keyboard.button(
                text=f"✨ Карта {i + 1}",
                callback_data=callback_data
            )

        keyboard.adjust(1)  # По 1 кнопке в строку (вертикально)

        await message.answer(
            CARD_CHOICE_TEXT_ONDEMAND,
            reply_markup=keyboard.as_markup()
        )

        logger.info(
            f"Card choice sent to user {message.from_user.id} via /get_card: "
            f"cards={cards}, deck={deck_type}"
        )

    except Exception as e:
        logger.error(
            f"Failed to send card choice to user {message.from_user.id}: {e}",
            exc_info=True
        )
        await message.answer(
            "😔 Произошла ошибка при отправке карты. Попробуйте позже."
        )


@router.callback_query(F.data == "start:get_card")
async def callback_start_get_card(callback: CallbackQuery, bot: Bot):
    """
    Обработчик нажатия на кнопку 'Получить карту дня' в /start.
    Вызывает функционал команды /get_card.
    """
    # Убеждаемся, что пользователь есть в БД
    await add_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    # Проверяем роль пользователя
    user_role = await get_user_role(callback.from_user.id)
    if user_role != 'ADMIN':
        # Для обычных пользователей проверяем, можно ли получить карту дня
        if not await can_receive_daily_card(callback.from_user.id):
            await callback.answer(
                "Вы уже получили карту дня сегодня!",
                show_alert=True
            )
            return

    # Получаем тип колоды пользователя
    deck_type = await get_user_deck(callback.from_user.id)

    try:
        # Генерируем 3 случайные уникальные карты
        try:
            cards = generate_unique_cards(deck_type, count=3)
        except ValueError as e:
            logger.error(
                f"Failed to generate cards for user {callback.from_user.id}: {e}"
            )
            await callback.answer(
                "😔 Не удалось сгенерировать карты. Попробуйте выбрать другую колоду в /settings",
                show_alert=True
            )
            return

        # Формируем callback_data с номерами карт
        # Для обычных пользователей используем daily, для ADMIN - ondemand
        # Формат: card:daily/ondemand:<user_id>:<card0>:<card1>:<card2>:<selected>
        keyboard = InlineKeyboardBuilder()

        card_type = "daily" if user_role != 'ADMIN' else "ondemand"

        for i in range(3):
            callback_data = f"card:{card_type}:{callback.from_user.id}:{cards[0]}:{cards[1]}:{cards[2]}:{i}"
            keyboard.button(
                text=f"✨ Карта {i + 1}",
                callback_data=callback_data
            )

        keyboard.adjust(1)  # По 1 кнопке в строку (вертикально)

        await callback.message.answer(
            CARD_CHOICE_TEXT_ONDEMAND,
            reply_markup=keyboard.as_markup()
        )

        await callback.answer()

        logger.info(
            f"Card choice sent to user {callback.from_user.id} via start button: "
            f"cards={cards}, deck={deck_type}"
        )

    except Exception as e:
        logger.error(
            f"Failed to send card choice to user {callback.from_user.id}: {e}",
            exc_info=True
        )
        await callback.answer(
            "😔 Произошла ошибка при отправке карты. Попробуйте позже.",
            show_alert=True
        )
