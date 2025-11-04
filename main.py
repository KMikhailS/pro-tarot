import asyncio
import os
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv, find_dotenv

from messages import ABOUT_TEXT
from database.db import init_db, add_user
from handlers import settings, cards
from scheduler.daily_sender import start_daily_sender

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
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    # Сохраняем пользователя в БД
    await add_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )
    await message.answer(ABOUT_TEXT)


async def main():
    """Основная функция запуска бота"""
    logger.info("Starting bot...")

    # Инициализация БД
    await init_db()
    logger.info("Database initialized")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Регистрируем роутеры
    dp.include_router(router)
    dp.include_router(settings.router)
    dp.include_router(cards.router)

    # Запуск планировщика в фоне
    start_daily_sender(bot)
    logger.info("Daily sender started")

    # Запускаем polling
    logger.info("Bot is running...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")