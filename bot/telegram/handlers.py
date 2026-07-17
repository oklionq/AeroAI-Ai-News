import asyncio
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InputMediaPhoto
from bot.config import config
from bot.telegram.keyboards import get_main_keyboard, get_review_keyboard
from bot.telegram.formatting import safe_format
from bot.services.stats_service import get_stats
from bot.db import get_db_connection
from bot.utils.logger import logger

dp = Dispatcher()


# ---------------------------------------------------------------------------
# Sending draft to admin (supports 0, 1, or 2+ photos)
# ---------------------------------------------------------------------------

async def send_draft_to_admin(bot: Bot, item_id: int, post_text: str, image_urls: list[str] | None):
    """
    Send a news draft to admin for review.
    - 0 images: text message with inline buttons
    - 1 image: sendPhoto with caption + inline buttons
    - 2+ images: sendMediaGroup (album, no buttons) + separate text message with inline buttons
    """
    if image_urls is None:
        image_urls = []

    markup = get_review_keyboard(item_id)

    try:
        if len(image_urls) >= 2:
            # --- Album mode ---
            media = [InputMediaPhoto(media=url) for url in image_urls[:4]]
            album_messages = await bot.send_media_group(
                chat_id=config.admin_chat_id,
                media=media
            )

            # Save file_ids from Telegram's response (largest size = last in .photo list)
            file_ids = []
            album_msg_ids = []
            for msg in album_messages:
                album_msg_ids.append(msg.message_id)
                if msg.photo:
                    file_ids.append(msg.photo[-1].file_id)

            # Send control message with full text + buttons
            text = safe_format(post_text, max_len=4096)
            control_msg = await bot.send_message(
                chat_id=config.admin_chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True
            )

            # Persist file_ids, album message IDs, and control message ID
            async with get_db_connection() as db:
                await db.execute("""
                    UPDATE news_items 
                    SET image_file_ids = ?, telegram_album_message_ids = ?, telegram_control_message_id = ?
                    WHERE id = ?
                """, (json.dumps(file_ids), json.dumps(album_msg_ids), control_msg.message_id, item_id))
                await db.commit()

        elif len(image_urls) == 1:
            # --- Single photo mode ---
            text = safe_format(post_text, max_len=1024)
            if len(post_text) > 1024:
                # Caption too long for photo — send as text instead
                control_msg = await bot.send_message(
                    chat_id=config.admin_chat_id,
                    text=safe_format(post_text, max_len=4096),
                    parse_mode="HTML",
                    reply_markup=markup,
                    disable_web_page_preview=True
                )
                async with get_db_connection() as db:
                    await db.execute(
                        "UPDATE news_items SET telegram_control_message_id = ? WHERE id = ?",
                        (control_msg.message_id, item_id)
                    )
                    await db.commit()
            else:
                msg = await bot.send_photo(
                    chat_id=config.admin_chat_id,
                    photo=image_urls[0],
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=markup
                )
                # Save file_id for reuse during publishing
                file_ids = [msg.photo[-1].file_id] if msg.photo else []
                async with get_db_connection() as db:
                    await db.execute("""
                        UPDATE news_items 
                        SET image_file_ids = ?, telegram_control_message_id = ?
                        WHERE id = ?
                    """, (json.dumps(file_ids), msg.message_id, item_id))
                    await db.commit()
        else:
            # --- No image mode ---
            text = safe_format(post_text, max_len=4096)
            control_msg = await bot.send_message(
                chat_id=config.admin_chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True
            )
            async with get_db_connection() as db:
                await db.execute(
                    "UPDATE news_items SET telegram_control_message_id = ? WHERE id = ?",
                    (control_msg.message_id, item_id)
                )
                await db.commit()

    except TelegramRetryAfter as e:
        logger.warning(f"Rate limited by Telegram. Retrying after {e.retry_after} seconds.")
        await asyncio.sleep(e.retry_after)
        await send_draft_to_admin(bot, item_id, post_text, image_urls)
    except Exception as e:
        logger.error(f"Failed to send draft for item {item_id}: {e}")
        # fallback: retry without images
        if image_urls:
            await send_draft_to_admin(bot, item_id, post_text, [])


# ---------------------------------------------------------------------------
# Bot commands
# ---------------------------------------------------------------------------

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
        
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone
    tz = ZoneInfo(config.display_timezone)
    
    text = "<b>Очередь модерации:</b>\n\n"
    for title, collected_at in items:
        try:
            dt = datetime.strptime(collected_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            local_time = dt.astimezone(tz).strftime("%d.%m.%Y %H:%M:%S")
        except:
            local_time = collected_at
        text += f"— {title} <i>({local_time})</i>\n"
        
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("cycle"))
@dp.message(Command("parsing"))
@dp.message(F.text == "🔄 Парсинг")
async def cmd_cycle(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    async with get_db_connection() as db:
        async with db.execute("SELECT is_paused, next_poll_at FROM bot_state WHERE id = 1") as cur:
            state_row = await cur.fetchone()
            is_paused = state_row[0] if state_row else 0
            next_poll_at_str = state_row[1] if state_row else None
            
        async with db.execute("""
            SELECT started_at, finished_at, duration_seconds, sources_total, sources_ok, sources_failed,
                   items_raw, items_filtered_stale, items_after_dedup, items_passed_filter, items_sent_moderation, items_auto_published,
                   errors_count, last_errors_json, status
            FROM poll_cycles
            ORDER BY id DESC LIMIT 1
        """) as cur:
            cycle_row = await cur.fetchone()
            
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone
    tz = ZoneInfo(config.display_timezone)
    
    def format_time(t_str):
        if not t_str: return "неизвестно"
        try:
            dt = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M:%S")
        except:
            return t_str
            
    def format_relative(t_str):
        if not t_str: return ""
        try:
            dt = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            diff = (dt - datetime.now(timezone.utc)).total_seconds()
            if diff <= 0: return "уже скоро"
            mins = int(diff / 60)
            if mins == 0: return "меньше минуты"
            return f"~{mins} минут"
        except:
            return ""

    if not cycle_row:
        next_time = format_time(next_poll_at_str)
        await message.answer(f"Циклов парсинга пока не было — первый запланирован на {next_time}")
        return
        
    (started_at, finished_at, duration, sources_total, sources_ok, sources_failed,
     items_raw, items_filtered_stale, items_after_dedup, items_passed_filter, items_sent_moderation, items_auto_published,
     errors_count, last_errors_json, status) = cycle_row
     
    if status == 'running':
        await message.answer(
            f"⏳ Цикл парсинга выполняется сейчас\nНачат: {format_time(started_at)}"
        )
        return
        
    text = f"🔄 <b>Последний цикл парсинга</b>\n\n"
    text += f"Завершён: {format_time(finished_at)}\n"
    text += f"Длительность: {duration} сек\n\n"
    text += f"Источники: {sources_ok}/{sources_total} успешно\n"
    text += f"Новых записей найдено: {items_raw}\n"
    if items_filtered_stale is not None:
        text += f"Отсеяно как устаревшие: {items_filtered_stale}\n"
    text += f"После проверки на дубли: {items_after_dedup}\n"
    text += f"Прошло фильтр важности: {items_passed_filter}\n"
    text += f"Отправлено на модерацию: {items_sent_moderation}\n"
    text += f"Авто-опубликовано: {items_auto_published}\n"
    text += f"Ошибок за цикл: {errors_count}"
    
    if errors_count > 0 and last_errors_json:
        try:
            errs = json.loads(last_errors_json)
            if errs:
                text += "\n\n<i>Ошибки:</i>\n" + "\n".join([f"— {e}" for e in errs])
        except:
            pass
            
    text += "\n\n"
    if is_paused:
        text += "⏸ Агент на паузе — следующий цикл не запланирован. Возобновить: /resume"
    else:
        text += f"⏭ Следующий цикл: {format_time(next_poll_at_str)} (через {format_relative(next_poll_at_str)})"
        
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
        
        if new_state:
            # Paused
            await db.execute("UPDATE bot_state SET is_paused = ?, pause_reason = ?, next_poll_at = NULL WHERE id = 1", (new_state, reason))
        else:
            # Resumed
            from datetime import datetime, timezone
            import datetime as dt
            now = datetime.now(timezone.utc)
            next_poll_at = now + dt.timedelta(minutes=config.poll_interval_minutes)
            next_poll_at_str = next_poll_at.strftime("%Y-%m-%d %H:%M:%S")
            await db.execute("UPDATE bot_state SET is_paused = ?, pause_reason = ?, next_poll_at = ? WHERE id = 1", (new_state, reason, next_poll_at_str))
            
        await db.commit()
        
    if new_state:
        await message.answer("⏸ Бот поставлен на паузу.")
    else:
        await message.answer("▶️ Работа бота возобновлена.")


# ---------------------------------------------------------------------------
# Review callbacks (approve / reject) — supports album + control message
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("act:"))
async def on_review_action(callback: types.CallbackQuery, bot: Bot):
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Access denied", show_alert=True)
        return
        
    _, item_id_str, action = callback.data.split(":")
    item_id = int(item_id_str)
    
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT status, post_text_json, image_urls, image_file_ids, filter_category, url
            FROM news_items WHERE id = ?
        """, (item_id,)) as cur:
            row = await cur.fetchone()
            
    if not row:
        await callback.answer("Item not found", show_alert=True)
        return
        
    status, post_text, image_urls_json, image_file_ids_json, category, url = row
    
    if status != 'pending_review':
        await callback.answer("Уже обработано", show_alert=True)
        return

    # Parse JSON fields
    try:
        image_urls = json.loads(image_urls_json) if image_urls_json else []
    except (json.JSONDecodeError, TypeError):
        image_urls = []

    try:
        image_file_ids = json.loads(image_file_ids_json) if image_file_ids_json else []
    except (json.JSONDecodeError, TypeError):
        image_file_ids = []
        
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
        
        if thread_id is None:
            thread_id = config.default_topic_id
            logger.info(f"Категория '{category}' не найдена в TOPIC_MAPPING. Используем DEFAULT_TOPIC_ID ({thread_id}).")
        
        try:
            if len(image_file_ids) >= 2:
                # --- Publish album using saved file_ids ---
                media = [InputMediaPhoto(media=fid) for fid in image_file_ids[:4]]
                await bot.send_media_group(
                    chat_id=config.target_group_id,
                    message_thread_id=thread_id,
                    media=media
                )
                # Send text as separate message
                text = safe_format(post_text, max_len=4096)
                await bot.send_message(
                    chat_id=config.target_group_id,
                    message_thread_id=thread_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )

            elif len(image_file_ids) == 1:
                text = safe_format(post_text, max_len=1024)
                if len(post_text) > 1024:
                    # Caption too long — send text only
                    await bot.send_message(
                        chat_id=config.target_group_id,
                        message_thread_id=thread_id,
                        text=safe_format(post_text, max_len=4096),
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                else:
                    await bot.send_photo(
                        chat_id=config.target_group_id,
                        message_thread_id=thread_id,
                        photo=image_file_ids[0],
                        caption=text,
                        parse_mode="HTML"
                    )

            elif len(image_urls) >= 2:
                # Fallback: no file_ids saved, use original URLs
                media = [InputMediaPhoto(media=u) for u in image_urls[:4]]
                await bot.send_media_group(
                    chat_id=config.target_group_id,
                    message_thread_id=thread_id,
                    media=media
                )
                text = safe_format(post_text, max_len=4096)
                await bot.send_message(
                    chat_id=config.target_group_id,
                    message_thread_id=thread_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )

            elif len(image_urls) == 1:
                text = safe_format(post_text, max_len=1024)
                if len(post_text) > 1024:
                    await bot.send_message(
                        chat_id=config.target_group_id,
                        message_thread_id=thread_id,
                        text=safe_format(post_text, max_len=4096),
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                else:
                    await bot.send_photo(
                        chat_id=config.target_group_id,
                        message_thread_id=thread_id,
                        photo=image_urls[0],
                        caption=text,
                        parse_mode="HTML"
                    )
            else:
                # No images
                text = safe_format(post_text, max_len=4096)
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
                
            # Update control message to show "Published" status
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
