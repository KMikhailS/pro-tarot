"""Утилита для кэширования изображений бота"""

import aiofiles
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Модульный кэш для изображений
_image_cache: dict[str, bytes] = {}


async def load_main_image() -> Optional[bytes]:
    """
    Загрузить главное изображение main.png в память и сохранить в кэш

    Returns:
        bytes: Бинарные данные изображения или None при ошибке
    """
    try:
        async with aiofiles.open("images/main.png", "rb") as image_file:
            image_data = await image_file.read()

        # Сохраняем в модульный кэш
        _image_cache["main_image"] = image_data

        logger.info(f"Main image loaded successfully: {len(image_data)} bytes")
        return image_data
    except FileNotFoundError:
        logger.error("Main image not found at images/main.png")
        return None
    except Exception as e:
        logger.error(f"Failed to load main image: {e}")
        return None


def get_cached_image() -> Optional[bytes]:
    """
    Получить кэшированное изображение из модульного кэша

    Returns:
        bytes: Бинарные данные изображения или None
    """
    return _image_cache.get("main_image")
