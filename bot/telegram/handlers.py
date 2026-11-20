import os
import asyncio
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InputMediaPhoto, InputMediaVideo, FSInputFile
from bot.config import config
from bot.telegram.keyboards import get_main_keyboard, get_review_keyboard
from bot.telegram.formatting import safe_format, safe_caption
from bot.services.stats_service import get_stats
from bot.db import get_db_connection
from bot.utils.logger import logger

dp = Dispatcher()

def to_media_input(src: str):
    """Преобразует строку пути к файлу или URL в подходящий для aiogram тип."""
    if isinstance(src, str) and os.path.exists(src):
        return FSInputFile(src)
    return src

def is_video(media: any, fallback_url: str | None = None) -> bool:
    """
    Проверяет, является ли медиафайл видео:
    - по расширению локального файла или URL (.mp4, .mov, .webm, .mkv, .avi)
    - по оригинальному пути из fallback_url (если media - это Telegram file_id)
    - по сигнатуре Telegram Bot API file_id (префиксы BA... или BQ...)
    """
    if not media:
        return False
        
    s = str(media).strip()
    lower = s.lower()
    
    # 1. Проверка расширения в самом значении
    if lower.endswith(('.mp4', '.mov', '.webm', '.mkv', '.avi')):
        return True
    if lower.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
        return False
        
    # 2. Проверка по fallback_url
    if fallback_url and isinstance(fallback_url, str):
        fb_lower = fallback_url.lower()
        if fb_lower.endswith(('.mp4', '.mov', '.webm', '.mkv', '.avi')):
            return True
        if fb_lower.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
            return False
            
    # 3. Проверка сигнатуры Telegram Bot API file_id
    if len(s) > 30 and '/' not in s and '\\' not in s and not s.startswith("http"):
        if s.startswith(('BA', 'BQ')):
            return True
        if s.startswith(('Ag', 'AQ')):
            return False

    return False

# ---------------------------------------------------------------------------
# Sending draft to admin (supports 0, 1, or 2+ photos)
# ---------------------------------------------------------------------------

async def send_draft_to_admin(bot: Bot, item_id: int, post_text: str, image_urls: list[str] | None):
    """
    Отправляет черновик поста администратору в ЛС на проверку.
    Медиа (фото или видео) и текст ВСЕГДА отправляются вместе в ОДНОМ сообщении:
    - При наличии медиа: sendPhoto / sendVideo с текстом в caption и кнопками одобрить/отклонить.
    - Без медиа: send_message с текстом и кнопками.
    """
    if image_urls is None:
        image_urls = []

    markup = get_review_keyboard(item_id)

    try:
        if image_urls:
            main_media = image_urls[0]
            if len(image_urls) > 1:
                note = f"\n\n<i>📸 В посте {len(image_urls)} медиа (будут опубликованы вместе)</i>"
                caption = safe_caption(post_text, max_len=1024 - len(note)) + note
            else:
                caption = safe_caption(post_text, max_len=1024)

            if is_video(main_media):
                msg = await bot.send_video(
                    chat_id=config.admin_chat_id,
                    video=to_media_input(main_media),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=markup,
                    supports_streaming=True
                )
                file_ids = [msg.video.file_id] if msg.video else []
            else:
                msg = await bot.send_photo(
                    chat_id=config.admin_chat_id,
                    photo=to_media_input(main_media),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=markup
                )
                file_ids = [msg.photo[-1].file_id] if msg.photo else []

            async with get_db_connection() as db:
                await db.execute("""
                    UPDATE news_items 
                    SET image_file_ids = ?, telegram_control_message_id = ?
                    WHERE id = ?
                """, (json.dumps(file_ids), msg.message_id, item_id))
                await db.commit()
        else:
            # Текстовый пост без фото
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

    except TelegramRetryAfter as e:
        logger.warning(f"Rate limited by Telegram. Retrying after {e.retry_after} seconds.")
        await asyncio.sleep(e.retry_after)
        await send_draft_to_admin(bot, item_id, post_text, image_urls)
    except Exception as e:
        logger.error(f"Failed to send draft for item {item_id}: {e}", exc_info=True)
        err_str = str(e).lower()
        if "can't parse entities" in err_str:
            # Fallback: send as plain text without broken HTML tags
            clean_text = re.sub(r'<[^>]+>', '', post_text)
            try:
                control_msg = await bot.send_message(
                    chat_id=config.admin_chat_id,
                    text=clean_text[:4096],
                    reply_markup=markup
                )
                async with get_db_connection() as db:
                    await db.execute(
                        "UPDATE news_items SET telegram_control_message_id = ? WHERE id = ?",
                        (control_msg.message_id, item_id)
                    )
                    await db.commit()
                return
            except Exception as e2:
                logger.error(f"Fallback plain text failed for {item_id}: {e2}")
        elif image_urls:
            # fallback: retry without images
            await send_draft_to_admin(bot, item_id, post_text, [])


# ---------------------------------------------------------------------------
# Bot commands
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
    await message.answer(
        f"🤖 <b>AI News Agent готов к работе</b>\n\n"
        f"Источник: @{config.donor_channel}\n"
        f"Модель: <code>{config.filter_model}</code> (Venice AI)\n"
        f"Все посты направляются в этот диалог для вашей проверки.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@dp.message(Command("stats"))
@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
    
    stats = await get_stats()
    
    # Reasons text
    reasons_text = ""
    if stats.get('reject_reasons'):
        reason_map = {
            "not_important": "Не важно",
            "weak_text": "Слабый текст",
            "duplicate": "Дубликат",
            "other": "Другое"
        }
        reasons_list = [f"  • {reason_map.get(k, k)}: {v}" for k, v in stats['reject_reasons'].items()]
        reasons_text = "\n<i>Причины отклонения:</i>\n" + "\n".join(reasons_list)

    text = (
        f"📊 <b>Полная статистика:</b>\n\n"
        f"<b>Посты (@{config.donor_channel}):</b>\n"
        f"— Всего собрано: <b>{stats['total_collected']}</b> (за 24ч: {stats['collected_24h']})\n"
        f"— Прошло фильтр важности: <b>{stats['passed_filter']}</b>\n"
        f"— Отсеяно фильтром: <b>{stats['filtered_out']}</b>\n"
        f"— В очереди на модерацию: <b>{stats['pending_review']}</b>\n"
        f"— Одобрено и опубликовано: <b>{stats['approved']}</b>\n"
        f"— Отклонено: <b>{stats['rejected']}</b>{reasons_text}\n\n"
        f"<b>LLM и Бюджет (Venice AI):</b>\n"
        f"— Модель: <code>{config.filter_model}</code>\n"
        f"— Использовано токенов: <b>{stats['total_tokens']:,}</b>\n"
        f"— Потрачено: <b>${stats['budget_spent']:.3f}</b> / ${config.max_budget_usd:.2f}\n"
        f"— Расход за этот месяц: <b>${stats['cost_this_month']:.3f}</b>\n"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("status"))
@dp.message(F.text == "⚙️ Статус")
async def cmd_status(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    stats = await get_stats()
    status_text = "⏸ На паузе" if stats['is_paused'] else "▶️ Активен"
    reason = f" ({stats['pause_reason']})" if stats['pause_reason'] else ""
    
    text = (
        f"⚙️ <b>Статус бота:</b> {status_text}{reason}\n"
        f"Канал-донор: <b>@{config.donor_channel}</b>\n"
        f"Интервал опроса: <b>{config.poll_interval_minutes} мин.</b>\n"
        f"Режим публикации: <b>Премодерация в ЛС</b>\n"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("queue"))
@dp.message(F.text == "🗂 Очередь")
async def cmd_queue(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    async with get_db_connection() as db:
        async with db.execute("SELECT id, title, collected_at FROM news_items WHERE status = 'pending_review' ORDER BY collected_at ASC LIMIT 20") as cur:
            items = await cur.fetchall()
            
    if not items:
        await message.answer("🗂 Очередь модерации пуста.")
        return
        
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone
    tz = ZoneInfo(config.display_timezone)
    
    text = f"🗂 <b>Очередь модерации ({len(items)}):</b>\n\n"
    for item_id, title, collected_at in items:
        try:
            dt = datetime.strptime(collected_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            local_time = dt.astimezone(tz).strftime("%d.%m %H:%M")
        except:
            local_time = collected_at or ""
        text += f"• <b>[#{item_id}]</b> {title[:50]}... <i>({local_time})</i>\n"
        
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
            f"⏳ Цикл парсинга выполняется прямо сейчас...\nНачат: {format_time(started_at)}"
        )
        return
        
    text = f"🔄 <b>Последний цикл парсинга (@{config.donor_channel})</b>\n\n"
    text += f"Завершён: {format_time(finished_at)}\n"
    text += f"Длительность: {duration} сек\n\n"
    text += f"Новых сообщений найдено: {items_raw}\n"
    if items_filtered_stale is not None:
        text += f"Отсеяно (устаревшие): {items_filtered_stale}\n"
    text += f"После дедупликации: {items_after_dedup}\n"
    text += f"Прошло фильтр важности: {items_passed_filter}\n"
    text += f"Отправлено вам на проверку: {items_sent_moderation}\n"
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
        text += "⏸ Агент на паузе. Возобновить: /resume"
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
            await db.execute("UPDATE bot_state SET is_paused = ?, pause_reason = ?, next_poll_at = NULL WHERE id = 1", (new_state, reason))
        else:
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
# Review callbacks (approve / reject)
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("act:"))
async def on_review_action(callback: types.CallbackQuery, bot: Bot):
    if callback.from_user.id != config.admin_chat_id:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
        
    _, item_id_str, action = callback.data.split(":")
    item_id = int(item_id_str)
    
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT status, post_text_json, image_urls, image_file_ids, filter_category, url, telegram_message_id
            FROM news_items WHERE id = ?
        """, (item_id,)) as cur:
            row = await cur.fetchone()
            
    if row:
        status, post_text, image_urls_json, image_file_ids_json, category, url, existing_pub_id = row
    else:
        status = None
        post_text = None
        image_urls_json = "[]"
        image_file_ids_json = "[]"
        category = "other"
        url = ""
        existing_pub_id = None
        
    # Блокируем только если пост ДЕЙСТВИТЕЛЬНО уже опубликован в канал
    if status == 'published' and existing_pub_id:
        await callback.answer("Этот пост уже опубликован в приватную группу!", show_alert=True)
        return

    # Если текста нет в БД (например, после перезапуска контейнера), берем его прямо из сообщения Telegram
    if not post_text:
        msg_text = callback.message.caption or callback.message.text or ""
        # Очищаем от возможных системных пометок
        post_text = re.sub(r'\n\n📸 В посте \d+ медиа \(будут опубликованы вместе\)', '', msg_text).strip()

    # Парсим медиа
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
        
        caption_text = safe_caption(post_text, max_len=1024)

        # Разрешаем медиа:
        local_media = []
        if image_urls:
            for u in image_urls:
                if isinstance(u, str) and (os.path.exists(u) or u.startswith("http")):
                    local_media.append(u)

        stored_file_ids = [fid for fid in image_file_ids if fid] if image_file_ids else []

        # Если есть локальные файлы и их не меньше, чем сохраненных file_id (полный альбом), используем их
        if local_media and len(local_media) >= len(stored_file_ids):
            media_items = local_media
        elif stored_file_ids:
            media_items = stored_file_ids
        elif local_media:
            media_items = local_media
        else:
            media_items = []
        
        # Если в базе нет медиа, но само сообщение в ЛС содержит фото или видео
        if not media_items:
            if callback.message.photo:
                media_items.append(callback.message.photo[-1].file_id)
            elif callback.message.video:
                media_items.append(callback.message.video.file_id)

        try:
            published_msg_id = None
            published_album_ids = []
            if media_items:
                if len(media_items) >= 2:
                    # Публикуем альбом: текст прикреплён к ПЕРВОМУ элементу
                    media = []
                    for i, m in enumerate(media_items[:4]):
                        cap = caption_text if i == 0 else None
                        pm = "HTML" if i == 0 else None
                        orig_u = image_urls[i] if i < len(image_urls) else None
                        if is_video(m, fallback_url=orig_u):
                            media.append(InputMediaVideo(
                                media=to_media_input(m),
                                caption=cap,
                                parse_mode=pm,
                                supports_streaming=True
                            ))
                        else:
                            media.append(InputMediaPhoto(
                                media=to_media_input(m),
                                caption=cap,
                                parse_mode=pm
                            ))
                    try:
                        published_msgs = await bot.send_media_group(
                            chat_id=config.target_group_id,
                            message_thread_id=thread_id,
                            media=media
                        )
                        published_msg_id = published_msgs[0].message_id
                        published_album_ids = [m.message_id for m in published_msgs]
                    except Exception as album_err:
                        err_str = str(album_err).lower()
                        if "video as photo" in err_str:
                            logger.warning(f"send_media_group failed with video-as-photo for item {item_id}, retrying with converted types...")
                            retry_media = []
                            for itm in media:
                                if isinstance(itm, InputMediaPhoto):
                                    retry_media.append(InputMediaVideo(
                                        media=itm.media,
                                        caption=itm.caption,
                                        parse_mode=itm.parse_mode,
                                        supports_streaming=True
                                    ))
                                else:
                                    retry_media.append(itm)
                            published_msgs = await bot.send_media_group(
                                chat_id=config.target_group_id,
                                message_thread_id=thread_id,
                                media=retry_media
                            )
                            published_msg_id = published_msgs[0].message_id
                            published_album_ids = [m.message_id for m in published_msgs]
                        else:
                            raise
                else:
                    single_media = media_items[0]
                    orig_u = image_urls[0] if image_urls else None
                    is_vid = is_video(single_media, fallback_url=orig_u)
                    if not is_vid and callback.message.video and not callback.message.photo:
                        is_vid = True

                    try:
                        if is_vid:
                            published_msg = await bot.send_video(
                                chat_id=config.target_group_id,
                                message_thread_id=thread_id,
                                video=to_media_input(single_media),
                                caption=caption_text,
                                parse_mode="HTML",
                                supports_streaming=True
                            )
                        else:
                            published_msg = await bot.send_photo(
                                chat_id=config.target_group_id,
                                message_thread_id=thread_id,
                                photo=to_media_input(single_media),
                                caption=caption_text,
                                parse_mode="HTML"
                            )
                    except Exception as single_err:
                        err_str = str(single_err).lower()
                        if "video as photo" in err_str or "can't use file of type video as photo" in err_str:
                            logger.info(f"Retrying single publication as video for item {item_id}...")
                            published_msg = await bot.send_video(
                                chat_id=config.target_group_id,
                                message_thread_id=thread_id,
                                video=to_media_input(single_media),
                                caption=caption_text,
                                parse_mode="HTML",
                                supports_streaming=True
                            )
                        elif "photo as video" in err_str or "can't use file of type photo as video" in err_str:
                            logger.info(f"Retrying single publication as photo for item {item_id}...")
                            published_msg = await bot.send_photo(
                                chat_id=config.target_group_id,
                                message_thread_id=thread_id,
                                photo=to_media_input(single_media),
                                caption=caption_text,
                                parse_mode="HTML"
                            )
                        else:
                            raise

                    published_msg_id = published_msg.message_id
            else:
                # Текстовое сообщение
                published_msg = await bot.send_message(
                    chat_id=config.target_group_id,
                    message_thread_id=thread_id,
                    text=safe_format(post_text, max_len=4096),
                    parse_mode="HTML"
                )
                published_msg_id = published_msg.message_id
                
            async with get_db_connection() as db:
                if row:
                    await db.execute("""
                        UPDATE news_items 
                        SET status = 'published', decision_at = CURRENT_TIMESTAMP, telegram_message_id = ?, telegram_album_message_ids = ?
                        WHERE id = ?
                    """, (published_msg_id, json.dumps(published_album_ids), item_id))
                else:
                    await db.execute("""
                        INSERT INTO news_items (id, source_id, url, url_hash, title_hash, title, summary, status, decision_at, telegram_message_id, telegram_album_message_ids)
                        VALUES (?, 1, ?, ?, ?, ?, ?, 'published', CURRENT_TIMESTAMP, ?, ?)
                    """, (item_id, url or f"https://t.me/{config.donor_channel}", f"manual_{item_id}", f"manual_{item_id}", post_text[:60], post_text, published_msg_id, json.dumps(published_album_ids)))
                await db.commit()
                
            # Обновляем контрольное сообщение в ЛС (с защитой от превышения лимита caption 1024)
            tag = "\n\n✅ Опубликовано в приватную группу"
            try:
                if callback.message.caption:
                    new_cap = safe_caption(callback.message.caption, max_len=1024 - len(tag)) + tag
                    await callback.message.edit_caption(caption=new_cap, reply_markup=None)
                else:
                    await callback.message.edit_text(text=callback.message.text + tag, reply_markup=None)
            except Exception as em:
                logger.warning(f"Could not edit message markup in PM: {em}")
                
            await callback.answer("✅ Успешно опубликовано в приватную группу!", show_alert=True)
            
        except Exception as e:
            logger.error(f"Publishing failed for item {item_id}: {e}", exc_info=True)
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
    
    reason_title = reason_map.get(reason, reason)
    tag = f"\n\n❌ Отклонено ({reason_title})"
    if callback.message.caption:
        new_cap = safe_caption(callback.message.caption, max_len=1024 - len(tag)) + tag
        await callback.message.edit_caption(caption=new_cap, reply_markup=None)
    else:
        await callback.message.edit_text(text=callback.message.text + tag, reply_markup=None)
        
    await callback.answer(f"Отклонено: {reason_title}")
