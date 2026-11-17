"""
login_telethon.py — Однократная интерактивная авторизация сессии Telethon.

Запустите этот скрипт в терминале один раз:
    venv\Scripts\python.exe login_telethon.py

Скрипт запросит номер телефона, код подтверждения из Telegram и 2FA пароль (если включён),
после чего сохранит сессию в файл parser_session.session.
"""

import asyncio
import os
from telethon import TelegramClient
from bot.config import config

async def main():
    session_file = f"{config.telethon_session_name}.session"
    print(f"=== Авторизация Telethon Userbot ===")
    print(f"API_ID: {config.telethon_api_id}")
    print(f"SESSION: {session_file}")
    print(f"DONOR_CHANNEL: {config.donor_channel}")
    print("=" * 36)

    client = TelegramClient(
        config.telethon_session_name,
        config.telethon_api_id,
        config.telethon_api_hash
    )

    await client.connect()
    if not await client.is_user_authorized():
        print("Сессия не авторизована. Требуется вход:")
        phone = input("Введите номер телефона (с кодом страны, например +7...): ").strip()
        await client.send_code_request(phone)
        code = input("Введите проверочный код из Telegram: ").strip()
        try:
            await client.sign_in(phone, code)
        except Exception as e:
            from telethon.errors import SessionPasswordNeededError
            if isinstance(e, SessionPasswordNeededError):
                password = input("Введите пароль двухфакторной аутентификации (2FA): ").strip()
                await client.sign_in(password=password)
            else:
                raise e

    me = await client.get_me()
    print(f"\n[OK] Успешная авторизация как: {me.first_name} (@{me.username or 'без юзернейма'}, ID: {me.id})")

    # Проверка доступа к каналу-донору
    try:
        channel = await client.get_entity(config.donor_channel)
        print(f"[OK] Канал-донор найден: {channel.title} (@{channel.username}, ID: {channel.id})")
        messages = await client.get_messages(channel, limit=1)
        if messages:
            print(f"[OK] Последнее сообщение из канала ({messages[0].date}): {messages[0].text[:80]}...")
    except Exception as e:
        print(f"[WARN] Ошибка проверки канала {config.donor_channel}: {e}")

    await client.disconnect()
    print(f"\n[ГОТОВО] Файл сессии {session_file} успешно создан и готов к работе.")

if __name__ == "__main__":
    asyncio.run(main())
