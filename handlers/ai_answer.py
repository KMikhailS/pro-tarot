"""Обработчик команды /get_answer для AI-ответов через OpenRouter"""

import os
import logging
import asyncio
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatAction
from openai import AsyncOpenAI, OpenAI

from messages import GET_ANSWER_PROMPT, GET_ANSWER_PROCESSING, GET_ANSWER_ERROR

logger = logging.getLogger(__name__)

# Конфигурация для стриминга
STREAM_UPDATE_INTERVAL_CHARS = 50  # Обновлять сообщение каждые N символов
STREAM_UPDATE_MIN_DELAY = 1.5  # Минимальная задержка между обновлениями (секунды)
TYPING_ACTION_INTERVAL = 4.0  # Интервал отправки typing action (секунды)

router = Router()


class AIAnswerStates(StatesGroup):
    """Состояния для получения AI-ответа"""
    waiting_for_question = State()


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


@router.message(StateFilter(AIAnswerStates.waiting_for_question), F.text)
async def process_question(message: Message, state: FSMContext):
    """
    Обработчик текстового вопроса пользователя.
    Отправляет запрос в OpenRouter API и возвращает ответ со стримингом.
    """
    user_question = message.text

    # Показываем, что запрос обрабатывается
    processing_msg = await message.answer(GET_ANSWER_PROCESSING)

    # Создаем событие для остановки typing action
    typing_stop_event = asyncio.Event()
    typing_task = None

    try:
        # Получаем API ключ из переменных окружения
        api_key = os.getenv('OPENROUTER_API_KEY')

        if not api_key:
            logger.error("OPENROUTER_API_KEY not found in environment variables")
            await processing_msg.edit_text(
                "❌ Ошибка конфигурации: API ключ не найден"
            )
            await state.clear()
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

        # Отправляем запрос к API с системным промптом и включаем стриминг
        stream = await client.chat.completions.create(
            extra_body={},
            model="openai/gpt-4.1-mini",
            # model="openai/gpt-5-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Ты профессиональный таролог. Представь, что к тебе пришла клиентка и просит рассказать ей о картах Таро. Не пиши вступительные фразы, по типу \"Как профессиональный таролог...\", а сразу переходи к толкованию. Напиши текст не более 1500 символов. Добавь форматирование, выделяй важные моменты"
                },
                {
                    "role": "user",
                    "content": user_question
                }
            ],
            max_tokens=2000,
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
                    # Формируем текст сообщения
                    display_text = full_response

                    # Обновляем только если текст изменился
                    if display_text != last_update_text:
                        try:
                            await processing_msg.edit_text(
                                display_text,
                                parse_mode='Markdown'
                            )
                            last_update_text = display_text
                            last_update_time = current_time
                            chars_since_update = 0
                            message_updated = True
                        except Exception as e:
                            # Игнорируем ошибки редактирования (например, если текст не изменился)
                            logger.debug(f"Message edit skipped: {e}")

        # Финальное обновление с полным ответом (если еще не обновили)
        logger.info(f"User {message.from_user.id} finished processing {full_response}")
        final_text = full_response
        if final_text != last_update_text:
            try:
                await processing_msg.edit_text(
                    final_text,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.warning(f"Final message edit failed: {e}")

        logger.info(
            f"AI answer streamed to user {message.from_user.id}, "
            f"question length: {len(user_question)}, "
            f"answer length: {len(full_response)}"
        )

    except asyncio.CancelledError:
        logger.warning(f"AI request cancelled for user {message.from_user.id}")
        await processing_msg.edit_text(
            "❌ Запрос был отменён"
        )

    except Exception as e:
        logger.error(
            f"Error processing AI request for user {message.from_user.id}: {e}",
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

        # Очищаем состояние FSM
        await state.clear()
