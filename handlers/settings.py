"""Обработчик команды /settings и управление настройками"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timedelta

from database.db import (
    add_user,
    get_user_settings,
    toggle_daily_card,
    update_user_send_hour,
    get_user_send_hour,
    update_user_deck,
    get_user_deck,
    update_user_timezone
)
from messages import (
    SETTINGS_TEXT,
    TIME_SELECTION_TEXT,
    DECK_SELECTION_TEXT,
    TIME_SET_SUCCESS,
    DECK_SET_SUCCESS,
    DAILY_ENABLED_TEXT,
    DAILY_DISABLED_TEXT,
    ABOUT_TEXT,
    TIMEZONE_SELECTION_TEXT,
    TIMEZONE_SET_SUCCESS
)

router = Router()


def format_timezone(offset_minutes: int) -> str:
    """
    Форматировать часовой пояс в формат GMT+X или GMT-X

    Args:
        offset_minutes: Смещение в минутах

    Returns:
        Строка вида "GMT+3", "GMT-5", "GMT+0"
    """
    offset_hours = offset_minutes // 60
    if offset_hours >= 0:
        return f"GMT+{offset_hours}"
    else:
        return f"GMT{offset_hours}"


def calculate_user_local_time(timezone_offset_minutes: int) -> str:
    """
    Рассчитать локальное время пользователя

    Args:
        timezone_offset_minutes: Смещение часового пояса в минутах

    Returns:
        Строка времени в формате "HH:MM"
    """
    utc_now = datetime.utcnow()
    # Добавляем смещение в минутах
    local_time = utc_now + timedelta(minutes=timezone_offset_minutes)
    return local_time.strftime("%H:%M")


# ============== Главное меню настроек ==============

@router.message(Command("settings"))
async def cmd_settings(message: Message):
    """Команда /settings - показать главное меню настроек"""
    await add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

    settings = await get_user_settings(message.from_user.id)

    # Формируем информацию о текущих настройках
    daily_status = "✅ Включено" if settings and settings['daily_card_enabled'] else "❌ Выключено"
    send_hour = settings['send_hour'] if settings and settings['send_hour'] is not None else "Не установлено"
    if isinstance(send_hour, int):
        send_hour = f"{send_hour:02d}:00"

    deck_names = {
        'alfons_mucha': '🎴 Колода Альфонса Мухи',
        'rider_waite': '🎴 Колода Райдера-Уэйта'
    }
    deck_type = deck_names.get(settings['deck_type'], '🎴 Колода Райдера-Уэйта') if settings else '🎴 Колода Райдера-Уэйта'

    # Форматируем часовой пояс
    timezone_offset = settings['timezone_offset'] if settings else 180
    timezone = format_timezone(timezone_offset)

    text = SETTINGS_TEXT.format(
        daily_status=daily_status,
        send_hour=send_hour,
        deck_type=deck_type,
        timezone=timezone
    )

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎴 Выбор колоды", callback_data="settings:deck")
    keyboard.button(text="🌅 Карта дня", callback_data="settings:daily_menu")
    keyboard.button(text="🔙 Главное меню", callback_data="menu:main")
    keyboard.adjust(1)

    await message.answer(text, reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "settings:main")
async def callback_settings_main(callback: CallbackQuery):
    """Вернуться в главное меню настроек"""
    settings = await get_user_settings(callback.from_user.id)

    daily_status = "✅ Включено" if settings and settings['daily_card_enabled'] else "❌ Выключено"
    send_hour = settings['send_hour'] if settings and settings['send_hour'] is not None else "Не установлено"
    if isinstance(send_hour, int):
        send_hour = f"{send_hour:02d}:00"

    deck_names = {
        'alfons_mucha': '🎴 Колода Альфонса Мухи',
        'rider_waite': '🎴 Колода Райдера-Уэйта'
    }
    deck_type = deck_names.get(settings['deck_type'], '🎴 Колода Райдера-Уэйта') if settings else '🎴 Колода Райдера-Уэйта'

    # Форматируем часовой пояс
    timezone_offset = settings['timezone_offset'] if settings else 180
    timezone = format_timezone(timezone_offset)

    text = SETTINGS_TEXT.format(
        daily_status=daily_status,
        send_hour=send_hour,
        deck_type=deck_type,
        timezone=timezone
    )

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎴 Выбор колоды", callback_data="settings:deck")
    keyboard.button(text="🌅 Карта дня", callback_data="settings:daily_menu")
    keyboard.button(text="🔙 Главное меню", callback_data="menu:main")
    keyboard.adjust(1)

    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def callback_main_menu(callback: CallbackQuery):
    """Вернуться в главное меню бота"""
    await callback.message.edit_text(ABOUT_TEXT)
    await callback.answer()


# ============== Подменю "Карта дня" ==============

@router.callback_query(F.data == "settings:daily_menu")
async def callback_daily_menu(callback: CallbackQuery):
    """Показать подменю настроек карты дня"""
    settings = await get_user_settings(callback.from_user.id)

    daily_enabled = settings and settings['daily_card_enabled']
    daily_status = "✅ Включено" if daily_enabled else "❌ Выключено"
    send_hour = settings['send_hour'] if settings and settings['send_hour'] is not None else "Не установлено"
    if isinstance(send_hour, int):
        send_hour = f"{send_hour:02d}:00"

    # Форматируем часовой пояс
    timezone_offset = settings['timezone_offset'] if settings else 180
    timezone = format_timezone(timezone_offset)

    text = f"🌅 *Настройки карты дня*\n\n"
    text += f"Статус: {daily_status}\n"
    text += f"Время отправки: {send_hour}\n"
    text += f"Часовой пояс: {timezone}\n\n"
    text += "Выберите действие:"

    # Динамический текст кнопки toggle
    toggle_button_text = "❌ Выключить" if daily_enabled else "✅ Включить"

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text=toggle_button_text, callback_data="settings:daily_toggle")
    keyboard.button(text="⏰ Время отправки", callback_data="settings:time")
    keyboard.button(text="🌍 Часовой пояс", callback_data="settings:timezone")
    keyboard.button(text="🔙 Назад в настройки", callback_data="settings:main")
    keyboard.adjust(1)

    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    await callback.answer()


# ============== Переключение ежедневной карты ==============

@router.callback_query(F.data == "settings:daily_toggle")
async def callback_daily_toggle(callback: CallbackQuery):
    """Переключить статус ежедневной карты"""
    new_status = await toggle_daily_card(callback.from_user.id)

    if new_status:
        text = DAILY_ENABLED_TEXT
    else:
        text = DAILY_DISABLED_TEXT

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🔙 Назад к настройкам карты дня", callback_data="settings:daily_menu")

    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    await callback.answer()


# ============== Выбор времени отправки (только час) ==============

@router.callback_query(F.data == "settings:time")
async def callback_time_selection(callback: CallbackQuery):
    """Показать выбор часа отправки"""
    current_hour = await get_user_send_hour(callback.from_user.id)

    text = TIME_SELECTION_TEXT
    if current_hour is not None:
        text += f"\n\nТекущее время: {current_hour:02d}:00"

    # Создаем клавиатуру с 24 часами (0-23) в 4 строки по 6 кнопок
    keyboard = InlineKeyboardBuilder()

    for hour in range(24):
        # Отмечаем текущий выбранный час
        button_text = f"{'✓ ' if hour == current_hour else ''}{hour:02d}:00"
        keyboard.button(text=button_text, callback_data=f"time:select:{hour}")

    keyboard.button(text="🔙 Назад к настройкам карты дня", callback_data="settings:daily_menu")
    keyboard.adjust(6, 6, 6, 6, 1)  # 4 строки по 6 часов + кнопка назад

    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("time:select:"))
async def callback_time_select(callback: CallbackQuery):
    """Обработка выбора часа"""
    hour = int(callback.data.split(":")[2])

    await update_user_send_hour(callback.from_user.id, hour)

    text = TIME_SET_SUCCESS.format(hour=f"{hour:02d}:00")

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🔙 Назад к настройкам карты дня", callback_data="settings:daily_menu")

    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    await callback.answer("Время установлено!")


# ============== Настройка часового пояса ==============

@router.callback_query(F.data == "settings:timezone")
async def callback_timezone_selection(callback: CallbackQuery):
    """Показать экран автоопределения часового пояса"""
    settings = await get_user_settings(callback.from_user.id)

    # Получаем текущий timezone offset
    timezone_offset = settings['timezone_offset'] if settings else 180
    timezone = format_timezone(timezone_offset)

    # Рассчитываем локальное время пользователя
    user_local_time = calculate_user_local_time(timezone_offset)

    text = TIMEZONE_SELECTION_TEXT.format(
        timezone=timezone,
        local_time=user_local_time
    )

    # Создаем клавиатуру с 24 часами (00-23) в 4 строки по 6 кнопок
    keyboard = InlineKeyboardBuilder()

    for hour in range(24):
        button_text = f"{hour:02d}"
        keyboard.button(text=button_text, callback_data=f"timezone:set:{hour}")

    keyboard.button(text="🔙 Назад", callback_data="settings:daily_menu")
    keyboard.adjust(6, 6, 6, 6, 1)  # 4 строки по 6 часов + кнопка назад

    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("timezone:set:"))
async def callback_timezone_set(callback: CallbackQuery):
    """
    Обработка выбора часа для автоопределения часового пояса

    Логика:
    1. Пользователь указывает, какой час у него сейчас
    2. Мы знаем текущий UTC час
    3. Вычисляем разницу: offset = user_hour - utc_hour
    4. Учитываем переход через полночь
    5. Сохраняем offset в минутах
    """
    user_hour = int(callback.data.split(":")[2])

    # Получаем текущий UTC час
    utc_now = datetime.utcnow()
    utc_hour = utc_now.hour

    # Вычисляем смещение
    offset_hours = user_hour - utc_hour

    # Учитываем переход через полночь (нормализуем к диапазону -12...+12)
    if offset_hours > 12:
        offset_hours -= 24
    elif offset_hours < -12:
        offset_hours += 24

    # Конвертируем в минуты
    timezone_offset = offset_hours * 60

    # Валидация диапазона (от -12 до +14 часов)
    if -720 <= timezone_offset <= 840:
        await update_user_timezone(callback.from_user.id, timezone_offset)

        timezone_str = format_timezone(timezone_offset)
        text = TIMEZONE_SET_SUCCESS.format(timezone=timezone_str)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🔙 Назад к настройкам карты дня", callback_data="settings:daily_menu")

        await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
        await callback.answer("Часовой пояс установлен!")
    else:
        # Если вышли за пределы допустимого диапазона (что маловероятно)
        await callback.answer("Ошибка: недопустимый часовой пояс", show_alert=True)


# ============== Выбор колоды ==============

@router.callback_query(F.data == "settings:deck")
async def callback_deck_selection(callback: CallbackQuery):
    """Показать выбор колоды"""
    current_deck = await get_user_deck(callback.from_user.id)

    keyboard = InlineKeyboardBuilder()

    decks = [
        ("alfons_mucha", "Колода Альфонса Мухи"),
        ("rider_waite", "Колода Райдера-Уэйта"),
    ]

    for deck_id, deck_name in decks:
        # Отмечаем текущую колоду
        button_text = f"{'✓ ' if deck_id == current_deck else ''}{deck_name}"
        keyboard.button(text=button_text, callback_data=f"deck:select:{deck_id}")

    keyboard.button(text="🔙 Назад в настройки", callback_data="settings:main")
    keyboard.adjust(1)

    await callback.message.edit_text(DECK_SELECTION_TEXT, reply_markup=keyboard.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("deck:select:"))
async def callback_deck_select(callback: CallbackQuery):
    """Обработка выбора колоды"""
    deck_type = callback.data.split(":")[2]

    await update_user_deck(callback.from_user.id, deck_type)

    deck_names = {
        'alfons_mucha': 'Колода Альфонса Мухи',
        'rider_waite': 'Колода Райдера-Уэйта',
    }

    text = DECK_SET_SUCCESS.format(deck_name=deck_names[deck_type])

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🔙 Назад в настройки", callback_data="settings:main")

    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    await callback.answer("Колода выбрана!")
