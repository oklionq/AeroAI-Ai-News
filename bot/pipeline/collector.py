import asyncio
import json
from datetime import datetime, timezone
from bot.db import get_db_connection
from bot.utils.dedup import normalize_title, generate_hash, get_url_hash, title_similarity
from bot.sources.telegram_channel import fetch_telegram_channel
from bot.utils.logger import logger
from bot.config import config

async def run_collector(tracker=None):
    logger.info("Starting collector cycle.")
    
    async with get_db_connection() as db:
        # Re-enable sources disabled more than 6 hours ago
        await db.execute("""
            UPDATE sources 
            SET enabled = 1, fail_count = 0, disabled_at = NULL 
            WHERE enabled = 0 AND disabled_at IS NOT NULL AND disabled_at < datetime('now', '-6 hour')
        """)
        await db.commit()
        
        async with db.execute("SELECT id, name, type, url FROM sources WHERE enabled = 1") as cursor:
            sources = await cursor.fetchall()
            
    if not sources:
        # Ensure default source exists if table is empty
        async with get_db_connection() as db:
            await db.execute("""
                INSERT OR IGNORE INTO sources (id, name, type, url, enabled)
                VALUES (1, 'ThePerceptronChannel', 'telegram_channel', 'https://t.me/ThePerceptronChannel', 1)
            """)
            await db.commit()
            sources = [(1, 'ThePerceptronChannel', 'telegram_channel', 'https://t.me/ThePerceptronChannel')]

    if tracker:
        await tracker.set_sources_total(len(sources))
            
    all_items = []
    
    for s_id, s_name, s_type, s_url in sources:
        try:
            if s_type == "telegram_channel":
                items = await fetch_telegram_channel(s_id, s_name)
            else:
                logger.warning(f"Unknown or deprecated source type {s_type} for {s_name}. Using Telegram parser.")
                items = await fetch_telegram_channel(s_id, s_name)
                
            all_items.extend(items)
            if tracker:
                await tracker.add_source_ok()
        except Exception as e:
            logger.error(f"Collector error for {s_name}: {e}", exc_info=True)
            if tracker:
                await tracker.add_source_failed()
                await tracker.add_error(f"Source {s_name} ({s_type}): {e}")
            
    if tracker:
        await tracker.add_items_raw(len(all_items))
        
    await save_new_items(all_items, tracker)

async def save_new_items(items: list[dict], tracker=None):
    async with get_db_connection() as db:
        # Load recent items for deduplication
        async with db.execute("""
            SELECT title, title_hash, url_hash, telegram_source_msg_id 
            FROM news_items 
            WHERE created_at >= datetime('now', '-14 day')
        """) as cursor:
            recent_records = await cursor.fetchall()
            
        recent_url_hashes = {r[2] for r in recent_records if r[2]}
        recent_title_hashes = {r[1] for r in recent_records if r[1]}
        recent_msg_ids = {r[3] for r in recent_records if r[3] is not None}
        recent_titles_norm = [normalize_title(r[0]) for r in recent_records if r[0]]
        
        new_count = 0
        stale_count = 0
        now_utc = datetime.now(timezone.utc)

        for item in items:
            title = item["title"]
            url = item["url"]
            source_msg_id = item.get("source_msg_id")
            
            if not title or not url:
                continue

            # Exact message ID match from donor channel
            if source_msg_id and source_msg_id in recent_msg_ids:
                continue
                
            # Pre-filter by age
            is_stale = False
            pub_at = item.get("published_at")
            if not pub_at:
                pub_at = now_utc
            else:
                if pub_at.tzinfo is None:
                    pub_at = pub_at.replace(tzinfo=timezone.utc)
                age_hours = (now_utc - pub_at).total_seconds() / 3600.0
                if age_hours > config.max_news_age_hours:
                    is_stale = True
                    
            if is_stale:
                stale_count += 1
                continue
                
            title_norm = normalize_title(title)
            title_hash = generate_hash(title_norm) if title_norm else ""
            url_hash = get_url_hash(url)
            
            # Exact URL match
            if url_hash in recent_url_hashes:
                continue
                
            # Title dedup: only apply if no source_msg_id (e.g. non-Telegram sources)
            if not source_msg_id:
                if title_hash and len(title_norm) > 10 and title_hash in recent_title_hashes:
                    continue
                    
                is_dup = False
                for r_title_norm in recent_titles_norm:
                    if len(r_title_norm) > 10 and len(title_norm) > 10 and title_similarity(title_norm, r_title_norm) > 0.8:
                        is_dup = True
                        break
                if is_dup:
                    continue
                    
            # Insert new item directly as pending_generation (bypassing LLM filter)
            pub_str = pub_at.strftime("%Y-%m-%d %H:%M:%S") if pub_at else None
            await db.execute("""
                INSERT INTO news_items (
                    source_id, url, url_hash, title_hash, title, summary, 
                    published_at, image_urls, status, telegram_source_msg_id,
                    filter_category
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item["source_id"], url, url_hash, title_hash, title, 
                item["summary"], pub_str, item.get("image_urls", "[]"), 
                "pending_generation", source_msg_id, "other"
            ))
            
            if title_hash:
                recent_title_hashes.add(title_hash)
            recent_url_hashes.add(url_hash)
            if source_msg_id:
                recent_msg_ids.add(source_msg_id)
            if title_norm:
                recent_titles_norm.append(title_norm)
            new_count += 1
            
        await db.commit()
        if tracker:
            await tracker.add_items_filtered_stale(stale_count)
            await tracker.add_items_after_dedup(new_count)
            await tracker.add_items_passed_filter(new_count)
            
        logger.info(f"Collector finished. Inserted {new_count} new items (stale filtered: {stale_count}).")
