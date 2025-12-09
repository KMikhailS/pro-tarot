"""
Тестовый скрипт для демонстрации стриминга OpenRouter API.
Запустите: python3 test_streaming.py
"""

import asyncio
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()


async def test_streaming():
    """Тест стриминга ответов от OpenRouter API"""

    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not found in .env file")
        return

    print("Starting streaming test...\n")
    print("=" * 60)

    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    try:
        stream = await client.chat.completions.create(
            model="openai/gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Ты профессиональный таролог. Дай краткий ответ."
                },
                {
                    "role": "user",
                    "content": "Что означает карта Таро 'Дурак'?"
                }
            ],
            max_tokens=500,
            stream=True
        )

        full_response = ""
        chunk_count = 0

        print("Streaming response:\n")

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                full_response += content
                chunk_count += 1
                print(content, end='', flush=True)

        print("\n")
        print("=" * 60)
        print(f"\nTotal chunks received: {chunk_count}")
        print(f"Total characters: {len(full_response)}")
        print("\nStreaming test completed successfully!")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_streaming())
