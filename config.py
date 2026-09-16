import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL_ID = os.getenv("MODEL_ID", "google/lyria-3-pro-preview")
BOT_ACCESS_KEY = os.getenv("BOT_ACCESS_KEY", "")

MOCK_MODE = os.getenv("MOCK_MODE", "0") == "1"
MOCK_FILE = os.getenv("MOCK_FILE", "")

DATABASE_URL = "sqlite+aiosqlite:///./bot.db"
