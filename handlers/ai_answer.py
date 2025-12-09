"""Обработчик команды /get_answer для AI-ответов через OpenRouter"""

import os
import logging
import asyncio
import re
from io import BytesIO
from PIL import Image
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatAction
from aiogram.utils.keyboard import InlineKeyboardBuilder
from openai import AsyncOpenAI, OpenAI

from messages import QUESTION_CATEGORY_SELECTION, CATEGORY_RESPONSE_TEXT, GET_ANSWER_PROMPT, GET_ANSWER_PROCESSING, \
    GET_ANSWER_ERROR
from database.db import get_user_deck
from scheduler.daily_sender import generate_unique_cards, get_card_description, get_card_path

logger = logging.getLogger(__name__)

# Конфигурация для стриминга
STREAM_UPDATE_INTERVAL_CHARS = 50  # Обновлять сообщение каждые N символов
STREAM_UPDATE_MIN_DELAY = 1.5  # Минимальная задержка между обновлениями (секунды)
TYPING_ACTION_INTERVAL = 4.0  # Интервал отправки typing action (секунды)

router = Router()


def convert_markdown_to_html(text: str) -> str:
    """
    Конвертирует Markdown форматирование в HTML для Telegram.

    Поддерживаемые преобразования:
    - **текст** -> <b>текст</b> (жирный)
    - *текст* -> <i>текст</i> (курсив)
    - __текст__ -> <u>текст</u> (подчеркнутый)
    - [текст](url) -> <a href="url">текст</a> (ссылка)
    """
    # Жирный шрифт: **текст** -> <b>текст</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    # Курсив: *текст* -> <i>текст</i> (только если не часть жирного шрифта)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)

    # Подчеркнутый: __текст__ -> <u>текст</u>
    text = re.sub(r'__(.+?)__', r'<u>\1</u>', text)

    # Ссылки: [текст](url) -> <a href="url">текст</a>
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)

    return text


class AIAnswerStates(StatesGroup):
    """Состояния для получения AI-ответа"""
    waiting_for_question = State()


def create_combined_cards_image(card_paths: list, spacing: int = 20, target_height: int = 600) -> BytesIO:
    """
    Объединяет 3 изображения карт в одно горизонтальное изображение.
    Все карты приводятся к одинаковой высоте с сохранением пропорций.

    Args:
        card_paths: Список из 3 путей к изображениям карт
        spacing: Расстояние между картами в пикселях
        target_height: Целевая высота для всех карт в пикселях

    Returns:
        BytesIO объект с объединённым изображением в формате JPEG
    """
    resized_images = []

    # Загружаем и изменяем размер всех изображений до одинаковой высоты
    for path in card_paths:
        img = Image.open(path)

        # Вычисляем новую ширину с сохранением пропорций
        aspect_ratio = img.width / img.height
        target_width = int(target_height * aspect_ratio)

        # Изменяем размер изображения с высоким качеством
        resized_img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
        resized_images.append(resized_img)

        # Закрываем оригинальное изображение
        img.close()

    # Все карты теперь имеют одинаковую высоту, вычисляем общую ширину
    total_width = sum(img.width for img in resized_images) + spacing * (len(resized_images) - 1)

    # Создаём новое изображение с белым фоном
    combined_image = Image.new('RGB', (total_width, target_height), color='white')

    # Вставляем карты по горизонтали
    x_offset = 0
    for img in resized_images:
        combined_image.paste(img, (x_offset, 0))
        x_offset += img.width + spacing

    # Сохраняем в BytesIO
    output = BytesIO()
    combined_image.save(output, format='JPEG', quality=95)
    output.seek(0)

    # Закрываем изображения
    for img in resized_images:
        img.close()

    return output


@router.callback_query(F.data == "ask:question")
async def callback_ask_question(callback: CallbackQuery, state: FSMContext):
    """
    Обработчик нажатия на кнопку "Задать вопрос Небесной канцелярии".
    Показывает меню выбора категории вопроса.
    """
    # Создаём клавиатуру с 6 категориями
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="💕 Любовь и отношения", callback_data="category:love")
    keyboard.button(text="💰 Финансы и реализация", callback_data="category:finance")
    keyboard.button(text="👨‍👩‍👧‍👦 Семья", callback_data="category:family")
    keyboard.button(text="🔮 Предстоящее будущее", callback_data="category:future")
    keyboard.button(text="✈️ Путешествия", callback_data="category:travel")
    keyboard.button(text="✍️ Свой вопрос", callback_data="category:custom")
    keyboard.adjust(1)  # По 1 кнопке в строку

    await callback.message.answer(
        QUESTION_CATEGORY_SELECTION,
        reply_markup=keyboard.as_markup()
    )
    await callback.answer()
    logger.info(f"User {callback.from_user.id} initiated ask question via button")


@router.callback_query(F.data.startswith("category:"))
async def callback_category_selected(callback: CallbackQuery, state: FSMContext):
    """
    Обработчик выбора категории вопроса.
    Для категории "custom" запрашивает вопрос.
    Для остальных категорий показывает инструкцию, а затем отправляет AI запрос.
    """
    # Извлекаем категорию из callback_data
    category = callback.data.split(":")[1]

    # Сохраняем категорию в state для дальнейшего использования
    await state.update_data(category=category)

    # Маппинг категорий на их названия
    category_names = {
        "love": "Любовь и отношения",
        "finance": "Финансы и реализация",
        "family": "Семья",
        "future": "Предстоящее будущее",
        "travel": "Путешествия"
    }

    if category == "custom":
        # Для категории "Свой вопрос" запрашиваем вопрос
        await callback.message.answer(GET_ANSWER_PROMPT)
        await state.set_state(AIAnswerStates.waiting_for_question)
        await callback.answer()
    else:
        # Для остальных категорий показываем инструкцию с кнопкой "Получить расклад"
        keyboard = InlineKeyboardBuilder()
        keyboard.button(
            text="🔮 Получить расклад",
            callback_data=f"spread:get:{category}"
        )
        keyboard.adjust(1)

        await callback.message.answer(
            CATEGORY_RESPONSE_TEXT,
            reply_markup=keyboard.as_markup()
        )
        await callback.answer()

    logger.info(f"User {callback.from_user.id} selected category: {category}")


@router.callback_query(F.data.startswith("spread:get:"))
async def callback_get_spread(callback: CallbackQuery):
    """
    Обработчик нажатия на кнопку "Получить расклад".
    Запускает генерацию расклада для выбранной категории.
    """
    # Извлекаем категорию из callback_data
    category = callback.data.split(":")[2]

    # Маппинг категорий на их названия
    category_names = {
        "love": "Любовь и отношения",
        "finance": "Финансы и реализация",
        "family": "Семья",
        "future": "Предстоящее будущее",
        "travel": "Путешествия"
    }

    # Получаем название категории
    category_name = category_names.get(category, "будущее")

    # Подтверждаем нажатие кнопки
    await callback.answer()

    # Отправляем AI запрос с генерацией карт
    await send_category_ai_request(
        callback.message,
        category_name,
        callback.from_user.id
    )

    logger.info(f"User {callback.from_user.id} requested spread for category: {category}")


@router.message(Command("get_answer"))
async def cmd_get_answer(message: Message, state: FSMContext):
    """
    Обработчик команды /get_answer.
    Запрашивает у пользователя вопрос для AI.
    """
    await message.answer(GET_ANSWER_PROMPT)
    await state.set_state(AIAnswerStates.waiting_for_question)
    logger.info(f"User {message.from_user.id} initiated /get_answer command")


async def send_typing_action(message: Message, stop_event: asyncio.Event):
    """
    Периодически отправляет typing action, пока идет обработка.
    Останавливается когда установлен stop_event.
    """
    try:
        while not stop_event.is_set():
            await message.bot.send_chat_action(
                chat_id=message.chat.id,
                action=ChatAction.TYPING
            )
            await asyncio.sleep(TYPING_ACTION_INTERVAL)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"Error sending typing action: {e}")


async def send_category_ai_request(message: Message, category_name: str, user_id: int):
    """
    Отправляет AI запрос для выбранной категории с генерацией карт Таро.

    Args:
        message: Сообщение пользователя
        category_name: Название категории (например, "Любовь и отношения")
        user_id: ID пользователя
    """
    # Создаем событие для остановки typing action
    typing_stop_event = asyncio.Event()
    typing_task = None

    try:
        # Получаем тип колоды пользователя
        deck_type = await get_user_deck(user_id)

        # Генерируем 3 случайные уникальные карты
        try:
            cards = generate_unique_cards(deck_type, count=3)
        except ValueError as e:
            logger.error(f"Failed to generate cards for user {user_id}: {e}")
            await message.answer("😔 Не удалось сгенерировать карты. Попробуйте позже.")
            return

        # Получаем названия карт и пути к изображениям
        card_names = []
        card_image_paths = []
        for card_number in cards:
            card_name, _ = await get_card_description(card_number)
            if card_name:
                card_names.append(card_name)
            else:
                card_names.append(f"Карта {card_number}")

            # Получаем путь к изображению карты
            card_path = get_card_path(deck_type, card_number)
            if card_path:
                card_image_paths.append(card_path)

        # Создаём и отправляем составное изображение с 3 картами ПЕРВЫМ
        if len(card_image_paths) == 3:
            try:
                combined_image_bytes = create_combined_cards_image(card_image_paths)
                combined_photo = BufferedInputFile(
                    combined_image_bytes.read(),
                    filename="tarot_spread.jpg"
                )
                await message.bot.send_photo(
                    chat_id=message.chat.id,
                    photo=combined_photo,
                    caption=f"Ваш расклад по вопросу: {category_name}\n\nКарты: <b>{card_names[0]}</b>, <b>{card_names[1]}</b>, <b>{card_names[2]}</b>",
                    parse_mode='HTML'
                )
                logger.info(f"Combined cards image sent to user {user_id}")
            except Exception as e:
                logger.error(f"Failed to create/send combined image: {e}", exc_info=True)
                # Продолжаем выполнение даже если не удалось отправить изображение

        # ТЕПЕРЬ показываем, что запрос обрабатывается (это сообщение будет ПОСЛЕ изображения)
        processing_msg = await message.answer(GET_ANSWER_PROCESSING)

        # Формируем промпт с заменой {тема} на category_name
        prompt_template = """
        Ты — опытный таролог. Сделай толкование на основе этих трёх карт по вопросу {тема}.
        Позиции:
        1) Ситуация/динамика.
        2) Совет/что делать.
        3) Вероятный исход в ближайшие 1–3 месяца при следовании совету.
        Дай цельную интерпретацию, связывая карты между собой: ключевой конфликт/ресурс, где узкое место и что поддерживает. Минимизируй «книжные» определения, больше контекста и причинно-следственных связей. Тон поддерживающий и прагматичный, без обещаний и вмешательства в волю третьих лиц.
        В конце предложи 2–3 конкретных шага. Без эзотерической «воды» и предупреждений. Объём 1200-1500 знаков.
        Каждый пункт в Шаги начинай с новой строки
        Формат ответа:
        Краткая картина: …
        Совет: …
        Исход: …
        Шаги: 1) … 2) … 3) …
        """

        system_prompt = prompt_template.replace("{тема}", category_name)

        # Формируем user message с названиями карт
        user_message = f"Карты: {card_names[0]}, {card_names[1]}, {card_names[2]} Отформатируй ответ, разбей на абзацы, выдели важные моменты жирным шрифтом."

        # Получаем API ключ из переменных окружения
        api_key = os.getenv('OPENROUTER_API_KEY')

        if not api_key:
            logger.error("OPENROUTER_API_KEY not found in environment variables")
            await processing_msg.edit_text(
                "❌ Ошибка конфигурации: API ключ не найден"
            )
            return

        # Запускаем задачу для отправки typing action
        typing_task = asyncio.create_task(
            send_typing_action(message, typing_stop_event)
        )

        # Создаём асинхронный клиент OpenAI с базовым URL для OpenRouter
        client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )

        # Создаём клавиатуру с двумя кнопками для финального ответа
        keyboard = InlineKeyboardBuilder()
        keyboard.button(
            text="🔮 Задать вопрос Небесной канцелярии",
            callback_data="ask:question"
        )
        keyboard.button(
            text="⚙️ Настройки",
            callback_data="settings:main"
        )
        keyboard.adjust(1)  # По 1 кнопке в строку

        # Отправляем запрос к API с системным промптом и включаем стриминг
        stream = await client.chat.completions.create(
            extra_body={},
            model="openai/gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            max_tokens=1500,
            stream=True  # Включаем стриминг
        )

        # Переменные для сбора и отображения ответа
        full_response = ""
        last_update_text = ""
        last_update_time = asyncio.get_event_loop().time()
        chars_since_update = 0
        message_updated = False

        # Обрабатываем стрим
        async for chunk in stream:
            # Проверяем наличие контента в чанке
            if chunk.choices and chunk.choices[0].delta.content:
                content_chunk = chunk.choices[0].delta.content
                full_response += content_chunk
                chars_since_update += len(content_chunk)

                current_time = asyncio.get_event_loop().time()
                time_since_update = current_time - last_update_time

                # Обновляем сообщение если:
                # 1. Накопилось достаточно символов И прошло минимальное время
                # 2. ИЛИ если это первое обновление (чтобы быстрее убрать "обрабатываю")
                should_update = (
                        (chars_since_update >= STREAM_UPDATE_INTERVAL_CHARS and
                         time_since_update >= STREAM_UPDATE_MIN_DELAY) or
                        not message_updated
                )

                if should_update:
                    # Формируем текст сообщения с конвертацией Markdown в HTML
                    display_text = convert_markdown_to_html(full_response)

                    # Обновляем только если текст изменился
                    if display_text != last_update_text:
                        try:
                            await processing_msg.edit_text(
                                display_text,
                                parse_mode='HTML'
                            )
                            last_update_text = display_text
                            last_update_time = current_time
                            chars_since_update = 0
                            message_updated = True
                        except Exception as e:
                            # Игнорируем ошибки редактирования
                            logger.debug(f"Message edit skipped: {e}")

        # Финальное обновление с полным ответом и кнопками
        # Всегда редактируем в конце, чтобы добавить кнопки
        final_text = convert_markdown_to_html(full_response)
        try:
            await processing_msg.edit_text(
                final_text,
                parse_mode='HTML',
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            logger.warning(f"Final message edit failed: {e}")

        logger.info(
            f"AI answer for category '{category_name}' sent to user {user_id}, "
            f"cards: {card_names}, "
            f"answer length: {len(full_response)}"
        )

    except asyncio.CancelledError:
        logger.warning(f"AI request cancelled for user {user_id}")
        await processing_msg.edit_text(
            "❌ Запрос был отменён"
        )

    except Exception as e:
        logger.error(
            f"Error processing AI request for user {user_id}: {e}",
            exc_info=True
        )
        try:
            await processing_msg.edit_text(GET_ANSWER_ERROR)
        except Exception as edit_error:
            logger.error(f"Failed to edit error message: {edit_error}")

    finally:
        # Останавливаем typing action
        typing_stop_event.set()
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass


@router.message(StateFilter(AIAnswerStates.waiting_for_question), F.text)
async def process_question(message: Message, state: FSMContext):
    """
    Обработчик текстового вопроса пользователя.
    Вызывает send_category_ai_request с вопросом пользователя.
    """
    user_question = message.text

    # Вызываем метод send_category_ai_request с вопросом пользователя
    await send_category_ai_request(
        message,
        category_name=user_question,
        user_id=message.from_user.id
    )

    # Очищаем состояние FSM
    await state.clear()

    logger.info(f"User {message.from_user.id} asked custom question: {user_question}")
