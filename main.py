import asyncio
import os
import logging

import aiofiles
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv, find_dotenv

from messages import ABOUT_TEXT
from database.db import init_db, add_user
from handlers import settings, cards, ai_answer
from scheduler.daily_sender import start_daily_sender_apscheduler
from utils.image_cache import load_main_image, get_cached_image

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()
load_dotenv(find_dotenv())
BOT_TOKEN = os.getenv('BOT_TOKEN')

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    """Обработчик команды /start"""
    # Сохраняем пользователя в БД
    await add_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    # Создаём inline клавиатуру с двумя кнопками
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text="🌅 Получить карту дня",
        callback_data="start:get_card"
    )
    keyboard.button(
        text="⚙️ Настройки",
        callback_data="settings:main"
    )
    keyboard.adjust(1)  # По 1 кнопке в строку

    # Получаем кэшированное изображение
    image_data = get_cached_image()

    if image_data is None:
        # Fallback: если кэш отсутствует, загружаем напрямую
        logger.warning("Image cache miss in /start handler")
        async with aiofiles.open("images/main.png", "rb") as image_file:
            image_data = await image_file.read()

    # Отправляем фото с приветственным текстом
    photo = BufferedInputFile(image_data, filename="main.png")
    await message.answer_photo(
        photo=photo,
        caption=ABOUT_TEXT,
        reply_markup=keyboard.as_markup()
    )


async def main():
    """Основная функция запуска бота"""
    logger.info("Starting bot...")

    # Инициализация БД
    await init_db()
    logger.info("Database initialized")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Загружаем изображение в кэш перед стартом
    main_image = await load_main_image()
    if main_image:
        logger.info("Main image cached successfully")
    else:
        logger.warning("Failed to cache main image, handlers will use fallback")

    # Регистрируем роутеры
    dp.include_router(router)
    dp.include_router(settings.router)
    dp.include_router(cards.router)
    dp.include_router(ai_answer.router)

    # Запуск APScheduler для ежедневных карт
    scheduler = start_daily_sender_apscheduler(bot)
    logger.info("APScheduler daily sender initialized")

    try:
        # Запускаем polling
        logger.info("Bot is running...")
        await dp.start_polling(bot)
    finally:
        # Graceful shutdown планировщика
        logger.info("Shutting down APScheduler...")
        scheduler.shutdown(wait=True)
        logger.info("APScheduler shut down successfully")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")