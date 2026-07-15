import json
import os
import aiosqlite
from aiogram import Bot, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from bot.config import config
from bot.db import get_db_connection
from bot.telegram.handlers import dp
from bot.utils.logger import logger
from bot.telegram.formatting import safe_format

@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    parts = message.text.split()
    days = None
    if len(parts) > 1 and parts[1].isdigit():
        days = int(parts[1])
        
    query = """
        SELECT title, url, summary, filter_category, filter_reason, filter_confidence, 
               post_text_json, status, reject_reason, decision_at
        FROM news_items
        WHERE status IN ('published', 'rejected', 'retracted')
    """
    
    if days:
        query += f" AND decision_at >= datetime('now', '-{days} day')"
        
    async with get_db_connection() as db:
        async with db.execute(query) as cur:
            rows = await cur.fetchall()
            
    if not rows:
        await message.answer("Нет данных для экспорта.")
        return
        
    out = []
    for r in rows:
        decision = "approved" if r[7] in ("published", "retracted") else "rejected"
        out.append(json.dumps({
            "title": r[0],
            "url": r[1],
            "summary": r[2],
            "filter_category": r[3],
            "filter_reason": r[4],
            "filter_confidence": r[5],
            "post_text": r[6],
            "decision": decision,
            "reject_reason": r[8],
            "decision_at": r[9]
        }, ensure_ascii=False))
        
    file_content = "\n".join(out).encode("utf-8")
    file = BufferedInputFile(file_content, filename="export.jsonl")
    await message.answer_document(file)

@dp.message(Command("review"))
async def cmd_review(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT status, COUNT(*) FROM news_items 
            WHERE status IN ('published', 'rejected', 'retracted')
            AND decision_at >= datetime('now', '-30 day')
            GROUP BY status
        """) as cur:
            status_counts = dict(await cur.fetchall())
            
        approved = status_counts.get("published", 0) + status_counts.get("retracted", 0)
        rejected = status_counts.get("rejected", 0)
        total = approved + rejected
        
        async with db.execute("""
            SELECT filter_category, 
                   SUM(CASE WHEN status IN ('published', 'retracted') THEN 1 ELSE 0 END) as app,
                   COUNT(*) as tot
            FROM news_items
            WHERE status IN ('published', 'rejected', 'retracted')
            AND decision_at >= datetime('now', '-30 day')
            GROUP BY filter_category
        """) as cur:
            cat_stats = await cur.fetchall()
            
        async with db.execute("""
            SELECT s.name, 
                   SUM(CASE WHEN n.status IN ('published', 'retracted') THEN 1 ELSE 0 END) as app,
                   COUNT(*) as tot
            FROM news_items n
            JOIN sources s ON n.source_id = s.id
            WHERE n.status IN ('published', 'rejected', 'retracted')
            AND n.decision_at >= datetime('now', '-30 day')
            GROUP BY s.id
        """) as cur:
            src_stats = await cur.fetchall()
            
        async with db.execute("""
            SELECT reject_reason, COUNT(*) 
            FROM news_items 
            WHERE status = 'rejected' 
            AND decision_at >= datetime('now', '-30 day')
            GROUP BY reject_reason
        """) as cur:
            reject_stats = await cur.fetchall()
            
        async with db.execute("""
            SELECT title, reject_reason 
            FROM news_items 
            WHERE status = 'rejected' 
            ORDER BY decision_at DESC LIMIT 10
        """) as cur:
            recent_rejects = await cur.fetchall()
            
    text = f"<b>Сводка за 30 дней:</b>\n"
    if total == 0:
        text += "Нет решений."
        await message.answer(text, parse_mode="HTML")
        return
        
    text += f"Всего решений: {total} ({int(approved/total*100)}% одобрено)\n\n"
    
    text += "<b>По категориям (Одобрено / Всего):</b>\n"
    for cat, app, tot in cat_stats:
        text += f"— {cat or 'none'}: {app}/{tot} ({int(app/tot*100)}%)\n"
        
    text += "\n<b>По источникам:</b>\n"
    for src, app, tot in src_stats:
        text += f"— {src}: {app}/{tot} ({int(app/tot*100)}%)\n"
        
    text += "\n<b>Причины отклонения:</b>\n"
    for reason, count in reject_stats:
        text += f"— {reason or 'none'}: {count}\n"
        
    text += "\n<b>Последние 10 отклонённых:</b>\n"
    for title, reason in recent_rejects:
        text += f"— {title[:30]}... ({reason or 'none'})\n"
        
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("autostats"))
async def cmd_autostats(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT filter_category, 
                   SUM(CASE WHEN status IN ('published', 'retracted') THEN 1 ELSE 0 END) as app,
                   COUNT(*) as tot
            FROM news_items
            WHERE filter_confidence IS NOT NULL
            AND status IN ('published', 'rejected', 'retracted')
            GROUP BY filter_category
        """) as cur:
            stats = await cur.fetchall()
            
    text = "<b>Agreement Rate (фильтр vs админ):</b>\n\n"
    if not stats:
        text += "Пока нет данных."
    for cat, app, tot in stats:
        if tot > 0:
            text += f"{cat or 'none'}: {int(app/tot*100)}% совпадений, {tot} решений\n"
            
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("autopublish"))
async def cmd_autopublish(message: types.Message):
    if message.from_user.id != config.admin_chat_id:
        return
        
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Формат: /autopublish [add|remove] [category]")
        return
        
    action = parts[1]
    category = parts[2]
    
    async with get_db_connection() as db:
        async with db.execute("SELECT auto_publish_categories FROM bot_state WHERE id = 1") as cur:
            cats = (await cur.fetchone())[0]
            
        cat_list = [c.strip() for c in cats.split(',')] if cats else []
        
        if action == "add":
            if category not in cat_list:
                cat_list.append(category)
        elif action == "remove":
            if category in cat_list:
                cat_list.remove(category)
                
        new_cats = ",".join(cat_list)
        await db.execute("UPDATE bot_state SET auto_publish_categories = ? WHERE id = 1", (new_cats,))
        await db.commit()
        
    await message.answer(f"Список авто-публикации обновлен: {new_cats}")

@dp.callback_query(F.data.startswith("undo:"))
async def on_undo_publish(callback: types.CallbackQuery, bot: Bot):
    if callback.from_user.id != config.admin_chat_id:
        return
        
    _, item_id_str = callback.data.split(":")
    item_id = int(item_id_str)
    
    async with get_db_connection() as db:
        async with db.execute("SELECT telegram_message_id FROM news_items WHERE id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
            
    if not row or not row[0]:
        await callback.answer("Message ID not found", show_alert=True)
        return
        
    try:
        await bot.delete_message(chat_id=config.target_group_id, message_id=row[0])
    except Exception as e:
        logger.warning(f"Could not delete message: {e}")
        
    async with get_db_connection() as db:
        await db.execute("UPDATE news_items SET status = 'retracted', decision_at = CURRENT_TIMESTAMP WHERE id = ?", (item_id,))
        await db.commit()
        
    await callback.message.edit_text(callback.message.text + "\n\n↩️ Отменено")
    await callback.answer("Отменено")

async def send_auto_published_to_group(bot: Bot, item_id: int, category: str, url: str, post_text: str, image_url: str | None):
    thread_id = config.topic_mapping.get(category, None)
    
    if thread_id is None:
        err_msg = f"Не настроена тема для категории '{category}' в TOPIC_MAPPING (Авто-публикация)."
        logger.error(err_msg)
        try:
            await bot.send_message(chat_id=config.admin_chat_id, text=f"❌ Ошибка авто-публикации: {err_msg}")
        except:
            pass
        return
        
    if image_url and len(post_text) > 1024:
        image_url = None
        
    text = safe_format(post_text, max_len=1024 if image_url else 4096)
    
    try:
        if image_url:
            msg = await bot.send_photo(
                chat_id=config.target_group_id,
                message_thread_id=thread_id,
                photo=image_url,
                caption=text,
                parse_mode="HTML"
            )
        else:
            msg = await bot.send_message(
                chat_id=config.target_group_id,
                message_thread_id=thread_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            
        async with get_db_connection() as db:
            await db.execute("UPDATE news_items SET telegram_message_id = ? WHERE id = ?", (msg.message_id, item_id))
            await db.commit()
            
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        import re
        title = "Новость"
        match = re.search(r'<b>(.*?)</b>', text)
        if match:
            title = match.group(1)
            
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Отменить", callback_data=f"undo:{item_id}")]
        ])
        await bot.send_message(
            chat_id=config.admin_chat_id,
            text=f"🤖 Авто-опубликовано: {title}",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Auto-publishing failed for item {item_id}: {e}")

@dp.message(Command("testpost"))
async def cmd_testpost(message: types.Message, bot: Bot):
    if message.from_user.id != config.admin_chat_id:
        return
        
    await message.answer("Создаю тестовый пост...")
    
    from bot.telegram.handlers import send_draft_to_admin
    
    test_title = "Тестовая новость для проверки публикации"
    test_url = "https://example.com/test-news"
    test_url_hash = "testhash123"
    test_title_hash = "testhash123"
    test_summary = "Это тестовое саммари новости."
    test_post_text = "<b>Тестовая новость для проверки публикации</b>\n\nЭто сгенерированный тестовый текст. Если вы нажмете «Опубликовать», он должен попасть в нужную тему (например, AI Новости с ID 13).\n\n<a href='https://example.com/test-news'>Читать далее</a>"
    
    async with get_db_connection() as db:
        async with db.execute("SELECT id FROM sources LIMIT 1") as cur:
            row = await cur.fetchone()
            source_id = row[0] if row else 1
            
        cursor = await db.execute("""
            INSERT INTO news_items (source_id, url, url_hash, title_hash, title, summary, status, filter_category, post_text_json)
            VALUES (?, ?, ?, ?, ?, ?, 'pending_review', 'other', ?)
        """, (source_id, test_url, test_url_hash, test_title_hash, test_title, test_summary, test_post_text))
        item_id = cursor.lastrowid
        await db.commit()
        
    await send_draft_to_admin(bot, item_id, test_post_text, None)

