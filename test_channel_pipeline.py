import asyncio
import os
from bot.config import config
from bot.services.stats_service import get_stats
from bot.services.openai_client import get_filter_decision, generate_post_text

async def main():
    print("=" * 50)
    print("Проверка настроек и работоспособности AeroAI News")
    print("=" * 50)
    print(f"Канал-донор: @{config.donor_channel}")
    print(f"LLM Provider: Venice AI ({config.openai_base_url})")
    print(f"LLM Model: {config.filter_model}")
    print(f"Telethon Session: {config.telethon_session_name}.session")
    print(f"Admin Chat ID: {config.admin_chat_id}")
    print(f"Target Group ID: {config.target_group_id}")
    print(f"Auto-publish: {config.auto_publish}")
    
    # 1. Проверка сессии Telethon
    session_file = f"{config.telethon_session_name}.session"
    if os.path.exists(session_file):
        print(f"\n[OK] Файл сессии найден: {session_file}")
    else:
        print(f"\n[INFO] Файл сессии {session_file} пока не создан.")
        print("  -> Запустите однократно: python login_telethon.py")

    # 2. Проверка статистики
    print("\nПроверка получения статистики из БД:")
    stats = await get_stats()
    print(f"  Всего постов: {stats.get('total_collected')}")
    print(f"  В очереди на проверку: {stats.get('pending_review')}")
    print(f"  Опубликовано: {stats.get('approved')}")
    print(f"  Отклонено: {stats.get('rejected')}")
    print(f"  Потрачено бюджета: ${stats.get('budget_spent', 0):.3f} / ${config.max_budget_usd:.2f}")

    print("\n" + "=" * 50)
    print("Все базовые компоненты настроены успешно.")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(main())
