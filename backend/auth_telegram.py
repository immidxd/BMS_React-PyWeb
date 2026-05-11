"""
Run this ONCE to authenticate Telegram and create the .session file.
After this, the web app will use the saved session automatically.

Usage:
    cd /Users/i.malashenko/Desktop/react-fastapi-app/backend
    python auth_telegram.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '../.env'))

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID   = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE    = os.getenv("TELEGRAM_PHONE", "")

SESSION_DIR = os.path.join(os.path.dirname(__file__), ".telegram_session")
os.makedirs(SESSION_DIR, exist_ok=True)
SESSION_FILE = os.path.join(SESSION_DIR, "bms")


async def main():
    print(f"\n🔐 Авторизація Telegram для BMS (Telethon)")
    print(f"   Номер: {PHONE}")
    print(f"   Сесія: {SESSION_FILE}.session\n")

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

    await client.start(phone=PHONE)

    me = await client.get_me()
    print(f"\n✅ Авторизація успішна!")
    print(f"   Підключено як: {me.first_name} (@{me.username})")
    print(f"\n   Сесія збережена. Тепер синхронізація через веб буде працювати.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
