"""Утилита для кэширования видео бота"""

import aiofiles
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Модульный кэш для видео
_video_cache: dict[str, bytes] = {}


async def load_main_video() -> Optional[bytes]:
    """
    Загрузить главное видео intro.mp4 в память и сохранить в кэш

    Returns:
        bytes: Бинарные данные видео или None при ошибке
    """
    try:
        async with aiofiles.open("video/intro.mp4", "rb") as video_file:
            video_data = await video_file.read()

        # Сохраняем в модульный кэш
        _video_cache["main_video"] = video_data

        logger.info(f"Main video loaded successfully: {len(video_data)} bytes")
        return video_data
    except FileNotFoundError:
        logger.error("Main video not found at video/intro.mp4")
        return None
    except Exception as e:
        logger.error(f"Failed to load main video: {e}")
        return None


def get_cached_video() -> Optional[bytes]:
    """
    Получить кэшированное видео из модульного кэша

    Returns:
        bytes: Бинарные данные видео или None
    """
    return _video_cache.get("main_video")
