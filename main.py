import asyncio
import os
import logging
from logging.handlers import RotatingFileHandler
import json
import ast
import base64

import aiofiles
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv, find_dotenv

from messages import ABOUT_TEXT
from database.db import init_db, add_user, add_subscription_analytics, get_link_params
from handlers import settings, cards, ai_answer, admin_links
from scheduler.daily_sender import start_daily_sender_apscheduler, preload_card_descriptions
from utils.video_cache import load_main_video, get_cached_video

# Создаём директорию для логов, если её нет
os.makedirs('logs', exist_ok=True)

# Настройка логирования
log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Обработчик для записи в файл с ротацией (макс. 10 МБ, хранить 5 файлов)
file_handler = RotatingFileHandler(
    'logs/bot.log',
    maxBytes=10 * 1024 * 1024,  # 10 МБ
    backupCount=5,
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(log_format)

# Обработчик для вывода в консоль
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(log_format)

# Настраиваем root logger
logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)

logger = logging.getLogger(__name__)

load_dotenv()
load_dotenv(find_dotenv())
BOT_TOKEN = os.getenv('BOT_TOKEN')

router = Router()


@router.message(CommandStart(deep_link=True))
async def cmd_start_with_deeplink(message: Message, bot: Bot):
    """Обработчик команды /start с deep link параметром"""
    # Сохраняем пользователя в БД
    await add_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    # Получаем deep link из текста команды
    start_param = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None

    logger.info(f"Getting message from start WITH deep link: {message.text}")
    logger.info(f"Deep link param: {start_param}")

    # Парсим параметр start для аналитики
    if start_param:
        try:
            # Пробуем распарсить как ID (новый формат - короткая ссылка)
            try:
                link_id = int(start_param)
                # Получаем параметры из БД по ID
                utm_params = await get_link_params(link_id)

                if utm_params:
                    # Сохраняем аналитику с link_id
                    await add_subscription_analytics(message.from_user.id, utm_params, link_id)
                    logger.info(
                        f"User {message.from_user.id} subscribed via link_id={link_id} with UTM params: {utm_params}"
                    )
                else:
                    logger.warning(f"Link ID {link_id} not found in database")

            except ValueError:
                # Если не число - пробуем старый формат (base64)
                logger.info(f"Trying legacy base64 format for param: {start_param}")

                # Декодируем base64
                decoded_bytes = base64.urlsafe_b64decode(start_param)
                decoded_str = decoded_bytes.decode('utf-8')

                # Парсим JSON (пробуем оба формата - стандартный и Python-синтаксис)
                try:
                    utm_params = json.loads(decoded_str)
                except json.JSONDecodeError:
                    utm_params = ast.literal_eval(decoded_str)

                # Проверяем что это словарь
                if isinstance(utm_params, dict) and utm_params:
                    # Сохраняем аналитику без link_id (старый формат)
                    await add_subscription_analytics(message.from_user.id, utm_params)
                    logger.info(
                        f"User {message.from_user.id} subscribed with legacy UTM params: {utm_params}"
                    )

        except Exception as e:
            # Игнорируем ошибки парсинга - невалидные параметры не критичны
            logger.warning(f"Failed to parse start parameter '{start_param}': {e}")

    # Отправляем приветствие
    await send_welcome_message(message, bot)


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    """Обработчик команды /start без параметров"""
    # Сохраняем пользователя в БД
    await add_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    logger.info(f"Getting message from start WITHOUT deep link: {message.text}")

    # Отправляем приветствие
    await send_welcome_message(message, bot)


async def send_welcome_message(message: Message, bot: Bot):
    """Отправка приветственного сообщения с видео"""
    # Создаём inline клавиатуру с тремя кнопками
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text="🔮 Таро расклад",
        callback_data="ask:question"
    )
    keyboard.button(
        text="🌅 Получить карту дня",
        callback_data="start:get_card"
    )
    keyboard.button(
        text="⚙️ Настройки",
        callback_data="settings:main"
    )
    keyboard.adjust(1)  # По 1 кнопке в строку

    # Получаем кэшированное видео
    video_data = get_cached_video()

    if video_data is None:
        # Fallback: если кэш отсутствует, загружаем напрямую
        logger.warning("Video cache miss in /start handler")
        try:
            async with aiofiles.open("video/intro.mp4", "rb") as video_file:
                video_data = await video_file.read()
        except FileNotFoundError:
            logger.error("Main video file not found at video/intro.mp4")
            await message.answer(
                f"{ABOUT_TEXT}\n\n⚠️ Видео временно недоступно.",
                reply_markup=keyboard.as_markup()
            )
            return
        except Exception as e:
            logger.error(f"Error loading main video: {e}", exc_info=True)
            await message.answer(
                f"{ABOUT_TEXT}\n\n⚠️ Произошла ошибка при загрузке видео.",
                reply_markup=keyboard.as_markup()
            )
            return

    # Отправляем видео с приветственным текстом
    video = BufferedInputFile(video_data, filename="intro.mp4")
    await message.answer_video(
        video=video,
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

    # Параллельная загрузка видео и описаний карт для ускорения старта
    logger.info("Loading video and card descriptions in parallel...")
    video_result, _ = await asyncio.gather(
        load_main_video(),
        preload_card_descriptions()
    )

    if video_result:
        logger.info("Main video cached successfully")
    else:
        logger.warning("Failed to cache main video, handlers will use fallback")

    # Регистрируем роутеры
    dp.include_router(router)
    dp.include_router(settings.router)
    dp.include_router(cards.router)
    dp.include_router(ai_answer.router)
    dp.include_router(admin_links.router)

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