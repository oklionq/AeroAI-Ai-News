from bot.db import get_db_connection
from bot.services.openai_client import generate_post_text
from bot.services.budget_guard import check_budget
from bot.pipeline.images import fetch_og_image, fetch_reddit_image
from bot.utils.logger import logger
import datetime
async def run_generator_stage(bot_instance, tracker=None):
    if not await check_budget():
        return
        
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT n.id, n.title, n.summary, s.name, n.filter_category, n.url, n.image_url, s.type, n.published_at, n.subject
            FROM news_items n
            JOIN sources s ON n.source_id = s.id
            WHERE n.status = 'pending_generation'
            LIMIT 10
        """) as cursor:
            items = await cursor.fetchall()
            
    for item_id, title, summary, source_name, category, url, existing_image_url, source_type, pub_date_str, subject in items:
        if not await check_budget():
            break
            
        # Lock item
        async with get_db_connection() as db:
            await db.execute("UPDATE news_items SET status = 'processing_generation' WHERE id = ?", (item_id,))
            await db.commit()
            
        import re
        def get_cyrillic_ratio(text):
            text_no_html = re.sub(r'<[^>]+>', '', text)
            cyr_chars = len(re.findall(r'[а-яА-ЯёЁ]', text_no_html))
            alpha_chars = len(re.findall(r'[a-zA-Zа-яА-ЯёЁ]', text_no_html))
            return cyr_chars / alpha_chars if alpha_chars > 0 else 1.0
            
        def has_hallucinated_year(text, pub_date_str):
            years = re.findall(r'\b(20\d{2})\b', text)
            if not years:
                return False
            try:
                pub_year = datetime.datetime.fromisoformat(pub_date_str).year if pub_date_str else datetime.datetime.utcnow().year
            except:
                pub_year = datetime.datetime.utcnow().year
            current_year = datetime.datetime.utcnow().year
            for y_str in years:
                y = int(y_str)
                if y != pub_year and y != current_year:
                    return True
            return False

        def is_invalid(p):
            return "<b>" not in p.post_html or "<blockquote>" not in p.post_html or "<a href=" not in p.post_html or get_cyrillic_ratio(p.post_html) < 0.55 or has_hallucinated_year(p.post_html, pub_date_str)

        # Generate text
        post = await generate_post_text(title, summary, source_name, item_id, url, category=category)
        if post and is_invalid(post):
            logger.warning(f"Formatting/Language failed for {item_id}, retrying...")
            post = await generate_post_text(title, summary, source_name, item_id, url, retry_format=True, category=category)
            if post and is_invalid(post):
                logger.error(f"Formatting/Language retry failed for {item_id}")
                post = None
                
        if post:
            post.post_html = re.sub(r'<blockquote>\s+', '<blockquote>', post.post_html)
            post.post_html = re.sub(r'\s+</blockquote>', '</blockquote>', post.post_html)
        if not post:
            async with get_db_connection() as db:
                await db.execute("UPDATE news_items SET status = 'error' WHERE id = ?", (item_id,))
                await db.commit()
            if tracker:
                await tracker.add_error(f"Generator LLM error for item {item_id}")
            continue
            
        status = 'pending_review'
        # Fetch image
        image_url = None
        if source_type == 'reddit':
            image_url = await fetch_reddit_image(summary, url)
            
        if not image_url:
            image_url = existing_image_url
            
        if not image_url:
            image_url = await fetch_og_image(url)
        
        if not image_url:
            if category != 'model_release':
                status = 'skipped_no_image'
                
        auto_published = False
        if status == 'pending_review':
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
                    SET status = ?, post_text_json = ?, image_url = ?, updated_at = CURRENT_TIMESTAMP, auto_published = 1, decision_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, post.post_html, image_url, item_id))
            else:
                await db.execute("""
                    UPDATE news_items 
                    SET status = ?, post_text_json = ?, image_url = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, post.post_html, image_url, item_id))
            await db.commit()
            
        if auto_published:
            from bot.telegram.admin_commands import send_auto_published_to_group
            await send_auto_published_to_group(bot_instance, item_id, category, url, post.post_html, image_url)
            if tracker:
                await tracker.add_items_auto_published(1)
        elif status == 'pending_review':
            from bot.telegram.handlers import send_draft_to_admin
            await send_draft_to_admin(bot_instance, item_id, post.post_html, image_url)
            if tracker:
                await tracker.add_items_sent_moderation(1)
