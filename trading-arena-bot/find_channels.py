"""
Run this once to find the correct channel IDs for your .env file.
Usage: python3 find_channels.py
"""
import asyncio
from dotenv import load_dotenv
import os
from telethon import TelegramClient

load_dotenv()

API_ID   = int(os.getenv('TELEGRAM_API_ID', '0'))
API_HASH = os.getenv('TELEGRAM_API_HASH', '')
PHONE    = os.getenv('TELEGRAM_PHONE', '')

async def main():
    client = TelegramClient('arena_session', API_ID, API_HASH)
    await client.start(phone=PHONE)
    print('\nYour Telegram channels and groups:\n')
    print(f'{"ID":<20} {"Username":<30} {"Title"}')
    print('-' * 75)
    async for dialog in client.iter_dialogs():
        if dialog.is_channel or dialog.is_group:
            username = f'@{dialog.entity.username}' if getattr(dialog.entity, 'username', None) else '(no username)'
            print(f'{dialog.id:<20} {username:<30} {dialog.name}')
    print('\nCopy the ID of each Arena channel and paste into your .env as:')
    print('CHANNEL_CASH=-1001234567890')
    print('CHANNEL_GAULS=-1001234567891')
    print('etc.\n')
    await client.disconnect()

asyncio.run(main())
