import os
import re
import json
import base64
import asyncio
import logging
import httpx

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, BufferedInputFile

from dotenv import load_dotenv

load_dotenv()

# --- Настройки через переменные окружения ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL_ID = os.getenv("MODEL_ID", "google/lyria-3-pro-preview")

# MOCK_MODE=1 -> берём аудио из файла. MOCK_MODE=0 -> идём в OpenRouter.
MOCK_MODE = os.getenv("MOCK_MODE", "1") == "1"
MOCK_FILE = os.getenv("MOCK_FILE", "")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def load_mock_audio() -> bytes:
    """Достаём готовое mp3 из сохранённого ответа Open WebUI."""
    with open(MOCK_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    for msg in data[0]["chat"]["history"]["messages"].values():
        content = msg.get("content", "")
        m = re.search(r'src="data:audio/mpeg;base64,([^"]+)"', content)
        if m:
            return base64.b64decode(m.group(1))
    raise RuntimeError("Аудио не найдено в мок-файле")


async def generate_song_real(prompt: str) -> bytes:
    """Реальный запрос к OpenRouter через SSE-поток."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me",
        "X-Title": "Lyria TG Bot",
    }
    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": prompt}]}
        ],
        "stream": True,
        "modalities": ["text", "audio"],
        "audio": {"format": "mp3"},
    }

    audio_chunks: list[str] = []
    async with httpx.AsyncClient(timeout=180.0) as client:
        async with client.stream(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                err = (await resp.aread()).decode("utf-8", "ignore")
                raise RuntimeError(f"OpenRouter {resp.status_code}: {err[:300]}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk_str = line[6:].strip()
                if chunk_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(chunk_str)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                audio = delta.get("audio") or {}
                if audio.get("data"):
                    audio_chunks.append(audio["data"])

    if not audio_chunks:
        raise RuntimeError("Аудио не пришло в потоке")

    return base64.b64decode("".join(audio_chunks))


@dp.message(CommandStart())
async def cmd_start(message: Message):
    mode = "🧪 ТЕСТ (мок)" if MOCK_MODE else "🎵 РАБОЧИЙ"
    await message.answer(
        f"Привет! Режим: {mode}\n\n"
        "Отправь мне стих — я сделаю из него песню."
    )


@dp.message(F.text)
async def handle_text(message: Message):
    prompt = message.text.strip()
    if not prompt:
        return

    await message.bot.send_chat_action(message.chat.id, "upload_voice")
    status = await message.answer("🎼 Генерирую... Это может занять до 1–2 минут.")

    try:
        if MOCK_MODE:
            await asyncio.sleep(3)  # имитируем задержку
            audio_bytes = load_mock_audio()
        else:
            audio_bytes = await generate_song_real(prompt)
    except Exception as e:
        log.exception("Generation failed")
        await status.edit_text(f"❌ Не получилось: {e}")
        return

    file = BufferedInputFile(audio_bytes, filename="song.mp3")
    await message.answer_audio(file, caption="🎵 Готово!")
    await status.delete()


async def main():
    log.info("Bot starting. MOCK_MODE=%s", MOCK_MODE)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())