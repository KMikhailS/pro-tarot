"""Обработчики административных команд для генерации реферальных ссылок"""

import json
import ast
import base64
import logging
import os
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database.db import get_user_role
from messages import (
    ADMIN_LINK_HELP,
    ADMIN_LINK_ACCESS_DENIED,
    ADMIN_LINK_INVALID_JSON,
    ADMIN_LINK_SUCCESS,
)

logger = logging.getLogger(__name__)

router = Router()


class LinkGenStates(StatesGroup):
    """Состояния для генерации ссылок"""
    waiting_for_json = State()


def generate_bot_link(bot_username: str, utm_params: dict) -> str:
    """
    Генерирует ссылку на бота с закодированными UTM параметрами.

    Args:
        bot_username: Имя пользователя бота (без @)
        utm_params: Словарь с UTM параметрами

    Returns:
        Готовая ссылка на бота с закодированными параметрами
    """
    # Кодируем UTM параметры в base64
    json_str = json.dumps(utm_params, ensure_ascii=False)
    encoded = base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('utf-8')

    return f"https://t.me/{bot_username}?start={encoded}"


def format_utm_params(params: dict) -> str:
    """
    Форматирует UTM параметры для отображения.

    Args:
        params: Словарь с параметрами

    Returns:
        Отформатированная строка с параметрами
    """
    lines = []
    for key, value in params.items():
        lines.append(f"• `{key}`: `{value}`")
    return "\n".join(lines)


@router.message(Command("get_link"))
async def cmd_get_link(message: Message, state: FSMContext):
    """
    Обработчик команды /get_link.
    Доступна только для администраторов.
    Генерирует реферальную ссылку с закодированными UTM параметрами.
    """
    # Проверяем роль пользователя
    user_role = await get_user_role(message.from_user.id)

    if user_role != "ADMIN":
        await message.answer(ADMIN_LINK_ACCESS_DENIED)
        logger.warning(
            f"User {message.from_user.id} (@{message.from_user.username}) "
            f"attempted to use /get_link without ADMIN role"
        )
        return

    # Отправляем инструкцию и переводим в режим ожидания JSON
    await message.answer(ADMIN_LINK_HELP, parse_mode="Markdown")
    await state.set_state(LinkGenStates.waiting_for_json)

    logger.info(
        f"Admin {message.from_user.id} (@{message.from_user.username}) "
        f"initiated link generation"
    )


@router.message(StateFilter(LinkGenStates.waiting_for_json))
async def process_utm_json(message: Message, state: FSMContext):
    """
    Обработчик получения JSON с UTM параметрами.
    Генерирует ссылку и отправляет её пользователю.
    """
    try:
        # Парсим JSON из сообщения
        # Сначала пробуем стандартный JSON (двойные кавычки)
        try:
            utm_params = json.loads(message.text)
        except json.JSONDecodeError:
            # Если не получилось, пробуем Python-синтаксис (одинарные кавычки)
            utm_params = ast.literal_eval(message.text)

        # Проверяем, что результат - словарь
        if not isinstance(utm_params, dict):
            raise ValueError("Expected a dictionary/object")

        # Получаем имя бота из переменной окружения или используем дефолтное
        bot_username = os.getenv("BOT_USERNAME", "pro_tarot_bot")
        # Убираем @ если он есть
        bot_username = bot_username.lstrip("@")

        # Генерируем ссылку
        link = generate_bot_link(bot_username, utm_params)

        # Форматируем параметры для отображения
        params_display = format_utm_params(utm_params)

        # Отправляем результат
        await message.answer(
            ADMIN_LINK_SUCCESS.format(
                link=link,
                params=params_display
            ),
            parse_mode="Markdown"
        )

        logger.info(
            f"Admin {message.from_user.id} (@{message.from_user.username}) "
            f"generated link with params: {utm_params}"
        )

        # Сбрасываем состояние
        await state.clear()

    except (json.JSONDecodeError, ValueError, SyntaxError) as e:
        await message.answer(ADMIN_LINK_INVALID_JSON)
        logger.warning(
            f"Admin {message.from_user.id} sent invalid JSON/dict: {e}"
        )
    except Exception as e:
        await message.answer(
            f"❌ Произошла ошибка при генерации ссылки: {str(e)}"
        )
        logger.error(
            f"Error generating link for admin {message.from_user.id}: {e}",
            exc_info=True
        )
        await state.clear()
