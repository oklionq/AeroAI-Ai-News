import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramRetryAfter
from bot.config import config
from bot.telegram.keyboards import get_main_keyboard, get_review_keyboard
from bot.telegram.formatting import safe_format
from bot.services.stats_service import get_stats
from bot.db import get_db_connection
from bot.utils.logger import logger

dp = Dispatcher()

async def send_draft_to_admin(bot: Bot, item_id: int, post_text: str, image_url: str | None):
    if image_url and len(post_text) > 1024:
        image_url = None
        
    markup = get_review_keyboard(item_id)
    text = safe_format(post_text, max_len=1024 if image_url else 4096)
    
    try:
        if image_url:
            await bot.send_photo(
                chat_id=config.admin_chat_id,
                photo=image_url,
                caption=text,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            await bot.send_message(
                chat_id=config.admin_chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True
            )
    except TelegramRetryAfter as e:
        logger.warning(f"Rate limited by Telegram. Retrying after {e.retry_after} seconds.")
        await asyncio.sleep(e.retry_after)
        await send_draft_to_admin(bot, item_id, post_text, image_url)
    except Exception as e:
        logger.error(f"Failed to send draft for item {item_id}: {e}")
        # fallback to text if photo failed
        if image_url:
            await send_draft_to_admin(bot, item_id, post_text, None)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
    await message.answer("AI News Agent запущен.", reply_markup=get_main_keyboard())

@dp.message(Command("stats"))
@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
    
    stats = await get_stats()
    text = (
        f"<b>Статистика:</b>\n"
        f"Всего собрано: {stats['total_collected']} ({stats['collected_24h']} за 24ч)\n"
        f"Прошло фильтр: {stats['passed_filter']}\n"
        f"Отсеяно (мусор): {stats['filtered_out']}\n"
        f"В ожидании: {stats['pending_review']}\n"
        f"Одобрено/Опубликовано: {stats['approved']}\n"
        f"Отклонено: {stats['rejected']}\n\n"
        f"<b>Бюджет OpenAI:</b>\n"
        f"Всего токенов: {stats['total_tokens']}\n"
        f"Потрачено: ${stats['budget_spent']:.2f} / ${config.max_budget_usd:.2f}\n"
        f"За текущий месяц: ${stats['cost_this_month']:.2f}\n"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("status"))
@dp.message(F.text == "⚙️ Статус")
async def cmd_status(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    stats = await get_stats()
    status_text = "Пауза" if stats['is_paused'] else "Активен"
    reason = f" ({stats['pause_reason']})" if stats['pause_reason'] else ""
    
    text = (
        f"<b>Статус:</b> {status_text}{reason}\n"
        # Can add uptime and error counts from DB here
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("queue"))
@dp.message(F.text == "🗂 Очередь")
async def cmd_queue(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    async with get_db_connection() as db:
        async with db.execute("SELECT title, collected_at FROM news_items WHERE status = 'pending_review' ORDER BY collected_at ASC LIMIT 20") as cur:
            items = await cur.fetchall()
            
    if not items:
        await message.answer("Очередь пуста.")
        return
        
    text = "<b>Очередь модерации:</b>\n\n"
    for title, collected_at in items:
        text += f"— {title} <i>({collected_at})</i>\n"
        
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("pause"))
@dp.message(Command("resume"))
@dp.message(F.text == "⏸ Пауза / ▶️ Продолжить")
async def cmd_pause_resume(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    async with get_db_connection() as db:
        async with db.execute("SELECT is_paused FROM bot_state WHERE id = 1") as cur:
            is_paused = (await cur.fetchone())[0]
            
        new_state = not is_paused
        reason = 'manual' if new_state else None
        
        await db.execute("UPDATE bot_state SET is_paused = ?, pause_reason = ? WHERE id = 1", (new_state, reason))
        await db.commit()
        
    if new_state:
        await message.answer("⏸ Бот поставлен на паузу.")
    else:
        await message.answer("▶️ Работа бота возобновлена.")

@dp.callback_query(F.data.startswith("act:"))
async def on_review_action(callback: types.CallbackQuery, bot: Bot):
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Access denied", show_alert=True)
        return
        
    _, item_id_str, action = callback.data.split(":")
    item_id = int(item_id_str)
    
    async with get_db_connection() as db:
        async with db.execute("SELECT status, post_text_json, image_url, filter_category, url FROM news_items WHERE id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
            
    if not row:
        await callback.answer("Item not found", show_alert=True)
        return
        
    status, post_text, image_url, category, url = row
    
    if status != 'pending_review':
        await callback.answer("Уже обработано", show_alert=True)
        return
        
    if action == "reject":
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Не важно", callback_data=f"reason:{item_id}:not_important")],
            [InlineKeyboardButton(text="Слабый текст", callback_data=f"reason:{item_id}:weak_text")],
            [InlineKeyboardButton(text="Дубликат", callback_data=f"reason:{item_id}:duplicate")],
            [InlineKeyboardButton(text="Другое", callback_data=f"reason:{item_id}:other")]
        ])
        
        try:
            await callback.message.edit_reply_markup(reply_markup=markup)
            await callback.answer("Выберите причину отклонения")
        except Exception as e:
            logger.error(f"Error editing markup: {e}")
            await callback.answer("Ошибка", show_alert=True)
            
    elif action == "approve":
        thread_id = config.topic_mapping.get(category, None)
        
        if image_url and len(post_text) > 1024:
            image_url = None
            
        text = safe_format(post_text, max_len=1024 if image_url else 4096)
        
        try:
            if image_url:
                await bot.send_photo(
                    chat_id=config.target_group_id,
                    message_thread_id=thread_id,
                    photo=image_url,
                    caption=text,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=config.target_group_id,
                    message_thread_id=thread_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                
            async with get_db_connection() as db:
                await db.execute("""
                    UPDATE news_items 
                    SET status = 'published', decision_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (item_id,))
                await db.commit()
                
            if callback.message.caption:
                await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ Опубликовано")
            else:
                await callback.message.edit_text(text=callback.message.text + "\n\n✅ Опубликовано")
                
            await callback.answer("Опубликовано")
            
        except Exception as e:
            logger.error(f"Publishing failed for item {item_id}: {e}")
            await callback.answer(f"Ошибка публикации: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("reason:"))
async def on_reject_reason(callback: types.CallbackQuery):
    if callback.from_user.id != config.admin_chat_id:
        return
        
    _, item_id_str, reason = callback.data.split(":")
    item_id = int(item_id_str)
    
    async with get_db_connection() as db:
        await db.execute("""
            UPDATE news_items 
            SET status = 'rejected', decision_at = CURRENT_TIMESTAMP, reject_reason = ? 
            WHERE id = ?
        """, (reason, item_id))
        await db.commit()
        
    reason_map = {
        "not_important": "Не важно",
        "weak_text": "Слабый текст",
        "duplicate": "Дубликат",
        "other": "Другое"
    }
    
    if callback.message.caption:
        await callback.message.edit_caption(caption=callback.message.caption + f"\n\n❌ Отклонено ({reason_map.get(reason, reason)})")
    else:
        await callback.message.edit_text(text=callback.message.text + f"\n\n❌ Отклонено ({reason_map.get(reason, reason)})")
        
    await callback.answer("Отклонено")
