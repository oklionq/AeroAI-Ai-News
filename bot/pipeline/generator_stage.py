import json
import re
import datetime

from bot.config import config
from bot.db import get_db_connection
from bot.services.openai_client import generate_post_text
from bot.services.budget_guard import check_budget
from bot.pipeline.images import filter_valid_local_images
from bot.utils.logger import logger

async def run_generator_stage(bot_instance, tracker=None):
    if not await check_budget():
        logger.info("Budget limit reached. Skipping generator stage.")
        return
        
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT n.id, n.title, n.summary, s.name, n.filter_category, n.url, n.image_urls, s.type, n.published_at, n.subject
            FROM news_items n
            JOIN sources s ON n.source_id = s.id
            WHERE n.status = 'pending_generation'
            LIMIT 10
        """) as cursor:
            items = await cursor.fetchall()
            
    for item_id, title, summary, source_name, category, url, existing_image_urls_json, source_type, pub_date_str, subject in items:
        if not await check_budget():
            break
            
        # Lock item
        async with get_db_connection() as db:
            await db.execute("UPDATE news_items SET status = 'processing_generation' WHERE id = ?", (item_id,))
            await db.commit()
            
        def get_cyrillic_ratio(text):
            text_no_html = re.sub(r'<[^>]+>', '', text)
            cyr_chars = len(re.findall(r'[а-яА-ЯёЁ]', text_no_html))
            alpha_chars = len(re.findall(r'[a-zA-Zа-яА-ЯёЁ]', text_no_html))
            return cyr_chars / alpha_chars if alpha_chars > 0 else 1.0

        def needs_retry(p):
            if not p or not p.post_html:
                return True
            return "<b>" not in p.post_html or get_cyrillic_ratio(p.post_html) < 0.45

        # Generate text
        post = await generate_post_text(title, summary, source_name, item_id, url, category=category)
        if post and needs_retry(post):
            logger.warning(f"Formatting/Language suboptimal for {item_id}, retrying once...")
            post_retry = await generate_post_text(title, summary, source_name, item_id, url, retry_format=True, category=category)
            if post_retry and post_retry.post_html:
                post = post_retry

        if post and post.post_html:
            # Guarantee <b> tag on header if missing
            if "<b>" not in post.post_html:
                parts = post.post_html.strip().split("\n\n", 1)
                if len(parts) == 2:
                    post.post_html = f"<b>{parts[0]}</b>\n\n{parts[1]}"
                else:
                    post.post_html = f"<b>{post.post_html}</b>"

            post.post_html = re.sub(r'<blockquote>\s+', '<blockquote>', post.post_html)
            post.post_html = re.sub(r'\s+</blockquote>', '</blockquote>', post.post_html)
        else:
            async with get_db_connection() as db:
                await db.execute("UPDATE news_items SET status = 'error' WHERE id = ?", (item_id,))
                await db.commit()
            if tracker:
                await tracker.add_error(f"Generator LLM error for item {item_id}")
            continue
            
        # Parse existing image URLs / paths from collector stage
        try:
            candidate_images = json.loads(existing_image_urls_json) if existing_image_urls_json else []
        except (json.JSONDecodeError, TypeError):
            candidate_images = []

        image_urls = filter_valid_local_images(candidate_images)
        status = 'pending_review'
        image_urls_json = json.dumps(image_urls)
                
        auto_published = False
        if config.auto_publish:
            async with get_db_connection() as db:
                async with db.execute("SELECT auto_publish_categories FROM bot_state WHERE id = 1") as c:
                    auto_cats = (await c.fetchone())[0]
                    
            if auto_cats and category in [c.strip() for c in auto_cats.split(',') if c.strip()]:
                auto_published = True
                status = 'published'

        async with get_db_connection() as db:
            if auto_published:
                await db.execute("""
                    UPDATE news_items 
                    SET status = ?, post_text_json = ?, image_urls = ?, updated_at = CURRENT_TIMESTAMP, auto_published = 1, decision_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, post.post_html, image_urls_json, item_id))
            else:
                await db.execute("""
                    UPDATE news_items 
                    SET status = ?, post_text_json = ?, image_urls = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, post.post_html, image_urls_json, item_id))
            await db.commit()
            
        if auto_published:
            from bot.telegram.admin_commands import send_auto_published_to_group
            await send_auto_published_to_group(bot_instance, item_id, category, url, post.post_html, image_urls)
            if tracker:
                await tracker.add_items_auto_published(1)
        else:
            # Strictly send to admin for review in PM
            from bot.telegram.handlers import send_draft_to_admin
            await send_draft_to_admin(bot_instance, item_id, post.post_html, image_urls)
            if tracker:
                await tracker.add_items_sent_moderation(1)
